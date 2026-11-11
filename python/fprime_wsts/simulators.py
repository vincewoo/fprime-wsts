"""Device simulators written as F Prime components.

A simulation project may hold simulators that are ordinary components, with their own
commands, telemetry, parameters and scheduling. The manifest's `simulators` section adds
their instances to the simulated topology and connects them to flight instances;
`devices` puts a stock adapter instance (Wsts.BusByteStreamDevice) on a bus endpoint, so
that a simulator talks to flight software through the bus like any device model.

This module plans those additions. It changes nothing in the flight topology's own
connections: every port number the flight model resolved stays as it is, and
`verify_flight_connections` checks that against the patched model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import fpp
from .fpp import WstsError
from .manifest import ConnectionSpec, DeviceSpec, Manifest

# Stock adapters: component -> the kind of bus endpoint it is the device on
DEVICE_ADAPTERS = {"Wsts.BusByteStreamDevice": "bytestream"}
PING_PORT = "Svc.Ping"
SCHEDULING_PORTS = {"Svc.Sched", "Svc.Cycle"}
SIMULATOR_GRAPH = "WstsSimulators"

# Identifiers that have to be written with a leading `$` in FPP
FPP_KEYWORDS = set("""
    F32 F64 I16 I32 I64 I8 U16 U32 U64 U8 action active activity always array assert async at base block bool
    change choice command component connections constant container cpu default diagnostic dictionary do drop else
    enter entry enum event every exit external false fatal format get group guard guarded health high hook id if
    import include initial input instance interface internal locate low machine match module omit on opcode
    orange output packet packets param passive phase port priority private product queue queued raw record recv
    red ref reg request resp save send serial set severity signal size stack state string struct sync system
    telemetry text throttle time topology true type unmatched update warning with yellow
