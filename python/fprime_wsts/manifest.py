"""Deployment manifest (wsts.yaml) and surrogate catalogs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .fpp import WstsError

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = TOOLKIT_ROOT / "catalog" / "wsts-catalog.yaml"


def _int(value, what: str) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 0)
    except ValueError:
        raise WstsError(f"{what}: '{value}' is not a number") from None


# ----------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------


@dataclass
class Surrogate:
    name: str  # FPP name, e.g. Wsts.SimI2c
    replaces: List[str] = field(default_factory=list)
    options: str = ""
    options_for: Dict[str, str] = field(default_factory=dict)
    compat: str = ""
    source: str = ""
    # Flight port name -> surrogate port name, or None when the surrogate has no such port
    ports: Dict[str, Optional[str]] = field(default_factory=dict)
    ports_for: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)
    # queue size, stack size, priority, cpu: for a queued or active surrogate that replaces
    # an instance which has none
    attributes: Dict[str, str] = field(default_factory=dict)
    # False for a surrogate that talks to flight components over F Prime ports only: it
    # has no bus endpoint and no wstsBind()
    bind: bool = True

    @property
    def cpp_name(self) -> str:
        return "::" + self.name.replace(".", "::")

    def options_when_replacing(self, component: str) -> str:
        return self.options_for.get(component, self.options)

    def ports_when_replacing(self, component: str) -> Dict[str, Optional[str]]:
        return {**self.ports, **self.ports_for.get(component, {})}


ATTRIBUTE_KEYS = {"queue_size": "queue size", "stack_size": "stack size", "priority": "priority", "cpu": "cpu"}


def _port_map(value, what: str) -> Dict[str, Optional[str]]:
    """`ports: {flightName: surrogateName}`; an empty value means the surrogate has no such port"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WstsError(f"{what}: 'ports' must map flight port names to surrogate port names")
    return {str(k): (str(v) if v not in (None, "", False) else None) for k, v in value.items()}


NO_ENDPOINT = "none"


def _no_endpoint(value, what: str, catalog: bool = False) -> bool:
    """`endpoint: none` says a surrogate does not use the bus"""
    if value is None:
        return False
    if value is False or str(value).strip().lower() == NO_ENDPOINT:
        return True
    if catalog and value not in ("", None):
        raise WstsError(f"{what}: in a catalog, 'endpoint' can only be 'none' (the surrogate does not use the bus); "
                        "endpoint names are given per instance in the manifest")
    return False


def _attributes(entry: dict, what: str) -> Dict[str, str]:
    found = {}
    for key, clause in ATTRIBUTE_KEYS.items():
        if entry.get(key) is not None:
            found[clause] = str(entry[key])
    return found


def load_catalog(path: Path) -> List[Surrogate]:
    if not path.is_file():
        raise WstsError(
            f"surrogate catalog not found: {path}. fprime-wsts must run from a source checkout; "
            "install it with 'pip install -e ./python'"
        )
    data = yaml.safe_load(path.read_text()) or {}
    surrogates = []
    for entry in data.get("surrogates", []):
        if "surrogate" not in entry:
            raise WstsError(f"{path}: catalog entry without 'surrogate'")
        surrogates.append(
            Surrogate(
                name=entry["surrogate"],
                replaces=list(entry.get("replaces", [])),
                options=str(entry.get("options", "")),
                options_for={k: str(v) for k, v in (entry.get("options_for") or {}).items()},
                compat=entry.get("compat", "") or "",
                source=str(path),
                ports=_port_map(entry.get("ports"), f"{path}: {entry['surrogate']}"),
                ports_for={str(k): _port_map(v, f"{path}: {entry['surrogate']}")
                           for k, v in (entry.get("ports_for") or {}).items()},
                attributes=_attributes(entry, str(path)),
                bind=not _no_endpoint(entry.get("endpoint", ""), f"{path}: {entry['surrogate']}", catalog=True),
            )
        )
    return surrogates


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


