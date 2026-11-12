"""Reading the flight project's CMake as text.

The generator never configures the flight build: on the host it usually cannot. What it
needs from the CMake files it reads from their text, and this module holds the parts that
go beyond single `register_fprime_*()` calls:

- the flight project's root `CMakeLists.txt`: what it does besides including F Prime, and
  which of that the simulated build takes over (`read_root`);
- which module directories a module adds itself, so that the overlay does not add them a
  second time (`added_directories`, `order_modules`);
- link dependencies of a module that are files of a target SDK (`sdk_dependencies`).

Nothing here evaluates CMake. A literal value is used; anything that needs a variable, a
condition or a generator expression is reported and left to the user.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .fpp import WstsError, read_calls

# ----------------------------------------------------------------------
# Commands of a CMake file
# ----------------------------------------------------------------------


@dataclass
class Command:
    name: str  # lower case
    args: List[str]  # quotes removed
    text: str  # as written, on one line
    line: int
    depth: int = 0  # how many if()/foreach()/while() blocks it is inside
    condition: str = ""  # the innermost enclosing block, as written


BLOCKS = {"if": "endif", "foreach": "endforeach", "while": "endwhile", "function": "endfunction",
          "macro": "endmacro", "block": "endblock"}
DEFINITIONS = {"function", "macro"}


def _strip_comments(text: str) -> str:
    """The text with comments blanked, same length and line count"""
    out = list(text)
    i = 0
    while i < len(text):
        char = text[i]
        if char == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif char == "#":
            bracket = re.match(r"#\[(=*)\[", text[i:])
            if bracket:
                close = text.find("]" + bracket.group(1) + "]", i)
                end = len(text) if close < 0 else close + len(bracket.group(1)) + 2
            else:
                end = text.find("\n", i)
                end = len(text) if end < 0 else end
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
        else:
            i += 1
    return "".join(out)


def _arguments(body: str) -> List[str]:
    found = []
    for token in re.findall(r'"(?:[^"\\]|\\.)*"|\((?:[^()]|\([^()]*\))*\)|[^\s()"]+(?:"(?:[^"\\]|\\.)*")?', body):
        found.append(token[1:-1] if token.startswith('"') and token.endswith('"') and len(token) >= 2 else token)
    return found


def read_commands(text: str) -> List[Command]:
    """The commands of a CMake file in order, with the block each is inside. The body of
    a function or macro definition is one command (`function`), not read further."""
    clean = _strip_comments(text)
    commands: List[Command] = []
    stack: List[Tuple[str, str]] = []  # (block command, its text)
    position = 0
    pattern = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
    while True:
        match = pattern.search(clean, position)
        if not match:
            break
        depth, end = 0, match.end() - 1
        in_string = False
        while end < len(clean):
            char = clean[end]
            if in_string:
                if char == "\\":
                    end += 1
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        name = match.group(1).lower()
        body = clean[match.end():end]
        written = " ".join(clean[match.start():end + 1].split())
        line = clean.count("\n", 0, match.start()) + 1
        position = end + 1
        if name in DEFINITIONS:
            # Up to the matching end: what a definition holds runs only when it is called
            closing = re.compile(r"\b" + BLOCKS[name] + r"[ \t]*\([^)]*\)", re.I).search(clean, position)
            position = closing.end() if closing else len(clean)
            commands.append(Command(name, _arguments(body), written + " ... " + BLOCKS[name] + "()", line,
                                    len(stack), stack[-1][1] if stack else ""))
            continue
        if name in BLOCKS.values():
            if stack:
                stack.pop()
            continue
        if name in ("else", "elseif"):
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1] if name == "else" else written)
            continue
        if name in BLOCKS:
            stack.append((name, written))
            continue
        commands.append(Command(name, _arguments(body), written, line, len(stack), stack[-1][1] if stack else ""))
    return commands


def is_literal(value: str) -> bool:
    """Whether a CMake argument means the same wherever it is read"""
    return "${" not in value and "$<" not in value and "$ENV{" not in value and "$CACHE{" not in value


# ----------------------------------------------------------------------
# The flight project's root CMakeLists.txt
# ----------------------------------------------------------------------

#: Commands of a root CMakeLists.txt that every F Prime project has, or that change nothing
#: in the build
BOILERPLATE = {"cmake_minimum_required", "project", "cmake_policy", "message", "enable_testing", "enable_language",
               "fprime_setup_included_code", "add_fprime_subdirectory", "include_guard", "mark_as_advanced"}
#: Variables the overlay sets itself, from its own settings.ini
OVERLAY_VARIABLES = {"FPRIME_INCLUDE_FRAMEWORK_CODE", "FPRIME_FRAMEWORK_PATH", "FPRIME_PROJECT_ROOT",
                     "FPRIME_LIBRARY_LOCATIONS", "FPRIME_SETTINGS_FILE", "FPRIME_ENVIRONMENT_FILE",
                     "FPRIME_AC_CONSTANTS_FILE", "FPRIME_CONFIG_DIR"}
#: Variables that select the flight toolchain, platform or compiler behaviour
TOOLCHAIN_VARIABLES = re.compile(r"CMAKE_\w+|FPRIME_PLATFORM|FPRIME_TOOLCHAIN\w*|FPRIME_USE_\w+")
VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CACHE_HELP = "Carried over by fprime-wsts from the flight project's CMakeLists.txt"

KEY_CACHE, KEY_DEFINES, KEY_EARLY, KEY_LATE = "cmake.cache", "defines", "cmake.include_early", "cmake.include"


@dataclass
class RootCMake:
    """What the flight project's root CMakeLists.txt does besides including F Prime"""
    file: Optional[Path] = None
    cache: Dict[str, List[str]] = field(default_factory=dict)  # variables set before F Prime is read
    defines_early: List[str] = field(default_factory=list)  # compile definitions made before F Prime is read
    defines_late: List[str] = field(default_factory=list)  # ... and after it: project modules only
    carried: List[dict] = field(default_factory=list)  # {"line", "statement", "as"}
    not_carried: List[dict] = field(default_factory=list)  # {"line", "statement", "why", "key"}
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.carried or self.not_carried)