""".split())


def fpp_name(qualified: str, topology: Optional[fpp.Topology] = None) -> str:
    """A qualified name as FPP source, with keywords escaped.

    Inside `topology` a name from the topology's own module is written relative to that
    module: a topology that shares its module's name (`module Demo { topology Demo }`)
    would otherwise capture the first part of the qualified name.
    """
    if topology is not None and topology.module and qualified.startswith(topology.module + "."):
        qualified = qualified[len(topology.module) + 1:]
    return ".".join("$" + part if part in FPP_KEYWORDS else part for part in qualified.split("."))


@dataclass
class AddedInstance:
    name: str  # qualified instance name
    component: str
    source: str  # "manifest" for one listed under simulators.instances, else the topology that brings it
    instance: fpp.Instance

    def base_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]


@dataclass
class PlannedConnection:
    spec: ConnectionSpec
    from_instance: str
    from_port: str
    from_index: Optional[int]  # as it will be written; None leaves a single port to FPP
    to_instance: str
    to_port: str
    to_index: Optional[int]
    from_written: str  # the end as FPP source: the instance's port, or a port a subtopology exports
    to_written: str
    port_type: Optional[str]
    assigned: bool = False  # the from index was chosen by the generator

    def fpp(self) -> str:
        source = self.from_written + (f"[{self.from_index}]" if self.from_index is not None else "")
        target = self.to_written + (f"[{self.to_index}]" if self.to_index is not None else "")
        return f"{source} -> {target}"


@dataclass
class PlannedDevice:
    spec: DeviceSpec
    instance: str
    component: str
    kind: str
    endpoint: str


@dataclass
class SimulatorPlan:
    added: List[AddedInstance] = field(default_factory=list)
    topologies: List[str] = field(default_factory=list)  # subtopologies added as a whole
    instances: List[str] = field(default_factory=list)  # instances added one by one
    connections: List[PlannedConnection] = field(default_factory=list)
    devices: List[PlannedDevice] = field(default_factory=list)
    # The flight health pattern to pin, and the instances it reaches in the flight topology
    health_pattern: Optional[fpp.Pattern] = None
    health_targets: List[str] = field(default_factory=list)
    unpinged: List[str] = field(default_factory=list)  # added instances with ping ports
    notes: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.devices)

    @property
    def names(self) -> List[str]:
        return [a.name for a in self.added]


def id_range(instance: fpp.Instance) -> Optional[Tuple[int, int]]:
    """IDs an instance uses, or None when its component has no dictionary items"""
    if instance.max_id < instance.base_id:
        return None
    return instance.base_id, instance.max_id


def collect_added(model: fpp.Model, manifest: Manifest, topology: fpp.Topology) -> SimulatorPlan:
    """The instances `simulators` adds: those listed, and those of the listed subtopologies"""
    plan = SimulatorPlan()
    spec = manifest.simulators
    seen: Dict[str, str] = {}

    def add(name: str, source: str) -> None:
        if name in topology.instances:
            raise WstsError(
                f"simulators: instance {name} is already in the flight topology {topology.name}. 'simulators' adds "
                "instances; to replace a flight instance use 'substitute'."
            )
        if name in seen:
            raise WstsError(f"simulators: instance {name} is added twice (by {seen[name]} and by {source})")
        seen[name] = source
        instance = model.instances[name]
        plan.added.append(AddedInstance(name, instance.component, source, instance))

    for name in spec.topologies:
        if name not in model.topologies:
            known = ", ".join(sorted(t for t in model.topologies if t != topology.name)) or "none"
            raise WstsError(
                f"simulators.topologies names '{name}', which is not a topology in the FPP model. Give the "
                f"qualified name (<module>.<topology>). Topologies found: {known}"
            )
        if name == topology.name:
            raise WstsError(f"simulators.topologies names the flight topology {name} itself")
        plan.topologies.append(name)
        for instance in model.topologies[name].instances:
            add(instance, name)
    for name in spec.instances:
        if name not in model.instances:
            raise WstsError(
                f"simulators.instances names '{name}', for which there is no instance definition in the FPP model. "
                "Give the qualified name (<module>.<instance>) of an instance defined in the simulation project."
            )
        plan.instances.append(name)
        add(name, "manifest")
    return plan


def check_ids(sim: SimulatorPlan, model: fpp.Model, topology: fpp.Topology, wsts: List[Tuple[str, int, int]]) -> None:
    """Every added instance must have IDs of its own. `wsts` lists (name, first, last) of
    the WSTS instances."""
    others: List[Tuple[str, str, int, int]] = []
    for name in topology.instances:
        span = id_range(model.instances[name])
        if span:
            others.append((name, "flight instance", *span))
    others += [(name, "WSTS instance", first, last) for name, first, last in wsts]
    for added in sim.added:
        span = id_range(added.instance)
        if span is None:
            continue
        for name, what, first, last in others:
            if span[0] <= last and first <= span[1]:
                raise WstsError(
                    f"simulator instance {added.name} uses IDs {span[0]:#x}..{span[1]:#x}, which overlap those of "
                    f"{what} {name} ({first:#x}..{last:#x}). Flight IDs do not move: give {added.name} a base id "
                    "that is free in its instance definition"
                    + (", or set control.base_id in the manifest to move the WSTS instances." if what.startswith("WSTS")
                       else ".")
                )
        others.append((added.name, "simulator instance", *span))


class _Ends:
    """Resolves the ends of manifest connections against the flight topology plus the
    added instances"""

    def __init__(self, model: fpp.Model, topology: fpp.Topology, sim: SimulatorPlan, component_of: Dict[str, str]):
        self.model = model
        self.topology = topology
        self.sim = sim
        self.component_of = component_of  # for substituted flight instances: the surrogate
        self.instances = topology.instances + sim.names
        # Subtopologies whose exported ports can be named: the added ones, and any whose
        # instances are all in the flight topology (the ones it imports)
        flight = set(topology.instances)
        self.topologies = list(sim.topologies) + [
            name for name, other in model.topologies.items()
            if name != topology.name and name not in sim.topologies and other.exported and set(other.instances) <= flight
        ]

    def component(self, instance: str) -> Optional[fpp.Component]:
        return self.model.components.get(self.component_of.get(instance, self.model.instances[instance].component))

    def resolve(self, text: str, spec: ConnectionSpec) -> Tuple[str, str, str]:
        """(instance, port, as written in FPP) for `<instance>.<port>` or `<topology>.<exported port>`"""
        from .generate import resolve_instance

        owner, _, port = text.rpartition(".")
        where = f"simulators.connections '{spec.text}'"
        try:
            instance = resolve_instance(owner, self.instances)
        except WstsError as error:
            raise WstsError(f"{where}: {error}") from None
        if instance is not None:
            component = self.component(instance)
            if component is None:
                raise WstsError(f"{where}: the component of {instance} was not found in the FPP model")
            if port not in component.ports:
                general = sorted(p.name for p in component.ports.values() if p.kind != "Special")
                raise WstsError(f"{where}: {instance} ({component.name}) has no port '{port}'. Its ports: {', '.join(general)}")
            return instance, port, f"{fpp_name(instance, self.topology)}.{fpp_name(port)}"
        matches = [name for name in self.topologies if name == owner or name.endswith("." + owner)]
        if len(matches) > 1:
            raise WstsError(f"{where}: '{owner}' is ambiguous: it matches topologies {', '.join(sorted(matches))}")
        if matches:
            exported = self.model.topologies[matches[0]].exported
            if port not in exported:
                raise WstsError(
                    f"{where}: topology {matches[0]} exports no port '{port}'. It exports: "
                    f"{', '.join(sorted(exported)) or 'none'}"
                )
            return (*exported[port], f"{fpp_name(matches[0], self.topology)}.{fpp_name(port)}")
        raise WstsError(
            f"{where}: '{owner}' is neither an instance of the topology (flight or added by 'simulators') nor a "
            "subtopology that exports ports. Use the qualified instance name."
        )


def plan_connections(sim: SimulatorPlan, model: fpp.Model, manifest: Manifest, topology: fpp.Topology,
                     component_of: Dict[str, str], renamed: Dict[Tuple[str, str], str]) -> None:
    """Resolve the manifest's connections and give each one that leaves a port array a
    port number that nothing in the flight topology uses.

    `renamed` maps (substituted instance, surrogate port) to the flight port it stands for,
    so that connections already on the flight port count as taken.
    """
    ends = _Ends(model, topology, sim, component_of)
    added = set(sim.names)

    # Port numbers in use on output ports: the flight topology's, and the added subtopologies' own
    used: Dict[Tuple[str, str], Set[int]] = {}
    for source in [topology] + [model.topologies[name] for name in sim.topologies]:
        for numbered in source.numbered:
            used.setdefault((numbered.from_instance, numbered.from_port), set()).add(numbered.from_index)

    for spec in manifest.simulators.connections:
        where = f"simulators.connections '{spec.text}'"
        from_instance, from_port, from_written = ends.resolve(spec.source, spec)
        to_instance, to_port, to_written = ends.resolve(spec.target, spec)
        if from_instance not in added and to_instance not in added:
            raise WstsError(
                f"{where}: neither end is an instance added by 'simulators'. Connections between flight instances "
                "belong to the flight topology and are not changed here."
            )
        source = ends.component(from_instance).ports[from_port]
        target = ends.component(to_instance).ports[to_port]
        if source.kind == "Special" or target.kind == "Special":
            raise WstsError(f"{where}: command, event, telemetry, parameter and time ports are connected by the "
                            "topology's pattern specifiers, not one by one")
        if source.is_input or not target.is_input:
            raise WstsError(
                f"{where}: a connection goes from an output port to an input port; "
                f"{from_instance}.{from_port} is an {'input' if source.is_input else 'output'} port and "
                f"{to_instance}.{to_port} is an {'input' if target.is_input else 'output'} port"
            )
        if source.type and target.type and source.type != target.type:
            raise WstsError(f"{where}: port types differ: {from_instance}.{from_port} is {source.type}, "
                            f"{to_instance}.{to_port} is {target.type}")
        if spec.target_index is not None and spec.target_index >= target.size:
            raise WstsError(f"{where}: {to_instance}.{to_port} has {target.size} port(s); there is no [{spec.target_index}]")

        key = (from_instance, renamed.get((from_instance, from_port), from_port))
        taken = used.setdefault(key, set())
        index = spec.source_index
        assigned = False
        if index is not None:
            if index >= source.size:
                raise WstsError(f"{where}: {from_instance}.{from_port} has {source.size} port(s); there is no [{index}]")
            if index in taken:
                raise WstsError(f"{where}: {from_instance}.{from_port}[{index}] is already connected. Leave the "
                                "number out and a free port is chosen.")
        else:
            free = [i for i in range(source.size) if i not in taken]
            if not free:
                if source.size == 1:
                    raise WstsError(
                        f"{where}: output port {from_instance}.{from_port} is already connected, and an output port "
                        "drives one input. A simulator that needs the same signal has to be fed from another port, "
                        "or through a component that fans it out."
                    )
                sized_by = f" Its size is set by the constant {source.size_constant}" if source.size_constant else ""
                component = ends.component(from_instance)
                raise WstsError(
                    f"{where}: {from_instance} ({component.name}) has no free {from_port} port: all {source.size} "
                    f"are connected.{sized_by}"
                    + (" (configuration FPP, AcConstants.fpp in a default F Prime project): raise it for the "
                       "simulated build, or connect the simulator to another rate group." if source.size_constant
                       else ". Connect the simulator to another instance.")
                )
            # A port array gets its number written out, so nothing is left for FPP to place
            # among the flight connections
            index = free[0] if source.size > 1 else None
            assigned = source.size > 1
        if index is not None:
            taken.add(index)
        else:
            taken.add(0)
        sim.connections.append(PlannedConnection(
            spec, from_instance, from_port, index, to_instance, to_port, spec.target_index,
            from_written, to_written, source.type or target.type, assigned))


def check_parameters(sim: SimulatorPlan, model: fpp.Model, topology: fpp.Topology) -> None:
    """A component with parameters asserts in loadParameters() when its parameter ports are
    not connected. In a flight topology with `param connections` the pattern connects the
    added instances too; without one, the simulation has to bring its own database."""
    if any(p.kind == "Param" for p in topology.patterns):
        return
    connected = {
        (c.from_instance, c.from_port)
        for name in sim.topologies for c in model.topologies[name].connections
    } | {(c.from_instance, c.from_port) for c in sim.connections}
    for added in sim.added:
        component = model.components.get(added.component)
        if component is None or not component.params:
            continue
        getter = next((p.name for p in component.ports.values() if p.special == "ParamGet"), None)
        if getter and (added.name, getter) not in connected:
            raise WstsError(
                f"simulator instance {added.name} ({added.component}) has parameters, and the flight topology "
                f"{topology.name} has no parameter database (no 'param connections'): {added.name}.{getter} would be "
                "unconnected and the component asserts when parameters are loaded. Add a Svc.PrmDb instance to the "
                f"simulation's FPP and connect it there: '{added.base_name()}.{getter} -> prmDb.getPrm' and the "
                "parameter set port to 'prmDb.setPrm'."
            )


def plan_health(sim: SimulatorPlan, model: fpp.Model, topology: fpp.Topology) -> None:
    """Health ping ports are numbered by instance name, over every instance the health
    pattern reaches. An added instance with ping ports would renumber the flight ones,
    and the flight ping table is indexed by port number. So the pattern is pinned to the
    instances it reaches in the flight topology, and added instances are not pinged."""
    for added in sim.added:
        component = model.components.get(added.component)
        if component and any(port.type == PING_PORT for port in component.ports.values()):
            sim.unpinged.append(added.name)
    if not sim.unpinged:
        return
    pattern = next((p for p in topology.patterns if p.kind == "Health"), None)
    if pattern is None or pattern.explicit_targets:
        return  # no health pattern, or flight already lists its targets: nothing reaches the added instances
    here = (pattern.file, pattern.line, pattern.col)
    targets = set()
    for connection in topology.connections:
        if connection.from_loc == here:
            targets |= {connection.from_instance, connection.to_instance}
    targets.discard(pattern.source)
    sim.health_pattern = pattern
    sim.health_targets = sorted(targets)


def plan_devices(sim: SimulatorPlan, model: fpp.Model, manifest: Manifest, topology: fpp.Topology,
                 endpoints: Dict[str, Tuple[str, bool]], linked: Dict[str, str]) -> None:
    """`endpoints`: endpoint -> (instance bound to it, whether it is a byte stream);
    `linked`: endpoint -> the system link that ends there"""
    from .generate import resolve_instance

    instances = topology.instances + sim.names
    served: Dict[str, str] = {}
    for spec in manifest.devices:
        where = f"devices: {spec.instance}"
        instance = resolve_instance(spec.instance, instances)
        if instance is None:
            raise WstsError(
                f"{where}: there is no such instance in the simulated topology. A device instance is defined in the "
                "simulation project's FPP and added with 'simulators' (listed in simulators.instances, or part of a "
                f"topology in simulators.topologies). Added instances: {', '.join(sim.names) or 'none'}"
            )
        component = model.instances[instance].component
        kind = DEVICE_ADAPTERS.get(component)
        if kind is None:
            raise WstsError(
                f"{where}: {instance} is a {component}, which is not a bus device adapter. Connect the simulator to an "
                f"instance of {' or '.join(sorted(DEVICE_ADAPTERS))} and name that instance here."
            )
        if spec.endpoint not in endpoints:
            raise WstsError(
                f"{where}: no surrogate is bound to endpoint '{spec.endpoint}'. Endpoints: "
                f"{', '.join(sorted(endpoints)) or 'none'}"
            )
        bound, is_byte_stream = endpoints[spec.endpoint]
        if kind == "bytestream" and not is_byte_stream:
            raise WstsError(
                f"{where}: endpoint '{spec.endpoint}' ({bound}) is not a byte stream, and {component} is a byte "
                "stream device. Adapters for I2C, SPI and GPIO endpoints do not exist yet: use a C++ device model there."
            )
        for model_spec in manifest.models:
            if model_spec.endpoint == spec.endpoint and model_spec.kind == kind:
                raise WstsError(
                    f"{where}: endpoint '{spec.endpoint}' already has the model '{model_spec.name}'. A byte stream "
                    "endpoint has one device: remove the model, or the device."
                )
        if spec.endpoint in linked:
            raise WstsError(f"{where}: endpoint '{spec.endpoint}' is an end of the system link '{linked[spec.endpoint]}'; "
                            "the other element is the device there")
        if spec.endpoint in served:
            raise WstsError(f"{where}: endpoint '{spec.endpoint}' is already served by {served[spec.endpoint]}")
        if instance in served.values():
            raise WstsError(f"{where}: {instance} is listed twice; an adapter serves one endpoint")
        served[spec.endpoint] = instance
        sim.devices.append(PlannedDevice(spec, instance, component, kind, spec.endpoint))


def render_block(sim: SimulatorPlan, topology: fpp.Topology) -> str:
    """What is added at the end of the topology's body"""
    lines = ["", "    # Simulator instances and their connections (generated from `simulators` in the manifest)"]
    lines += [f"    instance {fpp_name(name, topology)}" for name in sim.topologies + sim.instances]
    if sim.connections:
        lines += ["", f"    connections {SIMULATOR_GRAPH} {{"]
        lines += [f"      {connection.fpp()}" for connection in sim.connections]
        lines.append("    }")
    return "\n".join(lines) + "\n\n  "