@dataclass
class Substitution:
    surrogate: str
    instance: Optional[str] = None
    component: Optional[str] = None
    endpoint: Optional[str] = None
    options: Optional[str] = None
    ports: Optional[Dict[str, Optional[str]]] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    no_endpoint: bool = False  # `endpoint: none`


@dataclass
class ClockSpec:
    """A Wsts.SimCycle instance and the rate it cycles at"""
    instance: str
    hz: float
    options: str = ""


@dataclass
class DeviceSpec:
    """An F Prime instance that is the device on a bus endpoint (a stock adapter such as
    Wsts.BusByteStreamDevice, with a simulator component behind it)"""
    instance: str
    endpoint: str
    options: str = ""


@dataclass
class ConnectionSpec:
    """One connection written in the manifest: `<instance>.<port>[n] -> <instance>.<port>[n]`"""
    text: str
    source: str  # instance or topology, then port, as written
    source_index: Optional[int]
    target: str
    target_index: Optional[int]


@dataclass
class SimulatorsSpec:
    """F Prime instances the simulation adds to the topology"""
    instances: List[str] = field(default_factory=list)
    topologies: List[str] = field(default_factory=list)
    connections: List[ConnectionSpec] = field(default_factory=list)
    headers: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.instances or self.topologies or self.connections or self.headers)


@dataclass
class ModelSpec:
    name: str
    cls: str  # C++ class, e.g. Wsts::RegisterMapI2c
    header: str
    endpoint: str
    kind: str  # bytestream, i2c, spi, gpio, register, none
    address: int = 0
    params: Dict[str, str] = field(default_factory=dict)


@dataclass
class LinkEnd:
    """This deployment's end of a link to another element of a system. Not written in a
    deployment manifest: the system manifest declares links and `generate_system` fills
    these in."""
    link: str  # link name, shared by both ends
    instance: str  # the byte stream driver instance the link ends at
    role: str  # listen or connect
    peer: str  # the far end, "<element>.<instance>", for the report


@dataclass
class SetupSpec:
    """C++ the generated set-up runs once the surrogates are bound and the models attached,
    before the runtime starts"""
    code: str = ""
    headers: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.code.strip() or self.headers)


@dataclass
class MainSpec:
    generate: bool = False
    declarations: str = ""
    start: str = ""
    stop: str = ""
    sources: Optional[List[str]] = None


