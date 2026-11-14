#!/usr/bin/env python3
"""Survey F´ projects checked out beside fprime-wsts, for configuring a simulation.

For every F´ project found (a directory with a settings.ini that has an [fprime] section)
it lists the deployments, and for each deployment:

  - the instances of its topology that touch hardware, and what fprime-wsts will do with
    each: replaced from the catalog, kept, or in need of a decision;
  - byte stream drivers by role: the ground link (normally kept), a hub link to another
    computer (a system link), or a device;
  - register interface components (candidates for Wsts.SimRegister), project components
    whose sources look like they open devices, and tick or time sources that will not run
    on a workstation;
  - whether the deployment has a main(), and entry points if it does not;
  - its ID range, command splitters and hubs (for a system's `ids` windows and links);
  - the result of a trial generation with a minimal manifest: substitutions, warnings,
    and the first error the generator stops on.

It also lists existing WSTS manifests found under the same directories, so an earlier
configuration can be continued instead of started again.

Nothing is written unless --starter-dir is given; then starter manifests are written
there, never over an existing file.

Usage:
  survey.py [DIR ...] [--deployment NAME]... [--no-trial] [--json]
            [--starter-dir DIR]

DIR defaults to the directory that holds the fprime-wsts checkout (its siblings).
Needs fprime-wsts installed (pip install -e ./python) and its lib/fprime checked out.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

SKILL_DIR = Path(__file__).resolve().parents[1]
WSTS_ROOT = SKILL_DIR.parents[2]  # <checkout>/.claude/skills/<skill>

try:
    from fprime_wsts import fpp, registers
    from fprime_wsts.fpp import WstsError
    from fprime_wsts.generate import generate, read_registration
    from fprime_wsts.manifest import DEFAULT_CATALOG, load_catalog, parse_manifest
except ImportError as error:  # pragma: no cover - environment problem, reported plainly
    sys.exit(
        f"survey.py: cannot import fprime_wsts ({error}).\n"
        f"Activate the virtual environment where it is installed, or install it:\n"
        f"  pip install -r {WSTS_ROOT}/lib/fprime/requirements.txt -e {WSTS_ROOT}/python"
    )

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "wsts-logs", "logs", "build-artifacts"}
MAX_DEPTH = 4

# Interface kind of a model behind each stock surrogate
SURROGATE_KIND = {
    "Wsts.SimByteStream": "bytestream",
    "Wsts.SimI2c": "i2c",
    "Wsts.SimSpi": "spi",
    "Wsts.SimGpio": "gpio",
    "Wsts.SimRegister": "register",
    "Wsts.SimTick": None,
}
SOCKET_DRIVERS = {"Drv.TcpClient", "Drv.TcpServer", "Drv.Udp"}
HOST_TICK_SOURCES = {"Svc.LinuxTimer"}
GROUND_NEIGHBOURS = {"Svc.ComStub", "Svc.FrameAccumulator", "Svc.Ccsds.TcDeframer", "Svc.FprimeDeframer",
                     "Svc.Deframer", "Svc.Framer", "Svc.FprimeFramer", "Svc.Ccsds.TmFramer", "Svc.ComQueue"}

# Text in a component's own sources that suggests it reaches hardware itself
HARDWARE_MARKERS = [
    (re.compile(r'"/dev/[^"]*"'), "opens a /dev path"),
    (re.compile(r"\bioctl\s*\("), "ioctl()"),
    (re.compile(r"\bmmap\s*\("), "mmap()"),
    (re.compile(r"#\s*include\s*<(termios|linux/[\w/]+|sys/ioctl|gpiod|libusb[\w./-]*|i2c/smbus)\.h>"), "Linux device header"),
    (re.compile(r"#\s*include\s*[<\"](vxWorks|sysLib|intLib|iosLib|ioLib|taskLib|semLib|drv/[\w/]+)\.h[>\"]"), "VxWorks header"),
    (re.compile(r"\bintConnect\s*\(|\bsysClkConnect\s*\("), "connects an interrupt"),
    (re.compile(r"\bvolatile\s+(U32|U16|U8|uint32_t|uint16_t|uint8_t)\s*\*"), "volatile register pointer"),
]


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


def is_fprime_settings(path: Path) -> bool:
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(path)
    except configparser.Error:
        return False
    return parser.has_section("fprime")


def walk(root: Path, depth: int = 0):
    """Directories below root, not following links, skipping build and VCS directories"""
    yield root
    if depth >= MAX_DEPTH:
        return
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return
    for child in children:
        if child.name in SKIP_DIRS or child.name.startswith("build-") or (child / fpp.GENERATED_MARKER).exists():
            continue
        yield from walk(child, depth + 1)


@dataclass
class Found:
    projects: List[Path] = field(default_factory=list)
    manifests: List[dict] = field(default_factory=list)


def discover(dirs: List[Path]) -> Found:
    found = Found()
    framework_dirs: Set[Path] = set()
    seen: Set[Path] = set()
    for top in dirs:
        for directory in walk(top):
            if directory in seen or any(f in directory.parents or f == directory for f in framework_dirs):
                continue
            seen.add(directory)
            if directory == WSTS_ROOT or WSTS_ROOT in directory.parents:
                continue
            settings = directory / "settings.ini"
            if settings.is_file() and is_fprime_settings(settings):
                found.projects.append(directory)
                try:
                    framework_dirs.add(fpp.read_settings(directory).framework_path.resolve())
                except Exception:
                    pass
            for yaml_file in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
                kind = manifest_kind(yaml_file)
                if kind:
                    found.manifests.append({"path": str(yaml_file), "kind": kind})
    return found


def manifest_kind(path: Path) -> Optional[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    if re.search(r"^system:\s*\S", text, re.M) and re.search(r"^elements:", text, re.M):
        return "system"
    if re.search(r"^deployment:\s*\S", text, re.M):
        return "deployment"
    return None


def framework_pins(framework: Path) -> Dict[str, str]:
    """Versions of fprime-fpp and fprime-tools a framework checkout pins: how its version is told apart"""
    pins = {}
    requirements = framework / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text(errors="replace").splitlines():
            match = re.match(r"\s*(fprime-fpp|fprime-tools)==([\w.]+)", line)
            if match:
                pins[match.group(1)] = match.group(2)
    return pins


def find_deployments(project: Path, settings: fpp.ProjectSettings) -> List[Path]:
    """Deployment directories of a project: a Top/ with topology FPP, outside the framework
    and the libraries"""
    excluded = {settings.framework_path.resolve(), *(p.resolve() for p in settings.library_locations)}
    deployments = []
    for directory in walk(project):
        if directory.resolve() in excluded or any(e in directory.resolve().parents for e in excluded):
            continue
        top = directory / "Top"
        if not top.is_dir():
            continue
        cmake = directory / "CMakeLists.txt"
        registers_deployment = cmake.is_file() and "register_fprime_deployment" in cmake.read_text(errors="replace")
        has_topology = any(re.search(r"^\s*topology\s+\w+", f.read_text(errors="replace"), re.M)
                           for f in top.glob("*.fpp"))
        if registers_deployment or has_topology:
            deployments.append(directory)
    return deployments


# ----------------------------------------------------------------------
# Analysis of one deployment
# ----------------------------------------------------------------------


def load_model(project: Path, settings: fpp.ProjectSettings, deployment: Path, work: Path):
    """The FPP model of a deployment, read as fprime-wsts reads it (without the simulation)"""
    top_dir = deployment / "Top"
    roots = [settings.framework_path, *settings.library_locations, project]
    config_overrides, _ = fpp.find_config_overrides(roots)
    found_files = fpp.find_fpp_files(roots)
    config_overrides = [o for o in config_overrides if o.winner in found_files and any(r in found_files for r in o.replaced)]
    registration = read_registration(top_dir / "CMakeLists.txt", "register_fprime_module")
    top_fpp = [Path(f).resolve() for f in registration.get("AUTOCODER_INPUTS", []) if f.endswith(".fpp")]
    top_fpp = top_fpp or sorted(top_dir.glob("*.fpp"))
    candidates = [c for c in fpp.select_candidates(found_files, settings.framework_path, config_overrides)
                  if c not in set(top_fpp)]
    fpp.locate_defs(work, candidates)
    model = fpp.analyze(work, fpp.depend(work, top_fpp) + top_fpp)
    local = [t for t in model.topologies.values() if t.file in top_fpp or t.file.parent == top_dir]
    return model, local


def restriction(component: fpp.Component, stop_at: List[Path]) -> Optional[str]:
    directory = component.file.parent
    stops = {p.resolve() for p in stop_at}
    while True:
        for name in ("CMakeLists.txt", "library.cmake"):
            candidate = directory / name
            if candidate.is_file():
                for match in re.finditer(r"restrict_platforms\(([^)]*)\)", candidate.read_text(errors="replace")):
                    platforms = {p.strip().strip('"').lower() for p in match.group(1).split()}
                    if platforms and not platforms & {"linux", "posix", "sockets"}:
                        return f"restrict_platforms({match.group(1).strip()})"
        if directory in stops or directory.parent == directory:
            return None
        directory = directory.parent


def hardware_markers(component: fpp.Component) -> List[str]:
    directory = component.file.parent
    hits: List[str] = []
    for source in sorted(list(directory.glob("*.cpp")) + list(directory.glob("*.hpp")) + list(directory.glob("*.c"))):
        try:
            text = source.read_text(errors="replace")
        except OSError:
            continue
        for pattern, what in HARDWARE_MARKERS:
            match = pattern.search(text)
            if match:
                line = text[:match.start()].count("\n") + 1
                hits.append(f"{what} ({source.name}:{line})")
    return hits


DATA_PORTS = {"send", "$send", "recv", "$recv"}
ADAPTERS = {"Drv.ByteStreamBufferAdapter"}


def data_peers(topology: fpp.Topology, name: str, ports: Optional[Set[str]] = None) -> Set[str]:
    """Instances connected to `name` on the given ports of `name` (all ports when None)"""
    out = set()
    for c in topology.connections:
        if c.from_instance == name and (ports is None or c.from_port in ports):
            out.add(c.to_instance)
        if c.to_instance == name and (ports is None or c.to_port in ports):
            out.add(c.from_instance)
    return out


def bytestream_role(model: fpp.Model, topology: fpp.Topology, name: str) -> str:
    """ground, hub or device, from where the driver's data goes: what its send and recv
    ports are connected to, looking through a byte stream buffer adapter"""
    def component(i: str) -> str:
        return model.instances[i].component if i in model.instances else ""

    peers = data_peers(topology, name, DATA_PORTS)
    for adapter in [p for p in peers if component(p) in ADAPTERS]:
        peers |= {p for p in data_peers(topology, adapter) if p != name and component(p) != "Svc.BufferManager"}
    components = {component(p) for p in peers}
    if "Svc.GenericHub" in components:
        return "hub"
    if components & GROUND_NEIGHBOURS or any(p.split(".")[0].startswith("Com") for p in peers):
        return "ground"
    return "device"


def is_bytestream(component: fpp.Component) -> bool:
    names = set(component.ports)
    return {"send", "recv"} <= names or {"send", "$recv"} <= names


def code_lines(deployment: Path, instance_base: str) -> List[str]:
    """Lines of the deployment's hand-written code that set up an instance"""
    lines = []
    pattern = re.compile(rf"\b{re.escape(instance_base)}\s*\.\s*(configure|open|start|setup|init)\w*\s*\(")
    for source in sorted(deployment.rglob("*.cpp")) + sorted(deployment.rglob("*.hpp")):
        if any(part.startswith("build-") for part in source.parts):
            continue
        for number, line in enumerate(source.read_text(errors="replace").splitlines(), 1):
            if pattern.search(line):
                lines.append(f"{source.relative_to(deployment)}:{number}: {line.strip()}")
    return lines[:6]


