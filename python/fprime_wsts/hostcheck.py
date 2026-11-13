"""What the simulated build needs from the host, and whether this host has it.

A flight project is written for another toolchain and relies on what that toolchain's
sysroot provides. Two steps find that out before the build does:

- `requirements()` reads the sources and CMake files of the modules of the simulated build
  and lists what they take from outside the project: system headers, libraries named with
  `-l`, `find_package()` calls, files of an SDK. It reads text only, so its result is the
  same on every machine; `fprime-wsts generate` puts it in the report.
- `probe()` asks this host for each of them: the compiler for headers and libraries, CMake
  for packages. `fprime-wsts check-host` prints the result, and `fprime-wsts generate`
  a summary of it.

Nothing of the project is compiled or configured.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import flight_cmake
from .fpp import GENERATED_MARKER, WstsError

HOST_FILE = "wsts-host"  # wsts-host.json and wsts-host.md, beside the report; not checked in

CXX_STANDARD = """algorithm any array atomic barrier bit bitset charconv chrono codecvt compare complex concepts
condition_variable coroutine deque exception execution expected filesystem flat_map flat_set format forward_list
fstream functional future generator initializer_list iomanip ios iosfwd iostream istream iterator latch limits list
locale map mdspan memory memory_resource mutex new numbers numeric optional ostream print queue random ranges ratio
regex scoped_allocator semaphore set shared_mutex source_location span spanstream sstream stack stacktrace stdexcept
stdfloat stop_token streambuf string string_view strstream syncstream system_error thread tuple type_traits
typeindex typeinfo unordered_map unordered_set utility valarray variant vector version""".split()
C_STANDARD = """assert complex ctype errno fenv float inttypes iso646 limits locale math setjmp signal stdalign
stdarg stdatomic stdbit stdbool stdckdint stddef stdint stdio stdlib stdnoreturn string tgmath threads time uchar
wchar wctype""".split()
STANDARD_HEADERS = {*CXX_STANDARD, *(f"{name}.h" for name in C_STANDARD), *(f"c{name}" for name in C_STANDARD)}

#: Macros a C++ compile of the simulated build on a Linux host has, and ones it does not.
#: An include under any other condition is reported as conditional.
DEFINED = {"__cplusplus", "__linux__", "__linux", "linux", "__unix__", "__unix", "unix", "__GNUC__",
           "TGT_OS_TYPE_LINUX", "WSTS_SIM", "_POSIX_VERSION"}
UNDEFINED = {"_WIN32", "WIN32", "_WIN64", "__WIN32__", "__CYGWIN__", "__MINGW32__", "_MSC_VER", "__APPLE__",
             "__MACH__", "__VXWORKS__", "__vxworks", "VXWORKS", "_WRS_KERNEL", "__RTP__", "__QNX__", "__QNXNTO__",
             "__ZEPHYR__", "ARDUINO", "__rtems__", "__FreeBSD__", "__ANDROID__", "BUILD_UT", "__has_include"}
UNDEFINED_PATTERN = re.compile(r"TGT_OS_TYPE_(?!LINUX\b)\w+")

#: Usual development package of well-known headers and libraries, on Debian and Ubuntu.
#: A hint only: names differ between distributions and releases.
HEADER_HINTS = {
    "png.h": "libpng-dev", "jpeglib.h": "libjpeg-dev", "tiffio.h": "libtiff-dev", "zlib.h": "zlib1g-dev",
    "bzlib.h": "libbz2-dev", "lzma.h": "liblzma-dev", "zmq.h": "libzmq3-dev", "zmq.hpp": "cppzmq-dev",
    "czmq.h": "libczmq-dev", "mosquitto.h": "libmosquitto-dev", "curl/": "libcurl4-openssl-dev",
    "openssl/": "libssl-dev", "boost/": "libboost-all-dev", "Eigen/": "libeigen3-dev", "eigen3/": "libeigen3-dev",
    "opencv2/": "libopencv-dev", "yaml-cpp/": "libyaml-cpp-dev", "yaml.h": "libyaml-dev", "gsl/": "libgsl-dev",
    "google/protobuf/": "libprotobuf-dev", "sqlite3.h": "libsqlite3-dev", "expat.h": "libexpat1-dev",
    "libxml/": "libxml2-dev", "uuid/": "uuid-dev", "ncurses.h": "libncurses-dev", "readline/": "libreadline-dev",
    "fftw3.h": "libfftw3-dev", "hdf5.h": "libhdf5-dev", "cfitsio/": "libcfitsio-dev", "fitsio.h": "libcfitsio-dev",
    "libusb-1.0/": "libusb-1.0-0-dev", "gtest/": "libgtest-dev", "gmock/": "libgmock-dev", "pcap.h": "libpcap-dev",
    "nlohmann/": "nlohmann-json3-dev", "fmt/": "libfmt-dev", "spdlog/": "libspdlog-dev", "linux/": "linux-libc-dev",
    "sys/": "libc6-dev", "webp/": "libwebp-dev", "nanomsg/": "libnanomsg-dev",
}
LIBRARY_HINTS = {
    "png": "libpng-dev", "jpeg": "libjpeg-dev", "tiff": "libtiff-dev", "z": "zlib1g-dev", "bz2": "libbz2-dev",
    "lzma": "liblzma-dev", "zmq": "libzmq3-dev", "czmq": "libczmq-dev", "mosquitto": "libmosquitto-dev",
    "curl": "libcurl4-openssl-dev", "ssl": "libssl-dev", "crypto": "libssl-dev", "yaml-cpp": "libyaml-cpp-dev",
    "yaml": "libyaml-dev", "gsl": "libgsl-dev", "protobuf": "libprotobuf-dev", "sqlite3": "libsqlite3-dev",
    "expat": "libexpat1-dev", "xml2": "libxml2-dev", "uuid": "uuid-dev", "ncurses": "libncurses-dev",
    "readline": "libreadline-dev", "fftw3": "libfftw3-dev", "hdf5": "libhdf5-dev", "cfitsio": "libcfitsio-dev",
    "usb-1.0": "libusb-1.0-0-dev", "pcap": "libpcap-dev", "fmt": "libfmt-dev", "webp": "libwebp-dev",
    "nanomsg": "libnanomsg-dev", "asan": "libasan (the one of the compiler's version, e.g. libasan8 for GCC 13)",
    "ubsan": "libubsan (the one of the compiler's version)",
}

GENERATED_HEADER = re.compile(r"Ac\.(h|hpp|hh)$")
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\n]+)[>"]')
DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(ifdef|ifndef|if|elif|else|endif|define)\b[ \t]*(.*)$")
PRUNED = {".git", "__pycache__", "node_modules", "wsts-logs", "build-artifacts", ".pytest_cache"}


# ----------------------------------------------------------------------
# Headers
# ----------------------------------------------------------------------


def _walk_headers(directory: Path, stop_at: Set[Path]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for current, directories, files in os.walk(directory):
        here = Path(current)
        directories[:] = [
            d for d in directories
            if d not in PRUNED and not d.startswith("build-") and (here / d) not in stop_at
            and not (here / d / GENERATED_MARKER).exists()
        ]
        for name in files:
            if name.endswith(flight_cmake.HEADER_SUFFIXES) or "." not in name:
                index.setdefault(name, []).append((here / name).as_posix())
    return index


@lru_cache(maxsize=8)
def _fixed_index(directory: Path) -> Dict[str, List[str]]:
    """Headers of a tree that does not change while the generator runs (the framework, the toolkit)"""
    return _walk_headers(directory, set())


class HeaderIndex:
    """Finds the file an `#include` names among the build roots, the way the simulated
    build would: relative to the including file, relative to a build root, or under any
    directory of a root (a module may export an include directory of its own)."""

    def __init__(self, roots: Sequence[Path], walked: Sequence[Path] = (), fixed: Sequence[Path] = ()):
        """`roots` are the directories includes are written relative to. Headers anywhere
        below `walked` and `fixed` are found by the end of their path; `fixed` trees do not
        change while the generator runs and are read once."""
        self.roots = list(roots)
        everything = {*walked, *fixed}
        self.index: Dict[str, List[str]] = {}
        for directory in dict.fromkeys([*walked, *fixed]):
            found = _fixed_index(directory) if directory in fixed else _walk_headers(directory, everything - {directory})
            for name, paths in found.items():
                self.index.setdefault(name, []).extend(paths)

    def find(self, name: str, including: Path) -> Optional[Path]:
        beside = including.parent / name
        if beside.is_file():
            return beside.resolve()
        for root in self.roots:
            if (root / name).is_file():
                return (root / name).resolve()
        suffix = "/" + name
        for path in self.index.get(name.rpartition("/")[2], []):
            if path.endswith(suffix):
                return Path(path)
        return None


def _evaluate(kind: str, expression: str, defined: Set[str]) -> Optional[bool]:
    """A preprocessor condition, where it only asks whether a known macro is defined"""
    expression = re.sub(r"/\*.*?\*/|//.*", "", expression).strip()

    def known(name: str) -> Optional[bool]:
        if name in defined:
            return True
        if name in UNDEFINED or UNDEFINED_PATTERN.fullmatch(name):
            return False
        return None

    if kind in ("ifdef", "ifndef"):
        value = known(expression.split()[0]) if expression else None
        return value if kind == "ifdef" or value is None else not value
    if expression in ("0", "1"):
        return expression == "1"
    match = re.fullmatch(r"(!?)\s*defined\s*\(?\s*(\w+)\s*\)?", expression)
    if match:
        value = known(match.group(2))
        return value if value is None or not match.group(1) else not value
    match = re.fullmatch(r"(!?)\s*(\w+)", expression)
    if match and not match.group(2).isdigit():
        value = known(match.group(2))
        return value if value is None or not match.group(1) else not value
    return None


def read_includes(path: Path, defined: Set[str]) -> List[Tuple[str, bool, int, str]]:
    """The `#include` lines of a file that a compile on the host reaches:
    (name, written with <>, line, condition). The condition is empty when the line is
    always compiled, and the innermost `#if` that could not be decided otherwise. Lines
    under a condition that is known not to hold are left out, and so are lines under
    `__has_include`, which are optional by construction."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    found: List[Tuple[str, bool, int, str]] = []
    stack: List[Tuple[Optional[bool], str, bool]] = []  # (holds, as written, some branch already held)
    guard: Optional[str] = None
    for number, line in enumerate(lines, 1):
        directive = DIRECTIVE.match(line)
        if directive:
            kind, rest = directive.group(1), directive.group(2).strip()
            if kind in ("ifdef", "ifndef", "if"):
                state = _evaluate(kind, rest, defined)
                if "__has_include" in rest:
                    state = False
                written = f"#{kind} {rest}"
                if kind == "ifndef" and not stack and guard is None:
                    # An include guard: `#ifndef X` followed by `#define X`
                    following = next((l for l in lines[number:] if l.strip()), "")
                    define = DIRECTIVE.match(following)
                    if define and define.group(1) == "define" and define.group(2).split()[:1] == rest.split()[:1]:
                        guard, state = rest.split()[0], True
                stack.append((state, written, state is True))
            elif kind == "elif" and stack:
                _, _, held = stack[-1]
                state = False if held else _evaluate("if", rest, defined)
                if "__has_include" in rest:
                    state = False
                stack[-1] = (state, f"#elif {rest}", held or state is True)
            elif kind == "else" and stack:
                state, written, held = stack[-1]
                stack[-1] = (False if held else (None if state is None else not state), f"#else of {written}", True)
            elif kind == "endif" and stack:
                stack.pop()
            continue
        include = INCLUDE.match(line)
        if not include or any(state is False for state, _, _ in stack):
            continue
        condition = next((written for state, written, _ in reversed(stack) if state is None), "")
        found.append((include.group(2).strip(), include.group(1) == "<", number, condition))
    return found