def _includes_fprime(command: Command) -> bool:
    if command.name == "find_package":
        return bool(command.args) and command.args[0].lower() == "fprime"
    if command.name == "include" and command.args:
        return Path(command.args[0]).name in ("FPrime.cmake", "FPrime-Code.cmake")
    return False


def _set_values(command: Command) -> Tuple[Optional[List[str]], str]:
    """The values of a `set(NAME ...)`, or None and why they cannot be used"""
    values = command.args[1:]
    if "PARENT_SCOPE" in values:
        return None, "it sets the variable in a parent scope"
    if "CACHE" in values:
        values = values[:values.index("CACHE")]  # CACHE <type> <help> [FORCE]
    if not values:
        return None, "it unsets the variable"
    if not all(is_literal(v) for v in values):
        return None, "its value is not a plain one (it uses a variable or a generator expression)"
    if any(";" in v or '"' in v or "\n" in v for v in values):
        return None, "its value has ';' or quotes in it"
    return values, ""


def read_root(project_root: Path, framework: Path, manifest_cache: Iterable[str], manifest_defines: Iterable[str],
              include_base: Path, shown_as: Optional[str] = None) -> RootCMake:
    """Read `<project root>/CMakeLists.txt` and decide, statement by statement, what the
    simulated build takes over.

    Carried over: `set()` of plain values and `option()` made before F Prime is read (as
    cache entries), and `add_compile_definitions()` of plain values. Everything else that
    is not F Prime boilerplate is reported with the manifest key that reproduces it. A
    variable or definition the manifest sets is the manifest's.

    `include_base` is the directory the manifest's `cmake.include` paths are relative to;
    `shown_as` is how messages name the file (the report must not hold absolute paths).
    """
    file = project_root / "CMakeLists.txt"
    root = RootCMake(file if file.is_file() else None)
    if root.file is None:
        return root
    manifest_cache = set(manifest_cache)
    defined_by_manifest = {d.split("=", 1)[0] for d in manifest_defines} | {"WSTS_SIM"}
    after_fprime = False

    def carried(command: Command, how: str) -> None:
        root.carried.append({"line": command.line, "statement": command.text, "as": how})

    def left(command: Command, why: str, key: str, yaml: str = "") -> None:
        root.not_carried.append({"line": command.line, "statement": command.text, "why": why, "key": key,
                                 **({"yaml": yaml} if yaml else {})})

    for command in read_commands(file.read_text(errors="replace")):
        if _includes_fprime(command):
            after_fprime = True
            continue
        name = command.name
        if name in BOILERPLATE or (name == "include" and command.args and command.args[0] in ("CTest", "GNUInstallDirs")):
            continue
        if name == "add_subdirectory" and command.args:
            target = command.args[0].replace("${CMAKE_CURRENT_LIST_DIR}", str(project_root)) \
                .replace("${CMAKE_CURRENT_SOURCE_DIR}", str(project_root))
            if is_literal(target) and (project_root / target).resolve() == framework.resolve():
                continue  # the framework, added by hand
        variable = command.args[0] if command.args else ""
        if name in ("set", "option") and variable in OVERLAY_VARIABLES:
            continue
        position_key = KEY_LATE if after_fprime else KEY_EARLY
        inside = f"it is inside {command.condition}, which is not evaluated" if command.depth else ""

        if name in ("set", "option") and VARIABLE_NAME.fullmatch(variable):
            if variable in manifest_cache:
                carried(command, f"not needed: cmake.cache in the manifest sets {variable}")
                continue
            if name == "option":
                values, why = ([command.args[2]] if len(command.args) > 2 else ["OFF"]), ""
                if not is_literal(values[0]):
                    values, why = None, "its value is not a plain one"
            else:
                values, why = _set_values(command)
            why = inside or why
            if not why and TOOLCHAIN_VARIABLES.fullmatch(variable):
                why = "it selects the flight toolchain, platform or compiler settings"
            if not why and after_fprime:
                why = "it is made after F Prime is read, where only the statements after it see it"
            if why:
                suggestion = " ".join(values) if values else "<value for the simulated build>"
                if after_fprime and not inside:
                    left(command, why, KEY_LATE)
                else:
                    left(command, why, KEY_CACHE,
                         f"cmake:\n  cache:\n    {variable}: {suggestion if values is None or len(values) == 1 else '[' + ', '.join(values) + ']'}")
                continue
            root.cache[variable] = values
            carried(command, f"cache entry {variable}=" + ";".join(values))
            continue

        if name in ("add_compile_definitions", "add_definitions"):
            definitions = command.args
            usable = bool(definitions) and all(is_literal(d) and '"' not in d and ";" not in d for d in definitions)
            if name == "add_definitions":
                usable = usable and all(d.startswith("-D") and len(d) > 2 for d in definitions)
                definitions = [d[2:] for d in definitions] if usable else definitions
            if inside or not usable:
                left(command, inside or "its values are not plain ones", KEY_DEFINES,
                     "defines:\n" + "".join(f"  - {d}\n" for d in definitions if is_literal(d)).rstrip("\n")
                     if any(is_literal(d) for d in definitions) else "defines:\n  - <NAME>=<value>")
                continue
            kept = [d for d in definitions if d.split("=", 1)[0] not in defined_by_manifest]
            dropped = [d for d in definitions if d not in kept]
            target = root.defines_late if after_fprime else root.defines_early
            target += [d for d in kept if d not in root.defines_early + root.defines_late]
            how = "compile definition" + ("s" if len(kept) != 1 else "") + (" " + " ".join(kept) if kept else "")
            if after_fprime and kept:
                how += " (for the modules added after F Prime is read, as in flight)"
            if dropped:
                how = (how + "; " if kept else "") + "the manifest's 'defines' set " + ", ".join(d.split("=", 1)[0] for d in dropped)
            carried(command, how)
            continue

        if name == "include" and command.args and not inside:
            target = command.args[0].replace("${CMAKE_CURRENT_LIST_DIR}", str(project_root)) \
                .replace("${CMAKE_CURRENT_SOURCE_DIR}", str(project_root))
            if is_literal(target) and (project_root / target).is_file():
                relative = Path(os.path.relpath((project_root / target).resolve(), include_base)).as_posix()
                left(command, "what an included file does is not read", position_key,
                     f"cmake:\n  {position_key.split('.')[1]}: [{relative}]")
                continue
        why = inside or {
            "add_compile_options": "compiler options written for the flight compiler may not apply to the host's",
            "include_directories": "an include directory may belong to the target's SDK",
            "link_directories": "a link directory may belong to the target's SDK",
            "link_libraries": "a library may belong to the target's SDK",
        }.get(name, "it acts on targets, some of which are not part of the simulated build" if after_fprime
              else "what it does for the flight build is not known")
        left(command, why, position_key)

    relative_file = shown_as or str(file)
    groups: Dict[str, List[dict]] = {}
    for entry in root.not_carried:
        groups.setdefault(entry["key"], []).append(entry)
    for key in (KEY_CACHE, KEY_DEFINES, KEY_EARLY, KEY_LATE):
        entries = groups.get(key)
        if not entries:
            continue
        listing = "".join(f"\n    line {e['line']}: {e['statement']} ({e['why']})" for e in entries)
        head = (f"{relative_file}: {len(entries)} statement(s) of the flight project's root CMakeLists.txt "
                f"are not carried over to the simulated build:{listing}\n  ")
        if key == KEY_CACHE:
            advice = ("If the simulated build needs these variables (a library's library.cmake may stop without "
                      "them), give them values for the host in the manifest:\n"
                      + _indent(_merge_yaml([e["yaml"] for e in entries])))
        elif key == KEY_DEFINES:
            advice = ("If the simulated build needs these definitions, add them to the manifest:\n"
                      + _indent(_merge_yaml([e["yaml"] for e in entries])))
        else:
            when = ("before the framework and the libraries are read" if key == KEY_EARLY
                    else "after every module is added, before the deployment")
            option = key.split(".")[1]
            named = [e["yaml"] for e in entries if e.get("yaml")]
            example = "flight-early.cmake" if key == KEY_EARLY else "flight-late.cmake"
            advice = (f"If the simulated build needs them, copy them into a CMake file of the simulation "
                      f"(${{CMAKE_CURRENT_LIST_DIR}} and relative paths in it mean that file's directory, and a target "
                      f"that the simulated build does not have needs an `if (TARGET ...)`), and have it included {when}:\n"
                      + _indent(f"cmake:\n  {option}: [{example}]"))
            if named:
                advice += ("\n  A file the flight project includes can be named directly, if all of it applies to "
                           "the host:\n" + _indent(_merge_yaml(named)))
        root.warnings.append(head + advice)
    return root


