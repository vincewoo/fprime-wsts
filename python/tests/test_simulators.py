"""Device simulators written as F Prime components: instances and connections added to
the topology, bus device adapters, port mappings, cache variables. Needs the FPP tools."""

import shutil
from pathlib import Path

import pytest
import yaml

from fprime_wsts import fpp
from fprime_wsts import simulators as sims
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[2]
FLIGHT = REPO / "examples" / "subtop-demo"
SIM = REPO / "examples" / "subtop-demo-sim"

needs_fpp = pytest.mark.skipif(
    shutil.which("fpp-to-json") is None or not (REPO / "lib" / "fprime" / "cmake").is_dir(),
    reason="needs the FPP tools on PATH and the lib/fprime submodule",
)


def manifest_of(sim_project: Path = SIM, **changes):
    data = yaml.safe_load((sim_project / "wsts.yaml").read_text())
    data["project"] = str(FLIGHT)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return parse_manifest(data, sim_project / "wsts.yaml")


def run(sim_project: Path, out: Path, **changes):
    return generate(FLIGHT, manifest_of(sim_project, **changes), out)


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    return run(SIM, tmp_path_factory.mktemp("sim") / "out")


@pytest.fixture
def project(tmp_path):
    """A copy of the simulation project that a test may change"""
    copy = tmp_path / "sim"
    shutil.copytree(SIM, copy, ignore=shutil.ignore_patterns("wsts"))
    return copy


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


def analyze_overlay(out: Path, tmp: Path, sim_project: Path = SIM) -> fpp.Topology:
    """The topology the overlay builds, with the port numbers FPP resolves"""
    settings = fpp.read_settings(FLIGHT)
    roots = [settings.framework_path, *settings.library_locations, FLIGHT, REPO / "Wsts", sim_project]
    overrides, _ = fpp.find_config_overrides(roots)
    copies = sorted((out / "SensorLib").rglob("*.fpp"))
    originals = {FLIGHT / "lib" / "sensor-lib" / c.relative_to(out) for c in copies}
    top = sorted((out / "SubtopDemo" / "Top").glob("*.fpp"))
    candidates = [c for c in fpp.select_candidates(fpp.find_fpp_files(roots), settings.framework_path, overrides)
                  if FLIGHT / "SubtopDemo" not in c.parents and c not in originals]
    fpp.locate_defs(tmp, candidates + copies)
    return fpp.analyze(tmp, fpp.depend(tmp, top) + top).topologies["SubtopDemo.SubtopDemo"]


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def base():
    return {"deployment": "D", "simulators": {"instances": ["Sim.a"]}}


def test_manifest_simulators_and_devices():
    manifest = parse_manifest({
        "deployment": "D",
        "simulators": {
            "instances": ["Sim.buffers"], "topologies": ["Sim.Simulator"], "headers": ["Sim/Top/Defs.hpp"],
            "connections": ["rg.RateGroupMemberOut -> Sim.Simulator.schedIn",
                            "Sim.port.$recv[0]->Flight.$health.pingIn [ 2 ]"],
        },
        "devices": [{"instance": "Sim.port", "endpoint": "uart", "options": "chunk=64"}],
        "cmake": {"cache": {"SIM_COMPONENTS": ["A", "B"], "SIM_LEVEL": 2, "SIM_FAST": True}},
        "substitute": [{"instance": "drv", "with": "Sim.Drv", "queue_size": "Cfg.Q", "priority": 5,
                        "ports": {"runIn": "schedIn", "regOut": None, "irqOut": ""}}],
    })
    first, second = manifest.simulators.connections
    assert (first.source, first.source_index, first.target, first.target_index) == \
        ("rg.RateGroupMemberOut", None, "Sim.Simulator.schedIn", None)
    assert (second.source, second.source_index, second.target, second.target_index) == \
        ("Sim.port.recv", 0, "Flight.health.pingIn", 2), "FPP's $ escapes are accepted and dropped"
    assert manifest.devices[0].endpoint == "uart" and manifest.devices[0].options == "chunk=64"
    assert manifest.cmake_cache == {"SIM_COMPONENTS": ["A", "B"], "SIM_LEVEL": ["2"], "SIM_FAST": ["ON"]}
    substitution = manifest.substitutions[0]
    assert substitution.ports == {"runIn": "schedIn", "regOut": None, "irqOut": None}
    assert substitution.attributes == {"queue size": "Cfg.Q", "priority": "5"}