def scan_headers(sources: Sequence[Tuple[Path, str]], index: HeaderIndex, follow: Callable[[Path], bool],
                 defined: Iterable[str], shown: Callable[[Path], str]) -> Dict[str, List[dict]]:
    """Headers that the given sources include, directly or through project headers, and
    that are in no build root: {header: [{module, file, line, condition?}]}. `sources` are
    (file, the module it is compiled in); a header for which `follow` holds is read in turn."""
    defined = {*DEFINED, *defined}
    needs: Dict[str, List[dict]] = {}
    seen: Dict[Path, str] = {}  # file -> the weakest condition it was reached under ("" is always)
    queue: List[Tuple[Path, str, str]] = [(source.resolve(), module, "") for source, module in sources]
    while queue:
        path, module, inherited = queue.pop(0)
        if path in seen and (seen[path] == "" or inherited != ""):
            continue
        seen[path] = inherited
        for name, angle, line, condition in read_includes(path, defined):
            condition = inherited or condition
            if "/" not in name and name in STANDARD_HEADERS:
                continue
            target = index.find(name, path)
            if target is not None:
                if follow(target):
                    queue.append((target, module, condition))
                continue
            if GENERATED_HEADER.search(name):
                continue  # autocoded by the build
            entry = {"module": module, "file": shown(path), "line": line, **({"condition": condition} if condition else {})}
            listed = needs.setdefault(name, [])
            if not any(e["file"] == entry["file"] and e["line"] == line for e in listed):
                listed.append(entry)
    for name, listed in needs.items():
        # Where something needs the header unconditionally, the conditional uses add nothing
        if any("condition" not in entry for entry in listed):
            needs[name] = [entry for entry in listed if "condition" not in entry]
    return dict(sorted(needs.items()))


