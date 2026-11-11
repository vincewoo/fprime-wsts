"""Loading an F Prime project's FPP model.

The model is obtained from the FPP tools themselves (fpp-locate-defs, fpp-depend,
fpp-to-json) rather than from a build of the project. That makes the analysis
independent of the platform the project targets: a VxWorks deployment can be analysed
on a Linux host that could never configure its build.
"""

from __future__ import annotations

import configparser
import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


class WstsError(Exception):
    """A problem the user can act on; reported without a traceback."""


# Directories that hold FPP which is not part of a flight build
EXCLUDED_DIR_NAMES = {
    ".git",
    "test",
    "tests",
    "docs",
    "FppTestProject",
    "TestDeploymentsProject",
    "googletest",
    "__pycache__",
    "node_modules",
}
EXCLUDED_DIR_PREFIXES = ("build-", "fprime-venv", ".venv", "venv")
GENERATED_MARKER = ".wsts-generated"


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------


@dataclass
class Port:
    name: str
    kind: str  # Output, SyncInput, GuardedInput, AsyncInput, or Special
    type: Optional[str]  # qualified port type; None for special and serial ports
    size: int = 1
    size_constant: Optional[str] = None  # the constant that sets the size, when it is written as one
    special: Optional[str] = None  # for a special port: CommandRecv, ParamGet, Telemetry, TimeGet, ...

    @property
    def is_input(self) -> bool:
        return self.kind.endswith("Input")


@dataclass
class Component:
    name: str
    kind: str  # passive, queued, active
    file: Path
    line: int = 1  # 1-based position of the definition
    col: int = 1
    ports: Dict[str, Port] = field(default_factory=dict)
    commands: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)
    params: List[str] = field(default_factory=list)