def entry_points(deployment: Path) -> Tuple[bool, List[str]]:
    has_main = False
    entries = []
    for source in sorted(deployment.rglob("*.cpp")):
        if any(part.startswith("build-") for part in source.parts) or "/test/" in source.as_posix():
            continue
        text = source.read_text(errors="replace")
        if re.search(r"\bint\s+main\s*\(", text):
            has_main = True
        for match in re.finditer(r'^\s*(extern\s+"C"\s+)?[\w:<>\*\s]+?\b(\w+)\s*\(([^)]*)\)\s*\{', text, re.M):
            if match.group(1):
                line = text[:match.start()].count("\n") + 1
                entries.append(f"{source.relative_to(deployment)}:{line}: {' '.join(match.group(0).rstrip('{').split())}")
    return has_main, entries[:8]


def splitter_bases(project: Path, deployment: Path) -> List[str]:
    """How command splitters are configured: the remote opcode base"""
    hits = []
    for directory in (deployment, project / "config"):
        if not directory.is_dir():
            continue
        for source in sorted(directory.rglob("*")):
            if source.suffix not in (".cpp", ".hpp", ".fpp", ".h") or any(p.startswith("build-") for p in source.parts):
                continue
            for number, line in enumerate(source.read_text(errors="replace").splitlines(), 1):
                if re.search(r"cmdSplitter\w*\.configure\s*\(|OPCODE_BASE|BASE_OPCODE", line):
                    hits.append(f"{source.relative_to(project)}:{number}: {line.strip()}")
    return hits[:6]