# ----------------------------------------------------------------------
# CMake files: libraries, packages, search directories
# ----------------------------------------------------------------------

LINK_COMMANDS = ("target_link_libraries", "link_libraries", "target_link_options", "add_link_options")


def _where(command: flight_cmake.Command) -> str:
    """The condition a CMake command is under, other than BUILD_TESTING (which is decided)"""
    for text, in_else in reversed(command.conditions):
        if flight_cmake.TESTING not in text and text.split("(")[0].strip().lower() in ("if", "elseif"):
            return ("else of " if in_else else "") + text
    return ""


def scan_cmake(tree: flight_cmake.BuildTree, module_of: Callable[[Path], str], shown: Callable[[Path], str]) -> dict:
    """Libraries named with `-l`, `find_package()` calls and search directories in the
    CMake files a build root reads on the host"""
    libraries: Dict[str, List[dict]] = {}
    packages: Dict[str, dict] = {}
    include_directories: List[str] = []
    link_directories: List[str] = []
    for cmake_file, commands in tree.files.items():
        for command in commands:
            entry = {"module": module_of(cmake_file), "file": shown(cmake_file), "line": command.line}
            condition = _where(command)
            if condition:
                entry["condition"] = condition
            if command.name == "find_package" and command.args and command.args[0].lower() != "fprime":
                name = command.args[0]
                package = packages.setdefault(name, {"required": False, "components": [], "needed_by": []})
                options = command.args[1:]
                package["required"] = package["required"] or ("REQUIRED" in options and not condition)
                if "COMPONENTS" in options:
                    for component in options[options.index("COMPONENTS") + 1:]:
                        if component.isupper() and component in ("OPTIONAL_COMPONENTS", "REQUIRED", "QUIET", "CONFIG",
                                                                 "NO_MODULE", "MODULE", "EXACT", "GLOBAL"):
                            break
                        if component not in package["components"]:
                            package["components"].append(component)
                package["needed_by"].append(entry)
                continue
            if command.name.startswith("register_fprime_") or command.name in LINK_COMMANDS or (
                    command.name in ("set", "list") and command.args and command.args[0].endswith(("DEPS", "LIBS", "LIBRARIES"))):
                for token in command.args:
                    if token.startswith("-l") and len(token) > 2 and flight_cmake.is_literal(token):
                        libraries.setdefault(token[2:], []).append(entry)
            for name, target in (("include_directories", include_directories), ("link_directories", link_directories)):
                if command.name == name:
                    for token in command.args:
                        if token in ("BEFORE", "AFTER", "SYSTEM"):
                            continue
                        resolved = flight_cmake._resolved(token, cmake_file)
                        if resolved is not None and shown(resolved) not in target:
                            target.append(shown(resolved))
    return {"libraries": libraries, "packages": packages, "include_directories": include_directories,
            "link_directories": link_directories}