@dataclass
class Manifest:
    path: Optional[Path]
    deployment: str
    # Set for a standalone simulation project: the flight project root, relative to the
    # manifest. The directory holding the manifest is then the simulation project's root.
    project: Optional[str] = None
    topology: Optional[str] = None
    output: Optional[str] = None
    substitutions: List[Substitution] = field(default_factory=list)
    keep: List[str] = field(default_factory=list)
    models: List[ModelSpec] = field(default_factory=list)
    control_enabled: bool = True
    control_base_id: Optional[int] = None
    bridge: str = "none"
    tick_hz: int = 10
    catalogs: List[str] = field(default_factory=list)
    topology_fpp: Optional[List[str]] = None
    topology_sources: Optional[List[str]] = None
    main: MainSpec = field(default_factory=MainSpec)
    setup: SetupSpec = field(default_factory=SetupSpec)
    extra_subdirectories: List[str] = field(default_factory=list)
    exclude_subdirectories: List[str] = field(default_factory=list)
    extra_depends: List[str] = field(default_factory=list)
    cmake_cache: Dict[str, List[str]] = field(default_factory=dict)  # name -> values (one, or a CMake list)
    # Module directories read with BUILD_TESTING set (their stub variant), relative to the flight project
    stub_variants: List[str] = field(default_factory=list)
    # Flags for the host's C++ compiler: for every module (key ALL_MODULES), or for one module directory
    cxx_flags: Dict[str, List[str]] = field(default_factory=dict)
    # Names the host check does not report: headers, libraries (-l<name>), packages, SDK entries
    host_ignore: List[str] = field(default_factory=list)
    simulators: SimulatorsSpec = field(default_factory=SimulatorsSpec)
    devices: List[DeviceSpec] = field(default_factory=list)
    clocks: List[ClockSpec] = field(default_factory=list)
    # Further F Prime libraries the simulation uses, relative to the simulation project
    sim_libraries: List[str] = field(default_factory=list)
    # CMake files included in the overlay: after every module is added, or before the libraries are read
    cmake_include: List[str] = field(default_factory=list)
    cmake_include_early: List[str] = field(default_factory=list)
    run_env: Dict[str, str] = field(default_factory=dict)  # environment of the simulated deployment
    keep_libraries: List[str] = field(default_factory=list)
    drop_libraries: List[str] = field(default_factory=list)
    fpp_exclude: List[str] = field(default_factory=list)
    defines: List[str] = field(default_factory=list)
    # Set when the deployment is generated as one element of a system (see system.py)
    system: Optional[str] = None
    element: Optional[str] = None
    links: List[LinkEnd] = field(default_factory=list)
    id_window: Optional[Tuple[int, int]] = None  # inclusive range of IDs routed to this element

    @property
    def sim_root(self) -> Optional[Path]:
        """Root of a standalone simulation project (the manifest's directory), or None when
        the manifest lives in the flight project"""
        if self.project is None:
            return None
        return (self.path.parent if self.path else Path.cwd()).resolve()

    def project_root(self) -> Optional[Path]:
        """The flight project named by `project`, if any"""
        if self.project is None:
            return None
        return (self.sim_root / self.project).resolve()

    def resolve(self, value: str, project_root: Path) -> Path:
        """Paths to flight things (deployment, sources, libraries, subdirectories) are
        relative to the flight project root"""
        return (project_root / value).resolve()

    def resolve_sim(self, value: str, project_root: Path) -> Path:
        """Paths to simulation things (catalogs, output) are relative to the simulation
        project root when there is one, else to the flight project root"""
        return ((self.sim_root or project_root) / value).resolve()


KNOWN_KEYS = {
    "project", "deployment", "topology", "output", "substitute", "keep", "models", "control", "bridge", "tick_hz",
    "catalogs", "sources", "main", "cmake", "libraries", "fpp_exclude", "defines", "simulators", "devices",
    "clocks", "run", "host", "setup",
}
SETUP_KEYS = {"code", "headers"}
HOST_KEYS = {"ignore"}
#: Key of `Manifest.cxx_flags` for flags that apply to every module
ALL_MODULES = ""
CLOCK_KEYS = {"instance", "hz", "options"}
LIBRARY_KEYS = {"keep", "drop", "sim"}
RUN_KEYS = {"env"}
MAX_CLOCK_HZ = 100000.0  # Wsts::CycleClock::MAX_HZ
ENV_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
SUBSTITUTE_KEYS = {"instance", "component", "with", "endpoint", "options", "ports", *ATTRIBUTE_KEYS}
SIMULATOR_KEYS = {"instances", "topologies", "connections", "headers"}
DEVICE_KEYS = {"instance", "endpoint", "options"}
CMAKE_KEYS = {"extra_subdirectories", "exclude_subdirectories", "depends", "cache", "include", "include_early",
              "stub_variants", "cxx_flags"}
CONNECTION_END = r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*(?:\[\s*(\d+)\s*\])?"
CACHE_NAME = r"[A-Za-z_][A-Za-z0-9_]*"


def parse_connection(text: str, where: str) -> ConnectionSpec:
    match = re.fullmatch(rf"\s*{CONNECTION_END}\s*->\s*{CONNECTION_END}\s*", str(text))
    if not match:
        raise WstsError(
            f"{where}: connection '{text}' is not of the form '<instance>.<port> -> <instance>.<port>' "
            "(a port number in brackets is optional)"
        )
    source, source_index, target, target_index = match.groups()
    return ConnectionSpec(
        text=" ".join(str(text).split()),
        source=source.replace("$", ""), source_index=int(source_index) if source_index is not None else None,
        target=target.replace("$", ""), target_index=int(target_index) if target_index is not None else None,
    )
MODEL_KINDS = {"bytestream", "i2c", "spi", "gpio", "register", "none"}


