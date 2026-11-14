"""Builder for generated simulation deployments: `fprime-wsts build`.

An overlay is an ordinary F Prime project, built with `fprime-util generate` (CMake's
configure step) and `fprime-util build`. This command runs the two for a deployment or for
every element of a system, and decides what the first one has to do:

- no build directory yet: configure;
- a build directory this command configured with the same settings: build only
  (`fprime-util generate` refuses an existing directory, and CMake picks up changed
  CMake files by itself);
- the settings a build directory caches have changed (`cmake.cache` of the manifest, values
  carried over from the flight project, the `-D` options): configure again from nothing,
  because a `set(... CACHE ...)` does not change what a build directory has cached;
- a build directory left by a configure step that failed: configure again.

The whole output of each element goes to a log, and the lines that matter of a failed
build are in the summary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .fpp import WstsError

STAMP = "wsts-build.json"  # in the build directory: the settings it was configured with

ERROR_PATTERNS = [
    re.compile(r": (fatal )?error:|^error:|: Error:"),                                            # compiler, FPP tools
    re.compile(r"undefined reference|multiple definition|cannot find -l|ld returned|collect2:"),  # linker
    re.compile(r"CMake Error|Configuring incomplete|not available to deployment|\[ERROR\]"),      # CMake, fprime-util
    re.compile(r"No such file or directory"),
]
NOISE = re.compile(r"CMakeExecutionException|ninja: build stopped")
ERROR_LINES = 20


def elements_of(directory: Path) -> List[Tuple[str, Path]]:
    """(name, overlay directory) of what a generated directory holds: the elements of a
    system, or the one deployment"""
    from .system import SYSTEM_FILE

    directory = Path(directory).resolve()
    if (directory / SYSTEM_FILE).is_file():
        description = json.loads((directory / SYSTEM_FILE).read_text())
        return [(element["name"], directory / element["directory"]) for element in description["elements"]]
    if (directory / "wsts-report.json").is_file():
        return [(directory.name, directory)]
    raise WstsError(f"{directory} is not a generated simulation deployment or system (no wsts-report.json or "
                    f"{SYSTEM_FILE}): give the directory `fprime-wsts generate` wrote, or the manifest")


def build_directories(directory: Path) -> List[Path]:
    return sorted(p for p in Path(directory).glob("build-fprime-automatic-*") if p.is_dir())


def configured(build_directory: Path) -> bool:
    """Whether CMake finished configuring a build directory"""
    return (build_directory / "CMakeCache.txt").is_file() and any(
        (build_directory / name).is_file() for name in ("build.ninja", "Makefile"))


def settings(directory: Path, options: List[str]) -> dict:
    """What a build directory caches when it is configured, and does not read again: the
    cache entries the overlay's root CMakeLists.txt sets, the CMake options of its
    settings.ini, and the options given when configuring"""
    root = directory / "CMakeLists.txt"
    cached = re.findall(r"(?m)^\s*set\(.*\bCACHE\b.*$", root.read_text(errors="replace")) if root.is_file() else []
    ini = directory / "settings.ini"
    text = "\n".join([*cached, ini.read_text(errors="replace") if ini.is_file() else "", *options])
    return {"options": list(options), "digest": hashlib.sha256(text.encode()).hexdigest()}


def plan(directory: Path, options: List[str], fresh: bool = False) -> Tuple[str, str]:
    """What the configure step has to do for an overlay: ("configure" | "reconfigure" |
    "keep", the reason)"""
    existing = build_directories(directory)
    if not existing:
        return "configure", "no build directory yet"
    if fresh:
        return "reconfigure", "--fresh"
    for build_directory in existing:
        if not configured(build_directory):
            return "reconfigure", f"{build_directory.name} was left by a configure step that did not finish"
    wanted = settings(directory, options)
    for build_directory in existing:
        stamp = build_directory / STAMP
        if not stamp.is_file():
            return "keep", (f"{build_directory.name} was configured outside `fprime-wsts build` and is used as it is "
                            "(--fresh configures it again)")
        try:
            had = json.loads(stamp.read_text())
        except ValueError:
            had = {}
        if had != wanted:
            what = "the -D options" if had.get("options") != wanted["options"] else \
                "the cached settings of the overlay (cmake.cache, values carried over from flight, settings.ini)"
            return "reconfigure", f"{what} changed since {build_directory.name} was configured"
    return "keep", "configured with the same settings"


def error_lines(log: Path) -> List[str]:
    """The lines of a build log that say what went wrong, in order, without repeats"""
    found: List[str] = []
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.rstrip()
        if NOISE.search(line) or not any(pattern.search(line) for pattern in ERROR_PATTERNS):
            continue
        if line not in found:
            found.append(line)
    return found[:ERROR_LINES]


def _run(command: List[str], cwd: Path, log, quiet: bool) -> int:
    log.write(f"$ (cd {cwd} && {' '.join(command)})\n")
    log.flush()
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               errors="replace")
    try:
        for line in process.stdout:
            log.write(line)
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise
    return process.wait()


def binary_of(directory: Path) -> Optional[Path]:
    found = sorted(p for p in directory.glob("build-artifacts/*/*/bin/*") if p.is_file() and os.access(p, os.X_OK))
    return found[0] if found else None


def build_element(name: str, directory: Path, log: Path, jobs: Optional[int] = None, fresh: bool = False,
                  defines: Optional[List[str]] = None, quiet: bool = False, executable: str = "fprime-util") -> dict:
    """Configure (when needed) and build one overlay. Returns its part of the summary."""
    report = json.loads((directory / "wsts-report.json").read_text())
    # Options the generator says must be given when configuring (what project() caches), then the caller's
    options = [*report.get("configure_options", []), *(f"-D{define}" for define in defines or [])]
    action, reason = plan(directory, options, fresh)
    result = {"directory": str(directory), "configure": action, "reason": reason, "log": str(log), "exit_code": 0}
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as out:
        if action != "keep":
            if not quiet:
                print(f"{name}: configuring ({reason})", flush=True)
            command = [executable, "generate", *(["-f"] if action == "reconfigure" else []), *options]
            result["exit_code"] = _run(command, directory, out, quiet)
            result["stage"] = "configure"
            if result["exit_code"] == 0:
                wanted = settings(directory, options)
                for build_directory in build_directories(directory):
                    (build_directory / STAMP).write_text(json.dumps(wanted, indent=2) + "\n")
        elif not quiet:
            print(f"{name}: not configuring again ({reason})", flush=True)
        if result["exit_code"] == 0:
            if not quiet:
                print(f"{name}: building", flush=True)
            command = [executable, "build", *(["-j", str(jobs)] if jobs else [])]
            result["exit_code"] = _run(command, directory, out, quiet)
            result["stage"] = "build"
    result["seconds"] = round(time.monotonic() - started, 1)
    if result["exit_code"] == 0:
        binary = binary_of(directory)
        result["binary"] = str(binary) if binary else None
    else:
        result["errors"] = error_lines(log)
    return result


def build(directory: Path, jobs: Optional[int] = None, fresh: bool = False, only: Optional[List[str]] = None,
          defines: Optional[List[str]] = None, quiet: bool = False, keep_going: bool = False) -> dict:
    """Build a generated deployment, or the elements of a generated system one after
    another. Stops at the first element that fails unless `keep_going`."""
    directory = Path(directory).resolve()
    elements = elements_of(directory)
    system = len(elements) > 1 or elements[0][1] != directory
    names = [name for name, _ in elements]
    for name in only or []:
        if name not in names:
            raise WstsError(f"--element {name}: not an element of {directory}"
                            + (f" (elements: {', '.join(names)})" if system else ", which is a single deployment"))
    executable = shutil.which("fprime-util")
    if executable is None:
        raise WstsError("fprime-util was not found on PATH: activate the virtual environment the F Prime tools are "
                        "installed in (pip install -r lib/fprime/requirements.txt)")
    for define in defines or []:
        if "=" not in define or not define.split("=", 1)[0]:
            raise WstsError(f"-D {define}: expected NAME=VALUE")
    summary: Dict[str, dict] = {}
    for name, element_directory in elements:
        if only and name not in only:
            continue
        log = directory / "wsts-logs" / name / "build.log" if system else directory / "wsts-logs" / "build.log"
        summary[name] = build_element(name, element_directory, log, jobs, fresh, defines, quiet, executable)
        if summary[name]["exit_code"] != 0 and not keep_going:
            break
    return {"output": str(directory), "ok": all(r["exit_code"] == 0 for r in summary.values()) and bool(summary),
            "elements": summary}


def build_cli(args) -> int:
    target = Path(args.target).resolve()
    if target.is_file():
        directory = _generate(target, args)
    elif target.is_dir():
        directory = target
    else:
        raise WstsError(f"{target} not found: give a generated directory, or a manifest to generate from first")
    jobs = args.jobs if args.jobs else os.cpu_count()
    result = build(directory, jobs=jobs, fresh=args.fresh, only=args.element, defines=args.define, quiet=args.json,
                   keep_going=args.keep_going)
    if args.json:
        print(json.dumps(result, indent=2))
    stream = sys.stdout if not args.json else sys.stderr
    for name, element in result["elements"].items():
        if element["exit_code"] == 0:
            if not args.json:
                print(f"{name}: built in {element['seconds']} s: {element['binary']}")
            continue
        print(f"{name}: {element['stage']} failed (exit code {element['exit_code']}); the whole output is in "
              f"{element['log']}", file=stream)
        for line in element["errors"]:
            print(f"  {line}", file=stream)
    skipped = [name for name, _ in elements_of(directory) if name not in result["elements"]
               and (not args.element or name in args.element)]
    if skipped:
        print(f"not built after that failure: {', '.join(skipped)} (--keep-going builds the others anyway)", file=stream)
    if result["ok"] and not args.json:
        print(f"Run it with: fprime-wsts run {directory} --gds")
    return 0 if result["ok"] else 1


def _generate(manifest: Path, args) -> Path:
    """`fprime-wsts generate` on a manifest, as the first step of a build. Its report goes
    to standard error when the build's own output is JSON."""
    import argparse
    import contextlib

    from .cli import cmd_generate

    options = argparse.Namespace(manifest=str(manifest), project=args.project, output=None, check=False, json=False)
    with contextlib.redirect_stdout(sys.stderr if args.json else sys.stdout):
        cmd_generate(options, hint=False)
    return options.result_output
