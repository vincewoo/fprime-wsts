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
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


class SseError(Exception):
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
GENERATED_MARKER = ".sse-generated"


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------


@dataclass
class Port:
    name: str
    kind: str  # Output, SyncInput, GuardedInput, AsyncInput, or Special
    type: Optional[str]  # qualified port type; None for special and serial ports
    size: int = 1

    @property
    def is_input(self) -> bool:
        return self.kind.endswith("Input")


@dataclass
class Component:
    name: str
    kind: str  # passive, queued, active
    file: Path
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
        raise SseError(f"{project_root} is not an F Prime project root: no settings.ini")
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
        raise SseError(f"{ini}: framework_path is not set and no framework was found under the project")
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


def select_candidates(files: List[Path], framework: Path) -> List[Path]:
    """Prefer a project's or library's configuration FPP over the framework default.

    F Prime resolves configuration by file name: a project that supplies its own
    `AcConstants.fpp` replaces `default/config/AcConstants.fpp`. Mirroring that keeps the
    analysis from seeing every configuration symbol twice.
    """
    default_dir = (framework / "default" / "config").resolve()
    overriding = {f.name for f in files if default_dir not in f.parents and "config" in {p.name.lower() for p in f.parents}}
    kept = [f for f in files if not (default_dir in f.parents and f.name in overriding)]
    # Platform type definitions exist once per platform; the simulation runs on the
    # framework's unix platform, so every other platform's types are left out.
    unix_platform = (framework / "cmake" / "platform" / "unix").resolve()
    return [f for f in kept if "cmake/platform/" not in f.as_posix() or unix_platform in f.parents]


# ----------------------------------------------------------------------
# Running the FPP tools
# ----------------------------------------------------------------------


def _run(tool: str, args: List[str], cwd: Path) -> str:
    executable = shutil.which(tool)
    if executable is None:
        raise SseError(f"'{tool}' was not found on PATH. Activate the F Prime virtual environment (pip install fprime-fpp).")
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
    raise SseError(f"{tool} failed:\n{detail.strip() or result.stdout.strip()}")


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
    """Run fpp-check, raising SseError with the tool's message on failure"""
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

    components: Dict[str, Component] = {}
    for key, entry in analysis["componentMap"].items():
        node_id = int(key)
        name = names.get(node_id)
        if name is None:
            continue
        file, _, _ = _position(locations, node_id)
        component = Component(name, component_kinds.get(node_id, "passive"), file)
        for port_name, port in entry["portInterface"]["portMap"].items():
            (variant, detail), = port.items()
            if variant == "General":
                port_type = None
                symbol = detail.get("ty", {}).get("DefPort", {}).get("symbol", {}).get("Port")
                if symbol is not None:
                    port_type = names.get(symbol["nodeId"], symbol["unqualifiedName"])
                component.ports[port_name] = Port(port_name, detail["kind"], port_type, detail.get("size", 1))
            else:
                component.ports[port_name] = Port(port_name, "Special", None)
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
        for graph, connections in entry["connectionMap"].items():
            for connection in connections:
                ends = []
                for side in ("from", "to"):
                    port = connection[side]["port"]
                    ref = port["interfaceInstance"].get("InterfaceComponentInstance")
                    ends.append((instance_by_node.get(ref["astNodeId"]) if ref else None, _port_name(port["portInstance"])))
                if ends[0][0] and ends[1][0]:
                    topology.connections.append(Connection(graph, ends[0][0], ends[0][1], ends[1][0], ends[1][1]))
        for pattern in entry.get("patternMap", {}).values():
            if pattern.get("ast", {}).get("targets"):
                (pattern_kind,) = pattern["ast"]["kind"].keys()
                topology.explicit_pattern_targets.append(pattern_kind)
        for member in topology_defs.get(node_id, {}).get("members", []):
            (kind, wrapper), = member[1].items()
            if kind == "SpecTlmPacketSet":
                _, set_id = _node(wrapper)
                topology.packet_sets.append(_position(locations, set_id))
        topologies[name] = topology

    return Model(components, instances, topologies, files)


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