def _indent(text: str, by: str = "    ") -> str:
    return "\n".join(by + line for line in text.splitlines())


def _merge_yaml(snippets: List[str]) -> str:
    """Several `a:\n  b:\n    c: d` snippets as one, sharing their leading keys"""
    lines: List[str] = []
    for snippet in snippets:
        for line in snippet.splitlines():
            if line.rstrip().endswith(":") and line in lines:
                continue
            lines.append(line)
    # `key: [a]` and `key: [b]` of the same list key become one list
    merged: List[str] = []
    for line in lines:
        match = re.fullmatch(r"(\s*[\w.]+: )\[(.*)\]", line)
        previous = re.fullmatch(r"(\s*[\w.]+: )\[(.*)\]", merged[-1]) if merged else None
        if match and previous and match.group(1) == previous.group(1):
            merged[-1] = f"{match.group(1)}[{previous.group(2)}, {match.group(2)}]"
        else:
            merged.append(line)
    return "\n".join(merged)


# ----------------------------------------------------------------------
# Modules that add other modules
# ----------------------------------------------------------------------


def _resolved(token: str, cmake_file: Path) -> Optional[Path]:
    token = token.replace("${CMAKE_CURRENT_LIST_DIR}", str(cmake_file.parent))
    token = token.replace("${CMAKE_CURRENT_SOURCE_DIR}", str(cmake_file.parent))
    if not is_literal(token):
        return None
    return (cmake_file.parent / token).resolve()