def module_sources(tree: flight_cmake.BuildTree, module_of: Callable[[Path], str]) -> List[Tuple[Path, str]]:
    """The source files the CMake files of a build root compile on the host. A module
    whose sources cannot be read from its CMakeLists.txt (they are in a variable) is
    taken to compile every source file in its directory."""
    found: List[Tuple[Path, str]] = []
    for cmake_file, commands in tree.files.items():
        directory = cmake_file.parent
        sources = [f for command in commands for f in flight_cmake.named_sources(command, directory)]
        registers = any(c.name.startswith(("register_fprime_", "add_library", "add_executable")) for c in commands)
        if not sources and registers and cmake_file.name == "CMakeLists.txt":
            sources = sorted(p for p in directory.iterdir() if p.suffix in flight_cmake.SOURCE_SUFFIXES and p.is_file())
        found += [(source, module_of(cmake_file)) for source in sources if source.is_file()]
    return found


def flight_compiler(toolchain: str, roots: Sequence[Path], shown: Callable[[Path], str]) -> Optional[dict]:
    """What the flight toolchain file says its compiler is, as written"""
    if not toolchain or toolchain == "native":
        return None
    for root in roots:
        candidate = root / "cmake" / "toolchain" / f"{toolchain}.cmake"
        if not candidate.is_file():
            continue
        found = {"toolchain_file": shown(candidate)}
        for command in flight_cmake.read_commands(candidate.read_text(errors="replace")):
            if command.name == "set" and len(command.args) >= 2 and command.args[0] in ("CMAKE_CXX_COMPILER", "CMAKE_C_COMPILER"):
                found.setdefault("cxx" if command.args[0] == "CMAKE_CXX_COMPILER" else "c", command.args[1])
        return found
    return None


