"""Deployment transform: turn a flight deployment into a simulated one.

The output is an overlay F Prime project. It reuses the original project's components
and hand-written topology code by path, and carries only what differs: patched topology
FPP (hardware instances retyped to surrogates, WSTS instances added), generated set-up
code, and build files.
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import flight_cmake
from . import fpp
from . import hostcheck
from . import simulators as sims
from .fpp import WstsError
from .manifest import ALL_MODULES, DEFAULT_CATALOG, TOOLKIT_ROOT, LinkEnd, Manifest, ModelSpec, Surrogate, load_catalog

SETUP_COMPONENT = "Wsts.SimSetup"
CONTROL_COMPONENT = "Wsts.SimControl"
SETUP_INSTANCE = "wstsSetup"
CONTROL_INSTANCE = "wstsControl"
LINK_SURROGATE = "Wsts.SimByteStream"
ID_BLOCK = 0x1000
HOST_PLATFORMS = {"linux", "posix", "sockets"}
ATTRIBUTE_CLAUSE = re.compile(r"\b(queue\s+size|stack\s+size|priority|cpu)\s+[^\\\n{]+")
ATTRIBUTE_ORDER = ["queue size", "stack size", "priority", "cpu"]  # the order FPP wants them in
DEFAULT_QUEUE_SIZE = 10


# ----------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------


@dataclass
class PlannedSubstitution:
    instance: fpp.Instance
    original: fpp.Component
    surrogate: fpp.Component
    catalog: Surrogate
    endpoint: str
    options: str
    reason: str  # manifest, catalog, structural, link
    # Flight port name -> the surrogate's name for it, or None when the surrogate has none
    port_map: Dict[str, Optional[str]] = field(default_factory=dict)
    # Instance attributes to write (queue size, ...): the surrogate is queued or active
    # and the flight definition does not have them
    attributes: Dict[str, str] = field(default_factory=dict)
    # False for a surrogate that does not use the bus (`endpoint: none`): no endpoint, no wstsBind()
    bind: bool = True


@dataclass
class PlannedLink:
    end: LinkEnd
    instance: str  # qualified instance name
    endpoint: str


@dataclass
class Plan:
    topology: fpp.Topology
    substitutions: List[PlannedSubstitution] = field(default_factory=list)
    kept_hardware: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fidelity_notes: List[str] = field(default_factory=list)
    setup_base_id: int = 0
    control_base_id: int = 0
    links: List[PlannedLink] = field(default_factory=list)
    sim: sims.SimulatorPlan = field(default_factory=sims.SimulatorPlan)


def _host_restriction(text: str) -> Optional[re.Match]:
    """The first restrict_platforms() call in CMake text that leaves a Linux host out"""
    for match in re.finditer(r"restrict_platforms\(([^)]*)\)", text):
        platforms = {p.strip().strip('"').lower() for p in match.group(1).split() if p.strip()}
        if platforms and not (platforms & HOST_PLATFORMS):
            return match
    return None


def _restricted_from_host(component: fpp.Component, stop_at: List[Path]) -> Optional[str]:
    """If the component's module is platform-restricted away from Linux, say how"""
    directory = component.file.parent
    roots = {r.resolve() for r in stop_at}
    while True:
        for name in ("CMakeLists.txt", "library.cmake"):
            candidate = directory / name
            if not candidate.is_file():
                continue
            match = _host_restriction(candidate.read_text(errors="replace"))
            if match:
                return f"restrict_platforms({match.group(1).strip()}) in {candidate}"
        if directory in roots or directory.parent == directory:
            return None
        directory = directory.parent


def _covers(surrogate: fpp.Component, original: fpp.Component, used_ports: Set[str],
            port_map: Optional[Dict[str, Optional[str]]] = None) -> List[str]:
    """Ports of `original` in use that `surrogate` cannot serve. Empty means it fits.
    `port_map` gives the surrogate's name for a flight port, or None to leave it unserved."""
    missing = []
    port_map = port_map or {}
    for name in sorted(used_ports):
        wanted = original.ports.get(name)
        if name in port_map and port_map[name] is None:
            continue  # the mapping says the surrogate has no such port: the connection is dropped
        offered = surrogate.ports.get(port_map.get(name, name))
        if wanted is None or wanted.kind == "Special":
            continue  # pattern-connected ports simply have nothing to connect to
        if offered is None:
            missing.append(f"{name} (not present)" if name not in port_map
                           else f"{name} (mapped to {port_map[name]}, which {surrogate.name} does not have)")
        elif offered.type != wanted.type:
            missing.append(f"{name} (type {offered.type}, flight uses {wanted.type})")
        elif offered.is_input != wanted.is_input:
            missing.append(f"{name} (direction differs)")
    return missing


CYCLE_PORT = "Svc.Cycle"
# Components that produce ticks by themselves on a Linux host
HOST_TICK_SOURCES = {"Svc.LinuxTimer", "Wsts.SimTick"}


def resolve_instance(spec: str, names: List[str]) -> Optional[str]:
    """The instance of a topology that a manifest names.

    The qualified name always works (`SensorLink.uart`). A shorter name works when it ends
    exactly one instance's qualified name (`uart`); naming several that way is an error.
    Returns None when nothing matches.
    """
    if spec in names:
        return spec
    found = [name for name in names if name.endswith("." + spec)]
    if len(found) > 1:
        raise WstsError(
            f"instance name '{spec}' is ambiguous: it matches {', '.join(sorted(found))}. Use the qualified name."
        )
    return found[0] if found else None


def _check_tick_sources(plan: Plan, model: fpp.Model, framework: Optional[Path]) -> None:
    """Warn when what cycles the flight rate groups, or supplies time, is a kept component
    that will probably do nothing on a workstation"""
    topology = plan.topology
    substituted = {sub.instance.name for sub in plan.substitutions}

    def port_type(instance: str, port: str) -> Optional[str]:
        component = model.components.get(model.instances[instance].component)
        found = component.ports.get(port) if component else None
        return found.type if found else None

    cycles = [c for c in topology.connections if port_type(c.to_instance, c.to_port) == CYCLE_PORT]
    cycled = {c.to_instance for c in cycles}
    # A rate group driver passes cycles on: what matters is where they start
    for source in sorted({c.from_instance for c in cycles} - cycled):
        component = model.instances[source].component
        if source in substituted or component in HOST_TICK_SOURCES:
            continue
        targets = ", ".join(sorted({c.to_instance for c in cycles if c.from_instance == source}))
        plan.warnings.append(
            f"{source} ({component}) cycles the flight rate groups (it drives {targets}) and is kept as it is. "
            "Unless that component produces ticks on a Linux host, the rate groups will never run: nothing "
            "scheduled executes and no telemetry is downlinked. Write a project surrogate for it that cycles "
            "from the WSTS tick (docs/surrogate-guide.md, 'A project timer'), and name it under 'substitute'."
        )
        plan.fidelity_notes.append(
            f"The flight rate groups are cycled by {source} ({component}), which is not simulated. If it depends on "
            "a hardware interrupt, the rate groups may never run in this simulation."
        )
    source = topology.time_source
    if source and source not in substituted and source in model.instances:
        component = model.components.get(model.instances[source].component)
        if component and framework is not None and framework not in component.file.parents:
            plan.warnings.append(
                f"time comes from {source} ({component.name}), a project component that is kept as it is. If it "
                "reads a hardware clock, time stamps will not advance: substitute it with a project surrogate."
            )
            plan.fidelity_notes.append(
                f"Time stamps come from {source} ({component.name}), which is not simulated and may depend on hardware."
            )


