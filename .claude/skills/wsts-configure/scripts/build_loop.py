#!/usr/bin/env python3
"""Generate, check the host, build and run a WSTS configuration, and say where it stops.

One pass over the stages a new configuration has to get through:

  generate   fprime-wsts generate <manifest>                     (deployment or system manifest)
  host       fprime-wsts check-host <generated dir>              (what the host lacks)
  build      fprime-util generate [options] && fprime-util build, per element
  run        fprime-wsts run <generated dir> --duration S --json (no GDS unless --gds)

Each stage's full output goes to <generated dir>/build-wsts-loop/<stage>[-<element>].log
(a build-* directory: `generate --check` and git ignore it). The summary names the first
stage that failed and the lines of its log that matter: generator errors, compiler and
linker errors, CMake errors, an F´ assert, an element that exited, links that did not come
up, bus errors.

Usage:
  build_loop.py MANIFEST [--stages generate,host,build,run] [--run-seconds 20] [--jobs N]
                [--fresh] [--gds] [--keep-going] [--json]

Exit status 0 when every stage asked for passed, 1 when one failed, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

STAGES = ["generate", "host", "build", "run"]
ERROR_PATTERNS = [
    re.compile(r": (fatal )?error:|^error:|: Error:"),                                  # compiler, FPP tools
    re.compile(r"undefined reference|multiple definition|cannot find -l|ld returned|collect2:"),  # linker
    re.compile(r"CMake Error|Configuring incomplete|not available to deployment|\[ERROR\]"),    # CMake, fprime-util
    re.compile(r"No such file or directory"),
]
PROGRESS = re.compile(r"^\[\d+/\d+\] |^FAILED: |^/usr/bin/|^\S*c\+\+ |^\S*g\+\+ |^\S*clang")  # ninja lines, commands
NOISE = re.compile(r"CMakeExecutionException|ninja: build stopped")


def run(command: List[str], cwd: Path, log: Path, timeout: Optional[float] = None, env: Optional[dict] = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as out:
        out.write(f"$ (cd {cwd} && {' '.join(shlex.quote(c) for c in command)})\n")
        out.flush()
        try:
            process = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout,
                                     env={**os.environ, **(env or {})})
        except FileNotFoundError as error:
            out.write(f"\n{error}\n")
            return 127
        except subprocess.TimeoutExpired:
            out.write(f"\ntimed out after {timeout} s\n")
            return 124
    return process.returncode


def excerpt(log: Path, limit: int = 25, context: int = 2) -> List[str]:
    """The lines of a log that say what went wrong, with a little context, first ones first"""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return []
    picked: List[int] = []
    for number, line in enumerate(lines):
        if NOISE.search(line) or PROGRESS.search(line):
            continue
        if any(p.search(line) for p in ERROR_PATTERNS):
            picked.append(number)
    out: List[str] = []
    shown = set()
    for number in picked:
        for n in range(max(0, number - (1 if lines[number].lstrip().startswith("CMake Error") else 0)),
                       min(len(lines), number + context + 1)):
            if n not in shown and not PROGRESS.search(lines[n]):
                shown.add(n)
                out.append(lines[n][:300])
        if len(out) >= limit:
            break
    if not out:
        out = lines[-12:]
    return out[:limit]


def generator_error(log: Path) -> List[str]:
    text = log.read_text(errors="replace")
    match = re.search(r"fprime-wsts: error: (.*)", text, re.S)
    return match.group(1).strip().splitlines()[:30] if match else excerpt(log)


def elements_of(output: Path) -> Dict[str, Path]:
    system = output / "wsts-system.json"
    if system.is_file():
        description = json.loads(system.read_text())
        return {e["name"]: output / e["directory"] for e in description["elements"]}
    return {output.name: output}


def configure_options(directory: Path) -> List[str]:
    report = directory / "wsts-report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("configure_options", [])
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", help="deployment manifest or system manifest")
    parser.add_argument("--stages", default=",".join(STAGES), help="comma-separated subset of " + ",".join(STAGES))
    parser.add_argument("--output", help="generated directory (default: what the manifest says)")
    parser.add_argument("--run-seconds", type=float, default=20.0, help="how long the run stage lasts (default 20)")
    parser.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--fresh", action="store_true", help="regenerate build directories (fprime-util generate -f)")
    parser.add_argument("--gds", action="store_true", help="run with a headless GDS (and the merged dictionary)")
    parser.add_argument("--keep-going", action="store_true", help="build the other elements when one fails")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        parser.error(f"unknown stage(s): {', '.join(unknown)}")
    manifest = Path(args.manifest).resolve()
    if not manifest.is_file():
        parser.error(f"{manifest} does not exist")

    summary: dict = {"manifest": str(manifest), "stages": {}, "failed": None}
    started = time.monotonic()

    def finish() -> int:
        summary["seconds"] = round(time.monotonic() - started, 1)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(render(summary))
        return 1 if summary["failed"] else 0

    def fail(stage: str, **details) -> int:
        summary["stages"].setdefault(stage, {}).update({"ok": False, **details})
        summary["failed"] = stage
        return finish()

    # ---- generate ----
    output: Optional[Path] = Path(args.output).resolve() if args.output else None
    scratch_log_dir = manifest.parent / "build-wsts-loop"
    if "generate" in stages:
        log = scratch_log_dir / "generate.log"
        command = ["fprime-wsts", "generate", str(manifest), "--json"] + (["--output", str(output)] if output else [])
        code = run(command, manifest.parent, log)
        text = log.read_text(errors="replace")
        if code != 0:
            return fail("generate", exit_code=code, log=str(log), error=generator_error(log))
        start = text.find("{")
        result = json.loads(text[start:text.rfind("}") + 1]) if start >= 0 else {}
        output = Path(result.get("output", output or ""))
        reports = result.get("reports") or {output.name: result.get("report", {})}
        summary["stages"]["generate"] = {
            "ok": True, "output": str(output), "log": str(log),
            "warnings": {name: r.get("warnings", []) for name, r in reports.items() if r.get("warnings")},
            "substitutions": {name: [f"{s['instance']}: {s['flight_component']} -> {s['surrogate']} [{s['chosen_by']}]"
                                     for s in r.get("substitutions", [])] for name, r in reports.items()},
            "notes": [line for line in text.splitlines() if line.startswith("note:")],
        }
    if output is None:
        output = guess_output(manifest)
        if output is None:
            return fail("generate", error=["no generated directory: run the generate stage, or pass --output"])
    summary["output"] = str(output)
    logs = output / "build-wsts-loop"
    elements = elements_of(output)

    # ---- host ----
    if "host" in stages:
        log = logs / "host.log"
        code = run(["fprime-wsts", "check-host", str(output), "--json"], output, log)
        text = log.read_text(errors="replace")
        start = text.find("{")
        try:
            result = json.loads(text[start:text.rfind("}") + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            result = {}
        per_element = result if result and "ok" not in result else {next(iter(elements)): result}
        def needed_by(item: dict) -> str:
            users = item.get("needed_by") or []
            users = users if isinstance(users, list) else [users]
            return ", ".join(u.get("module", str(u)) + (f" ({Path(u['file']).name}:{u['line']})" if u.get("file") else "")
                             if isinstance(u, dict) else str(u) for u in users[:2])

        missing = {name: [f"{m['kind']} {m['name']}, needed by {needed_by(m)}; remedies: {' | '.join(m.get('remedies', []))}"
                          for m in r.get("missing", [])] for name, r in per_element.items() if r.get("missing")}
        # The host check can be wrong (a header found another way): the build has the last word
        summary["stages"]["host"] = {"ok": code == 0, "log": str(log), "missing": missing,
                                     "note": None if code == 0 else
                                     "the build stage still runs: it confirms or refutes each missing item"}

    # ---- build ----
    if "build" in stages:
        built: Dict[str, dict] = {}
        failed = None
        for name, directory in elements.items():
            options = configure_options(directory)
            build_dirs = sorted(directory.glob("build-fprime-automatic-*"))
            entry: dict = {"directory": str(directory), "configure_options": options}
            if args.fresh or not build_dirs:
                log = logs / f"configure-{name}.log"
                code = run(["fprime-util", "generate", *options, *(["-f"] if args.fresh and build_dirs else [])],
                           directory, log, timeout=1800)
                entry["configure_log"] = str(log)
                if code != 0:
                    entry.update(ok=False, step="fprime-util generate", exit_code=code, error=excerpt(log))
                    built[name] = entry
                    failed = failed or name
                    if not args.keep_going:
                        break
                    continue
            log = logs / f"build-{name}.log"
            code = run(["fprime-util", "build", "-j", str(args.jobs)], directory, log, timeout=7200)
            entry["build_log"] = str(log)
            if code != 0:
                entry.update(ok=False, step="fprime-util build", exit_code=code, error=excerpt(log))
                failed = failed or name
                built[name] = entry
                if not args.keep_going:
                    break
                continue
            entry["ok"] = True
            built[name] = entry
        summary["stages"]["build"] = {"ok": failed is None, "elements": built}
        if failed:
            summary["failed"] = "build"
            return finish()

    # ---- run ----
    if "run" in stages:
        log = logs / "run.log"
        command = ["fprime-wsts", "run", str(output), "--duration", str(args.run_seconds), "--json"]
        if args.gds:
            command.append("--gds")
        code = run(command, output, log, timeout=args.run_seconds + 300)
        text = log.read_text(errors="replace")
        start = text.find("{\n")
        try:
            result = json.loads(text[start:text.rfind("}") + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            result = {}
        per_element = result.get("elements") or ({output.name: result} if result else {})
        findings = []
        for name, element in per_element.items():
            status = element.get("final_status", {})
            if element.get("exited_early"):
                findings.append(f"{name}: exited on its own (exit code {element.get('exit_code')})")
            if element.get("assert"):
                findings.append(f"{name}: F´ assert {json.dumps(element['assert'])}")
            if element.get("error"):
                findings.append(f"{name}: {element['error']}")
            if status.get("errors"):
                findings.append(f"{name}: {status['errors']} bus error(s) in {status.get('transactions', '?')} "
                                "transaction(s): an endpoint without a model (I2C, register), a fault, or a model that "
                                "refuses what flight sends. Record a run to see which endpoint (--record)")
            if status.get("links") and status.get("links_up", 0) < status.get("links", 0):
                findings.append(f"{name}: only {status.get('links_up')} of {status.get('links')} link end(s) connected")
            sim_log = Path(element.get("logs", "")) / "sim.log"
            if sim_log.is_file():
                warnings = [l for l in sim_log.read_text(errors="replace").splitlines()
                            if re.search(r"WARNING_HI|FATAL|\bASSERT|Assert|\[ERROR\]", l)]
                if warnings:
                    findings.append(f"{name}: {len(warnings)} warning/error line(s) in {sim_log}, first: {warnings[0].strip()[:200]}")
        ok = code == 0 and not any("exited" in f or "assert" in f.lower() for f in findings)
        summary["stages"]["run"] = {"ok": ok, "exit_code": code, "log": str(log), "findings": findings,
                                    "summary": result or excerpt(log)}
        if not ok:
            summary["failed"] = "run"
    return finish()


def guess_output(manifest: Path) -> Optional[Path]:
    """The generated directory of a manifest that was generated before (from its marker)"""
    text = manifest.read_text(errors="replace")
    match = re.search(r"^output:\s*(\S+)", text, re.M)
    if match:
        return (manifest.parent / match.group(1)).resolve()
    name = re.search(r"^(system|deployment):\s*(\S+)", text, re.M)
    if name:
        candidate = (manifest.parent / "wsts" / Path(name.group(2)).name).resolve()
        if candidate.is_dir():
            return candidate
    return None


def render(summary: dict) -> str:
    out = [f"manifest: {summary['manifest']}"]
    if summary.get("output"):
        out.append(f"generated: {summary['output']}")
    for stage in STAGES:
        info = summary["stages"].get(stage)
        if info is None:
            continue
        out.append(f"\n[{stage}] {'ok' if info.get('ok') else 'FAILED'}")
        if stage == "generate":
            for name, subs in info.get("substitutions", {}).items():
                out.append(f"  {name}: {len(subs)} substitution(s)")
            for name, warnings in info.get("warnings", {}).items():
                for w in warnings:
                    out.append(f"  warning ({name}): {w}")
            for line in info.get("error", []):
                out.append(f"  | {line}")
        elif stage == "host":
            for name, items in info.get("missing", {}).items():
                for item in items:
                    out.append(f"  missing ({name}): {item}")
            if info.get("note"):
                out.append(f"  note: {info['note']}")
        elif stage == "build":
            for name, entry in info.get("elements", {}).items():
                out.append(f"  {name}: {'ok' if entry.get('ok') else 'FAILED at ' + entry.get('step', '?')}"
                           + (f" (options: {' '.join(entry['configure_options'])})" if entry.get("configure_options") else ""))
                for line in entry.get("error", []):
                    out.append(f"  | {line}")
        elif stage == "run":
            for finding in info.get("findings", []):
                out.append(f"  {finding}")
            if not info.get("ok") and not info.get("findings"):
                summary_text = info.get("summary")
                for line in (summary_text if isinstance(summary_text, list) else json.dumps(summary_text, indent=1).splitlines())[:20]:
                    out.append(f"  | {line}")
        if info.get("log"):
            out.append(f"  log: {info['log']}")
    if summary["failed"]:
        verdict = f"FAILED at {summary['failed']}"
    elif not summary["stages"].get("host", {}).get("ok", True):
        verdict = "no stage stopped, but the host check lists missing items: settle each one (the build confirms them)"
    elif summary["stages"].get("run", {}).get("findings"):
        verdict = "all stages passed; the run has findings to review (above)"
    else:
        verdict = "all stages passed"
    out.append(f"\n{verdict} ({summary.get('seconds', 0)} s)")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
