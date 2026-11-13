"""Register blocks: the `register` model kind, `Wsts.SimRegister` written for a flight
register interface component, `setup` in the manifest. The generator tests need the FPP
tools; the example is Payload, whose FPGA driver reads and writes registers through
`SensorLib.RegisterInterface`."""

import json

import pytest

from fprime_wsts import fpp, registers
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import Plan, PlannedSubstitution, _check_tick_sources, generate
from fprime_wsts.manifest import Manifest, ModelSpec, Surrogate, parse_manifest

from mission import MISSION, REPO, copy_flight, element_manifest, flight_manifest, needs_fpp

FPGA_MODEL = {"name": "fpga", "class": "Wsts::RegisterFile", "endpoint": "fpgaRegs", "kind": "register",
              "params": {"reg.0x00": "0xF00D0001", "irq.period": "20"}}
SUBSTITUTE = [{"instance": "fpgaRegs", "with": "Wsts.SimRegister"}]


def payload(**changes):
    """A manifest for Payload that lives in the flight project: the register interface is
    named, everything else comes from the toolkit's catalog"""
    return flight_manifest("Payload", **{"substitute": SUBSTITUTE, "models": [FPGA_MODEL], **changes})


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def test_register_is_a_model_kind():
    manifest = parse_manifest({"deployment": "Payload", "models": [FPGA_MODEL]})
    assert manifest.models[0].kind == "register"
    assert manifest.models[0].params == {"reg.0x00": "0xF00D0001", "irq.period": "20"}
    with pytest.raises(WstsError, match="kind must be one of bytestream, gpio, i2c, none, register, spi"):
        parse_manifest({"deployment": "Payload", "models": [{**FPGA_MODEL, "kind": "mmio"}]})


def test_setup_is_text_or_a_mapping():
    assert not parse_manifest({"deployment": "D"}).setup
    text = parse_manifest({"deployment": "D", "setup": "Proj::registerDma();\n"}).setup
    assert text.code == "Proj::registerDma();\n" and text.headers == []
    both = parse_manifest({"deployment": "D", "setup": {"code": "a();", "headers": ["Proj/Dma.hpp"]}}).setup
    assert both.code == "a();" and both.headers == ["Proj/Dma.hpp"]
    with pytest.raises(WstsError, match="setup: unknown key.*source"):
        parse_manifest({"deployment": "D", "setup": {"source": "x"}})
    with pytest.raises(WstsError, match="setup.headers is given without setup.code"):
        parse_manifest({"deployment": "D", "setup": {"headers": ["a.hpp"]}})
    with pytest.raises(WstsError, match="setup.headers must be a list"):
        parse_manifest({"deployment": "D", "setup": {"code": "a();", "headers": "a.hpp"}})


# ----------------------------------------------------------------------
# Ports are recognised by their signatures
# ----------------------------------------------------------------------


def _definition(params, returns=None):
    return fpp.PortDef("P", [(f"a{i}", t, False) for i, t in enumerate(params)], returns)


@pytest.mark.parametrize("kind, params, returns, role", [
    ("SyncInput", ["U64"], "U32", "read32"),
    ("GuardedInput", ["U32"], "U64", "read64"),
    ("SyncInput", ["U64", "U32"], None, "write32"),
    ("SyncInput", ["U32", "U64"], None, "write64"),
    ("Output", ["U32"], None, "interrupt"),
    ("Output", [], None, "interrupt"),
    ("SyncInput", ["U32"], None, None),            # a scheduling port: one argument, nothing back
    ("SyncInput", ["U64"], "bool", None),
    ("SyncInput", ["U64", "F32"], None, None),
    ("SyncInput", ["Proj.Offset"], "U32", None),   # a type of the project's: not read as an integer
    ("Output", ["U32", "U32"], None, None),
    ("Output", ["U64"], "U32", None),              # the driver's side of a read, not an interrupt
])
def test_role_of_a_port(kind, params, returns, role):
    port = fpp.Port("p", kind, "Proj.P")
    assert registers.role_of(port, _definition(params, returns)) == role


def test_framework_ports_and_references_have_no_role():
    ping = fpp.Port("PingOut", "Output", "Svc.Ping")
    assert registers.role_of(ping, _definition(["U32"])) is None, "same signature as an interrupt"
    by_reference = fpp.PortDef("P", [("offset", "U64", True)], "U32")
    assert registers.role_of(fpp.Port("p", "SyncInput", "Proj.P"), by_reference) is None
    assert registers.role_of(fpp.Port("cmdIn", "Special", None), None) is None