def _cxx_flags(value, where: str) -> Dict[str, List[str]]:
    """`cmake.cxx_flags`: a list of flags for every module, or a mapping from a module
    directory (relative to the flight project) to its flags. One entry is one flag with
    its argument: `-include cstring`."""
    if value is None:
        return {}

    def flags(entries, what: str) -> List[str]:
        entries = [entries] if isinstance(entries, str) else entries
        if not isinstance(entries, list) or not all(isinstance(e, str) and e.strip() for e in entries):
            raise WstsError(f"{where}: {what} must be a list of compiler flags (one entry per flag, with its "
                            "argument: '-include cstring')")
        for entry in entries:
            if any(char in entry for char in '";$\n') or not entry.strip().startswith("-"):
                raise WstsError(f"{where}: {what}: '{entry}' is not a plain compiler flag (it must start with '-' "
                                "and have no quotes, ';' or '$' in it)")
        return [" ".join(entry.split()) for entry in entries]

    if isinstance(value, dict):
        # `"*"` is every module, as the list form is
        return {(ALL_MODULES if str(module).strip() == "*" else str(module).strip().rstrip("/")):
                flags(entries, f"cmake.cxx_flags.{module}") for module, entries in value.items()}
    return {ALL_MODULES: flags(value, "cmake.cxx_flags")}


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise WstsError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return parse_manifest(data, path)