# ----------------------------------------------------------------------
# Asking the host
# ----------------------------------------------------------------------


def host_compiler(env: Optional[Dict[str, str]] = None) -> Tuple[List[str], str]:
    """The C++ compiler CMake would pick on this host (as a command) and why it is that one"""
    env = os.environ if env is None else env
    if env.get("CXX", "").strip():
        command = shlex.split(env["CXX"])
        return (command if shutil.which(command[0], path=env.get("PATH")) else []), f"CXX={env['CXX']}"
    for name in ("c++", "g++", "clang++"):
        if shutil.which(name, path=env.get("PATH")):
            return [name], f"{name} on PATH"
    return [], "no c++, g++ or clang++ on PATH"


def _run(command: List[str], stdin: str = "", env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None):
    try:
        return subprocess.run(command, input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def _hint(table: Dict[str, str], name: str) -> str:
    for key, package in table.items():
        if name == key or (key.endswith("/") and name.startswith(key)):
            return package
    return ""


def _users(needs: List[dict], base: Optional[Path] = None) -> str:
    """Who needs something, with files as paths on this machine when `base` (the generated directory) is known"""
    def place(need: dict) -> str:
        file = os.path.normpath(base / need["file"]) if base is not None else need["file"]
        return f"{file}:{need['line']}" if need.get("line") else str(file)

    shown = [f"{n['module']} ({place(n)})" for n in needs[:3]]
    return ", ".join(shown) + (f" and {len(needs) - 3} more" if len(needs) > 3 else "")


def _stand_in(needs: List[dict], stubs: Dict[str, bool]) -> str:
    modules = list(dict.fromkeys(n["module"] for n in needs))
    with_stub = [m for m in modules if stubs.get(m)]
    text = ("stand in for the module: substitute its instances (substitute), leave it out "
            f"(cmake.exclude_subdirectories: [{', '.join(modules[:2])}])")
    if with_stub:
        text += f", or take its stub variant (cmake.stub_variants: [{', '.join(with_stub)}])"
    return text


def probe(requirements: dict, base: Path, env: Optional[Dict[str, str]] = None) -> dict:
    """Ask this host for everything `requirements` lists. `base` is the generated
    directory its paths are relative to. Returns {compiler, missing, notes, warnings, ok}."""
    env = dict(os.environ if env is None else env)
    ignore = set(requirements.get("ignore", []))
    stubs = {m["module"]: m.get("has_stub_variant", False) and not m.get("stub_variant")
             for m in requirements.get("modules", [])}
    missing: List[dict] = []
    notes: List[str] = []
    warnings: List[str] = []
    search = requirements.get("search", {})
    includes = [str((base / d).resolve()) for d in search.get("include_directories", [])]
    link_dirs = [str((base / d).resolve()) for d in search.get("link_directories", [])]
    flags = [part for flag in search.get("cxx_flags", []) for part in flag.split()]
    compiler, origin = host_compiler(env)
    result = {"compiler": {"command": " ".join(compiler), "from": origin, "version": "", "id": ""},
              "missing": missing, "notes": notes, "warnings": warnings}

    def add(kind: str, name: str, needs: List[dict], remedies: List[str], hint: str = "", detail: str = "") -> None:
        if name in ignore or (kind == "library" and f"-l{name}" in ignore):
            return
        conditional = bool(needs) and all("condition" in n for n in needs)
        if conditional:
            notes.append(f"{kind} {name} is not on this host. It is only needed under a condition the check cannot "
                         f"decide ({needs[0]['condition']}), in {_users(needs)}.")
            return
        missing.append({"kind": kind, "name": name, "needed_by": needs, "remedies": remedies,
                        **({"hint": hint} if hint else {}), **({"detail": detail} if detail else {})})

    # ---- Compiler ----
    if compiler:
        version = _run([*compiler, "--version"], env=env)
        if version.returncode != 0:
            compiler = []
            origin += " (it does not run)"
        else:
            first = (version.stdout.splitlines() or [""])[0]
            result["compiler"]["version"] = first
            number = _run([*compiler, "-dumpfullversion", "-dumpversion"], env=env).stdout.strip().splitlines()
            result["compiler"]["number"] = number[0] if number else ""
            result["compiler"]["id"] = "Clang" if "clang" in first.lower() else "GNU" if number else ""
    if not compiler:
        warnings.append(f"no C++ compiler to ask ({origin}): headers, libraries and the sanitizer runtime were not "
                        "checked. Set CXX, or put a compiler on PATH.")
    flight = flight_build_compiler(requirements, base)
    if flight:
        result["flight_compiler"] = flight
        if compiler and result["compiler"].get("number") and flight.get("version"):
            same = flight.get("id", "") == result["compiler"]["id"] and \
                flight["version"].split(".")[0] == result["compiler"]["number"].split(".")[0]
            if not same:
                notes.append(
                    f"the flight build uses {flight.get('id') or 'the compiler'} {flight['version']} "
                    f"({flight.get('from', '')}), this host {result['compiler']['version']}. Standard library headers "
                    "include one another differently between compilers and versions: code that uses memcpy, strlen or "
                    "uint32_t without including <cstring> or <cstdint> may compile for flight and not here. "
                    "cmake.cxx_flags: ['-include cstring'] in the manifest adds a missing include for the host build.")
        else:
            named = f"compiles with {flight['cxx']}" if flight.get("cxx") else "has a compiler of its own"
            notes.append(
                f"the flight toolchain ({flight.get('toolchain_file', '')}) {named}, this host uses "
                f"{result['compiler']['version'] or 'another one'}. Standard library headers include one another "
                "differently between compilers and versions: code that relies on an indirect include (memcpy without "
                "<cstring>) may compile for flight and not here. cmake.cxx_flags: ['-include cstring'] in the "
                "manifest adds a missing include for the host build.")

    # ---- Headers: one preprocessor run ----
    headers = requirements.get("system_headers", {})
    if compiler and headers:
        names = list(headers)
        program = "".join(f"#if __has_include(<{name}>)\nwsts_found {i}\n#endif\n" for i, name in enumerate(names))
        program += "wsts_probe_done\n"
        run = _run([*compiler, "-E", "-P", "-x", "c++", *[f"-I{d}" for d in includes], *flags, "-"], program, env)
        if "wsts_probe_done" in run.stdout:
            present = {int(m) for m in re.findall(r"wsts_found (\d+)", run.stdout)}
        else:  # a compiler without __has_include: one run per header
            present = {i for i, name in enumerate(names) if _run(
                [*compiler, "-E", "-x", "c++", *[f"-I{d}" for d in includes], *flags, "-"], f"#include <{name}>\n", env
            ).returncode == 0}
        for i, name in enumerate(names):
            if i in present:
                continue
            needs = headers[name]
            hint = _hint(HEADER_HINTS, name)
            add("header", name, needs, [
                *([f"install the package that has it (usually {hint}; a hint, names differ between distributions)"]
                  if hint else ["install the development package that has it"]),
                "point the build at the directory that has it: include_directories(<dir>) in a file named under "
                "cmake.include_early",
                _stand_in(needs, stubs),
            ], hint)

    # ---- Libraries ----
    for name, needs in requirements.get("libraries", {}).items():
        if not compiler:
            break
        run = _run([*compiler, "-x", "c++", "-", "-o", os.devnull, *[f"-L{d}" for d in link_dirs], f"-l{name}"],
                   "int main() { return 0; }\n", env)
        if run.returncode != 0:
            hint = _hint(LIBRARY_HINTS, name)
            add("library", name, needs, [
                *([f"install the package that has it (usually {hint}; a hint, names differ between distributions)"]
                  if hint else [f"install the development package that has lib{name}"]),
                "point the build at the directory that has it: link_directories(<dir>) in a file named under "
                "cmake.include_early",
                _stand_in(needs, stubs),
            ], hint)

    # ---- Packages: one CMake run ----
    packages = {n: p for n, p in requirements.get("packages", {}).items()}
    if packages:
        found = find_packages(packages, requirements.get("cache", {}), env)
        if found is None:
            warnings.append("find_package() calls were not checked: cmake did not run. Packages the build asks for: "
                            + ", ".join(packages))
        for name, package in packages.items():
            if found is None or found.get(name):
                continue
            if not package["required"]:
                notes.append(f"package {name} is not found on this host. The build asks for it without REQUIRED or "
                             f"under a condition, in {_users(package['needed_by'])}.")
                continue
            add("package", name, package["needed_by"], [
                f"install it, or tell CMake where it is (cmake.cache: {{{name}_DIR: <dir>}} or CMAKE_PREFIX_PATH)",
                _stand_in(package["needed_by"], stubs),
            ])

    # ---- SDK paths ----
    for dependency in requirements.get("sdk_paths", []):
        path = dependency.get("path")
        if path and (base / path).exists():
            continue
        needs = [{"module": dependency["module"], "file": dependency["file"], "line": 0}]
        entry = {"kind": "sdk file", "name": dependency["entry"], "needed_by": needs, "detail": dependency["problem"],
                 "remedies": [
                     *([f"point {dependency['variables'][0]} at a host build of the SDK (cmake.cache: "
                        f"{{{dependency['variables'][0]}: <path>}})"] if dependency.get("variables") else []),
                     _stand_in(needs, stubs)]}
        if dependency["entry"] not in ignore:
            missing.append(entry)

    # ---- Sanitizer runtime ----
    sanitizers = requirements.get("sanitizers", [])
    if sanitizers and compiler:
        option = "-fsanitize=" + ",".join(sanitizers)
        run = _run([*compiler, "-x", "c++", "-", "-o", os.devnull, option], "int main() { return 0; }\n", env)
        if run.returncode != 0 and "sanitizer runtime" not in ignore:
            last = (run.stderr.strip().splitlines() or [""])[-1]
            missing.append({"kind": "sanitizer runtime", "name": option, "needed_by": [], "detail": last, "remedies": [
                "install the sanitizer runtime of this compiler's version (libasan and libubsan; a hint)",
                "leave BUILD_TESTING off for the whole build and list the modules whose stub variant is wanted under "
                "cmake.stub_variants",
                "turn the sanitizers off (cmake.cache: {ENABLE_SANITIZER_ADDRESS: OFF, ENABLE_SANITIZER_LEAK: OFF, "
                "ENABLE_SANITIZER_UNDEFINED_BEHAVIOR: OFF})"]})
    result["ok"] = not missing
    return result


def find_packages(packages: Dict[str, dict], cache: Dict[str, List[str]], env: Dict[str, str]) -> Optional[Dict[str, bool]]:
    """Whether CMake finds each package on this host, in a project of its own. None when CMake does not run."""
    cmake = shutil.which("cmake", path=env.get("PATH"))
    if cmake is None:
        return None
    with tempfile.TemporaryDirectory(prefix="wsts-host-") as scratch:
        lines = ["cmake_minimum_required(VERSION 3.16)", "project(WstsHostCheck LANGUAGES C CXX)"]
        for name, package in packages.items():
            components = (" COMPONENTS " + " ".join(package["components"])) if package.get("components") else ""
            lines += [f"find_package({name} QUIET{components})",
                      f'file(APPEND "${{CMAKE_BINARY_DIR}}/found.txt" "{name}=${{{name}_FOUND}}\\n")']
        Path(scratch, "CMakeLists.txt").write_text("\n".join(lines) + "\n")
        definitions = [f"-D{name}={';'.join(values)}" for name, values in cache.items()
                       if not name.startswith("CMAKE_") or name in ("CMAKE_PREFIX_PATH", "CMAKE_MODULE_PATH")]
        run = _run([cmake, "-S", scratch, "-B", str(Path(scratch, "b")), *definitions], env=env)
        result = Path(scratch, "b", "found.txt")
        if run.returncode != 0 or not result.is_file():
            return None
        found = {}
        for line in result.read_text().splitlines():
            name, _, value = line.partition("=")
            found[name] = value.strip().upper() in ("1", "ON", "TRUE", "YES", "Y")
        return found


def flight_build_compiler(requirements: dict, base: Path) -> Optional[dict]:
    """The compiler of the flight build: from a flight build directory on this machine if
    there is one (compiler and version), else what the toolchain file names"""
    known = dict(requirements.get("flight_compiler") or {})
    project = requirements.get("flight_project")
    toolchain = requirements.get("flight_toolchain") or ""
    if project:
        for build in sorted((base / project).glob(f"build-fprime-automatic-{toolchain}*")):
            for detected in sorted(build.glob("CMakeFiles/*/CMakeCXXCompiler.cmake")):
                text = detected.read_text(errors="replace")
                values = dict(re.findall(r'set\((CMAKE_CXX_COMPILER(?:_ID|_VERSION)?) "([^"]*)"\)', text))
                if values.get("CMAKE_CXX_COMPILER_VERSION"):
                    return {**known, "cxx": values.get("CMAKE_CXX_COMPILER", known.get("cxx", "")),
                            "id": values.get("CMAKE_CXX_COMPILER_ID", ""),
                            "version": values["CMAKE_CXX_COMPILER_VERSION"], "from": f"{build.name}, the flight build directory"}
    return known or None


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def render(result: dict, requirements: dict, base: Optional[Path] = None) -> str:
    """The result of `probe()` as text. With `base`, the generated directory, files are
    shown by their paths on this machine."""
    compiler = result["compiler"]
    lines = [f"Host compiler: {compiler['version'] or 'none found'} ({compiler['from']})"]
    flight = result.get("flight_compiler")
    if flight:
        described = " ".join(x for x in (flight.get("id", ""), flight.get("version", "")) if x) \
            or flight.get("cxx", "not named by a plain set() in the toolchain file")
        origin = flight.get("from") or flight.get("toolchain_file", "")
        if base is not None and not flight.get("from") and origin:
            origin = os.path.normpath(base / origin)
        lines.append(f"Flight compiler: {described} ({origin})")
    counted = (f"{len(requirements.get('system_headers', {}))} system header(s), "
               f"{len(requirements.get('libraries', {}))} librar(ies), {len(requirements.get('packages', {}))} package(s), "
               f"{len(requirements.get('sdk_paths', []))} SDK file(s)")
    lines.append(f"Checked: {counted} that {len(requirements.get('modules', []))} module(s) of the simulated build take from the host")
    if result["missing"]:
        lines += ["", f"Missing on this host ({len(result['missing'])}):"]
    for item in result["missing"]:
        lines.append(f"  {item['kind']} {item['name']}" + (f": {item['detail']}" if item.get("detail") else ""))
        if item["needed_by"]:
            lines.append(f"    needed by {_users(item['needed_by'], base)}")
        lines += [f"    - {remedy}" for remedy in item["remedies"]]
    if not result["missing"]:
        lines += ["", "Nothing is missing on this host."]
    if result["notes"]:
        lines += ["", "Notes:"] + [f"  - {note}" for note in result["notes"]]
    if result["warnings"]:
        lines += ["", "Not checked:"] + [f"  - {warning}" for warning in result["warnings"]]
    if result["missing"] and requirements.get("ignore") is not None:
        lines += ["", "If the build does get one of these from somewhere this check does not see, list its name under "
                  "host.ignore in the manifest."]
    return "\n".join(lines) + "\n"


def summary(result: dict) -> List[str]:
    """A few lines for `fprime-wsts generate`"""
    lines = []
    if result["missing"]:
        kinds: Dict[str, List[str]] = {}
        for item in result["missing"]:
            kinds.setdefault(item["kind"], []).append(item["name"])
        listing = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in kinds.items())
        lines.append(f"this host lacks {len(result['missing'])} thing(s) the build needs ({listing})")
    lines += result["warnings"]
    return lines


def write(output: Path, result: dict, requirements: dict) -> None:
    (output / f"{HOST_FILE}.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / f"{HOST_FILE}.md").write_text(
        "# Host check\n\nWritten by `fprime-wsts generate` and `fprime-wsts check-host` for the machine they ran on. "
        "Not part of the checked-in output.\n\n```\n" + render(result, requirements, output) + "```\n")


def load_requirements(directory: Path) -> dict:
    report = directory / "wsts-report.json"
    if not report.is_file():
        raise WstsError(f"{report} not found: is {directory} a generated simulation deployment?")
    data = json.loads(report.read_text())
    if "host_requirements" not in data:
        raise WstsError(f"{report} has no host requirements: regenerate it with this version of fprime-wsts")
    return data["host_requirements"]