def test_adapting_a_component_without_register_ports_says_what_the_shapes_are(tmp_path):
    component = fpp.Component("Proj.Uart", "passive", tmp_path / "Uart.fpp")
    component.ports = {"send": fpp.Port("send", "SyncInput", "Proj.Send")}
    with pytest.raises(WstsError) as error:
        registers.adapt(component, {"Proj.Send": fpp.PortDef("Proj.Send", [("data", "Fw.Buffer", True)])}, tmp_path)
    assert "none of its ports has the shape of a register access" in str(error.value)
    assert "read32: input port (offset) -> U32" in str(error.value)
    assert "send (input Proj.Send(data: Fw.Buffer))" in str(error.value)


def test_rate_groups_cycled_through_a_simulated_register_interface(tmp_path):
    """A kept time unit driver whose interrupt arrives from a register interface that is
    simulated: the warning says what has to raise the interrupt"""
    def component(name, ports):
        made = fpp.Component(name, "passive", tmp_path / "proj" / f"{name}.fpp")
        made.ports = {p.name: p for p in ports}
        return made

    cycle_out = fpp.Port("CycleOut", "Output", "Svc.Cycle")
    cycle_in = fpp.Port("CycleIn", "SyncInput", "Svc.Cycle")
    regif_ports = [fpp.Port("read32In", "SyncInput", "Fc.Read"), fpp.Port("interrupt", "Output", "Fc.Irq")]
    components = {
        "Svc.RateGroupDriver": component("Svc.RateGroupDriver", [cycle_in, cycle_out]),
        "Fc.TimeUnit": component("Fc.TimeUnit", [cycle_out, fpp.Port("interruptIn", "SyncInput", "Fc.Irq")]),
        "Fc.RegIf": component("Fc.RegIf", regif_ports),
        "Sim.RegIf": component("Sim.RegIf", regif_ports),
    }
    instances = {
        name: fpp.Instance(name, kind, 0, 0, tmp_path / "instances.fpp", 1, 1, 1, 1, kind)
        for name, kind in (("Fc.timeUnit", "Fc.TimeUnit"), ("Fc.timeRegs", "Fc.RegIf"), ("Fc.rgDriver", "Svc.RateGroupDriver"))
    }
    topology = fpp.Topology("Fc.Fc", tmp_path / "topology.fpp", 1, 1, instances=list(instances))
    topology.connections = [
        fpp.Connection("RateGroups", "Fc.timeUnit", "CycleOut", "Fc.rgDriver", "CycleIn"),
        fpp.Connection("Time", "Fc.timeRegs", "interrupt", "Fc.timeUnit", "interruptIn"),
    ]
    model = fpp.Model(components, instances, {topology.name: topology}, [])
    plan = Plan(topology)
    plan.substitutions.append(PlannedSubstitution(
        instances["Fc.timeRegs"], components["Fc.RegIf"], components["Sim.RegIf"], Surrogate("Sim.RegIf"), "timeRegs",
        "", "manifest", register_roles={"read32In": "read32", "interrupt": "interrupt"}))

    _check_tick_sources(plan, model, None, Manifest(None, "Fc"))
    assert len(plan.warnings) == 1
    assert "Its interrupt arrives on interruptIn from Fc.timeRegs, which is simulated (register endpoint 'timeRegs')" in plan.warnings[0]
    assert "none is attached in the manifest" in plan.warnings[0]

    # With a model on the endpoint it is a note in the report, not a warning
    plan.warnings.clear()
    plan.fidelity_notes.clear()
    manifest = Manifest(None, "Fc", models=[ModelSpec("clock", "Sim::TimeUnitModel", "x.hpp", "timeRegs", "register")])
    _check_tick_sources(plan, model, None, manifest)
    assert plan.warnings == []
    assert "the model 'clock' on that endpoint raises that interrupt" in plan.fidelity_notes[0]
    assert "has to be the simulation's tick source" in plan.fidelity_notes[0]


# ----------------------------------------------------------------------
# The generator, on the example
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("registers") / "out"
    return generate(MISSION, payload(keep=["hubDriver"]), out), out