@pytest.mark.parametrize("change, message", [
    ({"simulators": {"instances": ["Sim.a"], "connections": ["a.b to c.d"]}}, "is not of the form"),
    ({"simulators": {"instances": ["Sim.a"], "connections": ["a -> c.d"]}}, "is not of the form"),
    ({"simulators": {"connections": ["a.b -> c.d"]}}, "needs simulators.instances or simulators.topologies"),
    ({"simulators": {"instance": ["Sim.a"]}}, "unknown key"),
    ({"devices": [{"instance": "Sim.a"}]}, "needs 'instance' and 'endpoint'"),
    ({"cmake": {"cache": {"BAD NAME": 1}}}, "not a CMake variable name"),
    ({"cmake": {"cache": {"X": "a;b"}}}, "YAML list"),
    ({"cmake": {"cach": {}}}, "unknown key"),
    ({"substitute": [{"instance": "x", "with": "Y", "port": {}}]}, "unknown key"),
])
def test_manifest_errors(change, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest({**base(), **change})


def test_fpp_names_are_escaped_and_relative_to_the_topology():
    topology = fpp.Topology("Demo.Demo", Path("t.fpp"), 1, 1)
    assert sims.fpp_name("CdhCore.health") == "CdhCore.$health"
    assert sims.fpp_name("Demo.rateGroup1Hz", topology) == "rateGroup1Hz"
    assert sims.fpp_name("Sim.port.recv", topology) == "Sim.$port.$recv"


# ----------------------------------------------------------------------
# Plan and output
# ----------------------------------------------------------------------


@needs_fpp
def test_added_instances_are_in_the_patched_topology(default):
    topology = (default.output / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    flight = (FLIGHT / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    assert "    instance SensorSim.Simulator\n" in topology
    assert "      rateGroup1Hz.RateGroupMemberOut[5] -> SensorSim.Simulator.schedIn\n" in topology
    assert "      rateGroup1Hz.RateGroupMemberOut[6] -> SensorSim.txBuffers.schedIn\n" in topology
    # After everything the flight topology has, and every flight connection as written
    assert topology.index("instance SensorSim.Simulator") > topology.index("connections ComCcsds_CdhCore")
    for line in flight.splitlines():
        if "->" in line:
            assert line in topology
    setup = (default.output / "SubtopDemo" / "Top" / "WstsSetup.cpp").read_text()
    assert setup.index('::SensorLink::uart.wstsBind("uart", "");') < setup.index('::SensorSim::sensorPort.wstsServe("uart", "");')
    compat = (default.output / "SubtopDemo" / "Top" / "WstsCompat.hpp").read_text()
    assert "#include <SensorSim/Top/SubtopologyTopologyDefs.hpp>" in compat
    settings = (default.output / "settings.ini").read_text()
    assert settings.splitlines()[4].endswith("subtop-demo-sim"), "the simulation project is a library of the overlay"


@needs_fpp
def test_flight_numbering_is_unchanged(default, tmp_path):
    """Rate group ports, ping ports and every other flight connection keep their numbers,
    and flight base IDs do not move"""
    flight_out = generate(FLIGHT, parse_manifest(yaml.safe_load((FLIGHT / "SubtopDemo" / "wsts.yaml").read_text())),
                          tmp_path / "flight")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    before = analyze_overlay(flight_out.output, tmp_path / "a")
    after = analyze_overlay(default.output, tmp_path / "b")

    def numbers(topology, port):
        return {(n.from_instance, n.from_index, n.to_instance) for n in topology.numbered if n.from_port == port}

    assert numbers(before, "PingSend") == numbers(after, "PingSend") == {
        ("CdhCore.health", 0, "CdhCore.cmdDisp"), ("CdhCore.health", 1, "CdhCore.events"),
        ("CdhCore.health", 2, "CdhCore.tlmSend"), ("CdhCore.health", 3, "SubtopDemo.rateGroup10Hz"),
        ("CdhCore.health", 4, "SubtopDemo.rateGroup1Hz")}, "SensorSim.prmDb has ping ports and sorts between them"
    assert numbers(before, "RateGroupMemberOut") < numbers(after, "RateGroupMemberOut")
    assert numbers(after, "RateGroupMemberOut") - numbers(before, "RateGroupMemberOut") == {
        ("SubtopDemo.rateGroup1Hz", 5, "SensorSim.sensorSim"), ("SubtopDemo.rateGroup1Hz", 6, "SensorSim.txBuffers")}
    # Everything else the flight topology connects explicitly (pattern-made command,
    # event and telemetry connections aside) is where it was
    explicit = {n for n in before.numbered
                if not n.from_port.lower().startswith(("cmd", "compcmd", "log", "tlm", "time", "prm")) and "Cmd" not in n.to_port}
    assert explicit and explicit <= set(after.numbered)
    assert [s["base_id"] for s in default.report["substitutions"]] == [s["base_id"] for s in flight_out.report["substitutions"]]
    # The WSTS instances go above every ID in use, which now includes the simulators'
    assert flight_out.report["control"]["setup_base_id"] == 0x20022000
    assert default.report["control"]["setup_base_id"] == 0x70004000
    assert default.report["dictionary"]["flight_ids_unchanged"]

    listed = (default.output / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    assert "health connections instance CdhCore.$health {" in listed and "      rateGroup1Hz\n    }" in listed


@needs_fpp
def test_simulator_items_are_reported(default):
    report = default.report
    simulators = report["simulators"]
    assert [(i["instance"], i["component"], i["from"]) for i in simulators["instances"]] == [
        ("SensorSim.sensorSim", "SensorSim.SerialSensorSim", "SensorSim.Simulator"),
        ("SensorSim.txBuffers", "Svc.BufferManager", "SensorSim.Simulator"),
        ("SensorSim.sensorPort", "Wsts.BusByteStreamDevice", "SensorSim.Simulator"),
        ("SensorSim.prmDb", "Svc.PrmDb", "SensorSim.Simulator"),
    ]
    assert simulators["instances"][0]["base_id"] == 0x70000000
    assert simulators["devices"] == [{"instance": "SensorSim.sensorPort", "component": "Wsts.BusByteStreamDevice",
                                      "endpoint": "uart", "kind": "bytestream"}]
    assert simulators["scheduling"] == [
        {"instance": "SensorSim.sensorSim", "port": "schedIn", "driven_by": "SubtopDemo.rateGroup1Hz.RateGroupMemberOut[5]"},
        {"instance": "SensorSim.txBuffers", "port": "schedIn", "driven_by": "SubtopDemo.rateGroup1Hz.RateGroupMemberOut[6]"},
    ]
    assert simulators["health"]["not_pinged"] == ["SensorSim.prmDb"]
    added = report["dictionary"]["added_by_simulators"]
    assert "SensorSim.sensorSim.SET_BIAS" in added["commands"] and "SensorSim.sensorSim.TEMPERATURE" in added["params"]
    assert "SensorSim.sensorSim.QueryReceived" in added["events"] and "SensorSim.sensorSim.ReportsSent" in added["channels"]
    assert not any(item.startswith("SensorSim") for item in report["dictionary"]["added"]["commands"]), \
        "WSTS items and simulator items are listed apart"
    assert not report["dictionary"]["identical_to_flight"]
    assert report["capacity"]["command_table"]["size"] == 150 and report["capacity"]["command_ports"]["size"] == 30
    text = (default.output / "wsts-report.md").read_text()
    assert "## Simulator components" in text and "**Added (simulator components):**" in text
    assert "serves endpoint `uart`" in text and "are not pinged" in text


@needs_fpp
def test_instances_without_a_subtopology(project, tmp_path):
    replace_in(project / "SensorSim" / "SerialSensorSim" / "SerialSensorSim.fpp",
               "    param TEMPERATURE: F32 default 21.5\n", "")
    for port in ("    param get port prmGetOut\n", "    param set port prmSetOut\n"):
        replace_in(project / "SensorSim" / "SerialSensorSim" / "SerialSensorSim.fpp", port, "")
    replace_in(project / "SensorSim" / "Top" / "SensorSim.fpp",
               "    connections Parameters {\n      sensorSim.prmGetOut -> prmDb.getPrm\n      sensorSim.prmSetOut -> prmDb.setPrm\n    }\n", "")
    result = run(project, tmp_path / "out", simulators={
        "instances": ["SensorSim.sensorSim", "SensorSim.txBuffers", "SensorSim.sensorPort"],
        "connections": [
            "rateGroup10Hz.RateGroupMemberOut -> sensorSim.schedIn",
            "sensorPort.ready -> sensorSim.drvConnected",
            "SensorSim.sensorPort.$recv -> sensorSim.drvReceiveIn",
            "sensorSim.drvReceiveReturnOut -> sensorPort.recvReturnIn",
            "sensorSim.drvSendOut -> sensorPort.send",
            "sensorSim.bufferGetOut -> txBuffers.bufferGetCallee",
            "sensorSim.bufferReturnOut -> txBuffers.bufferSendIn",
        ],
    })
    topology = (result.output / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    assert "    instance SensorSim.sensorSim\n    instance SensorSim.txBuffers\n    instance SensorSim.sensorPort\n" in topology
    assert "      rateGroup10Hz.RateGroupMemberOut[4] -> SensorSim.sensorSim.schedIn\n" in topology
    assert "      SensorSim.sensorPort.$recv -> SensorSim.sensorSim.drvReceiveIn\n" in topology
    assert "      SensorSim.sensorSim.drvSendOut -> SensorSim.sensorPort.$send\n" in topology
    assert [i["from"] for i in result.report["simulators"]["instances"]] == ["manifest"] * 3
    assert "health connections instance CdhCore.$health\n" in topology, "no added instance has ping ports"


@needs_fpp
def test_connection_to_a_port_exported_by_a_flight_subtopology(project, tmp_path):
    """A simulator output drives an input that a flight subtopology exports"""
    simulators = yaml.safe_load((SIM / "wsts.yaml").read_text())["simulators"]
    simulators["connections"].append("SensorSim.sensorSim.bufferReturnOut -> ComCcsds.Subtopology.commsBufferSendIn")
    with pytest.raises(WstsError, match=r"already connected, and an output port drives one input"):
        run(project, tmp_path / "out", simulators=simulators)
    simulators["connections"][-1] = "SensorSim.sensorSim.drvSendOut -> SensorLink.Subtopology.uartRun"
    with pytest.raises(WstsError, match=r"port types differ.*Drv.ByteStreamSend.*SensorLink.uart.run is Svc.Sched"):
        run(project, tmp_path / "out", simulators=simulators)
    simulators["connections"][-1] = "rateGroup10Hz.RateGroupMemberOut -> SensorSim.Simulator.nothing"
    with pytest.raises(WstsError, match=r"SensorSim.Simulator exports no port 'nothing'. It exports: schedIn"):
        run(project, tmp_path / "out", simulators=simulators)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


@needs_fpp
def test_id_collision_names_both_instances(project, tmp_path):
    replace_in(project / "SensorSim" / "Top" / "SensorSim.fpp", "base id 0x70000000", "base id 0x06000001")
    with pytest.raises(WstsError) as error:
        run(project, tmp_path / "out")
    message = str(error.value)
    # FPP finds it, naming both instances; the remedy is added
    assert "for instance sensorSim lies inside the ID range" in message and "for instance sensor\n" in message
    assert "Flight IDs do not move: give the simulator instance a base id that is free" in message


@needs_fpp
def test_id_collision_with_the_wsts_instances(project, tmp_path):
    with pytest.raises(WstsError, match=r"(?s)SensorSim.sensorSim.*WSTS instance SubtopDemo.wstsControl.*control.base_id"):
        run(project, tmp_path / "out", control={"base_id": 0x6FFFF000})


@needs_fpp
def test_rate_group_out_of_ports(project, tmp_path):
    simulators = yaml.safe_load((SIM / "wsts.yaml").read_text())["simulators"]
    simulators["connections"] += ["rateGroup1Hz.RateGroupMemberOut -> SensorSim.txBuffers.schedIn"] * 4
    with pytest.raises(WstsError) as error:
        run(project, tmp_path / "out", simulators=simulators)
    message = str(error.value)
    assert "SubtopDemo.rateGroup1Hz (Svc.ActiveRateGroup) has no free RateGroupMemberOut port: all 10 are connected" in message
    assert "ActiveRateGroupOutputPorts" in message and "another rate group" in message


@needs_fpp
@pytest.mark.parametrize("devices, models, message", [
    ([{"instance": "SensorSim.sensorPort", "endpoint": "radio"}], None,
     r"no surrogate is bound to endpoint 'radio'. Endpoints: uart, vxTimer"),
    ([{"instance": "SensorSim.sensorPort", "endpoint": "vxTimer"}], None,
     r"endpoint 'vxTimer' \(SubtopDemo.vxTimer\) is not a byte stream"),
    ([{"instance": "SensorSim.sensorPort", "endpoint": "uart"}],
     [{"name": "scripted", "class": "Wsts::ScriptedByteStream", "endpoint": "uart", "kind": "bytestream"}],
     r"endpoint 'uart' already has the model 'scripted'"),
    ([{"instance": "SensorSim.nobody", "endpoint": "uart"}], None,
     r"devices: SensorSim.nobody: there is no such instance.*Added instances: SensorSim.sensorSim"),
    ([{"instance": "SensorSim.txBuffers", "endpoint": "uart"}], None,
     r"SensorSim.txBuffers is a Svc.BufferManager, which is not a bus device adapter.*Wsts.BusByteStreamDevice"),
    ([{"instance": "sensorPort", "endpoint": "uart"}, {"instance": "SensorSim.sensorPort", "endpoint": "uart"}], None,
     r"endpoint 'uart' is already served by SensorSim.sensorPort"),
])
def test_device_errors(tmp_path, devices, models, message):
    with pytest.raises(WstsError, match=message):
        run(SIM, tmp_path / "out", devices=devices, models=models)


@needs_fpp
def test_unknown_or_duplicate_simulator_names(tmp_path):
    with pytest.raises(WstsError, match=r"simulators.topologies names 'SensorSim.Nothing'.*Topologies found:.*SensorLink.Subtopology"):
        run(SIM, tmp_path / "out", simulators={"topologies": ["SensorSim.Nothing"]}, devices=None)
    with pytest.raises(WstsError, match=r"simulators.instances names 'SensorSim.nothing'"):
        run(SIM, tmp_path / "out", simulators={"instances": ["SensorSim.nothing"]}, devices=None)
    with pytest.raises(WstsError, match=r"SensorLink.sensor is already in the flight topology.*use 'substitute'"):
        run(SIM, tmp_path / "out", simulators={"instances": ["SensorLink.sensor"]}, devices=None)
    with pytest.raises(WstsError, match=r"SensorSim.txBuffers is added twice"):
        run(SIM, tmp_path / "out", simulators={"topologies": ["SensorSim.Simulator"], "instances": ["SensorSim.txBuffers"]})
    with pytest.raises(WstsError, match=r"neither end is an instance added by 'simulators'"):
        run(SIM, tmp_path / "out", simulators={
            "topologies": ["SensorSim.Simulator"],
            "connections": ["rateGroup10Hz.RateGroupMemberOut -> SensorLink.Subtopology.uartRun"]})
    with pytest.raises(WstsError, match=r"SubtopDemo.rateGroup1Hz \(Svc.ActiveRateGroup\) has no port 'Out'. Its ports:"):
        run(SIM, tmp_path / "out", simulators={
            "topologies": ["SensorSim.Simulator"], "connections": ["rateGroup1Hz.Out -> SensorSim.Simulator.schedIn"]})
    with pytest.raises(WstsError, match=r"RateGroupMemberOut\[3\] is already connected"):
        run(SIM, tmp_path / "out", simulators={
            "topologies": ["SensorSim.Simulator"],
            "connections": ["rateGroup1Hz.RateGroupMemberOut[3] -> SensorSim.Simulator.schedIn"]})


@needs_fpp
def test_parameters_need_a_database(project, tmp_path):
    replace_in(project / "SensorSim" / "Top" / "SensorSim.fpp",
               "      sensorSim.prmGetOut -> prmDb.getPrm\n      sensorSim.prmSetOut -> prmDb.setPrm\n", "")
    with pytest.raises(WstsError, match=r"(?s)SensorSim.sensorSim .* has parameters.*no parameter database.*Svc.PrmDb"):
        run(project, tmp_path / "out")


def test_moved_flight_connection_is_caught():
    flight = fpp.Topology("D.D", Path("t.fpp"), 1, 1)
    flight.patterns = [fpp.Pattern("Health", "D.health", Path("t.fpp"), 5, 5, 5, 30, False),
                       fpp.Pattern("Command", "D.cmd", Path("t.fpp"), 6, 5, 6, 30, False)]
    ping = fpp.Numbered("D.health", "PingSend", 0, "D.rg", "PingIn", 0)
    command = fpp.Numbered("D.cmd", "compCmdSend", 0, "D.rg", "cmdIn", 0)
    flight.numbered = [ping, command]
    flight.connections = [
        fpp.Connection("Health", "D.health", "PingSend", "D.rg", "PingIn", (Path("t.fpp"), 5, 5), (Path("t.fpp"), 5, 5)),
        fpp.Connection("Command", "D.cmd", "compCmdSend", "D.rg", "cmdIn", (Path("t.fpp"), 6, 5), (Path("t.fpp"), 6, 5)),
    ]
    patched = fpp.Topology("D.D", Path("t.fpp"), 1, 1)
    patched.numbered = [ping, fpp.Numbered("D.cmd", "compCmdSend", 1, "D.rg", "cmdIn", 0)]
    sims.verify_flight_connections(flight, patched, set())  # command ports may move: nothing is indexed by them
    patched.numbered = [fpp.Numbered("D.health", "PingSend", 1, "D.rg", "PingIn", 0)]
    with pytest.raises(WstsError, match=r"D.health.PingSend\[0\] -> D.rg.PingIn\[0\] is now \[1\] -> \[0\]"):
        sims.verify_flight_connections(flight, patched, set())


# ----------------------------------------------------------------------
# Surrogates of another kind, or with other port names
# ----------------------------------------------------------------------

SIM_UART = """module Sim {

  @ A serial driver replacement that is queued and names its ports its own way
  queued component SimUart {
    output port linkUp: Drv.ByteStreamReady
    output port $recv: Drv.ByteStreamData
    guarded input port $send: Drv.ByteStreamSend
    guarded input port recvReturnIn: Fw.BufferSend
    sync input port tick: Svc.Sched
    async input port poke: Svc.Sched
  }

}
"""


@pytest.fixture
def uart_project(project):
    (project / "Sim" / "SimUart").mkdir(parents=True)
    (project / "Sim" / "SimUart" / "SimUart.fpp").write_text(SIM_UART)
    return project


def substitute(**entry):
    return [{"instance": "SensorLink.uart", "with": "Sim.SimUart", **entry}]


@needs_fpp
def test_port_names_differ_without_a_mapping(uart_project, tmp_path):
    with pytest.raises(WstsError, match=r"(?s)Sim.SimUart cannot replace SensorLink.uart.*ready \(not present\).*map them with 'ports:"):
        run(uart_project, tmp_path / "out", substitute=substitute())


@needs_fpp
def test_passive_instance_retyped_to_a_queued_surrogate_gets_a_queue(uart_project, tmp_path):
    ports = {"ready": "linkUp", "run": "tick", "allocate": None, "deallocate": None}
    result = run(uart_project, tmp_path / "out", substitute=substitute(ports=ports, queue_size="SensorLinkConfig.BUFFER_COUNT"))
    patched = (result.output / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    assert "  instance uart: Sim.SimUart base id SensorLinkConfig.BASE_ID + 0x1000 queue size SensorLinkConfig.BUFFER_COUNT \\\n  {" in patched
    uart = next(s for s in result.report["substitutions"] if s["instance"] == "SensorLink.uart")
    assert uart["attributes"] == {"queue size": "SensorLinkConfig.BUFFER_COUNT"}
    assert not [w for w in result.report["warnings"] if "queue size" in w]

    result = run(uart_project, tmp_path / "out2", substitute=substitute(ports=ports))
    patched = (result.output / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    assert "base id SensorLinkConfig.BASE_ID + 0x1000 queue size 10 \\\n" in patched
    assert any("SensorLink.uart: Sim.SimUart is a queued component" in w and "queue size 10 is used" in w
               for w in result.report["warnings"])


@needs_fpp
def test_port_mapping_renames_and_removes_connections_in_a_subtopology(uart_project, tmp_path):
    ports = {"ready": "linkUp", "run": "tick", "allocate": None, "deallocate": ""}
    result = run(uart_project, tmp_path / "out", substitute=substitute(ports=ports, queue_size=4))
    patched = (result.output / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    original = (FLIGHT / "lib/sensor-lib/SensorLib/SensorLink/SensorLink.fpp").read_text()
    # Written directly in the subtopology: renamed in place
    assert "      uart.linkUp                 -> sensor.drvConnected\n" in patched
    # Exported by the subtopology and connected in the deployment: renamed where it is exported
    assert "    port uartRun = uart.tick\n" in patched
    top = (result.output / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    assert "rateGroup1Hz.RateGroupMemberOut[3] -> SensorLink.Subtopology.uartRun" in top
    # No counterpart: commented out, line for line
    assert "      # removed by fprime-wsts (Sim.SimUart has no counterpart of allocate): " \
           "uart.allocate -> bufferManager.bufferGetCallee\n" in patched
    assert "has no counterpart of deallocate): uart.deallocate -> bufferManager.bufferSendIn\n" in patched
    assert patched.count("\n") == original.count("\n")
    assert "      uart.$recv                 -> sensor.drvReceiveIn\n" in patched, "unmapped ports are untouched"
    notes = result.report["fidelity_notes"]
    assert any("SensorLink.uart.allocate -> SensorLink.bufferManager.bufferGetCallee is not made" in n for n in notes)
    uart = next(s for s in result.report["substitutions"] if s["instance"] == "SensorLink.uart")
    assert uart["ports"] == {"ready": "linkUp", "run": "tick", "allocate": None, "deallocate": None}
    assert uart["attributes"] == {"queue size": "4"}


@needs_fpp
def test_removing_an_exported_port_removes_its_connections(uart_project, tmp_path):
    ports = {"ready": "linkUp", "run": None, "allocate": None, "deallocate": None}
    result = run(uart_project, tmp_path / "out", substitute=substitute(ports=ports, queue_size=4))
    patched = (result.output / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    top = (result.output / "SubtopDemo" / "Top" / "topology.fpp").read_text()
    assert "    # removed by fprime-wsts (Sim.SimUart has no counterpart of run): port uartRun = uart.run\n" in patched
    assert "# removed by fprime-wsts (Sim.SimUart has no counterpart of run): " \
           "rateGroup1Hz.RateGroupMemberOut[3] -> SensorLink.Subtopology.uartRun\n" in top
    assert "rateGroup1Hz.RateGroupMemberOut[4] -> SensorLink.Subtopology.bufferManagerRun\n" in top, "numbers stay"


@needs_fpp
def test_port_mapping_from_a_catalog_and_bad_mappings(uart_project, tmp_path):
    (uart_project / "catalog.yaml").write_text(yaml.safe_dump({"surrogates": [{
        "surrogate": "Sim.SimUart", "replaces": ["VxWorksDrv.VxWorksUartDriver"], "queue_size": 7,
        "ports_for": {"VxWorksDrv.VxWorksUartDriver": {"ready": "linkUp", "run": "tick", "allocate": None, "deallocate": None}},
    }]}))
    result = run(uart_project, tmp_path / "out", catalogs=["catalog.yaml"])
    uart = next(s for s in result.report["substitutions"] if s["instance"] == "SensorLink.uart")
    assert (uart["surrogate"], uart["chosen_by"], uart["attributes"]) == ("Sim.SimUart", "catalog", {"queue size": "7"})
    assert uart["ports"]["run"] == "tick"
    with pytest.raises(WstsError, match=r"VxWorksDrv.VxWorksUartDriver has no port 'runIn'"):
        run(uart_project, tmp_path / "out", substitute=substitute(ports={"runIn": "tick"}))
    with pytest.raises(WstsError, match=r"Sim.SimUart has no port 'schedIn'"):
        run(uart_project, tmp_path / "out", substitute=substitute(ports={"run": "schedIn"}))
    with pytest.raises(WstsError, match=r"run \(type Drv.ByteStreamReady, flight uses Svc.Sched\)"):
        run(uart_project, tmp_path / "out", substitute=substitute(
            ports={"ready": "linkUp", "run": "linkUp", "allocate": None, "deallocate": None}))


def test_attribute_clauses_are_written_in_fpp_order():
    from fprime_wsts.generate import PlannedSubstitution, patch_instance

    def patched(text: str, kind: str, attributes: dict, has_queue: bool = False) -> str:
        instance = fpp.Instance("M.drv", "M.Drv", 0, 0, Path("i.fpp"), 2, 3, 2, 17, "M.Drv", has_queue=has_queue)
        surrogate = fpp.Component("Sim.Drv", kind, Path("s.fpp"))
        original = fpp.Component("M.Drv", "passive", Path("d.fpp"))
        return patch_instance(text, PlannedSubstitution(instance, original, surrogate, None, "drv", "", "manifest",
                                                        {}, attributes))

    plain = "module M {\n  instance drv: M.Drv base id 0x100 # the driver\n}\n"
    assert patched(plain, "active", {"priority": "30", "queue size": "8", "stack size": "64 * 1024"}) == \
        "module M {\n  instance drv: Sim.Drv base id 0x100 queue size 8 stack size 64 * 1024 priority 30 # the driver\n}\n"
    braces = 'module M {\n  instance drv: M.Drv base id Cfg.BASE + 0x100 \\\n  {\n    phase 1 """x"""\n  }\n}\n'
    assert patched(braces, "queued", {"queue size": "8"}) == braces.replace("M.Drv", "Sim.Drv").replace(
        "0x100 \\", "0x100 queue size 8 \\")
    active = "module M {\n  instance drv: M.Drv base id 0x100 \\\n    queue size 3 \\\n    stack size 1024 \\\n    priority 9\n}\n"
    # An active flight instance retyped to a queued surrogate keeps only its queue
    assert patched(active, "queued", {}, has_queue=True) == \
        "module M {\n  instance drv: Sim.Drv base id 0x100 queue size 3 \\\n    \\\n    \\\n    \n}\n"


# ----------------------------------------------------------------------
# Cache variables
# ----------------------------------------------------------------------


@needs_fpp
def test_cache_variables_reach_the_overlay(default, tmp_path):
    root = (default.output / "CMakeLists.txt").read_text()
    line = 'set(SENSOR_SIM_COMPONENTS "SerialSensorSim;Top" CACHE STRING "Set by fprime-wsts from cmake.cache in the manifest")'
    assert line in root and root.index(line) < root.index("find_package(FPrime")
    assert "FORCE" not in root, "a -D on the command line still wins"
    # The simulation project's library.cmake reads the variable: that is what builds the simulator
    assert "foreach (SENSOR_SIM_COMPONENT IN LISTS SENSOR_SIM_COMPONENTS)" in (SIM / "library.cmake").read_text()

    # A variable the flight settings.ini also sets: the manifest's value is the one used
    result = run(SIM, tmp_path / "out", cmake={"cache": {"SENSOR_SIM_COMPONENTS": ["SerialSensorSim", "Top"],
                                                         "FPRIME_ENABLE_AUTOCODER_UTS": True}})
    assert "FPRIME_ENABLE_AUTOCODER_UTS" not in (result.output / "settings.ini").read_text()
    assert "FPRIME_ENABLE_FRAMEWORK_UTS=OFF" in (result.output / "settings.ini").read_text()
    assert 'set(FPRIME_ENABLE_AUTOCODER_UTS "ON" CACHE STRING' in (result.output / "CMakeLists.txt").read_text()


@needs_fpp
def test_checked_in_example_is_current():
    result = generate(FLIGHT, parse_manifest(yaml.safe_load((SIM / "wsts.yaml").read_text()), SIM / "wsts.yaml"), check=True)
    assert result.changed == [], f"regenerate: fprime-wsts generate {SIM / 'wsts.yaml'}"