def added_directories(directory: Path, seen: Optional[Set[Path]] = None) -> Dict[Path, Path]:
    """Every directory that `directory/CMakeLists.txt` adds, itself or through what it adds:
    {added directory: the CMake file with the call}. Follows `add_fprime_subdirectory()`,
    `add_subdirectory()` and `include()` of files named literally; a path that needs a
    variable other than the current directory's is not seen."""
    found: Dict[Path, Path] = {}
    seen = seen if seen is not None else set()

    def read(cmake_file: Path) -> None:
        if cmake_file in seen or not cmake_file.is_file():
            return
        seen.add(cmake_file)
        for function in ("add_fprime_subdirectory", "add_subdirectory"):
            for call in read_calls(cmake_file, function):
                tokens = call.get("_", [])
                target = _resolved(tokens[0], cmake_file) if tokens else None
                if target is None or target == cmake_file.parent or not target.is_dir():
                    continue
                found.setdefault(target, cmake_file)
                read(target / "CMakeLists.txt")
        for call in read_calls(cmake_file, "include"):
            tokens = call.get("_", [])
            target = _resolved(tokens[0], cmake_file) if tokens else None
            if target is not None and target.is_file():
                read(target)

    read(directory.resolve() / "CMakeLists.txt")
    return found


@dataclass
class ModuleOrder:
    roots: List[Path]  # the module directories the overlay adds itself, in order
    nested: Dict[Path, Tuple[Path, Path]] = field(default_factory=dict)  # child -> (module that adds it, CMake file)
    members: Dict[Path, List[Path]] = field(default_factory=dict)  # root -> every directory it brings in