@needs_fpp
def test_surrogate_is_written_with_the_flight_components_ports(generated):
    result, out = generated
    chosen = {s["instance"]: (s["surrogate"], s["chosen_by"], s["endpoint"]) for s in result.report["substitutions"]}
    assert chosen["Payload.fpgaRegs"] == ("WstsAdapted.SensorLib_RegisterInterface", "manifest", "fpgaRegs")

    module = out / "WstsAdapted" / "SensorLib_RegisterInterface"
    model = (module / "SensorLib_RegisterInterface.fpp").read_text()
    assert "passive component SensorLib_RegisterInterface {" in model
    assert "sync input port read32In: SensorLib.RegRead32" in model
    assert "sync input port write64In: SensorLib.RegWrite64" in model
    assert "output port interrupt: SensorLib.RegInterrupt" in model
    assert "command" not in model, "the flight component's commands are not the surrogate's"

    header = (module / "SensorLib_RegisterInterface.hpp").read_text()
    assert "public SensorLib_RegisterInterfaceComponentBase, public Wsts::RegisterSurrogate" in header
    assert "U32 read32In_handler(FwIndexType portNum, U64 offset) override {" in header
    assert "void write64In_handler(FwIndexType portNum, U64 offset, U64 value) override {" in header
    assert "this->interrupt_out(port, static_cast<U32>(number));" in header
    assert "void configure(Args&&...) {}" in header
    assert "Wsts_Core" in (module / "CMakeLists.txt").read_text()

    root = (out / "CMakeLists.txt").read_text()
    added = 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/WstsAdapted/SensorLib_RegisterInterface")'
    assert added in root and root.index(added) < root.index("${WSTS_FLIGHT_PROJECT}/Components")
    # The instance is retyped, and flight code that names the flight class gets the surrogate
    assert "instance fpgaRegs: WstsAdapted.SensorLib_RegisterInterface base id 0x20037000" in \
        (out / "Payload" / "Top" / "instances.fpp").read_text()
    compat = (out / "Payload" / "Top" / "WstsCompat.hpp").read_text()
    assert "#include <WstsAdapted/SensorLib_RegisterInterface/SensorLib_RegisterInterface.hpp>" in compat
    assert "namespace SensorLib { using RegisterInterface = ::WstsAdapted::SensorLib_RegisterInterface; }" in compat
    standin = (out / "wsts-include" / "SensorLib" / "RegisterInterface" / "RegisterInterface.hpp").read_text()
    assert "WstsAdapted/SensorLib_RegisterInterface/SensorLib_RegisterInterface.hpp" in standin