def analyse(project: Path, settings: fpp.ProjectSettings, deployment: Path, catalog_replaces: Dict[str, str],
            trial: bool) -> dict:
    rel = deployment.relative_to(project).as_posix()
    result: dict = {"deployment": rel, "name": deployment.name, "directory": str(deployment)}
    stops = [settings.framework_path, *settings.library_locations, project]
    with tempfile.TemporaryDirectory(prefix="wsts-survey-") as scratch:
        try:
            model, local = load_model(project, settings, deployment, Path(scratch))
        except WstsError as error:
            result["model_error"] = str(error)
            return result
    if len(local) != 1:
        result["topologies"] = [t.name for t in local]
        result["model_error"] = (f"{len(local)} topologies in Top/: the manifest must name one with 'topology:'"
                                 if local else "no topology found in Top/")
        if not local:
            return result
    topology = local[0]
    result["topology"] = topology.name
    framework = settings.framework_path.resolve()

    instances = []
    ids = []
    endpoints: Dict[str, List[str]] = {}
    for name in topology.instances:
        instance = model.instances[name]
        ids += [instance.base_id, max(instance.base_id, instance.max_id)]
        component = model.components.get(instance.component)
        if component is None:
            continue
        in_framework = framework in component.file.resolve().parents
        entry = {"instance": name, "component": component.name}
        notes = []
        surrogate = catalog_replaces.get(component.name)
        restricted = restriction(component, stops)
        if surrogate:
            entry["action"] = "catalog"
            entry["surrogate"] = surrogate
            entry["endpoint"] = instance.base_name
            entry["model_kind"] = SURROGATE_KIND.get(surrogate)
        if is_bytestream(component):
            entry["bytestream_role"] = bytestream_role(model, topology, name)
            if component.name in SOCKET_DRIVERS or not surrogate:
                if entry["bytestream_role"] == "ground":
                    entry["action"] = "keep (ground link)"
                elif entry["bytestream_role"] == "hub":
                    entry["action"] = "link end (system); alone: substitute with Wsts.SimByteStream"
                elif not surrogate:
                    entry["action"] = "decide: device on a socket/driver the catalog does not know"
        roles = registers.register_roles(component, model.port_defs)
        if not in_framework and any(r != "interrupt" for r in roles.values()):
            entry["action"] = entry.get("action") or "decide: register interface (Wsts.SimRegister)"
            entry["register_ports"] = roles
        if restricted and not surrogate:
            entry["action"] = entry.get("action") or "decide: cannot build on Linux, needs a surrogate"
            notes.append(restricted)
        if not in_framework and not surrogate and "action" not in entry:
            markers = hardware_markers(component)
            if markers:
                entry["action"] = "decide: project component that may reach hardware"
                notes += markers
        if "action" in entry:
            setup = code_lines(deployment, instance.base_name)
            if setup:
                entry["setup_code"] = setup
            if notes:
                entry["notes"] = notes
            instances.append(entry)
        if surrogate or entry.get("bytestream_role") in ("hub",):
            endpoints.setdefault(instance.base_name, []).append(name)

    # Tick and time sources that a workstation will not drive
    cycle_targets = {c.to_instance for c in topology.connections
                     if _port_type(model, c.to_instance, c.to_port) == "Svc.Cycle"}
    tick_sources = sorted({c.from_instance for c in topology.connections
                           if _port_type(model, c.to_instance, c.to_port) == "Svc.Cycle"} - cycle_targets)
    timing = []
    for source in tick_sources:
        component = model.instances[source].component
        timing.append({"role": "rate group tick", "instance": source, "component": component,
                       "handled": component in HOST_TICK_SOURCES or component in catalog_replaces})
    if topology.time_source and topology.time_source in model.instances:
        component = model.instances[topology.time_source].component
        comp = model.components.get(component)
        timing.append({"role": "time source", "instance": topology.time_source, "component": component,
                       "handled": bool(comp and framework in comp.file.resolve().parents) or component in catalog_replaces})

    has_main, entries = entry_points(deployment)
    components_used = {model.instances[n].component for n in topology.instances}
    result.update({
        "toolchain": settings.default_toolchain,
        "instance_count": len(topology.instances),
        "id_range": [hex(min(ids)), hex(max(ids))] if ids else None,
        "hardware": instances,
        "timing": timing,
        "has_main": has_main,
        "entry_points": [] if has_main else entries,
        "hubs": sorted(n for n in topology.instances if model.instances[n].component == "Svc.GenericHub"),
        "splitters": sorted(n for n in topology.instances if model.instances[n].component == "Svc.CmdSplitter"),
        "splitter_config": splitter_bases(project, deployment) if "Svc.CmdSplitter" in components_used else [],
        "endpoint_collisions": {k: v for k, v in endpoints.items() if len(v) > 1},
    })
    if trial:
        result["trial"] = trial_generate(project, deployment, result)
    return result