def verify_flight_connections(flight: fpp.Topology, patched: fpp.Topology, ignore_instances: Set[str]) -> None:
    """Every connection of the flight topology keeps its port numbers in the patched one.

    Connections made by the command, event, telemetry, parameter and time patterns are
    left out: their port numbers follow the instance list and nothing is indexed by them.
    Health connections are checked, because the flight ping table is.
    """
    loose = {(p.file, p.line, p.col) for p in flight.patterns if p.kind != "Health"}
    skip = {
        (c.from_instance, c.from_port, c.to_instance, c.to_port)
        for c in flight.connections if c.from_loc in loose
    }
    after = set(patched.numbered)
    moved = []
    for numbered in flight.numbered:
        key = (numbered.from_instance, numbered.from_port, numbered.to_instance, numbered.to_port)
        if key in skip or numbered in after:
            continue
        if numbered.from_instance in ignore_instances or numbered.to_instance in ignore_instances:
            continue  # ports of a surrogate with a port mapping are renamed or dropped on purpose
        now = [n for n in after if (n.from_instance, n.from_port, n.to_instance, n.to_port) == key]
        was = f"{numbered.from_instance}.{numbered.from_port}[{numbered.from_index}] -> " \
              f"{numbered.to_instance}.{numbered.to_port}[{numbered.to_index}]"
        moved.append(was + (f" is now [{now[0].from_index}] -> [{now[0].to_index}]" if now else " is gone"))
    if moved:
        raise WstsError(
            "adding the simulator instances changed port numbers of flight connections, which flight code may "
            "depend on:\n  " + "\n  ".join(sorted(moved)[:10])
            + "\nThis is a limit of fprime-wsts: please report the topology that shows it."
        )