@needs_fpp
def test_register_model_is_attached_and_reported(generated):
    result, out = generated
    setup = (out / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    assert "Wsts::RegisterFile model_fpga;" in setup
    assert '::Payload::fpgaRegs.wstsBind("fpgaRegs", "");' in setup
    assert 'Wsts::configureModel(&model_fpga, "reg.0x00", "0xF00D0001")' in setup
    assert 'Wsts::Bus::get().attachRegister("fpgaRegs", &model_fpga);' in setup
    assert setup.index("attachRegister") < setup.index("Wsts::Runtime::start")

    report = result.report
    assert report["register_blocks"] == [{
        "instance": "Payload.fpgaRegs", "endpoint": "fpgaRegs", "flight_component": "SensorLib.RegisterInterface",
        "surrogate": "WstsAdapted.SensorLib_RegisterInterface", "written_for_the_component": True,
        "ports": {"interrupt": "interrupt", "read32In": "read32", "read64In": "read64", "write32In": "write32",
                  "write64In": "write64"},
        "ports_without_a_role": [], "drivers": ["Payload.fpga"], "interrupt_to": ["Payload.fpga.interruptIn"],
        "model": "fpga",
    }]
    assert {"name": "fpga", "class": "Wsts::RegisterFile", "endpoint": "fpgaRegs", "kind": "register", "address": 0} \
        in report["models"]
    # The flight component's debug commands and events leave the dictionary, and the report says so
    removed = report["dictionary"]["removed"]
    assert removed["commands"] == ["Payload.fpgaRegs.REG_READ", "Payload.fpgaRegs.REG_WRITE"]
    assert "Payload.fpgaRegs.RegisterValue" in removed["events"]
    assert report["dictionary"]["flight_ids_unchanged"] is True
    added = report["dictionary"]["added"]["commands"]
    assert "Payload.wstsControl.REG_SET" in added and "Payload.wstsControl.IRQ_RAISE" in added
    notes = "\n".join(report["fidelity_notes"])
    assert "Payload.fpgaRegs is a register interface component" in notes and "the model 'fpga'" in notes
    assert "DMA:" in notes
    assert not [w for w in report["warnings"] if "fpgaRegs" in w]

    text = (out / "wsts-report.md").read_text()
    assert "## Register blocks" in text
    assert ("| `Payload.fpgaRegs` | `SensorLib.RegisterInterface` | `WstsAdapted.SensorLib_RegisterInterface` | "
            "`fpgaRegs` | `fpga` | `Payload.fpga` | `Payload.fpga.interruptIn` |") in text
    assert "`read32In` (read32)" in text
    assert "- `fpga`: `Wsts::RegisterFile` on `fpgaRegs` (register)" in text
    assert json.loads((out / "wsts-report.json").read_text())["register_blocks"] == report["register_blocks"]


@needs_fpp
def test_register_interface_that_is_not_named_is_kept_as_in_flight(tmp_path):
    """It builds on a host (its registers are then plain memory), so nothing forces a
    substitution: the report has no register section"""
    result = generate(MISSION, flight_manifest("Payload", keep=["hubDriver"]), tmp_path / "out")
    assert "Payload.fpgaRegs" not in {s["instance"] for s in result.report["substitutions"]}
    assert "register_blocks" not in result.report and "setup" not in result.report
    assert not (tmp_path / "out" / "WstsAdapted").exists()
    assert "## Register blocks" not in (tmp_path / "out" / "wsts-report.md").read_text()


@needs_fpp
def test_every_instance_of_a_component_can_be_named(tmp_path):
    manifest = payload(keep=["hubDriver"], substitute=[{"component": "SensorLib.RegisterInterface", "with": "Wsts.SimRegister"}])
    result = generate(MISSION, manifest, tmp_path / "out")
    assert result.report["register_blocks"][0]["instance"] == "Payload.fpgaRegs"


@needs_fpp
@pytest.mark.parametrize("changes, message", [
    # No surrogate binds the endpoint: the register interface was not substituted
    ({"substitute": []},
     r"model 'fpga' attaches to endpoint 'fpgaRegs', which no surrogate is bound to"),
    # The endpoint belongs to a driver of another kind
    ({"models": [{**FPGA_MODEL, "endpoint": "i2cBus"}]},
     r"model 'fpga' is a register block \(kind: register\) on endpoint 'i2cBus', which Payload.i2cBus is bound to as "
     r"Wsts.SimI2c: that is not a surrogate for a register interface component.*Register endpoints: fpgaRegs\."),
    # One block per endpoint
    ({"models": [FPGA_MODEL, {**FPGA_MODEL, "name": "second"}]},
     r"models 'fpga' and 'second' are both register blocks on endpoint 'fpgaRegs'"),
    # An endpoint name that another instance has
    ({"substitute": [{"instance": "fpgaRegs", "with": "Wsts.SimRegister", "endpoint": "i2cBus"}], "models": []},
     r"endpoint 'i2cBus' is used by both Payload.i2cBus and Payload.fpgaRegs.*give one of\s+them its own name with"
     r"\s+substitute:\s+- instance: Payload.fpgaRegs\s+with: WstsAdapted.SensorLib_RegisterInterface\s+endpoint: <name>"),
    # The surrogate has the flight port names: nothing to rename
    ({"substitute": [{"instance": "fpgaRegs", "with": "Wsts.SimRegister", "ports": {"read32In": "readIn"}}]},
     r"'ports' renames read32In, but the surrogate is written with the flight component's own port names"),
    # A component that is not a register interface
    ({"substitute": [{"instance": "imu", "with": "Wsts.SimRegister"}], "models": []},
     r"Wsts.SimRegister cannot stand in for Components.ImuManager: none of its ports has the shape of a register access"),
])
def test_errors(tmp_path, changes, message):
    with pytest.raises(WstsError, match=f"(?s){message}"):
        generate(MISSION, payload(keep=["hubDriver"], **changes), tmp_path / "out")


@needs_fpp
def test_a_connected_port_that_is_not_a_register_access(tmp_path):
    """The flight component has a port the surrogate cannot serve: the error names it, and
    dropping the connection in the manifest is one way on"""
    flight = copy_flight(tmp_path)
    regif = flight / "lib/sensor-lib/SensorLib/RegisterInterface/RegisterInterface.fpp"
    regif.write_text(regif.read_text().replace(
        "    sync input port read32In: RegRead32\n",
        "    sync input port read32In: RegRead32\n\n    sync input port resetIn: Svc.Sched\n"))
    header = regif.with_suffix(".hpp")
    header.write_text(header.read_text().replace(
        "    U32 read32In_handler(", "    void resetIn_handler(FwIndexType, U32) override {}\n    U32 read32In_handler("))
    topology = flight / "Payload/Top/topology.fpp"
    topology.write_text(topology.read_text().replace(
        "      rateGroup10Hz.RateGroupMemberOut[1] -> fpga.run\n",
        "      rateGroup10Hz.RateGroupMemberOut[1] -> fpga.run\n      rateGroup10Hz.RateGroupMemberOut[2] -> fpgaRegs.resetIn\n"))

    with pytest.raises(WstsError) as error:
        generate(flight, payload(keep=["hubDriver"]), tmp_path / "refused")
    text = str(error.value)
    assert "Wsts.SimRegister cannot stand in for Payload.fpgaRegs (SensorLib.RegisterInterface)" in text
    assert "resetIn (input Svc.Sched(context: U32))" in text
    assert "ports: {<port>: }" in text and "fprime-wsts scaffold SensorLib.RegisterInterface" in text

    dropped = [{"instance": "fpgaRegs", "with": "Wsts.SimRegister", "ports": {"resetIn": None}}]
    result = generate(flight, payload(keep=["hubDriver"], substitute=dropped), tmp_path / "out")
    block = result.report["register_blocks"][0]
    assert block["ports_without_a_role"] == ["resetIn"] and "resetIn" not in block["ports"]
    assert "resetIn" not in (tmp_path / "out/WstsAdapted/SensorLib_RegisterInterface/SensorLib_RegisterInterface.fpp").read_text()
    patched = (tmp_path / "out/Payload/Top/topology.fpp").read_text()
    assert ("# removed by fprime-wsts (WstsAdapted.SensorLib_RegisterInterface has no counterpart of resetIn): "
            "rateGroup10Hz.RateGroupMemberOut[2] -> fpgaRegs.resetIn") in patched


@needs_fpp
def test_setup_code_runs_before_the_runtime_starts(tmp_path):
    code = "// tell the model where the driver's buffers are\nWsts::Dma::addRegion(Proj::dmaBase(), 0x80000000, 4096);\n"
    manifest = payload(keep=["hubDriver"], setup={"code": code, "headers": ["Wsts/Core/Dma.hpp", "Proj/Dma.hpp"]})
    result = generate(MISSION, manifest, tmp_path / "out")
    setup = (tmp_path / "out/Payload/Top/WstsSetup.cpp").read_text()
    assert "#include <Wsts/Core/Dma.hpp>\n#include <Proj/Dma.hpp>\n" in setup
    assert "        Wsts::Dma::addRegion(Proj::dmaBase(), 0x80000000, 4096);\n" in setup
    assert setup.index("attachRegister") < setup.index("Wsts::Dma::addRegion") < setup.index("Wsts::Runtime::start")
    assert result.report["setup"] == {"headers": ["Wsts/Core/Dma.hpp", "Proj/Dma.hpp"], "lines": 2}
    assert "## Set-up code" in (tmp_path / "out/wsts-report.md").read_text()


@needs_fpp
def test_the_examples_payload_element_uses_it():
    """The checked-in manifest: the register interface is named, the block is the toolkit's
    register file with one interrupt"""
    manifest = element_manifest("payload")
    assert [(s.instance, s.surrogate) for s in manifest.substitutions] == [("fpgaRegs", "Wsts.SimRegister")]
    model = next(m for m in manifest.models if m.kind == "register")
    assert (model.cls, model.endpoint, model.params["irq.period"]) == ("Wsts::RegisterFile", "fpgaRegs", "20")
    report = json.loads((REPO / "examples/mission-sim/wsts/Mission/payload/wsts-report.json").read_text())
    assert report["register_blocks"][0]["model"] == "fpga"


@needs_fpp
def test_a_mirror_keeps_the_flight_dictionary(tmp_path):
    """Sim.SimRegisterInterface, the simulation project's mirror of the register interface,
    written on Wsts::RegisterSurrogate: nothing is adapted, and the flight component's
    commands and events stay"""
    data = {**element_manifest("payload").__dict__}  # only to show the manifest loads as it is
    assert data["cmake_cache"]["MISSION_SIM_MODULES"][-1] == "Surrogates/SimRegisterInterface"
    from mission import element_data
    from mission import SIM
    changed = element_data("payload")
    changed["substitute"] = [{"instance": "fpgaRegs", "with": "Sim.SimRegisterInterface"}]
    changed["project"] = str(MISSION)
    result = generate(MISSION, parse_manifest(changed, SIM / "payload.yaml"), tmp_path / "out")
    block = result.report["register_blocks"][0]
    assert block["surrogate"] == "Sim.SimRegisterInterface" and block["written_for_the_component"] is False
    assert block["ports"]["read32In"] == "read32" and block["interrupt_to"] == ["Payload.fpga.interruptIn"]
    assert not (tmp_path / "out" / "WstsAdapted").exists()
    removed = result.report["dictionary"]["removed"]
    assert not [name for names in removed.values() for name in names if ".fpgaRegs." in name]
    assert "Wsts::Bus::get().attachRegister(\"fpgaRegs\", &model_fpga);" in \
        (tmp_path / "out/Payload/Top/WstsSetup.cpp").read_text()