@dataclass
class Instance:
    name: str
    component: str
    base_id: int
    max_id: int
    file: Path
    line: int  # 1-based position of the definition
    col: int
    type_line: int  # 1-based position of the component name in the definition
    type_col: int
    type_text: str  # component name as written in the source
    has_queue: bool = False
    has_phases: bool = False

    @property
    def base_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def module(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else ""


@dataclass
class Connection:
    graph: str
    from_instance: str
    from_port: str
    to_instance: str
    to_port: str
    # Where each end is written (file, line, column), and whether it is written as a port
    # that a subtopology exports rather than as the instance's own port
    from_loc: Optional[Tuple[Path, int, int]] = None
    to_loc: Optional[Tuple[Path, int, int]] = None
    from_exported: bool = False
    to_exported: bool = False
    from_index: Optional[int] = None  # port numbers as written; None when left to FPP
    to_index: Optional[int] = None


@dataclass
class Pattern:
    """A pattern connection specifier (`health connections instance $health`)"""
    kind: str  # Command, Event, Health, Param, Telemetry, TextEvent, Time
    source: Optional[str]  # the instance named
    file: Path
    line: int  # position of the specifier
    col: int
    source_line: int  # position of the source instance name in it
    source_col: int
    explicit_targets: bool


@dataclass(frozen=True)
class Numbered:
    """A connection with the port numbers FPP resolved for it. This includes the
    connections that pattern specifiers (command, health, ...) generate."""
    from_instance: str
    from_port: str
    from_index: int
    to_instance: str
    to_port: str
    to_index: int


@dataclass
class Topology:
    name: str
    file: Path
    line: int
    col: int
    instances: List[str] = field(default_factory=list)
    connections: List[Connection] = field(default_factory=list)
    packet_sets: List[Tuple[Path, int, int]] = field(default_factory=list)  # file, line, col
    explicit_pattern_targets: List[str] = field(default_factory=list)
    time_source: Optional[str] = None  # the instance named by `time connections instance`
    numbered: List[Numbered] = field(default_factory=list)
    # Ports the topology exports (`port x = instance.port`): name -> (instance, port)
    exported: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    exported_loc: Dict[str, Tuple[Path, int, int]] = field(default_factory=dict)  # where each is written
    patterns: List[Pattern] = field(default_factory=list)

    @property
    def base_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def module(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else ""


@dataclass
class Model:
    components: Dict[str, Component]
    instances: Dict[str, Instance]
    topologies: Dict[str, Topology]
    files: List[Path]  # every FPP file the analysis read


# ----------------------------------------------------------------------
# Project settings and file discovery
# ----------------------------------------------------------------------


@dataclass
class ProjectSettings:
    project_root: Path
    framework_path: Path
    library_locations: List[Path]
    default_toolchain: str


def read_settings(project_root: Path) -> ProjectSettings:
    """Read an F Prime project's settings.ini"""
    ini = project_root / "settings.ini"
    if not ini.is_file():
        raise WstsError(f"{project_root} is not an F Prime project root: no settings.ini")
    parser = configparser.ConfigParser()
    parser.read(ini)
    section = parser["fprime"] if parser.has_section("fprime") else {}

    def resolve(value: str) -> Path:
        return (project_root / value.strip()).resolve()

    root = resolve(section.get("project_root", "."))
    framework = section.get("framework_path")
    if framework is None:
        # Default F Prime layouts keep the framework inside the project
        for guess in ("lib/fprime", "fprime"):
            if (root / guess / "cmake" / "FPrime.cmake").is_file():
                framework = guess
                break
    if framework is None:
        raise WstsError(f"{ini}: framework_path is not set and no framework was found under the project")
    libraries = [
        resolve(entry)
        for entry in section.get("library_locations", "").replace("\n", ":").split(":")
        if entry.strip()
    ]
    return ProjectSettings(root, resolve(framework), libraries, section.get("default_toolchain", "native").strip())


def _is_excluded_dir(path: Path) -> bool:
    name = path.name
    if name in EXCLUDED_DIR_NAMES or name.startswith(EXCLUDED_DIR_PREFIXES):
        return True
    return (path / GENERATED_MARKER).exists()


def find_fpp_files(roots: Iterable[Path], exclude_globs: Iterable[str] = ()) -> List[Path]:
    """All FPP source files under the given roots that belong to a flight build"""
    found: Dict[Path, None] = {}
    globs = list(exclude_globs)
    for root in roots:
        if not root.is_dir():
            continue
        for directory, subdirs, files in os.walk(root):
            here = Path(directory)
            subdirs[:] = sorted(d for d in subdirs if not _is_excluded_dir(here / d))
            for name in sorted(files):
                if not name.endswith(".fpp"):
                    continue
                path = (here / name).resolve()
                if any(fnmatch.fnmatch(str(path), glob) for glob in globs):
                    continue
                found[path] = None
    return list(found)


@dataclass
class ConfigOverride:
    """One configuration file replaced through CONFIGURATION_OVERRIDES"""
    name: str  # file name: an override is matched to what it replaces by name only
    winner: Path
    replaced: List[Path]


CMAKE_CALL_DIRECTIVES = {
    "SOURCES", "AUTOCODER_INPUTS", "DEPENDS", "HEADERS", "CHOOSES_IMPLEMENTATIONS", "REQUIRES_IMPLEMENTATIONS",
    "IMPLEMENTS", "EXCLUDE_FROM_ALL", "INTERFACE", "STATIC", "SHARED", "UT_AUTO_HELPERS", "CONFIGURATION_OVERRIDES",
    "GLOBAL_IMPLICIT_DEPENDENCY", "BASE_CONFIG", "LINK_DEPENDS", "INCLUDE_GTEST",
}


def read_calls(cmake_file: Path, function: str) -> List[Dict[str, List[str]]]:
    """Arguments of every `function(...)` call in a CMake file, grouped by directive.

    This reads the text, it does not evaluate CMake: `${CMAKE_CURRENT_LIST_DIR}` and
    `${CMAKE_CURRENT_SOURCE_DIR}` are replaced, any other variable is left as written, and a
    call inside `if()` counts whether or not its condition holds.
    """
    if not cmake_file.is_file():
        return []
    text = re.sub(r"#[^\n]*", "", cmake_file.read_text(errors="replace"))
    calls = []
    for match in re.finditer(r"\b" + re.escape(function) + r"\s*\(([^)]*)\)", text, re.S):
        groups: Dict[str, List[str]] = {}
        current = "_"
        for token in re.findall(r'"[^"]*"|\S+', match.group(1)):
            token = token.strip('"')
            if token in CMAKE_CALL_DIRECTIVES:
                current = token
                groups.setdefault(current, [])
            else:
                token = token.replace("${CMAKE_CURRENT_LIST_DIR}", str(cmake_file.parent))
                token = token.replace("${CMAKE_CURRENT_SOURCE_DIR}", str(cmake_file.parent))
                groups.setdefault(current, []).append(token)
        calls.append(groups)
    return calls


def find_config_overrides(roots: Iterable[Path], suffixes: Tuple[str, ...] = (".fpp",)) -> Tuple[List[ConfigOverride], List[str]]:
    """Configuration FPP replaced through `register_fprime_config(CONFIGURATION_OVERRIDES)`.

    Mirrors F Prime 4.4: an override replaces the configuration file of the same file name
    supplied by an earlier configuration module, and the last registration wins. `roots`
    are given in F Prime's order (framework, then libraries, then the project); a
    directory inside two roots belongs to the deeper one.

    What this does not do: it reads the CMake text (see `read_calls`), so an override
    listed through a variable is not seen, and one inside `if()` is taken to apply. It
    cannot know which of a project's configuration modules a build adds: when two in the
    same root override the same file, the later path wins and a warning is returned. Use
    `fpp_exclude` in the manifest to settle such a case.

    Returns the overrides of FPP files and warnings. With other `suffixes` (".hpp") the same
    is done for configuration headers, which are supplied under HEADERS.
    """
    roots = [r.resolve() for r in roots if r.is_dir()]
    supplied: Dict[str, List[Path]] = {}
    overrides: Dict[str, List[Tuple[int, Path]]] = {}
    seen: Set[Path] = set()
    for order, root in enumerate(roots):
        for directory, subdirs, files in os.walk(root):
            here = Path(directory)
            subdirs[:] = sorted(
                d for d in subdirs
                if not _is_excluded_dir(here / d) and not any((here / d).resolve() == other for other in roots)
            )
            for name in sorted(files):
                if name != "CMakeLists.txt" and not name.endswith(".cmake"):
                    continue
                cmake = (here / name).resolve()
                if cmake in seen:
                    continue
                seen.add(cmake)
                try:
                    if "register_fprime_config" not in cmake.read_text(errors="replace"):
                        continue
                except OSError:
                    continue
                for call in read_calls(cmake, "register_fprime_config"):
                    for directive, table in (("AUTOCODER_INPUTS", None), ("HEADERS", None), ("CONFIGURATION_OVERRIDES", overrides)):
                        for token in call.get(directive, []):
                            if not token.endswith(suffixes) or "${" in token:
                                continue
                            path = (cmake.parent / token).resolve()
                            if table is None:
                                supplied.setdefault(path.name, []).append(path)
                            else:
                                table.setdefault(path.name, []).append((order, path))
    found: List[ConfigOverride] = []
    warnings: List[str] = []
    for name in sorted(overrides):
        ranked = sorted(overrides[name], key=lambda entry: (entry[0], str(entry[1])))
        order, winner = ranked[-1]
        same_root = [str(path) for other, path in ranked[:-1] if other == order]
        if same_root:
            warnings.append(
                f"configuration file {name} is overridden more than once in the same project or library "
                f"({', '.join(same_root)} and {winner}); the analysis uses {winner}. List the others under "
                "'fpp_exclude' if that is wrong."
            )
        replaced = [p for p in supplied.get(name, []) if p != winner] + [p for _, p in ranked[:-1] if p != winner]
        if not supplied.get(name):
            warnings.append(f"{winner} is a CONFIGURATION_OVERRIDES entry, but no configuration module supplies a file "
                            f"named {name}: F Prime will reject it")
        found.append(ConfigOverride(name, winner, sorted(dict.fromkeys(replaced))))
    return found, warnings


def config_constant(roots: List[Path], framework: Path, header: str, constant: str) -> Optional[Tuple[int, Path]]:
    """Value of an integer constant in a configuration header (`NAME = 150`), read from the
    file the build would use: a CONFIGURATION_OVERRIDES replacement, else the framework's
    default. None when the header or the constant is not found."""
    overrides, _ = find_config_overrides(roots, (".hpp", ".h"))
    file = next((o.winner for o in overrides if o.name == header), framework / "default" / "config" / header)
    if not file.is_file():
        return None
    match = re.search(r"\b" + re.escape(constant) + r"\s*=\s*(0[xX][0-9a-fA-F]+|\d+)", file.read_text(errors="replace"))
    return (int(match.group(1), 0), file) if match else None


def select_candidates(files: List[Path], framework: Path, overrides: Iterable[ConfigOverride] = ()) -> List[Path]:
    """Leave out configuration FPP that the build replaces.

    F Prime resolves configuration by file name. `overrides` are the replacements declared
    with CONFIGURATION_OVERRIDES (see `find_config_overrides`): of the files with such a
    name only the winner stays. As a fallback for projects that override by convention
    alone, a file in a directory called `config` outside the framework still replaces
    `default/config/<same name>`. Either way the analysis sees each configuration symbol
    once.
    """
    overrides = list(overrides)
    replaced = {p for override in overrides for p in override.replaced}
    declared = {override.name for override in overrides}
    files = [f for f in files if f not in replaced]
    default_dir = (framework / "default" / "config").resolve()
    overriding = {
        f.name for f in files
        if f.name not in declared and default_dir not in f.parents and "config" in {p.name.lower() for p in f.parents}
    }
    kept = [f for f in files if not (default_dir in f.parents and f.name in overriding)]
    # Platform type definitions exist once per platform; the simulation runs on the
    # framework's unix platform, so every other platform's types are left out.
    unix_platform = (framework / "cmake" / "platform" / "unix").resolve()
    return [f for f in kept if "cmake/platform/" not in f.as_posix() or unix_platform in f.parents]


# ----------------------------------------------------------------------
# Running the FPP tools
# ----------------------------------------------------------------------


# Set by check_tool_version(): appended to the message when an FPP tool fails
_version_hint = ""


def pinned_fpp_version(framework: Path) -> Optional[str]:
    """The `fprime-fpp==` version the framework's requirements.txt asks for"""
    requirements = framework / "requirements.txt"
    if not requirements.is_file():
        return None
    match = re.search(r"^\s*fprime-fpp\s*==\s*([^\s;#]+)", requirements.read_text(errors="replace"), re.M)
    return match.group(1) if match else None


def installed_fpp_version() -> Optional[str]:
    """The version of the FPP tools on PATH, from the first line of `fpp-check --help`"""
    executable = shutil.which("fpp-check")
    if executable is None:
        return None
    env = dict(os.environ)
    for _ in range(2):
        try:
            result = subprocess.run([executable, "--help"], env=env, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        match = re.search(r"\bfpp-check\s+v?(\d[\w.+-]*)", result.stdout + result.stderr)
        if match:
            return match.group(1)
        if "JAVA_HOME" not in env:
            break
        env.pop("JAVA_HOME")
    return None


def check_tool_version(framework: Path) -> Optional[str]:
    """Compare the FPP tools on PATH with the version the framework pins.

    Returns a warning when they differ, and remembers it so that a later tool failure can
    point at the likely cause. Returns None when they match or either is unknown.
    """
    global _version_hint
    _version_hint = ""
    pinned = pinned_fpp_version(framework)
    installed = installed_fpp_version()
    if not pinned or not installed or pinned == installed:
        return None
    _version_hint = (
        f"The FPP tools on PATH are version {installed}, but {framework / 'requirements.txt'} pins "
        f"fprime-fpp=={pinned}. A model written for one version may not parse with the other: install the pinned "
        f"tools (pip install -r {framework / 'requirements.txt'})."
    )
    return _version_hint


def _run(tool: str, args: List[str], cwd: Path) -> str:
    executable = shutil.which(tool)
    if executable is None:
        raise WstsError(f"'{tool}' was not found on PATH. Activate the F Prime virtual environment (pip install fprime-fpp).")
    env = dict(os.environ)
    for attempt in range(2):
        result = subprocess.run([executable, *args], cwd=cwd, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
        # The FPP launcher prefers JAVA_HOME even when it points at a JVM that is too old
        if attempt == 0 and "requires Java" in result.stderr and "JAVA_HOME" in env:
            env.pop("JAVA_HOME")
            continue
        break
    detail = "\n".join(line for line in result.stderr.splitlines() if not line.startswith("Picked up "))
    message = f"{tool} failed:\n{detail.strip() or result.stdout.strip()}"
    java = re.search(r"requires Java\s+(\d+)", result.stderr)
    if java:
        message += (
            f"\n\nThis version of the FPP tools needs Java {java.group(1)} or later, and the Java it found is "
            f"older. Install a Java {java.group(1)} runtime and point JAVA_HOME at it"
            + (" (the JAVA_HOME now set names an older one)." if "JAVA_HOME" in os.environ
               else ", or put its bin directory first on PATH.")
        )
    if _version_hint:
        message += "\n\n" + _version_hint
    raise WstsError(message)


def _write_args(path: Path, args: Iterable[Path]) -> None:
    path.write_text("\n".join(str(a) for a in args) + "\n")


def locate_defs(work: Path, candidates: List[Path]) -> Dict[Tuple[str, str], Path]:
    """Write the location file for `candidates` and return {(kind, name): file}"""
    work.mkdir(parents=True, exist_ok=True)
    text = _run("fpp-locate-defs", [str(c) for c in candidates], work)
    (work / "locs.fpp").write_text(text)
    located: Dict[Tuple[str, str], Path] = {}
    for line in text.splitlines():
        parts = line.split()
        # locate <kind...> <name> at "<path>"
        if len(parts) >= 5 and parts[0] == "locate" and parts[-2] == "at":
            located[(" ".join(parts[1:-3]), parts[-3])] = (work / parts[-1].strip('"')).resolve()
    return located


def depend(work: Path, sources: List[Path]) -> List[Path]:
    """FPP files that `sources` depend on, using the location file written by locate_defs"""
    out = _run("fpp-depend", [str(work / "locs.fpp"), *[str(s) for s in sources]], work)
    deps: Dict[Path, None] = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            deps[(work / line).resolve()] = None
    source_set = {s.resolve() for s in sources}
    return [d for d in deps if d not in source_set]


def check(work: Path, files: List[Path]) -> None:
    """Run fpp-check, raising WstsError with the tool's message on failure"""
    _run("fpp-check", [str(f) for f in files], work)


def analyze(work: Path, files: List[Path]) -> Model:
    """Run fpp-to-json over `files` and build the model"""
    out = work / "json"
    out.mkdir(parents=True, exist_ok=True)
    _run("fpp-to-json", ["-d", str(out), *[str(f) for f in files]], work)
    ast = json.loads((out / "fpp-ast.json").read_text())["ast"]
    locations = json.loads((out / "fpp-loc-map.json").read_text())["locationMap"]
    analysis = json.loads((out / "fpp-analysis.json").read_text())["analysis"]
    return _build_model(ast, locations, analysis, files)


# ----------------------------------------------------------------------
# Building the model from the tool output
# ----------------------------------------------------------------------


def _node(wrapper: dict) -> Tuple[dict, int]:
    inner = wrapper["node"]["AstNode"] if "node" in wrapper else wrapper["AstNode"]
    return inner["data"], inner["id"]


def _qual_ident(data) -> str:
    """Dotted text of a qualified identifier AST node"""
    if "Unqualified" in data:
        return data["Unqualified"]["name"]
    qualified = data["Qualified"]
    return _qual_ident(qualified["qualifier"]["AstNode"]["data"]) + "." + qualified["name"]["AstNode"]["data"]


def _position(locations: dict, node_id: int) -> Tuple[Path, int, int]:
    loc = locations[str(node_id)]
    line, col = loc["pos"].split(".")
    return Path(loc["file"]), int(line), int(col)


def _some(value):
    return value.get("Some") if isinstance(value, dict) else None


def _port_name(port_instance: dict) -> str:
    (variant,) = port_instance.values()
    if "specifier" in variant:
        return variant["specifier"]["name"]
    if "underlyingPort" in variant:
        return _port_name(variant["underlyingPort"])
    return variant.get("name", "?")


def _build_model(ast: list, locations: dict, analysis: dict, files: List[Path]) -> Model:
    names: Dict[int, str] = {}  # definition node id -> qualified name
    component_kinds: Dict[int, str] = {}
    instance_defs: Dict[int, dict] = {}
    topology_defs: Dict[int, dict] = {}

    def walk(members: list, scope: List[str]) -> None:
        for member in members:
            # A member is [pre-annotation, node, post-annotation]
            body = member[1]
            (kind, wrapper), = body.items()
            if kind not in ("DefModule", "DefComponent", "DefComponentInstance", "DefTopology", "DefPort", "DefInterface"):
                continue
            data, node_id = _node(wrapper)
            qualified = ".".join(scope + [data["name"]])
            names[node_id] = qualified
            if kind == "DefModule":
                walk(data["members"], scope + [data["name"]])
            elif kind == "DefComponent":
                (component_kind,) = data["kind"].keys()
                component_kinds[node_id] = component_kind.lower()
            elif kind == "DefComponentInstance":
                instance_defs[node_id] = data
            elif kind == "DefTopology":
                topology_defs[node_id] = data

    for unit in ast:
        walk(unit["members"], [])
    node_names: Dict[int, str] = {}
    _collect_node_names(ast, node_names)
    node_data: Dict[int, dict] = {}
    _collect_node_data(ast, node_data)

    def size_constant(detail: dict) -> Optional[str]:
        size = _some(detail.get("specifier", {}).get("size"))
        data = node_data.get(size.get("astNodeId")) if isinstance(size, dict) else None
        text = _expr_text(data) if data else None
        return text

    components: Dict[str, Component] = {}
    for key, entry in analysis["componentMap"].items():
        node_id = int(key)
        name = names.get(node_id)
        if name is None:
            continue
        file, line, col = _position(locations, node_id)
        component = Component(name, component_kinds.get(node_id, "passive"), file, line, col)
        for port_name, port in entry["portInterface"]["portMap"].items():
            (variant, detail), = port.items()
            if variant == "General":
                port_type = None
                symbol = detail.get("ty", {}).get("DefPort", {}).get("symbol", {}).get("Port")
                if symbol is not None:
                    port_type = names.get(symbol["nodeId"], symbol["unqualifiedName"])
                component.ports[port_name] = Port(port_name, detail["kind"], port_type, detail.get("size", 1),
                                                  size_constant(detail))
            else:
                special = next(iter(detail.get("specifier", {}).get("kind", {})), None)
                component.ports[port_name] = Port(port_name, "Special", None, special=special)
        component.commands = _entry_names(entry["commandMap"], node_names)
        component.events = _entry_names(entry["eventMap"], node_names)
        component.channels = _entry_names(entry["tlmChannelMap"], node_names)
        component.params = _entry_names(entry["paramMap"], node_names)
        components[name] = component

    instances: Dict[str, Instance] = {}
    instance_by_node: Dict[int, str] = {}
    for key, entry in analysis["componentInstanceMap"].items():
        node_id = int(key)
        name = names.get(node_id)
        data = instance_defs.get(node_id)
        if name is None or data is None:
            continue
        file, line, col = _position(locations, node_id)
        type_node = data["component"]["AstNode"]
        _, type_line, type_col = _position(locations, type_node["id"])
        instances[name] = Instance(
            name=name,
            component=names.get(entry["component"]["astNodeId"], "?"),
            base_id=entry["baseId"],
            max_id=entry["maxId"],
            file=file,
            line=line,
            col=col,
            type_line=type_line,
            type_col=type_col,
            type_text=_qual_ident(type_node["data"]),
            has_queue=_some(entry.get("queueSize")) is not None,
            has_phases=bool(data.get("initSpecs")),
        )
        instance_by_node[node_id] = name

    topologies: Dict[str, Topology] = {}
    for key, entry in analysis["topologyMap"].items():
        node_id = int(key)
        name = names.get(node_id)
        if name is None:
            continue
        file, line, col = _position(locations, node_id)
        topology = Topology(name, file, line, col)
        for instance_ref, _ in entry["instanceMap"]:
            ref = instance_ref.get("InterfaceComponentInstance")
            if ref is not None and ref["astNodeId"] in instance_by_node:
                topology.instances.append(instance_by_node[ref["astNodeId"]])
        def written(end: dict) -> Tuple[Optional[Tuple[Path, int, int]], bool, Optional[int]]:
            loc = end.get("loc") or {}
            where = None
            if loc.get("file") and loc.get("pos"):
                line, col = loc["pos"].split(".")
                where = (Path(loc["file"]), int(line), int(col))
            number = _some(end.get("portNumber"))
            return where, _some(end.get("topologyPort")) is not None, number if isinstance(number, int) else None

        for graph, connections in entry["connectionMap"].items():
            for connection in connections:
                ends = []
                for side in ("from", "to"):
                    port = connection[side]["port"]
                    ref = port["interfaceInstance"].get("InterfaceComponentInstance")
                    ends.append((instance_by_node.get(ref["astNodeId"]) if ref else None, _port_name(port["portInstance"])))
                if ends[0][0] and ends[1][0]:
                    from_loc, from_exported, from_index = written(connection["from"])
                    to_loc, to_exported, to_index = written(connection["to"])
                    topology.connections.append(Connection(
                        graph, ends[0][0], ends[0][1], ends[1][0], ends[1][1],
                        from_loc, to_loc, from_exported, to_exported, from_index, to_index))
        numbers: Dict[str, List[int]] = {}
        for index, table in enumerate(("fromPortNumberMap", "toPortNumberMap")):
            for connection, number in entry.get(table, []):
                numbers.setdefault(json.dumps(connection, sort_keys=True), [-1, -1])[index] = number
        for key, (from_index, to_index) in numbers.items():
            connection = json.loads(key)
            ends = []
            for side in ("from", "to"):
                port = connection[side]["port"]
                ref = port["interfaceInstance"].get("InterfaceComponentInstance")
                ends.append((instance_by_node.get(ref["astNodeId"]) if ref else None, _port_name(port["portInstance"])))
            if ends[0][0] and ends[1][0]:
                topology.numbered.append(Numbered(ends[0][0], ends[0][1], from_index, ends[1][0], ends[1][1], to_index))
        for port_name, exported in entry.get("portMap", {}).items():
            pii = exported.get("pii", {})
            ref = pii.get("interfaceInstance", {}).get("InterfaceComponentInstance")
            if ref and ref["astNodeId"] in instance_by_node and "portInstance" in pii:
                topology.exported[port_name] = (instance_by_node[ref["astNodeId"]], _port_name(pii["portInstance"]))
                try:
                    topology.exported_loc[port_name] = _position(locations, exported["aNode"]["astNodeId"])
                except (KeyError, TypeError):
                    pass
        for pattern in entry.get("patternMap", {}).values():
            (pattern_kind,) = pattern["ast"]["kind"].keys()
            if pattern.get("ast", {}).get("targets"):
                topology.explicit_pattern_targets.append(pattern_kind)
            source = pattern.get("source")
            source_node = source[0].get("aNode", {}).get("astNodeId") if isinstance(source, list) and source else None
            if pattern_kind == "Time":
                topology.time_source = instance_by_node.get(source_node)
            try:
                pattern_file, pattern_line, pattern_col = _position(locations, pattern["aNode"]["astNodeId"])
                _, source_line, source_col = _position(locations, pattern["ast"]["source"]["astNodeId"])
            except (KeyError, TypeError):
                continue
            topology.patterns.append(Pattern(
                pattern_kind, instance_by_node.get(source_node), pattern_file, pattern_line, pattern_col,
                source_line, source_col, bool(pattern.get("ast", {}).get("targets"))))
        for member in topology_defs.get(node_id, {}).get("members", []):
            (kind, wrapper), = member[1].items()
            if kind == "SpecTlmPacketSet":
                _, set_id = _node(wrapper)
                topology.packet_sets.append(_position(locations, set_id))
        topologies[name] = topology

    return Model(components, instances, topologies, files)


def _collect_node_data(tree, out: Dict[int, dict]) -> None:
    """Map every AST node id to its data"""
    if isinstance(tree, dict):
        node = tree.get("AstNode")
        if isinstance(node, dict) and "id" in node and isinstance(node.get("data"), dict):
            out[node["id"]] = node["data"]
        for value in tree.values():
            _collect_node_data(value, out)
    elif isinstance(tree, list):
        for value in tree:
            _collect_node_data(value, out)


def _expr_text(data: dict) -> Optional[str]:
    """Dotted name of an expression that is a plain (possibly qualified) identifier"""
    if "ExprIdent" in data:
        return data["ExprIdent"].get("value")
    if "ExprDot" in data:
        dot = data["ExprDot"]
        try:
            left = _expr_text(dot["e"]["AstNode"]["data"])
            return f"{left}.{dot['id']['AstNode']['data']}" if left else None
        except (KeyError, TypeError):
            return None
    return None


def _collect_node_names(tree, out: Dict[int, str]) -> None:
    """Map every named AST node to its name"""
    if isinstance(tree, dict):
        node = tree.get("AstNode")
        if isinstance(node, dict) and isinstance(node.get("data"), dict) and isinstance(node["data"].get("name"), str):
            out[node["id"]] = node["data"]["name"]
        for value in tree.values():
            _collect_node_names(value, out)
    elif isinstance(tree, list):
        for value in tree:
            _collect_node_names(value, out)


def _entry_names(mapping: dict, node_names: Dict[int, str]) -> List[str]:
    """Names of the commands, events, channels or parameters in an analysis map"""
    found = []
    for entry in mapping.values():
        # Commands are wrapped in a variant ({"NonParam": {...}}); the others are not
        candidates = [entry] if "aNode" in entry else [v for v in entry.values() if isinstance(v, dict)]
        for candidate in candidates:
            node_id = candidate.get("aNode", {}).get("astNodeId")
            if node_id in node_names:
                found.append(node_names[node_id])
    return sorted(found)