def order_modules(selected: List[Path], overriding: Set[Path], supplies: Dict[Path, Set[str]],
                  overrides: Dict[Path, Set[str]], relative_to: Path, excluded: Iterable[Path] = (),
                  last: Iterable[Path] = ()) -> ModuleOrder:
    """The order in which the overlay adds project modules.

    `selected` are the module directories the simulated deployment needs, in the order they
    were found. One that another of them adds itself is left out: F Prime registers it when
    its parent is added, and adding a directory twice is a CMake error. The rule for the
    rest is the one it always was: a module with CONFIGURATION_OVERRIDES (`overriding`, or
    one that brings such a module in) comes after the others, and the directories in `last`
    (the manifest's `cmake.extra_subdirectories`) after those.

    `supplies` and `overrides` map a directory to the configuration file names it registers.
    A file has to be supplied before it is overridden. Where a module the parent brings in
    supplies what another module overrides, the parent is moved ahead of that module; if
    the parent itself has to come after it, there is no order and that is an error.
    """
    excluded, last = set(excluded), list(last)
    reach = {d: added_directories(d) for d in selected}
    nested: Dict[Path, Tuple[Path, Path]] = {}
    for parent in selected:
        for child, cmake_file in reach[parent].items():
            if child in reach and child != parent and child not in nested and parent not in reach[child]:
                nested[child] = (parent, cmake_file)
    roots = [d for d in selected if d not in nested]
    # A child of a child belongs to the outermost parent
    for child, (parent, cmake_file) in list(nested.items()):
        while parent in nested:
            parent = nested[parent][0]
        nested[child] = (parent, cmake_file)
    members = {d: [d, *[c for c in reach[d] if c not in excluded]] for d in roots}
    roots.sort(key=lambda d: (d in last, d not in last and any(m in overriding for m in members[d])))

    def names(directory: Path, table: Dict[Path, Set[str]]) -> Set[str]:
        return set().union(*[table.get(m, set()) for m in members[directory]])

    # supplier -> overrider edges, remembering the file and who registers it
    before: Dict[Path, Dict[Path, Tuple[str, Path, Path]]] = {d: {} for d in roots}
    for supplier in roots:
        for overrider in roots:
            if supplier == overrider:
                continue
            for name in sorted(names(supplier, supplies) & names(overrider, overrides)):
                source = next(m for m in members[supplier] if name in supplies.get(m, set()))
                user = next(m for m in members[overrider] if name in overrides.get(m, set()))
                before[supplier].setdefault(overrider, (name, source, user))
    # Only a module that another one brings in makes a constraint the old rule did not meet
    brought_in = [(s, o) for s in roots for o, (_, source, user) in before[s].items() if source != s or user != o]
    if not brought_in:
        return ModuleOrder(roots, nested, members)
    ordered: List[Path] = []
    remaining = list(roots)
    while remaining:
        ready = next((d for d in remaining if not any(d in before[other] for other in remaining if other != d)), None)
        if ready is None:
            pair = next(((s, o) for s, o in brought_in if s in remaining and o in remaining), None)
            if pair is None:
                # A cycle among modules that add nothing themselves: the order they always had
                ordered += remaining
                break
            supplier, overrider = pair
            name, child, user = before[supplier][overrider]
            back = before[overrider].get(supplier)

            def rel(path: Path) -> str:
                try:
                    return path.relative_to(relative_to).as_posix()
                except ValueError:
                    return str(path)
            if child != supplier:
                brings = (f"But {rel(child)} is added by {rel(supplier)} ({rel(reach[supplier][child])}), so it is "
                          f"registered where {rel(supplier)} is")
            else:
                brings = (f"But {rel(user)} is added by {rel(overrider)} ({rel(reach[overrider][user])}), so it is "
                          f"registered where {rel(overrider)} is")
            raise WstsError(
                f"the project's configuration modules cannot be put in an order that F Prime accepts. "
                f"{rel(child)} supplies {name}, which {rel(user)} overrides, so it has to be registered before "
                f"{rel(user)}. {brings}"
                + (f", and {rel(supplier)} has to come after {rel(overrider)}: it overrides {back[0]}, which "
                   f"{rel(back[1])} supplies. " if back else
                   f", and other configuration these modules override puts {rel(supplier)} after {rel(overrider)}. ")
                + "The generator reads CMake as text and adds each module where the flight project's own files "
                "add it. If one of these registrations is inside an if() that does not hold for the simulated "
                f"build, leave that module out with\n  cmake:\n    exclude_subdirectories: [{rel(user)}]\n"
                "in the manifest (a module of the simulation can then supply or override the file instead); "
                "otherwise the flight build has the same conflict."
            )
        ordered.append(ready)
        remaining.remove(ready)
    return ModuleOrder(ordered, nested, members)