def command_count(component: fpp.Component) -> int:
    """Opcodes a component registers: its commands, and SET and SAVE for each parameter"""
    return len(component.commands) + 2 * len(component.params)


def check_capacity(sim: SimulatorPlan, model: fpp.Model, topology: fpp.Topology, components: List[str],
                   tables: Dict[str, Optional[Tuple[int, str]]]) -> Dict[str, dict]:
    """Whether the flight command dispatcher and parameter database have room for what the
    simulation adds. `components` are the components of every instance of the simulated
    topology; `tables` gives the size of each table and where it was read, when known.
    Returns the figures for the report."""
    found = [model.components[name] for name in components if name in model.components]
    figures: Dict[str, dict] = {}

    # Command registration ports: one per instance with commands
    pattern = next((p for p in topology.patterns if p.kind == "Command"), None)
    if pattern is not None and pattern.source in model.instances:
        dispatcher = model.components.get(model.instances[pattern.source].component)
        commanded = sum(1 for component in found if command_count(component))
        ports = [p for p in (dispatcher.ports.values() if dispatcher else []) if p.type == "Fw.Cmd" and not p.is_input]
        if ports and commanded > ports[0].size:
            raise WstsError(
                f"the simulated topology has {commanded} instances with commands, and {pattern.source} "
                f"({dispatcher.name}) has {ports[0].size} command ports"
                + (f", set by the constant {ports[0].size_constant}" if ports[0].size_constant else "")
                + ". Raise it in the configuration FPP for the simulated build, or add fewer simulator instances."
            )
        if ports:
            figures["command_ports"] = {"used": commanded, "size": ports[0].size, "constant": ports[0].size_constant}

    opcodes = sum(command_count(component) for component in found)
    parameters = sum(len(component.params) for component in found)
    for key, used, what in (("command_table", opcodes, "opcodes"), ("parameter_table", parameters, "parameters")):
        size = tables.get(key)
        figures[key] = {"used": used, "size": size[0] if size else None, "source": size[1] if size else None}
        if size and used > size[0] and key == "command_table":
            raise WstsError(
                f"the simulated topology registers {used} opcodes, and the command dispatcher's table holds "
                f"{size[0]} (CMD_DISPATCHER_DISPATCH_TABLE_SIZE in {size[1]}): it would assert at start-up. The "
                "simulators add to the flight total. Raise the constant for the simulated build with a "
                "configuration override."
            )
        if size and used > size[0]:
            sim.notes.append(
                f"The simulated topology has {used} parameters and the parameter database holds {size[0]} "
                f"(PRMDB_NUM_DB_ENTRIES in {size[1]}): saving all of them would fail."
            )
    return figures