def make_plan(model: fpp.Model, manifest: Manifest, catalog: List[Surrogate], topology: fpp.Topology, roots: List[Path],
              framework: Optional[Path] = None) -> Plan:
    plan = Plan(topology)
    plan.sim = sims.collect_added(model, manifest, topology)
    by_surrogate = {s.name: s for s in catalog}
    replaces: Dict[str, Surrogate] = {}
    for surrogate in catalog:
        for component in surrogate.replaces:
            replaces.setdefault(component, surrogate)

    used_ports: Dict[str, Set[str]] = {}
    for connection in topology.connections:
        used_ports.setdefault(connection.from_instance, set()).add(connection.from_port)
        used_ports.setdefault(connection.to_instance, set()).add(connection.to_port)

    # Every name in the manifest is resolved once, to a qualified instance name. Instances
    # of imported subtopologies are in the topology under their own qualified names.
    def named(spec: str) -> str:
        found = resolve_instance(spec, topology.instances)
        if found is None:
            raise WstsError(f"manifest names instance '{spec}', which is not in topology {topology.name}")
        return found

    keep = {named(spec) for spec in manifest.keep}
    entry_instance = {id(entry): named(entry.instance) for entry in manifest.substitutions if entry.instance}

    # Links of a multi-element system end at byte stream driver instances of this topology
    link_ends: Dict[str, LinkEnd] = {}
    for end in manifest.links:
        try:
            found = resolve_instance(end.instance, topology.instances)
        except WstsError as error:
            raise WstsError(f"link '{end.link}': {error}") from None
        if found is None:
            raise WstsError(f"link '{end.link}' ends at instance '{end.instance}', which is not in topology {topology.name}")
        if found in link_ends:
            raise WstsError(f"instance {found} is an end of both link '{link_ends[found].link}' and link '{end.link}'")
        link_ends[found] = end

    for name in topology.instances:
        instance = model.instances[name]
        original = model.components.get(instance.component)
        if original is None:
            if name in link_ends:
                raise WstsError(f"link '{link_ends[name].link}' ends at {name}, whose component was not found in the FPP model")
            continue
        chosen: Optional[Surrogate] = None
        endpoint = instance.base_name
        options: Optional[str] = None
        reason = ""
        entry_ports: Optional[Dict[str, Optional[str]]] = None
        entry_attributes: Dict[str, str] = {}
        entry_no_endpoint = False
        entry_endpoint = False
        kept = name in keep

        for entry in manifest.substitutions:
            if entry_instance.get(id(entry)) == name or (entry.component == original.name and not kept):
                if entry.surrogate not in model.components:
                    raise WstsError(f"manifest substitutes {name} with unknown component '{entry.surrogate}'")
                chosen = by_surrogate.get(entry.surrogate) or Surrogate(name=entry.surrogate, source="manifest")
                endpoint = entry.endpoint or endpoint
                options = entry.options
                entry_ports = entry.ports
                entry_attributes = entry.attributes
                entry_no_endpoint = entry.no_endpoint
                entry_endpoint = bool(entry.endpoint)
                reason = "manifest"
                break
        if chosen is None and not kept and original.name in replaces:
            chosen = replaces[original.name]
            reason = "catalog"
        if name in link_ends:
            # The link takes the place of whatever the driver talked to, so the driver has
            # to be simulated: a link is itself a reason to substitute
            if kept:
                raise WstsError(f"link '{link_ends[name].link}' ends at {name}, which the manifest lists under 'keep'")
            if chosen is None:
                chosen = by_surrogate.get(LINK_SURROGATE) or Surrogate(name=LINK_SURROGATE, source="link")
                reason = "link"
        restriction = None if chosen else _restricted_from_host(original, roots)
        if chosen is None and restriction and not kept:
            # Nothing was named for a component that cannot build on the host: accept a
            # surrogate only if exactly one fits the ports the topology actually uses.
            fits = [s for s in catalog if s.name in model.components
                    and not _covers(model.components[s.name], original, used_ports.get(name, set()))
                    and used_ports.get(name)]
            if len(fits) == 1:
                chosen = fits[0]
                reason = "structural"
            else:
                candidates = ", ".join(s.name for s in fits) or "none"
                raise WstsError(
                    f"instance {name} ({original.name}) cannot build on Linux [{restriction}] and no surrogate "
                    f"is mapped to it. Add a 'substitute' entry to the manifest. Surrogates whose ports fit: {candidates}."
                )
        if chosen is None:
            if restriction:
                plan.kept_hardware.append(name)
                plan.warnings.append(f"{name} ({original.name}) is kept by the manifest although it is restricted: {restriction}")
            continue

        if chosen.name not in model.components:
            raise WstsError(f"surrogate component {chosen.name} (for {name}) was not found in the FPP model")
        surrogate = model.components[chosen.name]
        port_map = entry_ports if entry_ports is not None else chosen.ports_when_replacing(original.name)
        for flight_port, surrogate_port in port_map.items():
            if flight_port not in original.ports:
                raise WstsError(f"port mapping for {name}: {original.name} has no port '{flight_port}'")
            if surrogate_port is not None and surrogate_port not in surrogate.ports:
                raise WstsError(f"port mapping for {name}: {chosen.name} has no port '{surrogate_port}'")
        missing = _covers(surrogate, original, used_ports.get(name, set()), port_map)
        if missing:
            raise WstsError(
                f"{chosen.name} cannot replace {name} ({original.name}): connected ports it does not serve: "
                + "; ".join(missing)
                + ". If the surrogate's ports are named differently, map them with 'ports: {flightName: "
                "surrogateName}' in the manifest's 'substitute' entry or in the catalog (an empty value drops the "
                "flight connection)."
            )
        if options is None:
            options = chosen.options_when_replacing(original.name)
        # A queued or active surrogate needs a queue; the flight definition of a passive
        # driver has none
        attributes: Dict[str, str] = {}
        if surrogate.kind != "passive":
            allowed = ("queue size",) if surrogate.kind == "queued" else tuple(ATTRIBUTE_ORDER)
            attributes = {k: v for k, v in {**chosen.attributes, **entry_attributes}.items() if k in allowed}
            if not instance.has_queue and "queue size" not in attributes:
                attributes["queue size"] = str(DEFAULT_QUEUE_SIZE)
                plan.warnings.append(
                    f"{name}: {chosen.name} is a {surrogate.kind} component and the flight instance has no queue; "
                    f"queue size {DEFAULT_QUEUE_SIZE} is used. Set 'queue_size' in the 'substitute' entry or the catalog."
                )
        # A surrogate that talks to flight components over F Prime ports only has no endpoint.
        # An endpoint named for the instance in the manifest overrides a catalog's `endpoint: none`.
        bind = not (entry_no_endpoint or (not chosen.bind and not entry_endpoint))
        if not bind and name in link_ends:
            raise WstsError(
                f"link '{link_ends[name].link}' ends at {name}, which is substituted with 'endpoint: none': "
                f"{chosen.name} does not use the bus, so there is no endpoint for a link to end at"
            )
        plan.substitutions.append(PlannedSubstitution(
            instance, original, surrogate, chosen, endpoint if bind else "", options, reason, port_map, attributes, bind))

    # Base IDs for the WSTS instances: above everything the flight topology uses
    highest = 0
    # FPP checks the IDs of every instance it reads, used or not: stay clear of all that
    # the simulators' files define
    simulator_files = {item.instance.file for item in plan.sim.added}
    beside = [name for name, instance in model.instances.items()
              if instance.file in simulator_files and name not in plan.sim.names]
    for name in topology.instances + plan.sim.names + beside:
        instance = model.instances[name]
        highest = max(highest, instance.base_id, instance.max_id)
    base = manifest.control_base_id
    if base is None:
        base = ((highest // ID_BLOCK) + 1) * ID_BLOCK
        if manifest.id_window and manifest.control_enabled and not _in_window(base, manifest.id_window):
            # Flight IDs fill, or lie outside, the window: fall back to the top of the window
            base = ((manifest.id_window[1] + 1) // ID_BLOCK - 2) * ID_BLOCK
    elif base <= highest:
        for name in topology.instances:
            instance = model.instances[name]
            top = max(instance.base_id, instance.max_id)
            if not (base + 2 * ID_BLOCK <= instance.base_id or base > top):
                raise WstsError(f"control.base_id {base:#x} overlaps the ID range of {name}")
    plan.setup_base_id = base
    plan.control_base_id = base + ID_BLOCK
    if manifest.id_window:
        low, high = manifest.id_window
        if manifest.control_enabled:
            if not _in_window(base, manifest.id_window):
                raise WstsError(
                    f"the WSTS instances need IDs {base:#x}..{base + 2 * ID_BLOCK - 1:#x}, outside the element's "
                    f"'ids' window {low:#x}..{high:#x}. Set control.base_id in the element's manifest to a free "
                    "range inside the window."
                )
            for name in topology.instances:
                instance = model.instances[name]
                if base <= max(instance.base_id, instance.max_id) and instance.base_id < base + 2 * ID_BLOCK:
                    raise WstsError(
                        f"no room for the WSTS instances at the top of the 'ids' window {low:#x}..{high:#x}: "
                        f"{name} is there. Set control.base_id in the element's manifest."
                    )
        outside = [name for name in topology.instances if not low <= model.instances[name].base_id <= high]
        if outside:
            plan.warnings.append(
                f"instances with base IDs outside the element's 'ids' window {low:#x}..{high:#x}: " + ", ".join(outside)
            )

    if manifest.control_enabled and topology.explicit_pattern_targets:
        plan.warnings.append(
            "topology lists explicit targets for pattern connections ("
            + ", ".join(sorted(set(topology.explicit_pattern_targets)))
            + f"): add {CONTROL_INSTANCE} to them or its commands/events/telemetry will not be connected"
        )
    endpoints: Dict[str, str] = {}
    # What an endpoint would have been called, for surrogates that have none
    unbound = {sub.instance.base_name: sub for sub in plan.substitutions if not sub.bind}

    def no_endpoint_hint(endpoint: str) -> str:
        sub = unbound.get(endpoint)
        if sub is None:
            return ""
        return (f" {sub.instance.name} is substituted with 'endpoint: none': {sub.surrogate.name} does not use the "
                "bus, so nothing can be attached there.")

    for sub in plan.substitutions:
        if not sub.bind:
            continue
        if sub.endpoint in endpoints:
            raise WstsError(
                f"endpoint '{sub.endpoint}' is used by both {endpoints[sub.endpoint]} and {sub.instance.name}. "
                "Endpoints are named after the instance's base name unless the manifest says otherwise: give one of "
                f"them its own name with\n  substitute:\n    - instance: {sub.instance.name}\n"
                f"      with: {sub.surrogate.name}\n      endpoint: <name>"
            )
        endpoints[sub.endpoint] = sub.instance.name
    for spec in manifest.models:
        if spec.kind != "none" and spec.endpoint not in endpoints:
            raise WstsError(
                f"model '{spec.name}' attaches to endpoint '{spec.endpoint}', which no surrogate is bound to."
                f"{no_endpoint_hint(spec.endpoint)} Endpoints: {', '.join(sorted(endpoints)) or 'none'}"
            )
    for sub in plan.substitutions:
        end = link_ends.get(sub.instance.name)
        if end is None:
            continue
        if not {"send", "recv"} <= set(sub.surrogate.ports):
            raise WstsError(
                f"link '{end.link}' ends at {sub.instance.name}, but its surrogate {sub.surrogate.name} is not a "
                "byte stream driver (no send and recv ports)"
            )
        for spec in manifest.models:
            if spec.kind == "bytestream" and spec.endpoint == sub.endpoint:
                raise WstsError(
                    f"endpoint '{sub.endpoint}' has both the model '{spec.name}' and the link '{end.link}': "
                    "a byte stream endpoint takes one or the other"
                )
        plan.links.append(PlannedLink(end, sub.instance.name, sub.endpoint))
    _check_tick_sources(plan, model, framework)

    # ---- Simulator components and bus devices ----
    wsts_ids = [(prefix_of(topology) + SETUP_INSTANCE, plan.setup_base_id, plan.setup_base_id + ID_BLOCK - 1)]
    if manifest.control_enabled:
        wsts_ids.append((prefix_of(topology) + CONTROL_INSTANCE, plan.control_base_id, plan.control_base_id + ID_BLOCK - 1))
    sims.check_ids(plan.sim, model, topology, wsts_ids)
    component_of = {sub.instance.name: sub.surrogate.name for sub in plan.substitutions}
    renamed = {
        (sub.instance.name, surrogate_port): flight_port
        for sub in plan.substitutions for flight_port, surrogate_port in sub.port_map.items() if surrogate_port
    }
    sims.plan_connections(plan.sim, model, manifest, topology, component_of, renamed)
    sims.check_parameters(plan.sim, model, topology)
    sims.plan_health(plan.sim, model, topology)
    for device in manifest.devices:
        if device.endpoint not in endpoints and device.endpoint in unbound:
            raise WstsError(f"devices: {device.instance}: there is no endpoint '{device.endpoint}'."
                            f"{no_endpoint_hint(device.endpoint)}")
    sims.plan_devices(
        plan.sim, model, manifest, topology,
        {sub.endpoint: (sub.instance.name, {"send", "recv"} <= set(sub.surrogate.ports))
         for sub in plan.substitutions if sub.bind},
        {link.endpoint: link.end.link for link in plan.links},
    )
    sims.plan_clocks(plan.sim, model, manifest, topology)
    for sub in plan.substitutions:
        if not sub.bind:
            plan.fidelity_notes.append(
                f"{sub.instance.name} is replaced by {sub.surrogate.name}, which has no bus endpoint: it talks to "
                "the flight components over their ports directly. Fault injection, recording, replay and the bridge "
                "do not reach that link."
            )
    for device in plan.sim.devices:
        for link in plan.links:
            if link.instance == device.instance:
                raise WstsError(f"devices: {device.instance} is also an end of the system link '{link.end.link}'")
    return plan


def prefix_of(topology: fpp.Topology) -> str:
    return topology.module + "." if topology.module else ""


def _in_window(base: int, window: Tuple[int, int]) -> bool:
    """Whether the two ID blocks of the WSTS instances starting at `base` fit the window"""
    return window[0] <= base and base + 2 * ID_BLOCK - 1 <= window[1]


# ----------------------------------------------------------------------
# FPP patching
# ----------------------------------------------------------------------


def _mask(text: str) -> str:
    """Copy of `text` with comments and string contents blanked, same length"""
    out = list(text)
    i = 0
    while i < len(text):
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            end = len(text) if end < 0 else end + 3
        elif text[i] == '"':
            end = i + 1
            while end < len(text) and text[end] != '"' and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            end = min(len(text), end + 1)
        elif text[i] in "#@":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
        else:
            i += 1
            continue
        for j in range(i, end):
            if out[j] != "\n":
                out[j] = " "
        i = end
    return "".join(out)


def _offset(text: str, line: int, col: int) -> int:
    lines = text.split("\n")
    return sum(len(l) + 1 for l in lines[: line - 1]) + col - 1


def _matching_brace(masked: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise WstsError("unbalanced braces while patching topology FPP")


def patch_instance(text: str, sub: PlannedSubstitution) -> str:
    """Retype one instance definition to its surrogate"""
    instance = sub.instance
    at = _offset(text, instance.type_line, instance.type_col)
    if not text.startswith(instance.type_text, at):
        raise WstsError(f"{instance.file}:{instance.type_line}: expected '{instance.type_text}' in the definition of {instance.name}")
    text = text[:at] + sub.surrogate.name + text[at + len(instance.type_text):]
    if instance.has_queue and sub.surrogate.kind == "passive":
        # A passive surrogate takes no queue or task attributes. The definition runs from
        # its first line through every line continued with a backslash.
        start = _offset(text, instance.line, 1)
        end = start
        while True:
            newline = text.find("\n", end)
            if newline < 0:
                end = len(text)
                break
            if not text[end:newline].rstrip().endswith("\\"):
                end = newline
                break
            end = newline + 1
        text = text[:start] + ATTRIBUTE_CLAUSE.sub("", text[start:end]) + text[end:]
    elif sub.surrogate.kind != "passive" and (sub.attributes or (sub.surrogate.kind == "queued" and instance.has_queue)):
        start, end = _definition_span(text, instance)
        text = text[:start] + _set_attributes(text[start:end], sub) + text[end:]
    return text


def _definition_span(text: str, instance: fpp.Instance) -> Tuple[int, int]:
    """An instance definition up to its init specifiers: from its first line through every
    line continued with a backslash, stopping at an opening brace"""
    start = _offset(text, instance.line, 1)
    end = start
    while True:
        newline = text.find("\n", end)
        if newline < 0:
            end = len(text)
            break
        if not text[end:newline].rstrip().endswith("\\"):
            end = newline
            break
        end = newline + 1
    brace = _mask(text[start:end]).find("{")
    return start, (start + brace if brace >= 0 else end)


def _set_attributes(header: str, sub: PlannedSubstitution) -> str:
    """Give an instance definition the queue and task attributes its surrogate's kind takes"""
    allowed = ["queue size"] if sub.surrogate.kind == "queued" else ATTRIBUTE_ORDER
    masked = _mask(header)
    existing: Dict[str, str] = {}
    spans = []
    for match in ATTRIBUTE_CLAUSE.finditer(masked):
        clause = " ".join(match.group(1).split())
        existing[clause] = header[match.start():match.end()][len(match.group(1)):].strip()
        spans.append(match.span())
    final = {k: v for k, v in {**existing, **sub.attributes}.items() if k in allowed}
    for begin, finish in reversed(spans):
        header = header[:begin] + header[finish:]
    masked = _mask(header)
    base = re.search(r"\bbase\s+id\b", masked)
    if base is None:
        raise WstsError(f"{sub.instance.file}:{sub.instance.line}: cannot find 'base id' in the definition of {sub.instance.name}")
    stop = re.search(r"\\|\n|\{", masked[base.end():])
    at = base.end() + stop.start() if stop else len(header)
    at = len(masked[:at].rstrip())  # before a trailing comment, which the mask blanks
    clauses = "".join(f" {name} {final[name]}" for name in ATTRIBUTE_ORDER if name in final)
    return header[:at] + clauses + header[at:]


def patch_topology(text: str, topology: fpp.Topology, instance_names: List[str], tail: str = "") -> str:
    """Add instance specifiers at the top of the topology body, and `tail` at its end"""
    masked = _mask(text)
    opening = masked.find("{", _offset(text, topology.line, topology.col))
    if opening < 0:
        raise WstsError(f"{topology.file}: cannot find the body of topology {topology.name}")
    if tail:
        closing = _matching_brace(masked, opening)
        text = text[:closing].rstrip(" \t") + tail + text[closing:]
    addition = "\n\n    # WSTS instances (generated)\n" + "".join(f"    instance {name}\n" for name in instance_names)
    return text[: opening + 1] + addition + text[opening + 1:]


QUALIFIED = re.compile(r"[\w$]+(?:\s*\.\s*[\w$]+)*")
PORT_END = re.compile(r"[\w$]+(?:\s*\.\s*[\w$]+)*(?:\s*\[[^\]\n]*\])?")


def rename_port(text: str, line: int, col: int, new_name: str) -> str:
    """Replace the port name of the connection end (`instance.port`) written at line, col"""
    at = _offset(text, line, col)
    match = QUALIFIED.match(text, at)
    if not match or "." not in match.group(0):
        raise WstsError(f"cannot find a port reference at line {line}, column {col} while renaming a port to {new_name}")
    dot = match.group(0).rindex(".")
    return text[: at + dot + 1] + sims.fpp_name(new_name) + text[match.end():]


def remove_span(text: str, line: int, col: int, end_line: int, end_col: int, why: str) -> str:
    """Turn the text from line, col through the port reference at end_line, end_col into a
    comment, keeping the line count"""
    start = _offset(text, line, col)
    last = PORT_END.match(text, _offset(text, end_line, end_col))
    if not last:
        raise WstsError(f"cannot find the end of the connection at line {end_line}, column {end_col}")
    removed = text[start:last.end()]
    return text[:start] + f"# {why}: " + " ".join(removed.split()) + "\n" * removed.count("\n") + text[last.end():]


def remove_line(text: str, line: int, why: str) -> str:
    start = _offset(text, line, 1)
    indent = len(text[start:]) - len(text[start:].lstrip(" \t"))
    return text[: start + indent] + f"# {why}: " + text[start + indent:]


def pin_pattern_targets(text: str, pattern: fpp.Pattern, targets: List[str], topology: Optional[fpp.Topology] = None) -> str:
    """Give a pattern specifier an explicit list of target instances"""
    at = _offset(text, pattern.source_line, pattern.source_col)
    match = QUALIFIED.match(text, at)
    if not match:
        raise WstsError(f"{pattern.file}:{pattern.line}: cannot read the {pattern.kind.lower()} connections specifier")
    listing = "".join(f"      {sims.fpp_name(name, topology)}\n" for name in targets)
    return (text[:match.end()] + " {\n      # Flight targets, listed so that added simulator instances do not "
            "change ping port numbers (generated)\n" + listing + "    }" + text[match.end():])


def patch_packets(text: str, line: int, col: int, channels: List[str]) -> str:
    """Omit the WSTS channels from a telemetry packet set"""
    masked = _mask(text)
    opening = masked.find("{", _offset(text, line, col))
    closing = _matching_brace(masked, opening)
    listing = "".join(f"    {channel}\n" for channel in channels)
    tail = re.match(r"\s*omit\s*\{", masked[closing + 1:])
    if tail:
        insert_at = closing + 1 + tail.end()
        return text[:insert_at] + "\n    # WSTS channels (generated)\n" + listing + text[insert_at:]
    return text[: closing + 1] + " omit {\n    # WSTS channels (generated)\n" + listing + "  }" + text[closing + 1:]


def port_edits(plan: Plan, model: fpp.Model, topology: fpp.Topology) -> List[Tuple[Path, int, int, object, str]]:
    """Edits that apply the substitutions' port mappings to the flight connections: a port
    the surrogate names differently is renamed where the connection is written (or where a
    subtopology exports it), and a connection on a port the surrogate does not have is
    commented out. Adds notes and warnings to the plan."""
    mapped = {sub.instance.name: sub for sub in plan.substitutions if sub.port_map}
    if not mapped:
        return []
    found: List[Tuple[Path, int, int, object, str]] = []
    pattern_locs = {(p.file, p.line, p.col) for p in topology.patterns}
    explicit = [c for c in topology.connections if c.from_loc not in pattern_locs and c.from_loc and c.to_loc]
    removed: Set[int] = set()
    exported_done: Set[Tuple[str, str]] = set()

    def exported_port(instance: str, port: str, at: Tuple[Path, int, int], new_name: Optional[str], why: str) -> None:
        """The end is written as a subtopology's exported port: change its definition"""
        for other in model.topologies.values():
            for name, (target_instance, target_port) in other.exported.items():
                if (target_instance, target_port) != (instance, port) or (other.name, name) in exported_done:
                    continue
                if name not in other.exported_loc:
                    raise WstsError(f"cannot find where {other.name} exports port {name}")
                exported_done.add((other.name, name))
                file, line, col = other.exported_loc[name]
                what = f"port {name} of {other.name}"
                if new_name is None:
                    found.append((file, line, col, lambda text, line=line: remove_line(text, line, why), what))
                else:
                    def change(text: str, line=line, col=col, new_name=new_name) -> str:
                        equals = text.index("=", _offset(text, line, col))
                        target = QUALIFIED.search(text, equals)
                        target_line = text.count("\n", 0, target.start()) + 1
                        target_col = target.start() - (text.rfind("\n", 0, target.start()) + 1) + 1
                        return rename_port(text, target_line, target_col, new_name)
                    found.append((file, line, col, change, what))

    for index, connection in enumerate(explicit):
        ends = (
            (connection.from_instance, connection.from_port, connection.from_loc, connection.from_exported),
            (connection.to_instance, connection.to_port, connection.to_loc, connection.to_exported),
        )
        touched = [(i, p, loc, exp) for i, p, loc, exp in ends if i in mapped and p in mapped[i].port_map]
        if not touched:
            continue
        text = f"{connection.from_instance}.{connection.from_port} -> {connection.to_instance}.{connection.to_port}"
        dropped = [(i, p) for i, p, _, _ in touched if mapped[i].port_map[p] is None]
        if dropped:
            instance, port = dropped[0]
            why = f"removed by fprime-wsts ({mapped[instance].surrogate.name} has no counterpart of {port})"
            if connection.from_loc[0] != connection.to_loc[0]:
                raise WstsError(f"connection {text} is written across two files; fprime-wsts cannot remove it")
            file, line, col = connection.from_loc
            end_line, end_col = connection.to_loc[1:]
            found.append((file, line, col,
                          lambda text, line=line, col=col, end_line=end_line, end_col=end_col, why=why:
                          remove_span(text, line, col, end_line, end_col, why), f"connection {text}"))
            removed.add(index)
            for instance, port, loc, exported in touched:
                if exported and mapped[instance].port_map[port] is None:
                    exported_port(instance, port, loc, None, why)
            plan.fidelity_notes.append(
                f"The flight connection {text} is not made: {mapped[instance].surrogate.name}, which replaces "
                f"{instance}, has no counterpart of port {port}."
            )
            continue
        for instance, port, loc, exported in touched:
            new_name = mapped[instance].port_map[port]
            if new_name == port:
                continue
            if exported:
                exported_port(instance, port, loc, new_name, "")
            else:
                file, line, col = loc
                found.append((file, line, col,
                              lambda text, line=line, col=col, new_name=new_name: rename_port(text, line, col, new_name),
                              f"connection {text}"))

    # A flight instance all of whose connections were removed does nothing any more
    for name in topology.instances:
        if name in mapped:
            continue
        own = [i for i, c in enumerate(explicit) if name in (c.from_instance, c.to_instance)]
        if own and all(i in removed for i in own):
            plan.warnings.append(
                f"{name} ({model.instances[name].component}) has no connections left: all of them went to ports "
                "that the surrogates do not have. It is still built and initialised. If it only served the replaced "
                "driver, substitute it as well, or accept it as dead weight in the simulation."
            )
    return found


# ----------------------------------------------------------------------
# CMake reading
# ----------------------------------------------------------------------

def read_registration(cmake_file: Path, function: str) -> Dict[str, List[str]]:
    """Arguments of the first register_fprime_<function>(...) call, grouped by directive"""
    calls = fpp.read_calls(cmake_file, function)
    return calls[0] if calls else {}


def module_dir(fpp_file: Path, root: Path) -> Optional[Path]:
    """Nearest directory at or above `fpp_file` that registers an F Prime module"""
    directory = fpp_file.parent
    while root == directory or root in directory.parents:
        cmake = directory / "CMakeLists.txt"
        if cmake.is_file() and "register_fprime_" in cmake.read_text(errors="replace"):
            return directory
        if directory == root:
            break
        directory = directory.parent
    return None


def module_name(directory: Path, roots: List[Path]) -> str:
    """F Prime's build target name for a module directory"""
    best = None
    for root in roots:
        if root == directory or root in directory.parents:
            relative = directory.relative_to(root).as_posix()
            if best is None or len(relative) < len(best):
                best = relative
    return (best or directory.name).replace("/", "_")


# ----------------------------------------------------------------------
# Modules replaced by a patched copy
# ----------------------------------------------------------------------
#
# An instance defined outside the deployment's Top directory (in a subtopology of a
# library, of the framework or of the project) is retyped in a copy of its FPP file. The
# copy lives in the overlay, in a module directory that takes the original's place in the
# build: the overlay's CMakeLists.txt redirects add_fprime_subdirectory() for that one
# directory. See "Instances inside subtopologies" in docs/design.md.


@dataclass
class ReplacedModule:
    original: Path  # module directory in the framework, a library or the flight project
    root: Path  # the build root (framework, library or project) it belongs to
    relative: str  # path of the module directory below its root, and below the overlay
    files: List[Path] = field(default_factory=list)  # original FPP files that are patched


CMAKE_PATH_CALL = re.compile(r"\b(register_fprime_\w+|add_fprime_subdirectory|add_subdirectory|include)(\s*)\(([^)]*)\)", re.S)


def render_replaced_cmake(module: ReplacedModule, original_reference: str, dropped_depends: Set[str]) -> str:
    """CMakeLists.txt of the overlay's copy of a module: the original's, with paths
    pointing back at the original directory except for the patched FPP files"""
    text = (module.original / "CMakeLists.txt").read_text()
    text = re.sub(r"(?m)^[ \t]*#.*\n", "", text)  # comments would describe the original
    text = re.sub(r"[ \t]+#[^\n\"]*$", "", text, flags=re.M)
    # The copy exists to be built on the host
    text = re.sub(r"(?m)^[ \t]*restrict_platforms\([^)]*\)[ \t]*\n", "", text)
    for variable in ("${CMAKE_CURRENT_LIST_DIR}", "${CMAKE_CURRENT_SOURCE_DIR}"):
        text = text.replace(variable, "${WSTS_ORIGINAL_DIR}")
    patched = {f.name for f in module.files}
    seen: Set[str] = set()

    def fix_token(match: re.Match) -> str:
        token = match.group(0)
        bare = token.strip('"')
        quote = '"' if token.startswith('"') else ""
        if bare in dropped_depends:
            return "\0"
        prefix = "${WSTS_ORIGINAL_DIR}/"
        relative = bare[len(prefix):] if bare.startswith(prefix) else bare
        if "${" in relative or os.path.isabs(relative) or relative in fpp.CMAKE_CALL_DIRECTIVES:
            return token
        if relative in patched:
            seen.add(relative)
            return f'"${{CMAKE_CURRENT_LIST_DIR}}/{relative}"'
        if not bare.startswith(prefix) and (module.original / relative).exists():
            # A path written relative to the module directory
            return f'{quote or chr(34)}{prefix}{relative}{quote or chr(34)}'
        return token

    def fix_call(match: re.Match) -> str:
        body = re.sub(r'"[^"]*"|[^\s()]+', fix_token, match.group(3))
        # A dependency that was dropped takes its line with it
        body = re.sub(r"\n[ \t]*\0[ \t]*(?=\n)", "", body).replace("\0", "")
        return f"{match.group(1)}{match.group(2)}({body})"

    text = CMAKE_PATH_CALL.sub(fix_call, text)
    missing = patched - seen
    if missing:
        raise WstsError(
            f"{module.original / 'CMakeLists.txt'} does not list {', '.join(sorted(missing))} in a "
            "register_fprime_*() call that fprime-wsts can read, so the patched copy cannot take its place"
        )
    text = re.sub(r"\n[ \t]*\n([ \t]*\n)+", "\n\n", text)
    return "\n".join([
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "#",
        f"# Takes the place of {module.relative} in the simulated build: the FPP listed with",
        "# CMAKE_CURRENT_LIST_DIR is a patched copy, everything else is the original's.",
        f'set(WSTS_ORIGINAL_DIR "${{CMAKE_CURRENT_LIST_DIR}}/{original_reference}")',
        "",
        text.strip("\n"),
        "",
    ])


DEFERRED_SETS = """
# Modules of the simulation that override configuration. Their library adds them while it is
# read, which is before the flight project's modules: they are held back and added below.
set(WSTS_DEFERRED_MODULES
{modules}
)"""

DEFERRED_CHECK = """
    if (NOT WSTS_ADDING_DEFERRED)
        foreach (WSTS_MODULE IN LISTS WSTS_DEFERRED_MODULES)
            get_filename_component(WSTS_MODULE "${WSTS_MODULE}" REALPATH)
            if (WSTS_MODULE STREQUAL WSTS_REQUESTED)
                return()
            endif()
        endforeach()
    endif()"""

EXCLUDED_SETS = """
# From `cmake.exclude_subdirectories` in the manifest: modules that a library, the framework or
# another module adds, and that are left out of the simulated build.
set(WSTS_EXCLUDED_MODULES
{modules}
)"""

EXCLUDED_CHECK = """
    foreach (WSTS_MODULE IN LISTS WSTS_EXCLUDED_MODULES)
        get_filename_component(WSTS_MODULE "${WSTS_MODULE}" REALPATH)
        if (WSTS_MODULE STREQUAL WSTS_REQUESTED)
            message(STATUS "[WSTS] ${WSTS_REQUESTED} is left out (cmake.exclude_subdirectories)")
            return()
        endif()
    endforeach()"""

STUB_SETS = """
# From `cmake.stub_variants` in the manifest: modules that are read with BUILD_TESTING set, so that
# they take the variant they have for unit tests (a stub in place of code that needs the target's
# SDK). Nothing else of a unit-test build comes with it: no unit-test targets, no sanitizers, no
# coverage flags, and BUILD_UT is not defined.
set(WSTS_STUB_VARIANT_MODULES
{modules}
)"""

STUB_CHECK = """
    # BUILD_TESTING is a variable of this call: the listed module's CMakeLists.txt sees it, a module
    # which that one adds gets the build's own value back, and it is gone when the call returns
    if (DEFINED WSTS_BUILD_TESTING_OUTSIDE)
        set(BUILD_TESTING "${WSTS_BUILD_TESTING_OUTSIDE}")
        set(__FPRIME_NO_UT_GEN__ "${WSTS_NO_UT_GEN_OUTSIDE}")
        set(FPRIME_TESTING_REQUIRED_COMPILE_FLAGS "${WSTS_TESTING_COMPILE_FLAGS_OUTSIDE}")
        set(FPRIME_TESTING_REQUIRED_LINK_FLAGS "${WSTS_TESTING_LINK_FLAGS_OUTSIDE}")
        unset(WSTS_BUILD_TESTING_OUTSIDE)
    endif()
    foreach (WSTS_MODULE IN LISTS WSTS_STUB_VARIANT_MODULES)
        get_filename_component(WSTS_MODULE "${WSTS_MODULE}" REALPATH)
        if (WSTS_MODULE STREQUAL WSTS_REQUESTED)
            set_property(GLOBAL APPEND PROPERTY WSTS_STUB_VARIANTS_READ "${WSTS_REQUESTED}")
            if (NOT BUILD_TESTING)
                message(STATUS "[WSTS] ${WSTS_REQUESTED} is read with BUILD_TESTING set (cmake.stub_variants)")
                set(WSTS_BUILD_TESTING_OUTSIDE "${BUILD_TESTING}")
                set(WSTS_NO_UT_GEN_OUTSIDE "${__FPRIME_NO_UT_GEN__}")
                set(WSTS_TESTING_COMPILE_FLAGS_OUTSIDE "${FPRIME_TESTING_REQUIRED_COMPILE_FLAGS}")
                set(WSTS_TESTING_LINK_FLAGS_OUTSIDE "${FPRIME_TESTING_REQUIRED_LINK_FLAGS}")
                set(BUILD_TESTING ON)
                # register_fprime_ut() returns at once with this set: no unit-test target
                set(__FPRIME_NO_UT_GEN__ ON)
                # What F Prime adds to a module's own target when BUILD_TESTING is set (coverage)
                set(FPRIME_TESTING_REQUIRED_COMPILE_FLAGS "")
                set(FPRIME_TESTING_REQUIRED_LINK_FLAGS "")
            endif()
        endif()
    endforeach()"""

STUB_VERIFY = """
# Every module listed under `cmake.stub_variants` has to have been added by now
get_property(WSTS_STUB_VARIANTS_READ GLOBAL PROPERTY WSTS_STUB_VARIANTS_READ)
foreach (WSTS_MODULE IN LISTS WSTS_STUB_VARIANT_MODULES)
    get_filename_component(WSTS_MODULE "${WSTS_MODULE}" REALPATH)
    if (NOT WSTS_MODULE IN_LIST WSTS_STUB_VARIANTS_READ)
        message(FATAL_ERROR "[WSTS] cmake.stub_variants in the manifest lists ${WSTS_MODULE}, and nothing in this "
                            "build adds that directory with add_fprime_subdirectory(): its stub variant is not "
                            "selected. Remove the entry, or list the directory that is added.")
    endif()
endforeach()"""

# A backstop for what the generator could not read from the flight project's CMake files: it
# leaves a module that another module adds out of its own list, where it can see that.
DUPLICATE_CHECK = """
    # A directory is added once: a module that another module adds itself is not added again
    get_property(WSTS_ADDED GLOBAL PROPERTY WSTS_ADDED_DIRECTORIES)
    if (WSTS_REQUESTED IN_LIST WSTS_ADDED)
        message(STATUS "[WSTS] ${WSTS_REQUESTED} is already part of the build: not added again")
        return()
    endif()
    set_property(GLOBAL APPEND PROPERTY WSTS_ADDED_DIRECTORIES "${WSTS_REQUESTED}")"""

REDIRECT_TEMPLATE = """
# Modules whose FPP is patched: the copy in this project is built in place of the original.
# The framework and the libraries add their own modules, so the redirection has to be in
# place before they are read.
set(WSTS_REPLACED_MODULES
{originals}
)
set(WSTS_REPLACEMENTS
{copies}
){deferred_sets}{excluded_sets}{stub_sets}
if (COMMAND _add_fprime_subdirectory)
    message(FATAL_ERROR "add_fprime_subdirectory() is already overridden: fprime-wsts cannot redirect modules")
endif()
function(add_fprime_subdirectory FP_SOURCE_DIR)
    get_filename_component(WSTS_REQUESTED "${{FP_SOURCE_DIR}}" REALPATH){deferred_check}{excluded_check}{duplicate_check}{stub_check}
    get_filename_component(WSTS_HERE "${{CMAKE_CURRENT_SOURCE_DIR}}" REALPATH)
    set(WSTS_SOURCE "")
    set(WSTS_INDEX 0)
    foreach (WSTS_MODULE IN LISTS WSTS_REPLACED_MODULES)
        list(GET WSTS_REPLACEMENTS ${{WSTS_INDEX}} WSTS_REPLACEMENT)
        get_filename_component(WSTS_MODULE "${{WSTS_MODULE}}" REALPATH)
        get_filename_component(WSTS_REPLACEMENT "${{WSTS_REPLACEMENT}}" REALPATH)
        if (WSTS_MODULE STREQUAL WSTS_REQUESTED)
            set(WSTS_SOURCE "${{WSTS_REPLACEMENT}}")
        elseif (WSTS_REPLACEMENT STREQUAL WSTS_HERE)
            # A module the original adds from below itself, now added from the copy
            set(WSTS_SOURCE "${{WSTS_REQUESTED}}")
        endif()
        math(EXPR WSTS_INDEX "${{WSTS_INDEX}} + 1")
    endforeach()
    if (WSTS_SOURCE STREQUAL "")
        _add_fprime_subdirectory(${{ARGV}})
        return()
    endif()
    # Same module name and same place in the build cache as in the flight build
    get_module_name("${{WSTS_REQUESTED}}")
    set(FPRIME_CURRENT_MODULE "${{MODULE_NAME}}")
    get_nearest_build_root("${{WSTS_REQUESTED}}")
    file(RELATIVE_PATH WSTS_BINARY_DIR "${{FPRIME_CLOSEST_BUILD_ROOT}}" "${{WSTS_REQUESTED}}")
    get_filename_component(WSTS_FRAMEWORK "${{FPRIME_FRAMEWORK_PATH}}" REALPATH)
    if (FPRIME_CLOSEST_BUILD_ROOT STREQUAL WSTS_FRAMEWORK)
        set(WSTS_BINARY_DIR "F-Prime/${{WSTS_BINARY_DIR}}")
    endif()
    if (NOT WSTS_SOURCE STREQUAL WSTS_REQUESTED)
        message(STATUS "[WSTS] ${{MODULE_NAME}} is built from ${{WSTS_SOURCE}}")
    endif()
    fprime_util_metadata_add_subdirectory("${{WSTS_SOURCE}}" "${{CMAKE_BINARY_DIR}}/${{WSTS_BINARY_DIR}}")
    add_subdirectory("${{WSTS_SOURCE}}" "${{CMAKE_BINARY_DIR}}/${{WSTS_BINARY_DIR}}")
endfunction()
fprime_setup_included_code()
"""


# ----------------------------------------------------------------------
# Emission
# ----------------------------------------------------------------------


def _cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _cpp_instance(name: str) -> str:
    return "::" + name.replace(".", "::")


def _rel(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start)).as_posix()


def render_instances(plan: Plan, manifest: Manifest) -> str:
    module = plan.topology.module
    indent = "  " if module else ""
    lines = ["# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.", ""]
    if module:
        lines.append(f"module {module} {{")
        lines.append("")
    lines += [
        f"{indent}@ Anchor for generated simulation set-up code",
        f"{indent}instance {SETUP_INSTANCE}: {SETUP_COMPONENT} base id {plan.setup_base_id:#010x} {{",
        f'{indent}  phase Fpp.ToCpp.Phases.configComponents """',
        f"{indent}  WstsGenerated::setup();",
        f'{indent}  """',
        f'{indent}  phase Fpp.ToCpp.Phases.tearDownComponents """',
        f"{indent}  WstsGenerated::teardown();",
        f'{indent}  """',
        f"{indent}}}",
        "",
    ]
    if manifest.control_enabled:
        lines += [
            f"{indent}@ WSTS commands, events and telemetry",
            f"{indent}instance {CONTROL_INSTANCE}: {CONTROL_COMPONENT} base id {plan.control_base_id:#010x} \\",
            f"{indent}  queue size 30 \\",
            f"{indent}  stack size 64 * 1024 \\",
            f"{indent}  priority 20",
            "",
        ]
    if module:
        lines.append("}")
    return "\n".join(lines) + "\n"


def alias_declaration(sub: PlannedSubstitution) -> str:
    """The C++ that gives the surrogate the flight component's class name. The one place
    this text comes from: WstsCompat.hpp and the stand-in headers both use it."""
    namespace, _, cls = sub.original.name.rpartition(".")
    alias = f"using {cls} = {sub.catalog.cpp_name};"
    return f"namespace {namespace.replace('.', '::')} {{ {alias} }}" if namespace else alias


#: Where the overlay keeps headers that stand in for those of replaced flight drivers. It is
#: the first include directory of the simulated build.
STANDIN_DIR = "wsts-include"
#: Defined for a flight driver's own module where that module is still part of the build
FLIGHT_DRIVER_MACRO = "WSTS_FLIGHT_DRIVER_BUILD"


def driver_header_names(component: fpp.Component) -> List[str]:
    """File names of the hand-written headers that declare a component's class: `<Name>.hpp`
    by convention, and any other header next to the FPP file that includes
    `<Name>ComponentAc.hpp` (`<Name>ComponentImpl.hpp` in older code)"""
    cls = component.name.rpartition(".")[2]
    directory = component.file.parent
    autocoded = re.compile(r'#\s*include\s*[<"][^">\n]*\b' + re.escape(cls) + r'ComponentAc\.hpp[">]')
    names = [f"{cls}.hpp"] if (directory / f"{cls}.hpp").is_file() else []
    for header in sorted(directory.iterdir()) if directory.is_dir() else []:
        if header.suffix in (".hpp", ".h", ".hh") and header.name not in names and header.is_file() \
                and autocoded.search(header.read_text(errors="replace")):
            names.append(header.name)
    return names or [f"{cls}.hpp"]


def _guard(text: str) -> str:
    return re.sub(r"\W", "_", text).upper()


def render_driver_header(include_path: str, sub: PlannedSubstitution, surrogate_header: str) -> str:
    """A header at a replaced flight driver's include path: flight code that includes the
    driver's header gets the surrogate under the driver's name"""
    lines = [
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "//",
        f"// Stands in for the header of {sub.original.name}, which {sub.surrogate.name} replaces in the",
        "// simulated build. Flight code that includes the driver's header compiles against the",
        "// surrogate under the driver's name.",
        f"#ifndef WSTS_STANDIN_{_guard(include_path)}",
        f"#define WSTS_STANDIN_{_guard(include_path)}",
        f"#ifdef {FLIGHT_DRIVER_MACRO}",
        "// The flight driver's own module, where it is still built: the real header",
        f"#include_next <{include_path}>",
        "#else",
        "",
        f"#include <{surrogate_header}>",
        "",
        alias_declaration(sub),
    ]
    if sub.catalog.compat:
        # WstsCompat.hpp declares the same for the topology module, where it is included first
        macro = f"WSTS_COMPAT_{_guard(sub.catalog.name)}"
        lines += [
            "",
            f"#if !defined(WSTS_GENERATED_COMPAT_HPP) && !defined({macro})",
            f"#define {macro}",
            sub.catalog.compat.rstrip(),
            "#endif",
        ]
    lines += ["", f"#endif  // {FLIGHT_DRIVER_MACRO}", "#endif"]
    return "\n".join(lines) + "\n"


def render_driver_autocoded_header(include_path: str, sub: PlannedSubstitution, standin: str) -> str:
    """The same for `<Name>ComponentAc.hpp`, which the build no longer generates"""
    return "\n".join([
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "//",
        f"// {sub.original.name} is replaced by {sub.surrogate.name}: its autocoded header is not generated in",
        f"// the simulated build. The class name {sub.original.name.rpartition('.')[2]} resolves to the surrogate; the autocoded base class",
        "// has no counterpart under the flight name.",
        f"#ifndef WSTS_STANDIN_{_guard(include_path)}",
        f"#define WSTS_STANDIN_{_guard(include_path)}",
        f"#ifdef {FLIGHT_DRIVER_MACRO}",
        f"#include_next <{include_path}>",
        "#else",
        f"#include <{standin}>",
        "#endif",
        "#endif",
    ]) + "\n"


def render_compat(plan: Plan, model: fpp.Model, manifest: Manifest, header_of) -> str:
    # An instance that is not substituted keeps its real class, so no alias may shadow it
    kept_components = {
        model.instances[name].component
        for name in plan.topology.instances
        if name not in {s.instance.name for s in plan.substitutions}
    }
    lines = [
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "//",
        "// Force-included into the topology module. It lets topology code written against the",
        "// flight drivers compile against their surrogates, and declares the generated set-up",
        "// entry points used by the topology phase code.",
        "#ifndef WSTS_GENERATED_COMPAT_HPP",
        "#define WSTS_GENERATED_COMPAT_HPP",
        "#ifdef __cplusplus",
        "",
    ]
    headers = sorted({header_of(sub.surrogate) for sub in plan.substitutions})
    lines += [f"#include <{header}>" for header in headers]
    if manifest.simulators.headers:
        lines += ["", "// For the phase code of the simulator instances (`simulators.headers` in the manifest)"]
        lines += [f"#include <{header}>" for header in manifest.simulators.headers]
    lines.append("")
    aliased: Set[str] = set()
    for sub in plan.substitutions:
        original = sub.original.name
        if original in aliased:
            continue
        aliased.add(original)
        if original in kept_components:
            lines.append(f"// {original}: no alias, another instance still uses the flight component")
            continue
        lines.append(alias_declaration(sub))
    lines.append("")
    seen_compat: Set[str] = set()
    for sub in plan.substitutions:
        if sub.catalog.compat and sub.catalog.name not in seen_compat:
            seen_compat.add(sub.catalog.name)
            lines.append(sub.catalog.compat.rstrip())
            lines.append("")
    lines += [
        "namespace WstsGenerated {",
        "//! Bind surrogates to bus endpoints, attach models and start the WSTS runtime",
        "void setup();",
        "void teardown();",
        "}  // namespace WstsGenerated",
        "",
        "#endif  // __cplusplus",
        "#endif",
    ]
    return "\n".join(lines) + "\n"


ATTACH = {
    "bytestream": "Wsts::Bus::get().attachByteStream({endpoint}, &{var});",
    "i2c": "Wsts::Bus::get().attachI2c({endpoint}, {address:#x}, &{var});",
    "spi": "Wsts::Bus::get().attachSpi({endpoint}, &{var});",
    "gpio": "Wsts::Bus::get().attachGpio({endpoint}, &{var});",
}


def render_setup(plan: Plan, manifest: Manifest, topology_header: str) -> str:
    lines = [
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "#include <cstdio>",
        "",
        "#include <Wsts/Core/Bus.hpp>",
        "#include <Wsts/Core/Control.hpp>",
        "#include <Wsts/Core/Runtime.hpp>",
    ]
    if plan.links:
        lines.append("#include <Wsts/Core/Link.hpp>")
    for header in sorted({m.header for m in manifest.models}):
        lines.append(f"#include <{header}>")
    lines += [f"#include <{topology_header}>", "", "namespace {", ""]
    for spec in manifest.models:
        lines.append(f"{spec.cls} model_{_identifier(spec.name)};")
    for link in plan.links:
        lines.append(f"Wsts::LinkByteStream link_{_identifier(link.end.link)};")
    lines += [
        "",
        "void configure(const char* model, bool accepted, const char* key) {",
        "    if (!accepted) {",
        '        std::fprintf(stderr, "[WSTS] model %s rejected parameter %s\\n", model, key);',
        "    }",
        "}",
        "",
        "}  // namespace",
        "",
        "namespace WstsGenerated {",
        "",
        "void setup() {",
        "    // Surrogates: one bus endpoint each",
    ]
    for sub in plan.substitutions:
        if not sub.bind:
            lines.append(f"    // {_cpp_instance(sub.instance.name)}: {sub.surrogate.name} does not use the bus")
            continue
        lines.append(
            f"    {_cpp_instance(sub.instance.name)}.wstsBind({_cpp_string(sub.endpoint)}, {_cpp_string(sub.options)});"
        )
    if plan.sim.devices:
        lines += ["", "    // Simulator components: each adapter is the device on its endpoint"]
    for device in plan.sim.devices:
        lines.append(
            f"    {_cpp_instance(device.instance)}.wstsServe({_cpp_string(device.endpoint)}, {_cpp_string(device.spec.options)});"
        )
    if plan.sim.clocks:
        lines += ["", "    // Cycle sources of the simulation: they follow the simulation's ticks from the first one on"]
    for clock in plan.sim.clocks:
        lines.append(f"    {_cpp_instance(clock.instance)}.wstsClock({clock.hz!r}, {_cpp_string(clock.spec.options)});")
    if manifest.models:
        lines += ["", "    // Device models"]
    for spec in manifest.models:
        var = f"model_{_identifier(spec.name)}"
        name = _cpp_string(spec.name)
        # Every model is told where it lives; models that do not care ignore it
        lines.append(f"    (void)Wsts::configureModel(&{var}, \"endpoint\", {_cpp_string(spec.endpoint)});")
        for key, value in spec.params.items():
            lines.append(
                f"    configure({name}, Wsts::configureModel(&{var}, {_cpp_string(key)}, {_cpp_string(value)}), {_cpp_string(key)});"
            )
        if spec.kind in ATTACH:
            lines.append("    " + ATTACH[spec.kind].format(endpoint=_cpp_string(spec.endpoint), address=spec.address, var=var))
        lines.append(f"    Wsts::registerModel({name}, &{var});")
    if plan.links:
        lines += ["", "    // Links to the other elements of the system"]
    for link in plan.links:
        var = f"link_{_identifier(link.end.link)}"
        name = _cpp_string(f"link {link.end.link}")
        for key, value in (("endpoint", link.endpoint), ("link", link.end.link), ("role", link.end.role)):
            lines.append(
                f"    configure({name}, Wsts::configureModel(&{var}, {_cpp_string(key)}, {_cpp_string(value)}), {_cpp_string(key)});"
            )
        lines.append(f"    Wsts::Bus::get().attachByteStream({_cpp_string(link.endpoint)}, &{var});")
    lines.append("")
    if manifest.control_enabled:
        control = _cpp_instance((plan.topology.module + "." if plan.topology.module else "") + CONTROL_INSTANCE)
        lines.append(f'    {control}.wstsBind("", "");')
    lines += [
        "    Wsts::RuntimeConfig config;",
        f"    config.bridge = {_cpp_string(manifest.bridge)};",
        f"    config.internalTickHz = {manifest.tick_hz};",
        "    Wsts::Runtime::start(config);",
        *[f"    link_{_identifier(link.end.link)}.start();" for link in plan.links],
        "}",
        "",
        "void teardown() {",
        *[f"    link_{_identifier(link.end.link)}.stop();" for link in plan.links],
        "    Wsts::Runtime::stop();",
        "}",
        "",
        "}  // namespace WstsGenerated",
    ]
    return "\n".join(lines) + "\n"


def _identifier(name: str) -> str:
    return re.sub(r"\W", "_", name)


MAIN_TEMPLATE = """// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.
//
// Linux entry point for a deployment whose flight entry point is called by an RTOS
// start-up script rather than by main().
#include <getopt.h>
#include <signal.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>

#include <Os/Os.hpp>

{declarations}

namespace {{
volatile sig_atomic_t s_stop = 0;
void onSignal(int) {{
    s_stop = 1;
}}
}}  // namespace

int main(int argc, char* argv[]) {{
    const char* hostname = nullptr;
    int port = 0;
    int option = 0;
    while ((option = getopt(argc, argv, "ha:p:")) != -1) {{
        switch (option) {{
            case 'a':
                hostname = optarg;
                break;
            case 'p':
                port = std::atoi(optarg);
                break;
            default:
                std::printf("Usage: %s [-a hostname] [-p port]\\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }}
    }}
    (void)hostname;
    (void)port;
    Os::init();
    signal(SIGINT, onSignal);
    signal(SIGTERM, onSignal);

    {start};
    while (!s_stop) {{
        (void)usleep(100000);
    }}
{stop}
    return 0;
}}
"""


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


def build_report(plan: Plan, model: fpp.Model, manifest: Manifest, libraries: Dict[str, List[str]],
                 modules: List[str], settings: fpp.ProjectSettings, reused: Dict[str, List[str]]) -> dict:
    substitutions = []
    removed = {"commands": [], "events": [], "channels": [], "params": []}
    for sub in plan.substitutions:
        lost = {
            kind: sorted(set(getattr(sub.original, kind)) - set(getattr(sub.surrogate, kind)))
            for kind in ("commands", "events", "channels", "params")
        }
        for kind, names in lost.items():
            removed[kind] += [f"{sub.instance.name}.{n}" for n in names]
        substitutions.append({
            "instance": sub.instance.name,
            "flight_component": sub.original.name,
            "surrogate": sub.surrogate.name,
            "seam": "S1 topology",
            "chosen_by": sub.reason,
            "endpoint": sub.endpoint if sub.bind else None,
            "options": sub.options,
            "base_id": sub.instance.base_id,
            "dictionary_items_removed": lost,
            # Only when used, so that reports of deployments without them are unchanged
            **({"ports": sub.port_map} if sub.port_map else {}),
            **({"attributes": sub.attributes} if sub.attributes else {}),
        })
    added = {"commands": [], "events": [], "channels": []}
    if manifest.control_enabled and CONTROL_COMPONENT in model.components:
        control = model.components[CONTROL_COMPONENT]
        prefix = (plan.topology.module + "." if plan.topology.module else "") + CONTROL_INSTANCE
        for kind in added:
            added[kind] = [f"{prefix}.{n}" for n in getattr(control, kind)]
    identical = not any(removed.values()) and not any(added.values()) and not plan.sim.added
    simulator_report = {}
    if plan.sim:
        sim = plan.sim
        from_simulators = {"commands": [], "events": [], "channels": [], "params": []}
        instances = []
        for item in sim.added:
            component = model.components.get(item.component)
            own = {kind: [f"{item.name}.{n}" for n in getattr(component, kind)] if component else [] for kind in from_simulators}
            for kind, names in own.items():
                from_simulators[kind] += names
            span = sims.id_range(item.instance)
            instances.append({
                "instance": item.name, "component": item.component, "kind": component.kind if component else None,
                "base_id": item.instance.base_id, "last_id": span[1] if span else None,
                "from": item.source, "defined_in": item.instance.file.name,
                "pinged": False if item.name in sim.unpinged else None,
                "dictionary_items": own,
            })
        scheduling = [
            {"instance": c.to_instance, "port": c.to_port,
             "driven_by": f"{c.from_instance}.{c.from_port}" + (f"[{c.from_index}]" if c.from_index is not None else "")}
            for c in sim.connections if c.port_type in sims.SCHEDULING_PORTS and c.to_instance in sim.names
        ]
        simulator_report = {"simulators": {
            "instances": instances,
            "devices": [{"instance": d.instance, "component": d.component, "endpoint": d.endpoint, "kind": d.kind}
                        for d in sim.devices],
            "connections": [{"connection": c.fpp(), "port_type": c.port_type, "port_number_chosen": c.assigned}
                            for c in sim.connections],
            "scheduling": scheduling,
            **({"clocks": [{"instance": c.instance, "hz": c.hz, "drives": c.drives} for c in sim.clocks]}
               if sim.clocks else {}),
            "health": {
                "flight_targets_listed": sim.health_targets if sim.health_pattern else None,
                "not_pinged": sim.unpinged,
            },
            "dictionary_items_added": from_simulators,
        }}
    system = {}
    if manifest.system:
        # Only for an element of a system, so that single-deployment reports are unchanged
        system = {
            "system": {
                "name": manifest.system,
                "element": manifest.element,
                "ids": list(manifest.id_window) if manifest.id_window else None,
            },
            "links": [
                {"link": link.end.link, "instance": link.instance, "endpoint": link.endpoint,
                 "role": link.end.role, "peer": link.end.peer}
                for link in plan.links
            ],
        }
    return {
        **system,
        **simulator_report,
        "generator": "fprime-wsts",
        "topology": plan.topology.name,
        "flight_toolchain": settings.default_toolchain,
        "sim_toolchain": "native (Linux)",
        "time": {"mode": "real-time", "tick_source": next(
            (s.instance.name for s in plan.substitutions if s.surrogate.name == "Wsts.SimTick"), "internal ticker")},
        "substitutions": substitutions,
        "kept_hardware_instances": plan.kept_hardware,
        "models": [
            {"name": m.name, "class": m.cls, "endpoint": m.endpoint, "kind": m.kind, "address": m.address}
            for m in manifest.models
        ],
        "control": {
            "enabled": manifest.control_enabled,
            "setup_base_id": plan.setup_base_id,
            "control_base_id": plan.control_base_id if manifest.control_enabled else None,
        },
        "bridge": manifest.bridge,
        "dictionary": {
            "identical_to_flight": identical,
            "flight_ids_unchanged": True,
            "removed": removed,
            "added": added,
        },
        "libraries": libraries,
        "project_modules": modules,
        "reused_sources": reused,
        "fidelity_notes": plan.fidelity_notes + plan.sim.notes + ([
            "Simulator components (see 'Simulator components') are part of the simulated deployment only: their "
            "commands, events, telemetry and parameters are in its dictionary and not in the flight one, and they "
            "use command dispatcher, telemetry and rate group capacity that flight does not."
        ] if plan.sim.added else []) + [
            "Operating system: flight OSAL implementations are replaced by the Linux/POSIX ones. Task priorities "
            "are advisory and scheduling is not real-time, so timing and preemption order differ from the target.",
            "Word size and byte order are those of the host; code that depends on the target's differs here.",
            "Surrogates replace drivers at their port interface: driver internals (register access, interrupt "
            "handling, device files) are not exercised.",
            "Time follows the host wall clock, scaled by the simulation rate. Runs are repeatable to tick "
            "granularity, not cycle-exact.",
        ],
        "warnings": plan.warnings,
    }


def module_users(model: fpp.Model, plan: Plan, topology: fpp.Topology, owner) -> Dict[Path, List[str]]:
    """Which instances of the simulated topology are of a component in each module directory"""
    substituted = {sub.instance.name for sub in plan.substitutions}
    users: Dict[Path, List[str]] = {}
    for name in topology.instances:
        component = model.components.get(model.instances[name].component)
        if name in substituted or component is None:
            continue
        root = owner(component.file)
        directory = module_dir(component.file, root) if root is not None else None
        if directory is not None:
            users.setdefault(directory, []).append(f"{name} ({component.name})")
    return users


def stub_note(branches: flight_cmake.TestingBranches, project_root: Path) -> str:
    """What the variant a module has under BUILD_TESTING builds, and what it leaves out, as far as that can be read"""
    stub_sources = [s for s in branches.sources(True) if s not in branches.sources(False)]
    real_sources = [s for s in branches.sources(False) if s not in branches.sources(True)]
    parts = []
    if stub_sources:
        parts.append("it builds " + ", ".join(s.name for s in stub_sources)
                     + (" in place of " + ", ".join(s.name for s in real_sources) if real_sources else ""))
    lacks = branches.describe().get("variant_with_build_testing_lacks", {})
    if lacks.get("functions"):
        names = lacks["functions"]
        parts.append("its sources do not define " + ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
                     + ", which the other sources do (read from the text: code that calls them will not link)")
    if lacks.get("include_directories"):
        parts.append("it does not have " + "; ".join(lacks["include_directories"])
                     + " (code that includes headers from there will not compile)")
    return "; ".join(parts)


def sdk_warnings(modules: Dict[Path, List[str]], owner, project_root: Path, cache: Dict[str, List[str]],
                 stub_dirs: List[Path], found: Optional[List[flight_cmake.SdkDependency]] = None) -> List[str]:
    """Warnings for modules of the simulated build that link a file of the target's SDK.
    F Prime takes a DEPENDS entry it cannot find as a file for a target name, and stops
    while configuring: said here first, with the instances that need the module.
    `modules` maps a module directory to the instances that use it. A module is read in
    the variant the build takes: the one under BUILD_TESTING for a directory in
    `stub_dirs`. Every dependency found is added to `found`."""
    warnings = []
    for directory, users in modules.items():
        selected = directory in stub_dirs
        branches = flight_cmake.testing_branches(directory)
        in_stub = {d.entry for d in flight_cmake.sdk_dependencies(directory, cache, testing=True)} if branches else set()
        for dependency in flight_cmake.sdk_dependencies(directory, cache, testing=selected):
            if found is not None:
                found.append(dependency)
            root = owner(directory / "CMakeLists.txt")
            shown = _rel(directory, project_root)
            needed = ("The topology needs the module for " + ", ".join(users) if users
                      else "No instance of the topology is of a component in it")
            options = []
            if selected:
                needed += ". This is its stub variant (cmake.stub_variants), which links the file as well"
            elif branches and dependency.entry not in in_stub:
                note = stub_note(branches, project_root)
                options.append(
                    "take the module's stub variant, the one its CMakeLists.txt has under if (BUILD_TESTING), which "
                    f"does not link that file (cmake: {{stub_variants: [{shown}]}}; only this module is read with "
                    "BUILD_TESTING set)" + (f". Of that variant: {note}" if note else ""))
            if users:
                options.append(
                    "substitute " + ("those instances" if len(users) != 1 else "that instance")
                    + " (a 'substitute' entry in the manifest: the deployment then no longer depends on the module)")
            if root != project_root:
                options.append(f"drop the library if nothing else in it is needed (libraries: {{drop: [{_rel(root, project_root)}]}})")
            options.append(
                f"leave the module out (cmake: {{exclude_subdirectories: [{shown}]}}) and give its target name to a "
                "stand-in of the simulation (add_library(<target> ALIAS <stand-in>) in a file named under cmake.include)")
            if dependency.variables:
                options.append(f"point {dependency.variables[0]} at a host build of the SDK "
                               f"(cmake: {{cache: {{{dependency.variables[0]}: <path>}}}})")
            warnings.append(
                f"module {shown} links '{dependency.entry}': {dependency.problem}. F Prime takes an entry it cannot "
                f"find as a file for a target name and stops while configuring ('target ... not available to "
                f"deployment'). {needed}. Either " + "; or ".join(options) + "."
            )
    return warnings


#: Variables that project() has put in the cache before the overlay's `cmake.cache` lines are read:
#: a plain set(... CACHE ...) does not change them
SET_BY_PROJECT = re.compile(
    r"CMAKE_(C|CXX|ASM)_(FLAGS\w*|COMPILER)|CMAKE_(EXE|SHARED|MODULE|STATIC)_LINKER_FLAGS\w*|CMAKE_BUILD_TYPE|"
    r"CMAKE_INSTALL_PREFIX|CMAKE_TOOLCHAIN_FILE")
COMPILER_FLAGS = re.compile(r"CMAKE_(C|CXX)_FLAGS\w*")


def _is_on(values: Optional[List[str]]) -> bool:
    return bool(values) and str(values[0]).strip().upper() in ("ON", "1", "TRUE", "YES", "Y")


def testing_warning(source: str, carried: bool, count: int) -> str:
    """BUILD_TESTING for the whole build is much more than a choice of stubs"""
    effect = (
        "In F Prime that is the switch for a unit-test build. For the whole build it turns on: every module's "
        "unit-test executable; the address, leak and undefined-behaviour sanitizers (every link then needs the "
        "sanitizer runtime that matches the host compiler); the BUILD_UT definition; and the unit-test variant of "
        f"every module that has one, stubs included ({count} module(s) of this build do: see 'Modules that change "
        "under BUILD_TESTING' in the report). A library that picks stubs under BUILD_TESTING then leaves the "
        "deployment without the symbols and include directories of its real targets."
    )
    remedy = ("To take the stub variant of chosen modules only, leave BUILD_TESTING off and list them in the manifest:\n"
              "  cmake:\n    stub_variants: [<module directory, relative to the flight project>]")
    if carried:
        return f"BUILD_TESTING is ON for the whole simulated build: {source}. {effect} {remedy}"
    return (f"{source}; it is not carried over to the simulated build. {effect} {remedy}")


def render_report(report: dict) -> str:
    lines = [
        f"# WSTS report: {report['topology']}",
        "",
        "Generated by `fprime-wsts generate`. This records what the simulated deployment changes "
        "relative to flight, and what that costs in fidelity.",
        "",
        f"- Flight toolchain: `{report['flight_toolchain']}`; simulation toolchain: `{report['sim_toolchain']}`",
        f"- Time: {report['time']['mode']}, tick source `{report['time']['tick_source']}`",
        f"- Bridge: `{report['bridge']}`",
        *([f"- Simulation project: `{report['simulation_project']}`"] if report.get("simulation_project") else []),
        *([f"- Flight build options not carried over: `{' '.join(report['dropped_cmake_options'])}`"]
          if report.get("dropped_cmake_options") else []),
        *([f"- Libraries not built for simulation: {', '.join('`' + l + '`' for l in report['libraries']['dropped'])}"]
          if report["libraries"]["dropped"] else []),
        *[f"- Library built only for its configuration: `{entry['library']}` (supplies `{entry['supplies']}`, "
          f"overridden by `{entry['overridden_by']}`)" for entry in report["libraries"].get("kept_for_configuration", [])],
        f"- Dictionary identical to flight: **{'yes' if report['dictionary']['identical_to_flight'] else 'no'}**; "
        "flight IDs unchanged: **yes**",
        "",
        "## Substituted instances",
        "",
        "| Instance | Flight component | Surrogate | Chosen by | Endpoint |",
        "|---|---|---|---|---|",
    ]
    for sub in report["substitutions"]:
        endpoint = f"`{sub['endpoint']}`" if sub["endpoint"] is not None else "no bus endpoint"
        lines.append(
            f"| `{sub['instance']}` | `{sub['flight_component']}` | `{sub['surrogate']}` | {sub['chosen_by']} | {endpoint} |"
        )
    if not report["substitutions"]:
        lines.append("| (none) | | | | |")
    if report.get("system"):
        lines += ["", f"## Links (element `{report['system']['element']}` of system `{report['system']['name']}`)", ""]
        for link in report["links"]:
            lines.append(f"- `{link['link']}`: `{link['instance']}` (endpoint `{link['endpoint']}`, {link['role']}s) "
                         f"to `{link['peer']}`")
        if not report["links"]:
            lines.append("- none")
    if report.get("simulators"):
        sim = report["simulators"]
        lines += ["", "## Simulator components", "",
                  "F´ components that the simulation adds to the topology. They are not in the flight deployment.", "",
                  "| Instance | Component | IDs | Added by |", "|---|---|---|---|"]
        for item in sim["instances"]:
            ids = f"`{item['base_id']:#x}`" + (f" to `{item['last_id']:#x}`" if item["last_id"] is not None else " (none used)")
            origin = "the manifest" if item["from"] == "manifest" else f"`{item['from']}`"
            lines.append(f"| `{item['instance']}` | `{item['component']}` ({item['kind']}) | {ids} | {origin} |")
        lines += ["", "**Bus devices** (an adapter instance is the device on an endpoint; the simulator is behind it):", ""]
        lines += [f"- `{d['instance']}` (`{d['component']}`) serves endpoint `{d['endpoint']}` ({d['kind']})" for d in sim["devices"]]
        if not sim["devices"]:
            lines.append("- none")
        lines += ["", "**Scheduling:**", ""]
        lines += [f"- `{s['instance']}.{s['port']}` is driven by `{s['driven_by']}`" for s in sim["scheduling"]]
        if not sim["scheduling"]:
            lines.append("- no simulator is connected to a flight rate group")
        if sim.get("clocks"):
            lines += ["", "**Clocks** (cycle sources with a rate of their own, in simulated time; they stop while the "
                      "simulation is paused):", ""]
            lines += [f"- `{c['instance']}` at {c['hz']:g} Hz drives " + ", ".join(f"`{d}`" for d in c["drives"])
                      for c in sim["clocks"]]
        lines += ["", "**Connections added** (flight connections keep their port numbers):", ""]
        lines += [f"- `{c['connection']}`" + (" (port number chosen by the generator)" if c["port_number_chosen"] else "")
                  for c in sim["connections"]]
        if not sim["connections"]:
            lines.append("- none")
        health = sim["health"]
        if health["not_pinged"]:
            lines += ["", "**Health:** " + ", ".join(f"`{n}`" for n in health["not_pinged"]) + " have ping ports and are "
                      "not pinged. Ping ports are numbered over every instance the health pattern reaches, and the "
                      "flight ping table is indexed by that number, so the pattern is limited to the flight instances"
                      + (" (listed explicitly in the patched topology)." if health["flight_targets_listed"] else ".")
                      + " To have a simulator pinged, the project's ping table needs an entry for it."]
        if report.get("capacity"):
            lines += ["", "**Capacity used by the simulated topology:**", ""]
            capacity = report["capacity"]
            if capacity.get("command_ports"):
                c = capacity["command_ports"]
                lines.append(f"- Command registration ports: {c['used']} of {c['size']}"
                             + (f" (`{c['constant']}`)" if c["constant"] else ""))
            for key, label, constant in (("command_table", "Opcodes", "CMD_DISPATCHER_DISPATCH_TABLE_SIZE"),
                                         ("parameter_table", "Parameters", "PRMDB_NUM_DB_ENTRIES")):
                c = capacity.get(key)
                if c:
                    size = f"of {c['size']} (`{constant}` in `{c['source']}`)" if c["size"] is not None else \
                        f"(`{constant}` could not be read: check it by hand)"
                    lines.append(f"- {label}: {c['used']} {size}")
    lines += ["", "## Device models", ""]
    for model in report["models"]:
        address = f" address {model['address']:#x}" if model["kind"] == "i2c" else ""
        lines.append(f"- `{model['name']}`: `{model['class']}` on `{model['endpoint']}` ({model['kind']}{address})")
    if not report["models"]:
        lines.append("- none in-process (attach external models through the bridge)")
    lines += ["", "## Dictionary changes", ""]
    titles = [("Removed (flight items of replaced drivers)", "removed"), ("Added (WSTS)", "added")]
    if report["dictionary"].get("added_by_simulators"):
        titles.append(("Added (simulator components)", "added_by_simulators"))
    for title, key in titles:
        items = [i for names in report["dictionary"][key].values() for i in names]
        lines.append(f"**{title}:** {len(items)}")
        lines += [f"- `{item}`" for item in items]
        lines.append("")
    lines += ["## Fidelity notes", ""]
    lines += [f"- {note}" for note in report["fidelity_notes"]]
    if report.get("replaced_modules"):
        lines += ["", "## Modules built from a patched copy", "",
                  "These hold substituted instances outside the deployment's Top directory. The simulated build "
                  "takes the copy in this overlay in place of the original module.", ""]
        for module in report["replaced_modules"]:
            lines.append(f"- `{module['module']}` (`{module['original']}`): {', '.join('`' + f + '`' for f in module['files'])}")
    if report.get("driver_headers"):
        headers = report["driver_headers"]
        lines += ["", "## Headers of replaced drivers", "",
                  f"Flight code that includes the header of a replaced driver gets a stand-in from `{headers['directory']}/`, "
                  "the first include directory of the simulated build: it includes the surrogate's header and gives "
                  "the surrogate the driver's class name.", ""]
        for entry in headers["components"]:
            lines.append(f"- `{entry['component']}` (now `{entry['surrogate']}`): "
                         + ", ".join(f"`{h}`" for h in entry["headers"]))
    if report.get("nested_modules") or report.get("excluded_modules"):
        lines += ["", "## Project modules", ""]
        for entry in report.get("nested_modules", []):
            lines.append(f"- `{entry['module']}` is added by `{entry['added_by']}` (`{entry['in']}`), so the generated "
                         "project does not add it a second time")
        for module in report.get("excluded_modules", []):
            lines.append(f"- `{module}` is left out where a library, the framework or another module adds it "
                         "(`cmake.exclude_subdirectories`)")
    if report.get("flight_cmake"):
        flight = report["flight_cmake"]
        lines += ["", "## Flight root CMakeLists.txt", "",
                  f"What `{flight['file']}` does besides including F´, and what the simulated build takes over.", ""]
        for entry in flight["carried_over"]:
            lines.append(f"- Carried over, line {entry['line']}: `{entry['statement']}` ({entry['as']})")
        for entry in flight["not_carried_over"]:
            lines.append(f"- Not carried over, line {entry['line']}: `{entry['statement']}` ({entry['why']}; "
                         f"`{entry['key']}` in the manifest reproduces it)")
    if report.get("config_overrides"):
        lines += ["", "## Configuration overrides applied", "",
                  "FPP configuration replaced through `CONFIGURATION_OVERRIDES`, as the build does.", ""]
        for override in report["config_overrides"]:
            lines.append(f"- `{override['file']}`: `{override['uses']}` replaces "
                         + ", ".join(f"`{r}`" for r in override["replaces"]))
    if report.get("simulation_config_overrides"):
        lines += ["", "## Configuration overrides of the simulation", "",
                  "Configuration the simulated build replaces and flight does not. These modules are registered after "
                  "the flight project's, so they win where flight overrides the same file.", ""]
        for override in report["simulation_config_overrides"]:
            lines.append(f"- `{override['file']}`: `{override['uses']}` replaces "
                         + ", ".join(f"`{r}`" for r in override["replaces"]))
    if report.get("simulation_libraries") or report.get("cmake_includes") or report.get("run"):
        lines += ["", "## Simulation build and run inputs", ""]
        for library in report.get("simulation_libraries", []):
            lines.append(f"- Library of the simulation: `{library}`")
        for when, label in (("before_libraries", "before the libraries are read"), ("after_modules", "after all modules are added")):
            for file in report.get("cmake_includes", {}).get(when, []):
                lines.append(f"- CMake included {label}: `{file}`")
        for name, value in report.get("run", {}).get("env", {}).items():
            lines.append(f"- Environment: `{name}={value}`"
                         + (" (a path relative to this directory)" if name in report["run"]["env_paths"] else ""))
    if report.get("testing_variants"):
        lines += ["", "## Modules that change under BUILD_TESTING", "",
                  "Modules of this build whose `CMakeLists.txt` does something else when `BUILD_TESTING` is set. "
                  "The simulated build reads a module that way only when the manifest lists it under "
                  "`cmake.stub_variants`; nothing else of a unit-test build comes with that.", ""]
        for entry in report["testing_variants"]:
            users = ", ".join(f"`{u}`" for u in entry["instances"]) or "no instance of the topology"
            lines.append(f"- `{entry['module']}` (used by {users}): "
                         + ("**read with `BUILD_TESTING` set**" if entry["selected"] else "read with `BUILD_TESTING` off"))
            for label, key in (("With `BUILD_TESTING`", "with_build_testing"), ("Without", "without")):
                for statement in entry[key]:
                    lines.append(f"  - {label}: `{statement}`")
            if entry["registers_unit_tests"]:
                lines.append("  - Its unit tests (`register_fprime_ut`) are not built in either case")
            lacks = entry.get("variant_with_build_testing_lacks", {})
            if lacks.get("depends"):
                lines.append("  - The variant with `BUILD_TESTING` does not depend on: " + ", ".join(f"`{d}`" for d in lacks["depends"]))
            if lacks.get("include_directories"):
                lines.append("  - It does not have: " + "; ".join(f"`{d}`" for d in lacks["include_directories"]))
            if lacks.get("functions"):
                lines.append("  - Its sources do not define (read from the text): " + ", ".join(f"`{f}`" for f in lacks["functions"]))
    if report.get("cxx_flags"):
        lines += ["", "## Compiler flags of the simulation", "", "From `cmake.cxx_flags` in the manifest, for C++ sources.", ""]
        for where, flags in report["cxx_flags"].items():
            lines.append(f"- {where if where == 'all modules' else '`' + where + '`'}: " + ", ".join(f"`{f}`" for f in flags))
    if report.get("host_requirements"):
        host = report["host_requirements"]
        lines += ["", "## What the build takes from the host", "",
                  "Read from the sources and CMake files of the modules of this build (the kept flight modules, the "
                  "libraries and the simulation's own). `fprime-wsts check-host` asks a machine whether it has them, "
                  "and records its compiler in `wsts-host.md`.", ""]
        compiler = host.get("flight_compiler")
        lines.append(
            f"- Flight toolchain `{host['flight_toolchain']}`"
            + ((f": compiler `{compiler['cxx']}`" if compiler.get("cxx") else ": its compiler is not named by a plain set()")
               + f" (`{compiler['toolchain_file']}`)" if compiler else
               ": its compiler could not be read from a toolchain file")
            + ". The host compiler is another one: standard library headers include one another differently "
            "between compilers and versions, so code that relies on an indirect include may not compile here.")
        always = [h for h, needs in host["system_headers"].items() if any("condition" not in n for n in needs)]
        conditional = [h for h in host["system_headers"] if h not in always]
        lines.append("- System headers: " + (", ".join(f"`{h}`" for h in always) or "none"))
        if conditional:
            lines.append("- System headers under a condition that is not decided here: " + ", ".join(f"`{h}`" for h in conditional))
        lines.append("- Libraries (`-l`): " + (", ".join(f"`{l}`" for l in host["libraries"]) or "none"))
        lines.append("- Packages (`find_package`): " + (", ".join(
            f"`{p}`" + ("" if spec["required"] else " (optional)") for p, spec in host["packages"].items()) or "none"))
        lines.append("- Files of an SDK: " + (", ".join(f"`{d['entry']}` (`{d['module']}`)" for d in host["sdk_paths"]) or "none"))
        if host["sanitizers"]:
            lines.append("- Sanitizer runtime for: " + ", ".join(host["sanitizers"]))
        for statement in host.get("not_read", []):
            lines.append(f"- Not read: `{statement}` adds directories through a variable, so every module directory "
                         "below that file is taken to be built")
    if report["warnings"]:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in report["warnings"]]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------


@dataclass
class Result:
    output: Path
    report: dict
    changed: List[str] = field(default_factory=list)  # --check: files that differ
    notices: List[str] = field(default_factory=list)  # about this run, not about the output: not in the report


def generate(project_root: Path, manifest: Manifest, output: Optional[Path] = None, check: bool = False) -> Result:
    project_root = project_root.resolve()
    settings = fpp.read_settings(project_root)
    project_root = settings.project_root
    if output is None:
        if manifest.output:
            output = manifest.resolve_sim(manifest.output, project_root)
        else:
            output = (manifest.sim_root or project_root) / "wsts" / Path(manifest.deployment).name
    given = output
    output = output.resolve()
    if output.exists() and any(output.iterdir()) and not (output / fpp.GENERATED_MARKER).exists():
        raise WstsError(f"{output} exists and was not generated by fprime-wsts; refusing to overwrite it")

    notices = [symlink_notice(given, output)] if os.path.abspath(given) != str(output) else []
    if check:
        with tempfile.TemporaryDirectory(prefix="wsts-check-") as scratch:
            target = Path(scratch) / "out"
            result = _generate(project_root, settings, manifest, target, final_output=output)
            result.changed = _diff(target, output)
            result.output = output
            result.notices = notices
            return result
    result = _generate(project_root, settings, manifest, output, final_output=output)
    result.notices = notices
    return result


def symlink_notice(given: Path, output: Path) -> str:
    """For an output directory that was named through a symbolic link"""
    return (
        f"{os.path.abspath(given)} is reached through a symbolic link: the output directory is {output}. "
        "fprime-util resolves links, and it stops with 'differs from CMake cache' when a build cache was made "
        f"through one path and is used through the other. Run it from {output} (the commands printed here use "
        "that path), and regenerate a build cache made through the link with 'fprime-util generate -f'."
    )


def _diff(new: Path, old: Path) -> List[str]:
    if not old.is_dir():
        return ["(output directory missing)"]
    changed = []
    names = {p.relative_to(new).as_posix() for p in new.rglob("*") if p.is_file()}
    names |= {p.relative_to(old).as_posix() for p in old.rglob("*") if p.is_file()}
    for name in sorted(names):
        if name.startswith(("build-", "wsts-logs/", hostcheck.HOST_FILE + ".")) or "/build-" in name:
            continue
        a, b = new / name, old / name
        if not a.is_file() or not b.is_file() or not filecmp.cmp(a, b, shallow=False):
            changed.append(name)
    return changed


def _generate(project_root: Path, settings: fpp.ProjectSettings, manifest: Manifest, output: Path, final_output: Path) -> Result:
    deployment_dir = (project_root / manifest.deployment).resolve()
    top_dir = deployment_dir / "Top"
    if not top_dir.is_dir():
        raise WstsError(f"{deployment_dir} has no Top directory: is '{manifest.deployment}' a deployment?")
    deployment = deployment_dir.name

    # A standalone simulation project is an F Prime library of its own: its modules are
    # built by its library.cmake and named relative to its root
    sim_root = manifest.sim_root
    if sim_root is not None and not sim_root.is_dir():
        raise WstsError(f"simulation project {sim_root} does not exist")
    sim_library = sim_root if sim_root is not None and (sim_root / "library.cmake").is_file() else None
    # Further libraries the simulation uses (`libraries.sim`): vendored in the simulation
    # project or outside it. Each is a root of its own, so its modules keep the names they
    # have wherever else the library is used.
    sim_libraries: List[Path] = []
    for entry in manifest.sim_libraries:
        library = manifest.resolve_sim(entry, project_root)
        if not library.is_dir() or not ((library / "library.cmake").is_file() or (library / f"{library.name}.cmake").is_file()):
            raise WstsError(
                f"libraries.sim names '{entry}' ({library}), which is not an F Prime library: there is no "
                "library.cmake in it. Paths are relative to the simulation project."
            )
        if library in settings.library_locations or library == project_root:
            raise WstsError(f"libraries.sim names '{entry}', which the flight project already uses as a library or is")
        if library not in sim_libraries:
            sim_libraries.append(library)
    sim_roots = [*sim_libraries, *([sim_root] if sim_root is not None else [])]
    includes = {
        key: [manifest.resolve_sim(entry, project_root) for entry in entries]
        for key, entries in (("early", manifest.cmake_include_early), ("late", manifest.cmake_include))
    }
    for key, files in includes.items():
        for file in files:
            if not file.is_file():
                raise WstsError(f"cmake.{'include_early' if key == 'early' else 'include'} names {file}, which does "
                                "not exist. Paths are relative to the simulation project.")

    # ---- Catalogs ----
    catalog: List[Surrogate] = []
    for entry in manifest.catalogs:
        catalog += load_catalog(manifest.resolve_sim(entry, project_root))
    catalog += load_catalog(DEFAULT_CATALOG)

    # ---- Analyse the flight topology ----
    early_warnings: List[str] = []
    version_warning = fpp.check_tool_version(settings.framework_path)
    if version_warning:
        early_warnings.append(version_warning)
    with tempfile.TemporaryDirectory(prefix="wsts-work-") as scratch:
        work = Path(scratch)
        toolkit_fpp_root = TOOLKIT_ROOT / "Wsts"
        roots = [settings.framework_path, *settings.library_locations, project_root, toolkit_fpp_root, *sim_roots]
        # Build roots in the order F Prime registers configuration: the last override wins
        # The simulation's come last: the overlay registers them after the flight project's
        config_roots = [settings.framework_path, *settings.library_locations, toolkit_fpp_root, project_root, *sim_roots]
        config_overrides, override_warnings = fpp.find_config_overrides(config_roots)
        header_overrides, header_warnings = fpp.find_config_overrides(config_roots, (".hpp", ".h"))

        def owner(path: Path) -> Optional[Path]:
            """A file belongs to the deepest root that contains it: the framework is often
            checked out inside the project, and a library may be too"""
            containing = [root for root in roots if root in path.parents]
            return max(containing, key=lambda root: len(root.parts)) if containing else None
        top_registration = read_registration(top_dir / "CMakeLists.txt", "register_fprime_module")
        if manifest.topology_fpp:
            top_fpp = [manifest.resolve(f, project_root) for f in manifest.topology_fpp]
        else:
            top_fpp = [Path(f).resolve() for f in top_registration.get("AUTOCODER_INPUTS", []) if f.endswith(".fpp")]
            top_fpp = top_fpp or sorted(top_dir.glob("*.fpp"))
        if not top_fpp:
            raise WstsError(f"no topology FPP files found in {top_dir}")
        def in_simulation(path: Path) -> bool:
            # A file belongs to the deepest root that contains it
            return owner(path) in sim_roots

        for override in config_overrides + header_overrides:
            if in_simulation(override.winner) and not override.replaced:
                raise WstsError(
                    f"{override.winner} is listed under CONFIGURATION_OVERRIDES, but no configuration module of the "
                    f"framework, a library or the flight project supplies a file named {override.name}: F Prime "
                    "rejects an override of nothing. If the file is new configuration of the simulation, register it "
                    "under AUTOCODER_INPUTS or HEADERS instead; if the name is misspelt, it must match the file it "
                    "replaces exactly."
                )
        early_warnings += [w for w in override_warnings + header_warnings if w not in early_warnings]
        # The simulation's modules that override configuration are held back until the flight
        # project's modules have been added (F Prime: the last registration wins)
        deferred_modules, loose_overrides = fpp.find_override_modules(sim_roots)
        if loose_overrides:
            raise WstsError(
                f"{loose_overrides[0]} registers CONFIGURATION_OVERRIDES directly. Put the register_fprime_config() "
                "call in a CMakeLists.txt of its own directory and add that directory with add_fprime_subdirectory(): "
                "fprime-wsts has to register the simulation's overrides after the flight project's, and can only "
                "hold back a module directory."
            )

        found_files = fpp.find_fpp_files(roots, manifest.fpp_exclude)
        # Only overrides that the analysis can see count: fpp_exclude may have removed them
        config_overrides = [o for o in config_overrides if o.winner in found_files and any(r in found_files for r in o.replaced)]
        candidates = fpp.select_candidates(found_files, settings.framework_path, config_overrides)
        # Other deployments in the project may define the same names; only this one is read
        candidates = [c for c in candidates if c not in set(top_fpp)]
        located = fpp.locate_defs(work, candidates)
        surrogate_files = sorted({
            located[("component", name)]
            for name in {s.name for s in catalog} | {SETUP_COMPONENT, CONTROL_COMPONENT} | {s.surrogate for s in manifest.substitutions}
            if ("component", name) in located
        })
        # Instances and subtopologies the manifest adds are defined in the simulation
        # project (or wherever the FPP tools find them): read their definitions too
        simulator_files = sorted({
            located[("instance", name)]
            for name in manifest.simulators.instances + manifest.simulators.topologies
            if ("instance", name) in located
        })
        sources = top_fpp + [f for f in surrogate_files + simulator_files if f not in top_fpp]
        sources = list(dict.fromkeys(sources))
        try:
            model = fpp.analyze(work, fpp.depend(work, sources) + sources)
        except WstsError as error:
            if simulator_files and re.search(r"base ID \d+ for instance", str(error)):
                # FPP checks IDs across every instance it reads
                raise WstsError(
                    f"{error}\n\nA simulator instance and another instance use the same IDs. Flight IDs do not "
                    "move: give the simulator instance a base id that is free, in its definition in the "
                    "simulation project."
                ) from None
            raise

        local = [t for t in model.topologies.values() if t.file in top_fpp or t.file.parent == top_dir]
        if manifest.topology:
            local = [t for t in local if t.name == manifest.topology or t.base_name == manifest.topology]
        if len(local) != 1:
            names = ", ".join(t.name for t in local) or "none"
            raise WstsError(f"expected exactly one topology in {top_dir}, found: {names}. Set 'topology' in the manifest.")
        topology = local[0]

        plan = make_plan(model, manifest, catalog, topology, roots, settings.framework_path)
        plan.warnings[:0] = early_warnings
        capacity: Dict[str, dict] = {}
        if plan.sim.added:
            tables = {}
            for key, header, constant in (("command_table", "CommandDispatcherImplCfg.hpp", "CMD_DISPATCHER_DISPATCH_TABLE_SIZE"),
                                          ("parameter_table", "PrmDbImplCfg.hpp", "PRMDB_NUM_DB_ENTRIES")):
                # The file the build uses: the simulation's override if there is one
                value = fpp.config_constant(config_roots, settings.framework_path, header, constant, header_overrides)
                tables[key] = (value[0], str(value[1])) if value else None
            retyped = {sub.instance.name: sub.surrogate.name for sub in plan.substitutions}
            components = [retyped.get(name, model.instances[name].component) for name in topology.instances]
            components += [item.component for item in plan.sim.added]
            components += [CONTROL_COMPONENT] if manifest.control_enabled else []
            capacity = sims.check_capacity(plan.sim, model, topology, components, tables)

        # ---- Edits to the flight model, by file and position ----
        edits: Dict[Path, List[Tuple[int, int, int, object, str]]] = {}

        def edit(file: Path, line: int, col: int, change, what: str) -> None:
            edits.setdefault(file, []).append((line, col, len(edits.get(file, [])), change, what))

        for sub in plan.substitutions:
            edit(sub.instance.file, sub.instance.type_line, sub.instance.type_col,
                 lambda text, sub=sub: patch_instance(text, sub), f"instance {sub.instance.name}")
        added_instances = [SETUP_INSTANCE] + ([CONTROL_INSTANCE] if manifest.control_enabled else [])
        if manifest.control_enabled and topology.packet_sets:
            # Named relative to the topology's module, as the topology's own packets are: a
            # topology that shares its module's name would capture a fully qualified name
            channels = [f"{CONTROL_INSTANCE}.{c}" for c in model.components[CONTROL_COMPONENT].channels]
            for file, line, col in topology.packet_sets:
                edit(file, line, col, lambda text, line=line, col=col: patch_packets(text, line, col, channels),
                     "telemetry packets")
        for file, line, col, change, what in port_edits(plan, model, topology):
            edit(file, line, col, change, what)
        if plan.sim.health_pattern is not None:
            pattern = plan.sim.health_pattern
            edit(pattern.file, pattern.source_line, pattern.source_col,
                 lambda text: pin_pattern_targets(text, pattern, plan.sim.health_targets, topology), "health connections")

        # ---- Files outside Top: their modules are replaced by patched copies ----
        replaced: Dict[Path, ReplacedModule] = {}
        for file, changes in edits.items():
            if file in top_fpp or file.parent == top_dir:
                continue
            what = changes[0][4]
            root = owner(file)
            directory = module_dir(file, root) if root is not None else None
            if directory is None or directory == root:
                raise WstsError(
                    f"{what} is defined in {file}, which is not inside a module directory of "
                    "the framework, a library or the project: there is no module to replace with a patched copy"
                )
            if root in (toolkit_fpp_root, *sim_roots):
                raise WstsError(f"{what} is defined in {file}, inside the simulation sources: change it there")
            module = replaced.setdefault(directory, ReplacedModule(directory, root, directory.relative_to(root).as_posix()))
            if file not in module.files:
                module.files.append(file)
        for module in replaced.values():
            first = module.relative.split("/")[0]
            if first == deployment or sum(1 for other in replaced.values() if other.relative == module.relative) > 1:
                raise WstsError(
                    f"cannot place the patched copy of {module.original}: its path '{module.relative}' is already "
                    "taken in the overlay"
                )

        # ---- Write the overlay ----
        if output.exists():
            for child in output.iterdir():
                if child.name.startswith("build-") or child.name == "wsts-logs" or child.name.startswith(hostcheck.HOST_FILE + "."):
                    continue  # keep the build cache (regeneration is incremental) and run logs
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        out_top = output / deployment / "Top"
        out_top.mkdir(parents=True, exist_ok=True)
        (output / fpp.GENERATED_MARKER).write_text("Generated by fprime-wsts. Safe to delete and regenerate.\n")

        texts: Dict[Path, str] = {}

        patchable = {f for module in replaced.values() for f in module.files}

        def text_of(path: Path) -> str:
            if path not in texts:
                if path.parent != top_dir and path not in patchable:
                    raise WstsError(f"{path} needs patching but is outside {top_dir}")
                texts[path] = path.read_text()
            return texts[path]

        for f in top_fpp:
            text_of(f)
        # Bottom-up within each file, so that positions taken from the FPP model stay valid
        for file, changes in edits.items():
            for _, _, _, change, _ in sorted(changes, key=lambda c: c[:3], reverse=True):
                texts[file] = change(text_of(file))
        # Last, because it is found from the topology's own position: the WSTS instances at
        # the top of its body, the simulators at the end
        texts[topology.file] = patch_topology(
            text_of(topology.file), topology, added_instances, sims.render_block(plan.sim, topology) if plan.sim.added else "")

        patched_fpp: List[Path] = []
        for path in sorted(top_dir.iterdir()):
            if path.suffix not in (".fpp", ".fppi"):
                continue
            target = out_top / path.name
            target.write_text(texts.get(path, path.read_text()))
            if path in top_fpp:
                patched_fpp.append(target)
        instances_file = out_top / "wsts_instances.fpp"
        instances_file.write_text(render_instances(plan, manifest))
        patched_fpp.append(instances_file)

        # Patched copies of modules outside Top, at the same path below the overlay as the
        # original has below its root: generated code and module names come out the same
        copy_of: Dict[Path, Path] = {}  # patched copy -> original
        for module in replaced.values():
            target_dir = output / module.relative
            target_dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(module.original.iterdir()):
                if path in module.files:
                    (target_dir / path.name).write_text(texts[path])
                    copy_of[target_dir / path.name] = path
                elif path.suffix == ".fppi":
                    shutil.copy(path, target_dir / path.name)  # included relative to the FPP file

        # ---- Validate the patched model and find what it needs ----
        post_candidates = [c for c in candidates if c not in set(top_fpp) and c not in patchable] + list(copy_of)
        fpp.locate_defs(work, post_candidates)
        # From here on a patched copy stands for its original: it is built in its place
        closure = [copy_of.get(f, f) for f in fpp.depend(work, patched_fpp)]
        check_files = [next((copy for copy, original in copy_of.items() if original == f), f) for f in closure]
        try:
            fpp.check(work, check_files + patched_fpp)
        except WstsError as error:
            raise WstsError(f"the generated topology does not pass fpp-check:\n{error}") from None
        if plan.sim.added or any(sub.port_map for sub in plan.substitutions):
            # The flight topology's own connections must be exactly where they were
            patched_model = fpp.analyze(work, check_files + patched_fpp)
            sims.verify_flight_connections(
                topology, patched_model.topologies[topology.name],
                {sub.instance.name for sub in plan.substitutions if sub.port_map})

    # ---- Project modules the simulated deployment builds ----
    # A file belongs to the deepest root that contains it: the framework is often checked
    # out inside the project, and a project may itself live inside a library's repository.
    excluded = {manifest.resolve(d, project_root) for d in manifest.exclude_subdirectories}
    found_modules: Dict[Path, None] = {}
    for f in closure:
        if owner(f) != project_root or output in f.parents or final_output in f.parents:
            continue
        directory = module_dir(f, project_root)
        if directory is None:
            plan.warnings.append(f"{f} is not inside a module directory; it will not be built")
        elif directory != top_dir and directory not in excluded:
            found_modules[directory] = None
    extra_dirs = [manifest.resolve(extra, project_root) for extra in manifest.extra_subdirectories]
    # Every configuration file that is overridden, whatever its kind, and which directory
    # registers it
    all_overrides, _ = fpp.find_config_overrides(config_roots, ("",))
    supplies: Dict[Path, Set[str]] = {}
    overrides_of: Dict[Path, Set[str]] = {}
    for override in all_overrides:
        for cmake, _ in override.suppliers:
            supplies.setdefault(cmake.parent, set()).add(override.name)
        for cmake, _ in override.overriders:
            overrides_of.setdefault(cmake.parent, set()).add(override.name)
    # A module with CONFIGURATION_OVERRIDES has to be registered after the configuration
    # it replaces, which may itself be a project module. A module that another one adds
    # itself is registered with that one, and is not added here.
    overriding_dirs = {o.winner.parent for o in config_overrides}
    order = flight_cmake.order_modules(
        [*found_modules, *[d for d in extra_dirs if d not in found_modules]], overriding_dirs,
        supplies, overrides_of, project_root, excluded, last=[d for d in extra_dirs if d not in found_modules])
    module_dirs: Dict[Path, None] = {d: None for d in order.roots}
    built_project_dirs = {member for members in order.members.values() for member in members} | {top_dir, deployment_dir}

    # ---- Libraries the simulated deployment builds ----
    # A library is kept when the patched model uses one of its files, or when it supplies a
    # configuration file that a module of this build overrides: F Prime rejects an override
    # of a file that no configuration module supplies.
    libraries = {"kept": [], "dropped": []}
    flight_libraries = list(dict.fromkeys(settings.library_locations))
    named_keep = {lib for lib in flight_libraries if any(manifest.resolve(k, project_root) == lib for k in manifest.keep_libraries)}
    named_drop = {lib for lib in flight_libraries if any(manifest.resolve(k, project_root) == lib for k in manifest.drop_libraries)}
    used_libraries = {lib for lib in flight_libraries if any(owner(f) == lib for f in closure)}
    kept_libraries = {lib for lib in flight_libraries if (lib in used_libraries or lib in named_keep) and lib not in named_drop}
    kept_for_config: Dict[Path, List[Tuple[fpp.ConfigOverride, Path, Path]]] = {}  # library -> (override, supplied file, overriding module)

    def registered(cmake: Path) -> bool:
        """Whether a CMake file that registers configuration is read by the simulated build"""
        root = owner(cmake)
        if root == project_root:
            return cmake.parent in built_project_dirs
        return root in kept_libraries or root not in flight_libraries

    while True:
        added = False
        for override in all_overrides:
            overriders = [cmake for cmake, _ in override.overriders if registered(cmake)]
            if not overriders or any(registered(cmake) for cmake, _ in override.suppliers):
                continue
            for cmake, supplied in override.suppliers:
                library = owner(cmake)
                if library not in flight_libraries:
                    continue
                if library in named_drop:
                    raise WstsError(
                        f"libraries.drop names {library}, but that library supplies the configuration file "
                        f"{override.name} ({supplied}), which {overriders[0].parent} overrides with "
                        "CONFIGURATION_OVERRIDES. F Prime stops when an override replaces a file that no "
                        "configuration module supplies, so the simulated build needs the library. Remove it from "
                        "libraries.drop: a platform library's library.cmake usually registers its configuration "
                        "and then stops on a host (restrict_platforms). To build without it, leave the overriding "
                        f"module out as well, with\n  cmake:\n    exclude_subdirectories: [{_rel(overriders[0].parent, project_root)}]"
                    )
                kept_for_config.setdefault(library, []).append((override, supplied, overriders[0].parent))
                if library not in kept_libraries:
                    kept_libraries.add(library)
                    added = True
        if not added:
            break
    library_paths: List[Path] = [TOOLKIT_ROOT]
    for library in flight_libraries:
        if library in kept_libraries:
            libraries["kept"].append(str(library))
            if library not in library_paths:
                library_paths.append(library)
        else:
            libraries["dropped"].append(str(library))
    library_paths.append(project_root)
    # Before the simulation project itself, which may build on them
    library_paths += [library for library in sim_libraries if library not in library_paths]
    if sim_root is not None:
        sim_files = sorted({f for f in closure if owner(f) == sim_root and output not in f.parents and final_output not in f.parents})
        if sim_files and sim_library is None:
            raise WstsError(
                f"the simulated deployment uses FPP files from the simulation project {sim_root} "
                f"(for example {sim_files[0]}), but it has no library.cmake to build them. "
                "Add one that calls add_fprime_subdirectory() for each of its modules."
            )
        if sim_library is not None:
            library_paths.append(sim_library)
    # A library that is kept for its configuration alone is read whole by F Prime
    for library in kept_for_config:
        if library in used_libraries:
            continue
        manifest_file = next((f for f in (library / "library.cmake", library / f"{library.name}.cmake") if f.is_file()), None)
        if manifest_file is None:
            continue
        text = manifest_file.read_text(errors="replace")
        # F Prime includes the file, and restrict_platforms() returns from it on another platform:
        # only what comes before such a call is added on the host
        text = re.sub(r"#[^\n]*", "", text)
        guard = _host_restriction(text)
        supplying = {cmake.parent for override, _, _ in kept_for_config[library] for cmake, _ in override.suppliers}
        unguarded: List[Path] = []
        for match in re.finditer(r"add_fprime_subdirectory\s*\(\s*\"?([^\s\")]+)", text if guard is None else text[:guard.start()]):
            target = match.group(1).replace("${CMAKE_CURRENT_LIST_DIR}", str(library))
            if "${" in target:
                continue
            directory = (library / target).resolve()
            if directory in supplying or any(directory in s.parents for s in supplying) or directory in excluded:
                continue
            module_cmake = directory / "CMakeLists.txt"
            if module_cmake.is_file() and _host_restriction(module_cmake.read_text(errors="replace")):
                continue
            unguarded.append(directory)
        if unguarded:
            listing = ", ".join(_rel(d, project_root) for d in unguarded)
            plan.warnings.append(
                f"{_rel(library, final_output)} is built only for the configuration it supplies, and its "
                f"{manifest_file.name} also adds modules that are not restricted to another platform: "
                f"{', '.join(_rel(d, library) for d in unguarded)}. They are configured on the host with the rest. "
                "If they do not configure or build there, leave them out with\n  cmake:\n    exclude_subdirectories: ["
                f"{listing}]\nin the manifest (paths relative to the flight project)."
            )

    # Directories the manifest excludes that the overlay does not add itself: a library, the
    # framework or another module adds them, so the overlay's add_fprime_subdirectory() skips them
    skipped_dirs = sorted(
        d for d in excluded
        if d.is_dir() and (owner(d / "CMakeLists.txt") != project_root
                           or any(d in flight_cmake.added_directories(root) for root in order.roots)))

    # ---- The flight project's own root CMakeLists.txt ----
    flight_root = flight_cmake.read_root(
        project_root, settings.framework_path, manifest.cmake_cache, manifest.defines,
        sim_root or project_root, _rel(project_root / "CMakeLists.txt", final_output))

    # ---- Modules read in their stub variant, or compiled with flags of their own ----
    def build_module(entry: str, key: str) -> Path:
        """A module directory the manifest names, relative to the flight project"""
        directory = manifest.resolve(entry, project_root)
        named = f"{key} in the manifest names '{entry}'"
        if not (directory / "CMakeLists.txt").is_file():
            raise WstsError(
                f"{named} ({directory}), which is not a module directory: there is no CMakeLists.txt in it. Paths "
                f"are relative to the flight project ({project_root}); a module of a library is named through the "
                "library's directory."
            )
        if directory in (deployment_dir, top_dir):
            raise WstsError(f"{named}: the generator writes the deployment's own CMakeLists.txt files and does not "
                            "read the flight ones, so there is nothing to select there")
        if directory in excluded:
            raise WstsError(f"{named}, which cmake.exclude_subdirectories leaves out of the build: remove one of the two entries")
        root = owner(directory / "CMakeLists.txt")
        if root is None:
            raise WstsError(f"{named} ({directory}), which is outside the flight project, its libraries, the framework "
                            "and the simulation project: nothing in the simulated build adds it")
        if root in flight_libraries and root not in kept_libraries:
            raise WstsError(f"{named}, a module of {_rel(root, project_root)}, which is not part of the simulated build: "
                            "the patched model uses nothing of that library. Remove the entry, or keep the library "
                            f"with libraries: {{keep: [{_rel(root, project_root)}]}}")
        return directory

    stub_dirs: List[Path] = list(dict.fromkeys(build_module(entry, "cmake.stub_variants") for entry in manifest.stub_variants))
    name_roots = [settings.framework_path, *settings.library_locations, project_root, *sim_roots]
    module_flags: Dict[Path, Tuple[str, List[str]]] = {}  # module directory -> (its target, flags)
    for entry, flags in manifest.cxx_flags.items():
        if entry != ALL_MODULES:
            directory = build_module(entry, "cmake.cxx_flags")
            module_flags[directory] = (module_name(directory, name_roots), flags)
    global_flags = manifest.cxx_flags.get(ALL_MODULES, [])

    # What each build root reads on the host, as far as its CMake files say with literal paths
    set_variables = {**flight_root.cache, **manifest.cmake_cache}
    trees: Dict[Path, flight_cmake.BuildTree] = {project_root: flight_cmake.BuildTree()}
    for directory in order.roots:
        trees[project_root].directories.setdefault(directory, project_root / "CMakeLists.txt")
        flight_cmake.read_tree(directory / "CMakeLists.txt", stub_dirs, excluded, trees[project_root], set_variables)
    unread_roots: List[str] = []
    for library in library_paths:
        start = next((f for f in (library / "library.cmake", library / f"{library.name}.cmake") if f.is_file()), None)
        if library in (TOOLKIT_ROOT, project_root) or start is None:
            continue
        tree = flight_cmake.read_tree(start, stub_dirs, excluded, None, set_variables)
        if tree.unread:
            # A directory is added through a variable: take every module directory below the root
            cmake_file, command = tree.unread[0]
            unread_roots.append(f"{_rel(cmake_file, final_output)}:{command.line}: {command.text}")
            for current, directories, files in os.walk(library):
                here = Path(current)
                directories[:] = sorted(
                    d for d in directories if not d.startswith(("build-", ".")) and d not in ("wsts-logs", "__pycache__")
                    and not (here / d / fpp.GENERATED_MARKER).exists() and (here / d) not in excluded
                    and (here / d) not in roots)
                if here != library and "CMakeLists.txt" in files:
                    tree.directories.setdefault(here, start)
                    flight_cmake.read_tree(here / "CMakeLists.txt", stub_dirs, excluded, tree, set_variables)
        trees[library] = tree
    for directory in dict.fromkeys([*stub_dirs, *module_flags]):
        root = owner(directory / "CMakeLists.txt")
        tree = trees.get(root)
        if tree is None or directory in tree.directories:
            continue  # the framework's modules are not followed
        key = "cmake.stub_variants" if directory in stub_dirs else "cmake.cxx_flags"
        if root == project_root:
            listing = ", ".join(_rel(d, project_root) for d in tree.directories) or "none"
            raise WstsError(
                f"{key} in the manifest names '{_rel(directory, project_root)}', which is not a module of the "
                f"simulated build: the patched model uses nothing of it, and no module that is built adds it. "
                f"Modules of the flight project that are built: {listing}. If it holds code without an FPP model "
                "that the deployment needs, add it with cmake.extra_subdirectories."
            )
        raise WstsError(
            f"{key} in the manifest names '{_rel(directory, project_root)}', which {_rel(root, project_root)} does not "
            "add on a Linux host: its library.cmake does not reach that directory (or stops before it with "
            "restrict_platforms()). Modules it adds: "
            + (", ".join(_rel(d, project_root) for d in tree.directories) or "none") + "."
        )
    variants: Dict[Path, flight_cmake.TestingBranches] = {}
    for root, tree in trees.items():
        for directory in tree.directories:
            branches = flight_cmake.testing_branches(directory)
            if branches:
                variants[directory] = branches
    for directory in stub_dirs:
        if directory not in variants and not flight_cmake.testing_branches(directory):
            plan.warnings.append(
                f"cmake.stub_variants lists {_rel(directory, project_root)}, and its CMakeLists.txt has nothing under "
                "if (BUILD_TESTING): the module is built as it would be anyway. If the stub is selected by another "
                "variable, set that one with cmake.cache; if it is selected in a file the module includes, list the "
                "directory whose CMakeLists.txt holds the if()."
            )

    # ---- Hand-written sources ----
    if manifest.topology_sources is not None:
        topology_sources = [manifest.resolve(s, project_root) for s in manifest.topology_sources]
    else:
        topology_sources = [Path(s).resolve() for s in top_registration.get("SOURCES", [])]
    # A flight driver's module leaves the build when no instance still uses the component
    still_used = {
        model.instances[name].component
        for name in topology.instances
        if name not in {s.instance.name for s in plan.substitutions}
    }
    substituted_modules = set()
    for sub in plan.substitutions:
        if sub.original.name not in still_used:
            substituted_modules.add(module_name(sub.original.file.parent, [settings.framework_path, *settings.library_locations, project_root]))
    def include_path(path: Path) -> str:
        """How C++ includes a file: relative to the deepest build root that holds it"""
        for root in sorted([TOOLKIT_ROOT, *settings.library_locations, project_root, settings.framework_path, *sim_roots], key=lambda r: -len(r.parts)):
            if root in path.parents:
                return path.relative_to(root).as_posix()
        return path.name

    def header_of(component: fpp.Component) -> str:
        return include_path(component.file.with_suffix(".hpp"))

    # Flight code includes the headers of the drivers it configures: a subtopology's
    # SubtopologyTopologyDefs.hpp for a type it needs, Main.cpp through the TopologyDefs. For
    # every replaced component no instance still uses, a header at the same include path
    # gives the surrogate instead. Not when the component is still used: that would shadow
    # the real header.
    driver_headers: List[dict] = []
    standin_files: Dict[str, str] = {}
    standin_modules: List[str] = []
    for sub in plan.substitutions:
        if sub.original.name in still_used or any(h["component"] == sub.original.name for h in driver_headers):
            continue
        component_dir = sub.original.file.parent
        include_dir = include_path(component_dir / "x").rpartition("/")[0]
        if not include_dir:
            continue  # the component is at a build root: no include path to stand at
        surrogate_include = header_of(sub.surrogate)
        names = driver_header_names(sub.original)
        for name in names:
            standin_files[f"{include_dir}/{name}"] = render_driver_header(f"{include_dir}/{name}", sub, surrogate_include)
        autocoded = f"{include_dir}/{sub.original.name.rpartition('.')[2]}ComponentAc.hpp"
        standin_files[autocoded] = render_driver_autocoded_header(autocoded, sub, f"{include_dir}/{names[0]}")
        standin_modules.append(module_name(component_dir, [settings.framework_path, *settings.library_locations, project_root]))
        driver_headers.append({"component": sub.original.name, "surrogate": sub.surrogate.name,
                               "headers": [f"{include_dir}/{name}" for name in names] + [autocoded]})
    for relative, text in standin_files.items():
        target = output / STANDIN_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    top_depends = [d for d in top_registration.get("DEPENDS", []) if d not in substituted_modules and "${" not in d]
    top_depends += [d for d in manifest.extra_depends if d not in top_depends]
    if "Wsts_Core" not in top_depends:
        top_depends.append("Wsts_Core")

    deployment_registration = read_registration(deployment_dir / "CMakeLists.txt", "register_fprime_deployment")
    main_spec = manifest.main
    if main_spec.sources is not None:
        main_sources = [manifest.resolve(s, project_root) for s in main_spec.sources]
    else:
        main_sources = [Path(s).resolve() for s in deployment_registration.get("SOURCES", [])]
    generated_main = main_spec.generate
    if not generated_main and not any(re.search(r"\bmain\s*\(", s.read_text(errors="replace")) for s in main_sources if s.is_file()):
        raise WstsError(
            f"deployment {deployment} has no main(): its entry point is started by the target OS. "
            "Add to the manifest:\n  main:\n    generate: true\n    declarations: <C++ declarations>\n"
            "    start: <C++ expression that starts the deployment>\n    stop: <C++ expression that stops it>"
        )
    for source in topology_sources + main_sources:
        if not source.is_file():
            raise WstsError(f"source file not found: {source}")

    # ---- Emit build files and generated code ----
    (out_top / "WstsCompat.hpp").write_text(render_compat(plan, model, manifest, header_of))
    (out_top / "WstsSetup.cpp").write_text(
        render_setup(plan, manifest, f"{deployment}/Top/{topology.base_name}TopologyAc.hpp")
    )
    out_deployment = output / deployment

    def cmake_path(path: Path) -> str:
        return '"${CMAKE_CURRENT_LIST_DIR}/' + _rel(path, cmake_path.base) + '"'

    # Relative to where the files will finally live, which differs from `output` under --check
    cmake_path.base = final_output / deployment / "Top"
    top_cmake = [
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "register_fprime_module(",
        "    AUTOCODER_INPUTS",
        *[f'        "${{CMAKE_CURRENT_LIST_DIR}}/{f.name}"' for f in patched_fpp],
        "    SOURCES",
        *[f"        {cmake_path(s)}" for s in topology_sources],
        '        "${CMAKE_CURRENT_LIST_DIR}/WstsSetup.cpp"',
        "    DEPENDS",
        *[f"        {d}" for d in top_depends],
        ")",
        "# Flight topology code is compiled against the surrogates through this header",
        'target_compile_options(${FPRIME_CURRENT_MODULE} PRIVATE -include "${CMAKE_CURRENT_LIST_DIR}/WstsCompat.hpp")',
        "",
    ]
    (out_top / "CMakeLists.txt").write_text("\n".join(top_cmake))

    cmake_path.base = final_output / deployment
    if generated_main:
        stop = f"    {main_spec.stop};" if main_spec.stop else ""
        (out_deployment / "WstsMain.cpp").write_text(
            MAIN_TEMPLATE.format(declarations=main_spec.declarations.strip(), start=main_spec.start, stop=stop)
        )
    deployment_sources = [f"        {cmake_path(s)}" for s in main_sources]
    if generated_main:
        deployment_sources.append('        "${CMAKE_CURRENT_LIST_DIR}/WstsMain.cpp"')
    deployment_depends = [
        d for d in deployment_registration.get("DEPENDS", [])
        if not d.endswith("_Top") and d not in substituted_modules and "${" not in d
    ]
    deployment_cmake = [
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Top/")',
        "",
        "register_fprime_deployment(",
        "    SOURCES",
        *deployment_sources,
        "    DEPENDS",
        "        ${FPRIME_CURRENT_MODULE}_Top",
        *[f"        {d}" for d in deployment_depends],
        ")",
        "",
    ]
    (out_deployment / "CMakeLists.txt").write_text("\n".join(deployment_cmake))
    for config in ("fprime-gds.yml", "gds.ini"):
        if (deployment_dir / config).is_file():
            shutil.copy(deployment_dir / config, out_deployment / config)

    # Paths are written relative to where the overlay will live so it can be checked in
    def rel(path: Path) -> str:
        return _rel(path, final_output)

    for module in replaced.values():
        final_dir = final_output / module.relative
        (output / module.relative / "CMakeLists.txt").write_text(
            render_replaced_cmake(module, _rel(module.original, final_dir), substituted_modules))
    def cmake_file(path: Path) -> str:
        return '"${CMAKE_CURRENT_LIST_DIR}/' + rel(path) + '"'

    plan.warnings += flight_root.warnings

    redirect: List[str] = []
    if includes["early"]:
        redirect += ["", "# From `cmake.include_early` in the manifest: before any framework or library module exists",
                     *[f"include({cmake_file(file)})" for file in includes["early"]]]
    if replaced or deferred_modules or skipped_dirs or stub_dirs:
        redirect += REDIRECT_TEMPLATE.format(
            originals="\n".join(f'    "${{CMAKE_CURRENT_LIST_DIR}}/{rel(m.original)}"' for m in replaced.values()),
            copies="\n".join(f'    "${{CMAKE_CURRENT_LIST_DIR}}/{m.relative}"' for m in replaced.values()),
            deferred_sets=DEFERRED_SETS.format(
                modules="\n".join(f"    {cmake_file(d)}" for d in deferred_modules)) if deferred_modules else "",
            deferred_check=DEFERRED_CHECK if deferred_modules else "",
            excluded_sets=EXCLUDED_SETS.format(
                modules="\n".join(f"    {cmake_file(d)}" for d in skipped_dirs)) if skipped_dirs else "",
            excluded_check=EXCLUDED_CHECK if skipped_dirs else "",
            stub_sets=STUB_SETS.format(modules="\n".join(f"    {cmake_file(d)}" for d in stub_dirs)) if stub_dirs else "",
            stub_check=STUB_CHECK if stub_dirs else "",
            duplicate_check=DUPLICATE_CHECK,
        ).rstrip("\n").split("\n")
    elif includes["early"]:
        redirect.append("fprime_setup_included_code()")
    own_setup = bool(replaced or deferred_modules or skipped_dirs or stub_dirs or includes["early"])
    if flight_root.defines_late:
        redirect += [
            "",
            "# Carried over from the flight project's CMakeLists.txt, which makes these definitions after F Prime is",
            "# read: they reach the modules added from here on, as in flight",
            f"add_compile_definitions({' '.join(flight_root.defines_late)})",
        ]
    after_modules: List[str] = []
    if deferred_modules:
        after_modules += [
            "",
            "# Configuration overrides of the simulation: registered after the flight project's, so that",
            "# where both replace a file the simulation's is the one built",
            "set(WSTS_ADDING_DEFERRED ON)",
            *[f"add_fprime_subdirectory({cmake_file(d)})" for d in deferred_modules],
        ]
    if stub_dirs:
        after_modules += STUB_VERIFY.rstrip("\n").split("\n")
    if module_flags:
        after_modules += [
            "",
            "# From `cmake.cxx_flags` in the manifest: flags for the C++ sources of one module",
        ]
        for directory, (target, flags) in module_flags.items():
            options = " ".join(f'"$<$<COMPILE_LANGUAGE:CXX>:SHELL:{flag}>"' for flag in flags)
            after_modules += [
                f"if (TARGET {target})",
                f"    get_target_property(WSTS_MODULE_TYPE {target} TYPE)",
                '    if (WSTS_MODULE_TYPE STREQUAL "INTERFACE_LIBRARY")',
                f'        message(WARNING "[WSTS] cmake.cxx_flags: {target} compiles nothing itself; its flags are not used")',
                "    else()",
                f"        target_compile_options({target} PRIVATE {options})",
                "    endif()",
                "else()",
                f'    message(FATAL_ERROR "[WSTS] cmake.cxx_flags in the manifest names {_rel(directory, project_root)}, and the '
                f'build has no target {target} for it. If the module registers under another name, set its flags with '
                'target_compile_options() in a file named under cmake.include.")',
                "endif()",
            ]
    if standin_modules:
        after_modules += [
            "",
            f"# Headers of replaced flight drivers resolve to stand-ins ({STANDIN_DIR}/). A driver module that is",
            "# still part of this build compiles against its own header.",
            f"foreach (WSTS_MODULE IN ITEMS {' '.join(sorted(set(standin_modules)))})",
            "    if (TARGET ${WSTS_MODULE})",
            "        get_target_property(WSTS_MODULE_TYPE ${WSTS_MODULE} TYPE)",
            '        if (NOT WSTS_MODULE_TYPE STREQUAL "INTERFACE_LIBRARY")',
            f"            target_compile_definitions(${{WSTS_MODULE}} PRIVATE {FLIGHT_DRIVER_MACRO})",
            "        endif()",
            "    endif()",
            "endforeach()",
        ]
    if includes["late"]:
        after_modules += [
            "",
            "# From `cmake.include` in the manifest: every flight, library and simulation module exists by now",
            *[f"include({cmake_file(file)})" for file in includes["late"]],
            "",
        ]

    defines = ["WSTS_SIM=1", *manifest.defines]
    root_cmake = [
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "#",
        f"# Simulated build of {topology.name}. Flight components are built from the original",
        "# project; hardware-facing instances are replaced by WSTS surrogates.",
        "cmake_minimum_required(VERSION 3.26)",
        f"project({deployment}Sim VERSION 1.0.0 LANGUAGES C CXX)",
        "",
        f"add_compile_definitions({' '.join(defines)})",
        *(["# Carried over from the flight project's CMakeLists.txt",
           f"add_compile_definitions({' '.join(flight_root.defines_early)})"] if flight_root.defines_early else []),
        *(["# Headers that stand in for those of replaced flight drivers: found before the originals",
           f'include_directories(BEFORE "${{CMAKE_CURRENT_LIST_DIR}}/{STANDIN_DIR}")'] if standin_files else []),
        *(["# From `cmake.cxx_flags` in the manifest: for every C++ source of this build",
           f'string(APPEND CMAKE_CXX_FLAGS " {" ".join(global_flags)}")'] if global_flags else []),
        *(["# From `cmake.cache` in the manifest. Set before F Prime reads the libraries; a value given on the",
           "# command line (-D) takes precedence.",
           *[f'set({name} "{";".join(values)}" CACHE STRING "Set by fprime-wsts from cmake.cache in the manifest")'
             for name, values in manifest.cmake_cache.items()]] if manifest.cmake_cache else []),
        *(["# Carried over from the flight project's CMakeLists.txt, which sets them before F Prime is read.",
           "# `cmake.cache` in the manifest takes precedence, and so does -D on the command line.",
           *[f'set({name} "{";".join(values)}" CACHE STRING "{flight_cmake.CACHE_HELP}")'
             for name, values in flight_root.cache.items()]] if flight_root.cache else []),
        *(["# The framework's and the libraries' modules are added below, once the redirection is set up",
           "set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)"] if own_setup else []),
        f'find_package(FPrime REQUIRED PATHS "${{CMAKE_CURRENT_LIST_DIR}}/{rel(settings.framework_path)}/cmake")',
        *redirect,
        "",
        f'set(WSTS_FLIGHT_PROJECT "${{CMAKE_CURRENT_LIST_DIR}}/{rel(project_root)}")',
        *[
            f'add_fprime_subdirectory("${{WSTS_FLIGHT_PROJECT}}/{_rel(d, project_root)}")'
            if d == project_root or project_root in d.parents
            else f'add_fprime_subdirectory("${{CMAKE_CURRENT_LIST_DIR}}/{rel(d)}")'
            for d in module_dirs
        ],
        *after_modules,
        f'add_fprime_subdirectory("${{CMAKE_CURRENT_LIST_DIR}}/{deployment}")',
        "",
    ]
    (output / "CMakeLists.txt").write_text("\n".join(root_cmake))

    original_ini = (project_root / "settings.ini").read_text()
    options = re.search(r"^default_cmake_options\s*[:=].*(?:\n[ \t]+\S.*)*", original_ini, re.M)
    settings_ini = [
        "; Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "[fprime]",
        f"framework_path: {rel(settings.framework_path)}",
        "project_root: .",
        "library_locations: " + ":".join(rel(p) for p in library_paths),
        "default_toolchain: native",
    ]
    dropped_options: List[str] = []
    settings_variables: Set[str] = set()
    if options:
        # Options that select the flight toolchain or platform do not apply to a host build
        kept_options = []
        for option in options.group(0).split(":", 1)[-1].replace("=", "=", 1).split():
            name = option.split("=", 1)[0]
            if name in manifest.cmake_cache:
                continue  # the manifest's cmake.cache sets it for the simulated build
            settings_variables.add(name)
            if (name.startswith("FPRIME_") and name != "FPRIME_PLATFORM") or name == "CMAKE_BUILD_TYPE":
                kept_options.append(option)
            else:
                dropped_options.append(option)
        if kept_options:
            settings_ini.append("default_cmake_options: " + "\n    ".join(kept_options))
    (output / "settings.ini").write_text("\n".join(settings_ini) + "\n")

    reused = {
        "topology": [str(s) for s in topology_sources],
        "main": [str(s) for s in main_sources] + (["WstsMain.cpp (generated)"] if generated_main else []),
    }
    report = build_report(plan, model, manifest, libraries, [_rel(d, project_root) for d in module_dirs], settings, reused)
    # The report is part of the checked-in output: keep machine-specific paths out of it
    report["libraries"] = {k: [rel(Path(p)) for p in v] for k, v in libraries.items()}
    # The keys below appear only when used, so that other reports are unchanged
    configuration_only = [
        {"library": rel(library), "supplies": override.name, "file": rel(supplied),
         "overridden_by": _rel(module, project_root) if owner(module / "CMakeLists.txt") == project_root else rel(module)}
        for library, entries in kept_for_config.items() if library not in used_libraries
        for override, supplied, module in entries
    ]
    if configuration_only:
        report["libraries"]["kept_for_configuration"] = configuration_only
    if order.nested:
        report["nested_modules"] = [
            {"module": _rel(child, project_root), "added_by": _rel(parent, project_root), "in": _rel(cmake, project_root)}
            for child, (parent, cmake) in order.nested.items()
        ]
    if driver_headers:
        report["driver_headers"] = {"directory": STANDIN_DIR, "components": driver_headers}
    if flight_root:
        report["flight_cmake"] = {
            "file": rel(flight_root.file), "carried_over": flight_root.carried, "not_carried_over": flight_root.not_carried}
    if skipped_dirs:
        report["excluded_modules"] = [rel(d) for d in skipped_dirs]
    # ---- What the build takes from outside the project ----
    users = module_users(model, plan, topology, owner)
    sdk_modules: Dict[Path, List[str]] = {}
    for f in [*closure, *[f for module in replaced.values() for f in module.files]]:
        root = owner(f)
        if root != project_root and root not in kept_libraries:
            continue
        directory = module_dir(f, root)
        if directory is not None and directory not in excluded:
            sdk_modules.setdefault(directory, users.get(directory, []))
    for root, tree in trees.items():
        if root == project_root or root in kept_libraries:
            for directory in tree.directories:
                sdk_modules.setdefault(directory, users.get(directory, []))
    known_variables = {**{name: [] for name in settings_variables}, **flight_root.cache, **manifest.cmake_cache}
    sdk_found: List[flight_cmake.SdkDependency] = []
    plan.warnings += sdk_warnings(sdk_modules, owner, project_root, known_variables, stub_dirs, sdk_found)

    # BUILD_TESTING for the whole build
    testing_on = False
    if _is_on(manifest.cmake_cache.get("BUILD_TESTING")):
        testing_on = True
        plan.warnings.append(testing_warning("cmake.cache in the manifest sets it", True, len(variants)))
    elif "BUILD_TESTING" not in manifest.cmake_cache and _is_on(flight_root.cache.get("BUILD_TESTING")):
        testing_on = True
        plan.warnings.append(testing_warning(
            f"{rel(project_root / 'CMakeLists.txt')} sets it before F Prime is read, and plain values of that file "
            "are carried over (cmake: {cache: {BUILD_TESTING: OFF}} in the manifest keeps it off)", True, len(variants)))
    elif any(option.split("=", 1)[0].split(":")[0] == "BUILD_TESTING" and _is_on(option.split("=", 1)[1:])
             for option in dropped_options):
        plan.warnings.append(testing_warning(
            "The flight project's default_cmake_options (settings.ini) set BUILD_TESTING to ON", False, len(variants)))
    # cmake.cache entries that the generated set(... CACHE ...) cannot change
    configure_options: List[str] = []
    for name, values in manifest.cmake_cache.items():
        if not SET_BY_PROJECT.fullmatch(name):
            continue
        value = ";".join(values)
        if COMPILER_FLAGS.fullmatch(name):
            plan.warnings.append(
                f"cmake.cache sets {name}, which has no effect: project() has put that variable in the cache before "
                "the manifest's entries are read, and an entry does not replace a value that is there. For flags of "
                f"the host's C++ compiler use\n  cmake:\n    cxx_flags: [{', '.join(repr(flag) for flag in re.split(r' +(?=-)', value.strip()))}]\n"
                "which the generated project applies itself (one list entry per flag, with its argument). "
                "fprime-util cannot pass a value with a space in it on the command line.")
        else:
            option = f"-D{name}={value}"
            passable = " " not in value
            plan.warnings.append(
                f"cmake.cache sets {name}, which has no effect: project() has put that variable in the cache before "
                "the manifest's entries are read. "
                + (f"Pass it when configuring: fprime-util generate {option} (directly, without '--': after '--' "
                   "fprime-util takes it for the name of a toolchain)." if passable else
                   "fprime-util cannot pass a value with a space in it; configure with cmake directly, or set it in "
                   "a file named under cmake.include_early with set(... CACHE ... FORCE)."))
            if passable:
                configure_options.append(option)
    if configure_options:
        report["configure_options"] = configure_options

    def shown_module(path: Path) -> str:
        return _rel(path, project_root)

    if variants:
        report["testing_variants"] = [
            {"module": shown_module(directory), "selected": directory in stub_dirs or testing_on,
             "instances": users.get(directory, []), **branches.describe()}
            for directory, branches in variants.items()
        ]
    if stub_dirs:
        report["stub_variants"] = [shown_module(d) for d in stub_dirs]
    if manifest.cxx_flags:
        report["cxx_flags"] = {
            **({"all modules": global_flags} if global_flags else {}),
            **{shown_module(d): flags for d, (_, flags) in module_flags.items()}}

    # Text only, so that the report is the same on every machine: `fprime-wsts check-host` asks the host
    sanitizers = [
        name for name, variable in (("address", "ENABLE_SANITIZER_ADDRESS"), ("leak", "ENABLE_SANITIZER_LEAK"),
                                    ("undefined", "ENABLE_SANITIZER_UNDEFINED_BEHAVIOR"), ("thread", "ENABLE_SANITIZER_THREAD"))
        if (_is_on(manifest.cmake_cache[variable]) if variable in manifest.cmake_cache
            else testing_on and name != "thread")
    ]
    scanned: Dict[str, dict] = {"libraries": {}, "packages": {}}
    search: Dict[str, List[str]] = {"include_directories": [], "link_directories": []}
    host_sources: List[Tuple[Path, str]] = [(s, f"{deployment}/Top") for s in topology_sources]
    host_sources += [(s, deployment) for s in main_sources]
    extra = flight_cmake.BuildTree()
    for file in [*includes["early"], *includes["late"]]:
        flight_cmake.read_tree(file, stub_dirs, excluded, extra)
    for tree, module_of in [*[(t, lambda f: shown_module(f.parent)) for t in trees.values()],
                            (extra, lambda f: f"{f.name} (the simulation's CMake)")]:
        host_sources += hostcheck.module_sources(tree, module_of) if tree is not extra else []
        found = hostcheck.scan_cmake(tree, module_of, rel)
        for name, needs in found["libraries"].items():
            scanned["libraries"].setdefault(name, []).extend(needs)
        for name, package in found["packages"].items():
            known = scanned["packages"].setdefault(name, {"required": False, "components": [], "needed_by": []})
            known["required"] = known["required"] or package["required"]
            known["components"] += [c for c in package["components"] if c not in known["components"]]
            known["needed_by"] += package["needed_by"]
        for key in search:
            search[key] += [d for d in found[key] if d not in search[key]]
    followed = {project_root, *kept_libraries, *sim_roots}
    index = hostcheck.HeaderIndex(
        [project_root, *[l for l in flight_libraries if l in kept_libraries], *sim_roots, settings.framework_path, TOOLKIT_ROOT],
        walked=[project_root, *[l for l in flight_libraries if l in kept_libraries], *sim_roots],
        fixed=[settings.framework_path, TOOLKIT_ROOT / "Wsts"])
    defined = {d.split("=", 1)[0] for d in [*manifest.defines, *flight_root.defines_early, *flight_root.defines_late]}
    compiler = hostcheck.flight_compiler(
        settings.default_toolchain, [project_root, *flight_libraries, settings.framework_path], rel)
    module_list = [
        {"module": shown_module(directory), "instances": users.get(directory, []),
         **({"has_stub_variant": True} if directory in variants and variants[directory].when_on else {}),
         **({"stub_variant": True} if directory in stub_dirs else {})}
        for tree in trees.values() for directory in tree.directories
    ]
    report["host_requirements"] = {
        "flight_project": rel(project_root),
        "flight_toolchain": settings.default_toolchain,
        **({"flight_compiler": compiler} if compiler else {}),
        "modules": module_list,
        "system_headers": hostcheck.scan_headers(host_sources, index, lambda path: owner(path) in followed, defined, rel),
        "libraries": scanned["libraries"],
        "packages": scanned["packages"],
        "sdk_paths": [
            {"entry": d.entry, "module": shown_module(d.module), "file": rel(d.module / "CMakeLists.txt"),
             "problem": d.problem.replace(str(d.module) + "/", ""), "variables": d.variables,
             **({"path": rel(Path(d.path)) if owner(Path(d.path)) is not None else d.path} if d.path else {})}
            for d in sdk_found
        ],
        "search": {**search, "cxx_flags": global_flags},
        "cache": manifest.cmake_cache,
        "sanitizers": sanitizers,
        "ignore": manifest.host_ignore,
        **({"not_read": unread_roots} if unread_roots else {}),
    }
    report["warnings"] = plan.warnings
    report["dropped_cmake_options"] = dropped_options
    if sim_root is not None:
        report["simulation_project"] = rel(sim_root)
    # Only when there are any, so that reports of deployments without them are unchanged
    if replaced:
        report["replaced_modules"] = [
            {"module": module_name(m.original, [settings.framework_path, *settings.library_locations, project_root]),
             "original": rel(m.original), "copy": m.relative, "files": sorted(f.name for f in m.files)}
            for m in replaced.values()
        ]
    if plan.sim.added:
        report["dictionary"]["added_by_simulators"] = report["simulators"]["dictionary_items_added"]
        report["capacity"] = {
            key: ({**value, "source": rel(Path(value["source"]))} if value.get("source") else value)
            for key, value in capacity.items()
        }
    flight_overrides = [o for o in config_overrides if not in_simulation(o.winner)]
    simulation_overrides = [o for o in config_overrides + header_overrides if in_simulation(o.winner)]
    if flight_overrides:
        report["config_overrides"] = [
            {"file": o.name, "uses": rel(o.winner), "replaces": [rel(r) for r in o.replaced]} for o in flight_overrides
        ]
    # The keys below appear only when used, so that other reports are unchanged
    if simulation_overrides:
        report["simulation_config_overrides"] = [
            {"file": o.name, "uses": rel(o.winner), "replaces": [rel(r) for r in o.replaced]} for o in simulation_overrides
        ]
    if sim_libraries:
        report["simulation_libraries"] = [rel(library) for library in sim_libraries]
    if includes["early"] or includes["late"]:
        report["cmake_includes"] = {
            "before_libraries": [rel(f) for f in includes["early"]], "after_modules": [rel(f) for f in includes["late"]]}
    if manifest.run_env:
        # A value that starts with ./ or ../ is a file of the simulation project: kept
        # relative to this directory, and made absolute when the deployment is started
        env, paths = {}, []
        for name, value in manifest.run_env.items():
            if value.startswith(("./", "../")):
                env[name] = rel(manifest.resolve_sim(value, project_root))
                paths.append(name)
            else:
                env[name] = value
        report["run"] = {"env": env, "env_paths": paths}
    report["reused_sources"] = {
        k: [rel(Path(p)) if os.path.isabs(p) else p for p in v] for k, v in reused.items()
    }
    (output / "wsts-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "wsts-report.md").write_text(render_report(report))
    return Result(output, report)