# ----------------------------------------------------------------------
# Link dependencies that are files of a target SDK
# ----------------------------------------------------------------------

#: Variables that F Prime or CMake always define where a module is registered
KNOWN_VARIABLES = re.compile(r"CMAKE_\w+|PROJECT_\w+|FPRIME_\w+|MODULE_NAME|[A-Za-z0-9]+_(SOURCE|BINARY)_DIR")
LIBRARY_FILE = re.compile(r"\.(a|so|lib|o)(\.\d+)*$")
VARIABLE_USE = re.compile(r"\$\{(\w+)\}")


@dataclass
class SdkDependency:
    module: Path  # module directory
    entry: str  # as written in the module's CMakeLists.txt
    problem: str
    variables: List[str]  # variables it needs that the simulated build does not set


def sdk_dependencies(module: Path, cache: Dict[str, List[str]]) -> List[SdkDependency]:
    """Entries of a module's DEPENDS (or MOD_DEPS) that name a file rather than a target,
    and that the host build cannot resolve: a path that does not exist here, or one that
    starts with a variable nothing in the simulated build sets."""
    cmake_file = module / "CMakeLists.txt"
    if not cmake_file.is_file():
        return []
    text = _strip_comments(cmake_file.read_text(errors="replace"))
    entries: List[str] = []
    for match in re.finditer(r"\bregister_fprime_\w+\s*\(([^)]*)\)", text, re.S):
        current = "_"
        for token in re.findall(r'"[^"]*"|\S+', match.group(1)):
            token = token.strip('"')
            if token.isupper() and re.fullmatch(r"[A-Z_]+", token):
                current = token
            elif current == "DEPENDS":
                entries.append(token)
    local: Set[str] = set()
    for command in read_commands(text):
        if command.name in ("set", "list", "option", "foreach", "find_library", "find_path", "find_package") and command.args:
            local.add(command.args[1] if command.name == "list" and len(command.args) > 1 else command.args[0])
        if command.name == "set" and command.args and command.args[0] in ("MOD_DEPS", "UT_MOD_DEPS"):
            if command.args[0] == "MOD_DEPS":
                entries += command.args[1:]
    found: List[SdkDependency] = []
    for entry in dict.fromkeys(entries):
        used = VARIABLE_USE.findall(entry)
        unset = [v for v in used if v not in cache and v not in local and not KNOWN_VARIABLES.fullmatch(v)]
        resolved = entry
        for name in used:
            if name in cache:
                resolved = resolved.replace("${" + name + "}", ";".join(cache[name]))
        resolved = resolved.replace("${CMAKE_CURRENT_LIST_DIR}", str(module)).replace("${CMAKE_CURRENT_SOURCE_DIR}", str(module))
        # `Fw/Logger` is the older way of naming a module, not a file
        path_like = resolved.startswith("/") or bool(LIBRARY_FILE.search(resolved))
        if resolved.startswith(("-", "$<")):
            continue  # a linker flag or a generator expression
        if unset and (path_like or entry.startswith("${")):
            where = "a file of the target's SDK" if path_like else "set by the flight build only"
            found.append(SdkDependency(module, entry, f"{', '.join(unset)} is not set in the simulated build ({where})", unset))
        elif path_like and is_literal(resolved) and not (module / resolved).exists():
            found.append(SdkDependency(module, entry, f"{resolved} does not exist on this host", []))
    return found