def parse_manifest(data: dict, path: Optional[Path] = None) -> Manifest:
    where = str(path) if path else "manifest"
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise WstsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    if "deployment" not in data:
        raise WstsError(f"{where}: 'deployment' is required (the deployment directory, relative to the project root)")
    manifest = Manifest(path=path, deployment=str(data["deployment"]).strip("/"))
    if data.get("project") is not None:
        manifest.project = str(data["project"])
    manifest.topology = data.get("topology")
    manifest.output = data.get("output")
    manifest.keep = [str(k) for k in data.get("keep", [])]
    manifest.bridge = str(data.get("bridge", "none"))
    manifest.tick_hz = _int(data.get("tick_hz", 10), f"{where}: tick_hz")
    manifest.catalogs = [str(c) for c in data.get("catalogs", [])]
    manifest.fpp_exclude = [str(g) for g in data.get("fpp_exclude", [])]
    manifest.defines = [str(d) for d in data.get("defines", [])]

    for entry in data.get("substitute", []):
        if "with" not in entry or ("instance" not in entry and "component" not in entry):
            raise WstsError(f"{where}: each 'substitute' entry needs 'with' and one of 'instance' or 'component'")
        unknown = set(entry) - SUBSTITUTE_KEYS
        if unknown:
            raise WstsError(f"{where}: 'substitute' entry for {entry.get('instance') or entry.get('component')}: "
                            f"unknown key(s): {', '.join(sorted(unknown))}")
        manifest.substitutions.append(
            Substitution(
                surrogate=entry["with"],
                instance=entry.get("instance"),
                component=entry.get("component"),
                endpoint=None if _no_endpoint(entry.get("endpoint"), where) else entry.get("endpoint"),
                options=entry.get("options"),
                ports=_port_map(entry["ports"], f"{where}: substitute") if "ports" in entry else None,
                attributes=_attributes(entry, where),
                no_endpoint=_no_endpoint(entry.get("endpoint"), where),
            )
        )

    simulators = data.get("simulators") or {}
    if not isinstance(simulators, dict):
        raise WstsError(f"{where}: 'simulators' must be a mapping with any of: {', '.join(sorted(SIMULATOR_KEYS))}")
    unknown = set(simulators) - SIMULATOR_KEYS
    if unknown:
        raise WstsError(f"{where}: simulators: unknown key(s): {', '.join(sorted(unknown))}")
    manifest.simulators = SimulatorsSpec(
        instances=[str(i).replace("$", "") for i in simulators.get("instances") or []],
        topologies=[str(i).replace("$", "") for i in simulators.get("topologies") or []],
        connections=[parse_connection(c, f"{where}: simulators") for c in simulators.get("connections") or []],
        headers=[str(h) for h in simulators.get("headers") or []],
    )
    if manifest.simulators.connections and not (manifest.simulators.instances or manifest.simulators.topologies):
        raise WstsError(f"{where}: simulators.connections needs simulators.instances or simulators.topologies: "
                        "connections are for instances the simulation adds")
    for entry in data.get("devices") or []:
        if not isinstance(entry, dict) or "instance" not in entry or "endpoint" not in entry:
            raise WstsError(f"{where}: each 'devices' entry needs 'instance' and 'endpoint'")
        unknown = set(entry) - DEVICE_KEYS
        if unknown:
            raise WstsError(f"{where}: device {entry['instance']}: unknown key(s): {', '.join(sorted(unknown))}")
        manifest.devices.append(DeviceSpec(str(entry["instance"]).replace("$", ""), str(entry["endpoint"]),
                                           str(entry.get("options", ""))))

    for entry in data.get("models", []):
        for key in ("name", "class", "endpoint", "kind"):
            if key not in entry:
                raise WstsError(f"{where}: model entry is missing '{key}'")
        kind = str(entry["kind"]).lower()
        if kind not in MODEL_KINDS:
            raise WstsError(f"{where}: model '{entry['name']}': kind must be one of {', '.join(sorted(MODEL_KINDS))}")
        manifest.models.append(
            ModelSpec(
                name=str(entry["name"]),
                cls=str(entry["class"]),
                header=str(entry.get("header", "Wsts/Core/Models.hpp")),
                endpoint=str(entry["endpoint"]),
                kind=kind,
                address=_int(entry.get("address", 0), f"{where}: model '{entry['name']}' address"),
                params={str(k): str(v) for k, v in (entry.get("params") or {}).items()},
            )
        )

    control = data.get("control", {})
    if isinstance(control, str):
        control = {"enabled": control.lower() not in ("none", "off", "false")}
    elif isinstance(control, bool):
        control = {"enabled": control}
    manifest.control_enabled = bool(control.get("enabled", True))
    if "base_id" in control:
        manifest.control_base_id = _int(control["base_id"], f"{where}: control.base_id")

    sources = data.get("sources") or {}
    manifest.topology_fpp = sources.get("fpp")
    manifest.topology_sources = sources.get("topology")
    main = data.get("main") or {}
    manifest.main = MainSpec(
        generate=bool(main.get("generate", False)),
        declarations=str(main.get("declarations", "")),
        start=str(main.get("start", "")),
        stop=str(main.get("stop", "")),
        sources=main.get("sources", sources.get("main")),
    )
    if manifest.main.generate and not manifest.main.start:
        raise WstsError(f"{where}: main.generate needs main.start (the C++ expression that starts the deployment)")

    setup = data.get("setup")
    if isinstance(setup, str):
        setup = {"code": setup}
    setup = setup or {}
    unknown = set(setup) - SETUP_KEYS if isinstance(setup, dict) else {"(neither C++ text nor a mapping)"}
    if unknown:
        raise WstsError(f"{where}: setup: unknown key(s): {', '.join(sorted(unknown))}. 'setup' is C++ text, or a "
                        f"mapping with {' and '.join(sorted(SETUP_KEYS))}")
    headers = setup.get("headers") or []
    if not isinstance(headers, list) or not all(isinstance(h, str) and h.strip() for h in headers):
        raise WstsError(f"{where}: setup.headers must be a list of header names, as they are written in #include <...>")
    manifest.setup = SetupSpec(code=str(setup.get("code") or ""), headers=[h.strip() for h in headers])
    if manifest.setup.headers and not manifest.setup.code.strip():
        raise WstsError(f"{where}: setup.headers is given without setup.code: the headers are for that code")

    for entry in data.get("clocks") or []:
        if not isinstance(entry, dict) or "instance" not in entry or "hz" not in entry:
            raise WstsError(f"{where}: each 'clocks' entry needs 'instance' and 'hz'")
        unknown = set(entry) - CLOCK_KEYS
        if unknown:
            raise WstsError(f"{where}: clock {entry['instance']}: unknown key(s): {', '.join(sorted(unknown))}")
        try:
            hz = float(entry["hz"])
        except (TypeError, ValueError):
            raise WstsError(f"{where}: clock {entry['instance']}: hz '{entry['hz']}' is not a number") from None
        if not 0.0 < hz <= MAX_CLOCK_HZ or round(hz * 1000) < 1:
            raise WstsError(
                f"{where}: clock {entry['instance']}: hz must be above 0 and at most {MAX_CLOCK_HZ:g} "
                f"(it is {entry['hz']}). The rate is in cycles per second of simulated time."
            )
        manifest.clocks.append(ClockSpec(str(entry["instance"]).replace("$", ""), hz, str(entry.get("options", ""))))

    run = data.get("run") or {}
    unknown = set(run) - RUN_KEYS if isinstance(run, dict) else {"(not a mapping)"}
    if unknown:
        raise WstsError(f"{where}: run: unknown key(s): {', '.join(sorted(unknown))}")
    for name, value in (run.get("env") or {}).items():
        if not re.fullmatch(ENV_NAME, str(name)):
            raise WstsError(f"{where}: run.env: '{name}' is not an environment variable name")
        manifest.run_env[str(name)] = "" if value is None else str(value)

    cmake = data.get("cmake") or {}
    unknown = set(cmake) - CMAKE_KEYS
    if unknown:
        raise WstsError(f"{where}: cmake: unknown key(s): {', '.join(sorted(unknown))}")
    for name, value in (cmake.get("cache") or {}).items():
        if not re.fullmatch(CACHE_NAME, str(name)):
            raise WstsError(f"{where}: cmake.cache: '{name}' is not a CMake variable name")
        values = value if isinstance(value, list) else [value]
        rendered = []
        for item in values:
            if isinstance(item, bool):
                item = "ON" if item else "OFF"
            item = str(item)
            if ";" in item or '"' in item or "\n" in item:
                raise WstsError(f"{where}: cmake.cache.{name}: write a list as a YAML list, without ';' or quotes")
            rendered.append(item)
        manifest.cmake_cache[str(name)] = rendered
    manifest.extra_subdirectories = [str(d) for d in cmake.get("extra_subdirectories", [])]
    manifest.exclude_subdirectories = [str(d) for d in cmake.get("exclude_subdirectories", [])]
    manifest.extra_depends = [str(d) for d in cmake.get("depends", [])]
    manifest.cmake_include = [str(f) for f in cmake.get("include") or []]
    stubs = cmake.get("stub_variants") or []
    if not isinstance(stubs, list) or not all(isinstance(d, str) and d.strip() for d in stubs):
        raise WstsError(f"{where}: cmake.stub_variants must be a list of module directories, relative to the flight project")
    manifest.stub_variants = [d.strip() for d in stubs]
    manifest.cxx_flags = _cxx_flags(cmake.get("cxx_flags"), where)
    host = data.get("host") or {}
    unknown = set(host) - HOST_KEYS if isinstance(host, dict) else {"(not a mapping)"}
    if unknown:
        raise WstsError(f"{where}: host: unknown key(s): {', '.join(sorted(unknown))}")
    ignore = host.get("ignore") or []
    if not isinstance(ignore, list):
        raise WstsError(f"{where}: host.ignore must be a list of header, library (-l<name>) and package names")
    manifest.host_ignore = [str(name).strip() for name in ignore]
    manifest.cmake_include_early = [str(f) for f in cmake.get("include_early") or []]
    libraries = data.get("libraries") or {}
    unknown = set(libraries) - LIBRARY_KEYS
    if unknown:
        raise WstsError(f"{where}: libraries: unknown key(s): {', '.join(sorted(unknown))}")
    manifest.sim_libraries = [str(d) for d in libraries.get("sim") or []]
    manifest.keep_libraries = [str(d) for d in libraries.get("keep", [])]
    manifest.drop_libraries = [str(d) for d in libraries.get("drop", [])]
    return manifest