def _port_type(model: fpp.Model, instance: str, port: str) -> Optional[str]:
    if instance not in model.instances:
        return None
    component = model.components.get(model.instances[instance].component)
    found = component.ports.get(port) if component else None
    return found.type if found else None


def minimal_manifest(project: Path, deployment_rel: str, analysis: dict, base: Path) -> dict:
    keep = [h["instance"] for h in analysis.get("hardware", []) if h.get("bytestream_role") == "ground"]
    data: dict = {"project": os.path.relpath(project, base), "deployment": deployment_rel}
    if keep:
        data["keep"] = keep
    if len(analysis.get("topologies", [])) > 1:
        data["topology"] = analysis["topologies"][0]
    return data


def trial_generate(project: Path, deployment: Path, analysis: dict) -> dict:
    """Generate with a minimal manifest, writing nothing, and keep what it says"""
    with tempfile.TemporaryDirectory(prefix="wsts-trial-") as scratch:
        base = Path(scratch)
        data = minimal_manifest(project, deployment.relative_to(project).as_posix(), analysis, base)
        trial: dict = {"manifest": data}
        try:
            manifest = parse_manifest(data, base / "wsts.yaml")
            result = generate(project, manifest, base / "out", check=True)
        except WstsError as error:
            trial["error"] = str(error)
            return trial
        except Exception as error:  # an unexpected failure is still worth reporting
            trial["error"] = f"{type(error).__name__}: {error}"
            return trial
        report = result.report
        trial["substitutions"] = [
            {k: s[k] for k in ("instance", "flight_component", "surrogate", "chosen_by", "endpoint")}
            for s in report.get("substitutions", [])
        ]
        trial["warnings"] = report.get("warnings", [])
        trial["kept_hardware_instances"] = report.get("kept_hardware_instances", [])
        trial["configure_options"] = report.get("configure_options", [])
        return trial


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def system_hints(projects: List[dict]) -> dict:
    deployments = [(p, d) for p in projects for d in p.get("deployments", []) if "topology" in d]
    hub_ends = []
    ground = []
    for project, d in deployments:
        for h in d.get("hardware", []):
            if h.get("bytestream_role") == "hub":
                hub_ends.append({"deployment": d["name"], "project": project["root"], "instance": h["instance"],
                                 "component": h["component"], "setup_code": h.get("setup_code", [])})
            if h.get("bytestream_role") == "ground":
                ground.append(d["name"])
    return {"hub_link_ends": hub_ends, "ground_candidates": sorted(set(ground))}


def element_name(deployment: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", deployment.lower()) or "element"


def starter_manifest(project_root: Path, d: dict, sim_dir: Path, system: bool) -> str:
    rel_project = os.path.relpath(project_root, sim_dir)
    lines = [
        f"# WSTS manifest for {d['name']} (starter written by the wsts-configure skill's survey).",
        "# Every TODO is a decision to make with the user; see the skill's references/decisions.md.",
        f"project: {rel_project}",
        f"deployment: {d['deployment']}",
    ]
    if len(d.get("topologies", [])) > 1:
        lines.append(f"topology: {d['topologies'][0]}   # TODO: which topology is the deployment's")
    ground = [h["instance"] for h in d.get("hardware", []) if h.get("bytestream_role") == "ground"]
    if ground:
        lines += ["", "# The ground link stays real so the GDS connects as in flight", "keep:"]
        lines += [f"  - {name}" for name in ground]
    if not d.get("has_main", True):
        lines += ["", "# TODO: the deployment has no main(); the target OS starts it. Entry points found:"]
        lines += [f"#   {e}" for e in d.get("entry_points", [])] or ["#   (none found: ask the user how it starts)"]
        declarations = [re.sub(r"\s*\{?\s*$", ";", e.split(": ", 1)[-1]) for e in d.get("entry_points", [])]
        lines += ["main:", "  generate: true", "  declarations: |"]
        lines += [f"    {decl}" for decl in declarations] or ["    // TODO: declarations of the entry points"]
        lines += ["  start: TODO(hostname, port)   # hostname and port come from -a and -p; fill in the other arguments",
                  "  stop: TODO()"]
    decide = [h for h in d.get("hardware", []) if h.get("action", "").startswith("decide")]
    if decide:
        lines += ["", "# TODO: instances that need a decision (substitute, keep, or a project surrogate):"]
        for h in decide:
            lines.append(f"#   {h['instance']} ({h['component']}): {h['action'].split(': ', 1)[-1]}")
    substitutes = [(h, "Wsts.SimRegister", ["# A register interface component: the generator writes Wsts.SimRegister",
                                            "# again with its port types; the flight drivers run unchanged"])
                   for h in decide if "register_ports" in h]
    if not system:
        substitutes += [(h, "Wsts.SimByteStream", [
            "# Simulated alone, a hub driver becomes a bus endpoint with nothing behind it, as if the",
            "# other computer were silent. Kept as a real socket it reports the link down, and flight",
            "# software's handling of that (events about the link, sent through the hub) runs too:",
            "# keep it only if that is what the user wants to test."])
            for h in d.get("hardware", []) if h.get("bytestream_role") == "hub"]
    if substitutes:
        lines += ["", "substitute:"]
        for h, surrogate, why in substitutes:
            lines += [f"  {line}" for line in why]
            lines += [f"  - instance: {h['instance']}", f"    with: {surrogate}"]
    endpoints = [h for h in d.get("hardware", []) if h.get("model_kind")]
    if endpoints:
        lines += ["", "# Bus endpoints that will exist. Without a model: an I2C transaction fails (address error),",
                  "# an SPI read is zeros, a byte stream accepts what is sent and nothing answers, a GPIO holds",
                  "# its level. TODO: decide with the user which devices need a model (stock or written).",
                  "# models:"]
        for h in endpoints:
            lines.append(f"#   - {{name: TODO, class: TODO, endpoint: {h['endpoint']}, kind: {h['model_kind']}}}"
                         f"   # {h['instance']} ({h['component']})")
    for t in d.get("timing", []):
        if not t["handled"]:
            lines += ["", f"# TODO: {t['role']} {t['instance']} ({t['component']}) is a project component that will "
                      "probably not run on a workstation: it needs a project surrogate (docs/surrogate-guide.md)."]
    if not system:
        if not ground:
            lines += ["", "# NOTE: no ground link of its own: the GDS cannot connect to this deployment alone.",
                      "# Drive it through the bridge (WstsBridge, scenarios), or simulate it in a system."]
        lines += ["", "bridge: tcp:50100"]
    return "\n".join(lines) + "\n"


def starter_system(elements: List[Tuple[str, str]], hints: dict) -> str:
    lines = ["# WSTS system manifest (starter written by the wsts-configure skill's survey).",
             "system: TODO", ""]
    if hints["ground_candidates"]:
        lines.append(f"ground: {element_name(hints['ground_candidates'][0])}   # element the GDS connects to")
    lines += ["", "elements:"]
    for name, manifest in elements:
        lines += [f"  {name}:", f"    manifest: {manifest}",
                  "    # ids: [TODO, TODO]   # IDs routed to this element (needed when a command splitter routes by opcode)"]
    ends = hints["hub_link_ends"]
    lines += ["", "links:"]
    if len(ends) == 2:
        a, b = ends
        lines += ["  - name: hub", f"    a: {element_name(a['deployment'])}.{a['instance'].rsplit('.', 1)[-1]}",
                  f"    b: {element_name(b['deployment'])}.{b['instance'].rsplit('.', 1)[-1]}"]
    else:
        lines.append("  # TODO: pair the byte stream drivers that join the elements:")
        lines += [f"  #   {e['deployment']}: {e['instance']} ({e['component']})" for e in ends] or ["  #   (none found)"]
    return "\n".join(lines) + "\n"


def render(report: dict) -> str:
    out = ["# F´ projects found", ""]
    if not report["projects"]:
        out.append(f"No F´ project (settings.ini with an [fprime] section) under: {', '.join(report['searched'])}")
    for project in report["projects"]:
        out.append(f"## {project['root']}")
        if "error" in project:
            out += [f"- **cannot read settings.ini**: {project['error']}", ""]
            continue
        out.append(f"- framework: `{project['framework']}`" + ("" if project["framework_exists"] else "  **(missing: submodule not checked out?)**"))
        if project["libraries"]:
            out.append(f"- libraries: {', '.join(f'`{l}`' for l in project['libraries'])}")
        out.append(f"- default toolchain: `{project['toolchain']}`")
        if project["framework_pins"] != project["toolkit_pins"]:
            out.append(f"- **framework version differs from the toolkit's**: project pins {project['framework_pins'] or 'unknown'}, "
                       f"fprime-wsts is built against {project['toolkit_pins']} (lib/fprime)")
        for missing in project["missing_libraries"]:
            out.append(f"- **library location missing**: `{missing}` (submodule not checked out?)")
        if not project["deployments"]:
            out.append("- no deployments found")
        for d in project["deployments"]:
            out += ["", f"### Deployment `{d['deployment']}`"]
            if "model_error" in d:
                out.append(f"- **model**: {d['model_error']}")
                if "topology" not in d:
                    continue
            out.append(f"- topology `{d['topology']}`, {d['instance_count']} instances, IDs {d['id_range'][0]}..{d['id_range'][1]}")
            out.append(f"- main(): {'yes' if d['has_main'] else '**no** (needs `main:` in the manifest)'}")
            for e in d.get("entry_points", []):
                out.append(f"  - entry point: `{e}`")
            if d["hubs"] or d["splitters"]:
                out.append(f"- hubs: {', '.join(d['hubs']) or 'none'}; command splitters: {', '.join(d['splitters']) or 'none'}")
                for line in d["splitter_config"]:
                    out.append(f"  - `{line}`")
            if d["hardware"]:
                out += ["", "| Instance | Component | What happens |", "|---|---|---|"]
                for h in d["hardware"]:
                    what = h.get("action", "")
                    if h.get("surrogate"):
                        what += f" → `{h['surrogate']}`, endpoint `{h['endpoint']}`"
                    if h.get("bytestream_role"):
                        what += f" (byte stream: {h['bytestream_role']})"
                    if h.get("notes"):
                        what += "; " + "; ".join(h["notes"][:3])
                    out.append(f"| `{h['instance']}` | `{h['component']}` | {what} |")
                for h in d["hardware"]:
                    for line in h.get("setup_code", [])[:3]:
                        out.append(f"  - `{h['instance']}` set up at `{line}`")
            for t in d["timing"]:
                if not t["handled"]:
                    out.append(f"- **{t['role']}** `{t['instance']}` (`{t['component']}`) is a project component: it "
                               "probably will not run on a workstation and needs a project surrogate")
            if d["endpoint_collisions"]:
                out.append(f"- **endpoint name collisions** (need `endpoint:` on one): {d['endpoint_collisions']}")
            trial = d.get("trial")
            if trial:
                out.append("")
                if "error" in trial:
                    out.append("- trial generation **stopped**:")
                    out += ["  > " + line for line in trial["error"].splitlines()]
                else:
                    out.append(f"- trial generation succeeded: {len(trial['substitutions'])} substitution(s), "
                               f"{len(trial['warnings'])} warning(s)")
                    for w in trial["warnings"]:
                        out.append(f"  - warning: {w}")
                    if trial["configure_options"]:
                        out.append(f"  - configure options: {' '.join(trial['configure_options'])}")
        out.append("")
    hints = report["system_hints"]
    if hints["hub_link_ends"] or len(report.get("deployment_names", [])) > 1:
        out += ["# System hints", ""]
        out.append(f"- ground candidates (have a ground link): {', '.join(hints['ground_candidates']) or 'none'}")
        for e in hints["hub_link_ends"]:
            out.append(f"- hub link end in {e['deployment']}: {e['instance']} ({e['component']})")
            for line in e["setup_code"][:2]:
                out.append(f"  - `{line}`")
        out.append("")
    if report["manifests"]:
        out += ["# Existing WSTS manifests", ""]
        out += [f"- {m['kind']}: `{m['path']}`" for m in report["manifests"]]
        out.append("")
    if report.get("starters_written"):
        out += ["# Starter manifests written", ""] + [f"- `{p}`" for p in report["starters_written"]] + [""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dirs", nargs="*", help="directories to search (default: the siblings of the fprime-wsts checkout)")
    parser.add_argument("--deployment", action="append", default=[], help="only deployments with this directory name (repeatable)")
    parser.add_argument("--no-trial", action="store_true", help="skip the trial generation")
    parser.add_argument("--starter-dir", help="write starter manifests here (never overwrites)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    dirs = [Path(d).resolve() for d in args.dirs] or [WSTS_ROOT.parent]
    found = discover(dirs)
    catalog = load_catalog(DEFAULT_CATALOG)
    catalog_replaces = {component: s.name for s in catalog for component in s.replaces}

    report: dict = {"searched": [str(d) for d in dirs], "wsts_root": str(WSTS_ROOT), "projects": [],
                    "manifests": found.manifests}
    for project in found.projects:
        entry: dict = {"root": str(project)}
        try:
            settings = fpp.read_settings(project)
        except Exception as error:
            entry["error"] = str(error)
            report["projects"].append(entry)
            continue
        entry.update({
            "framework": str(settings.framework_path),
            "framework_exists": (settings.framework_path / "cmake").is_dir(),
            "libraries": [str(p) for p in settings.library_locations],
            "missing_libraries": [str(p) for p in settings.library_locations if not p.is_dir()],
            "toolchain": settings.default_toolchain,
            "framework_pins": framework_pins(settings.framework_path),
            "toolkit_pins": framework_pins(WSTS_ROOT / "lib" / "fprime"),
            "deployments": [],
        })
        for deployment in find_deployments(settings.project_root, settings):
            if args.deployment and deployment.name not in args.deployment:
                continue
            if not entry["framework_exists"]:
                entry["deployments"].append({"deployment": deployment.relative_to(project).as_posix(),
                                             "name": deployment.name,
                                             "model_error": "framework not checked out: run git submodule update --init"})
                continue
            print(f"survey: reading {deployment}", file=sys.stderr)
            entry["deployments"].append(analyse(settings.project_root, settings, deployment, catalog_replaces,
                                                not args.no_trial))
        report["projects"].append(entry)
    report["deployment_names"] = [d["name"] for p in report["projects"] for d in p.get("deployments", [])]
    report["system_hints"] = system_hints(report["projects"])

    if args.starter_dir:
        target = Path(args.starter_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        written = []
        analysed = [(Path(p["root"]), d) for p in report["projects"] for d in p.get("deployments", []) if "topology" in d]
        system = len(analysed) > 1
        elements = []
        for root, d in analysed:
            name = element_name(d["name"])
            path = target / (f"{name}.yaml" if system else "wsts.yaml")
            elements.append((name, path.name))
            if not path.exists():
                path.write_text(starter_manifest(root, d, target, system))
                written.append(str(path))
        if system and not (target / "wsts-system.yaml").exists():
            (target / "wsts-system.yaml").write_text(starter_system(elements, report["system_hints"]))
            written.append(str(target / "wsts-system.yaml"))
        report["starters_written"] = written

    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
