"""The simulation's own telemetry (sim_tlm.py): each bus endpoint's traffic, the run's timing and
each system link, as channels of wstsTlm, and `telemetry` in the manifest to turn groups off."""

import pytest

from fprime_wsts import sim_tlm
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import TELEMETRY_KEYS, parse_manifest

from mission import MISSION, element_manifest, needs_fpp


def test_the_telemetry_groups_of_a_manifest():
    assert parse_manifest({"deployment": "D"}).telemetry == {key: True for key in TELEMETRY_KEYS}
    assert parse_manifest({"deployment": "D", "telemetry": False}).telemetry == {key: False for key in TELEMETRY_KEYS}
    off = parse_manifest({"deployment": "D", "telemetry": {"bus": False}}).telemetry
    assert off == {"models": True, "bus": False, "timing": True, "links": True}
    with pytest.raises(WstsError, match="telemetry: unknown key"):
        parse_manifest({"deployment": "D", "telemetry": {"buses": False}})
    with pytest.raises(WstsError, match="telemetry.links must be true or false"):
        parse_manifest({"deployment": "D", "telemetry": {"links": "no"}})


def test_channels_and_the_code_that_writes_them():
    plan = sim_tlm.Plan(["i2cBus", "uart-2"], ["hub"], True)
    names = plan.channel_names()
    assert names[:6] == ["i2cBus_FromFlight", "i2cBus_ToFlight", "i2cBus_BytesOut", "i2cBus_BytesIn", "i2cBus_Errors",
                         "i2cBus_Unanswered"]
    assert "uart_2_RateIn" in names and "Speed" in names and names[-3:] == ["hub_Sent", "hub_Received", "hub_Up"]
    assert len(names) == 2 * 8 + 4 + 3
    fpp = sim_tlm.render_fpp(plan)
    assert "telemetry i2cBus_RateOut: F32" in fpp and "telemetry hub_Up: bool" in fpp and "telemetry SimSeconds: F64" in fpp
    header = sim_tlm.render_header(plan)
    assert 'traffic.find("uart-2")' in header and "this->tlmWrite_uart_2_Unanswered(t.unanswered);" in header
    assert 'Wsts::LinkRegistry::find("hub")' in header
    assert "if (!status.stepped && m_published && seconds > 0.0) {" in header, "Speed is the host's: real time only"
    # A group off leaves its channels out; with every group off there is nothing to write
    assert "Speed" not in sim_tlm.Plan(["i2cBus"], [], False).channel_names()
    assert not sim_tlm.Plan([], [], False)
    assert sim_tlm.check(sim_tlm.Plan(["a-b", "a_b"], [], False)).startswith("'a-b' and 'a_b' give simulation channels")
    assert sim_tlm.check(sim_tlm.Plan(["uart"], ["uart"], True)) == "", "an endpoint and a link may share a name"
    assert sim_tlm.check(plan) == ""


@needs_fpp
def test_generated_deployment_publishes_its_simulation_telemetry(tmp_path):
    result = generate(MISSION, element_manifest("payload", telemetry={"links": False}), tmp_path / "out")
    top = tmp_path / "out" / "Payload" / "Top"
    assert f"instance {sim_tlm.INSTANCE}: {sim_tlm.COMPONENT} base id" in (top / "wsts_instances.fpp").read_text()
    setup = (top / "WstsSetup.cpp").read_text()
    assert "Wsts::Control::get().addTelemetrySource([] { ::Payload::wstsTlm.wstsPublish(); });" in setup
    report = result.report["sim_telemetry"]
    # Generated alone, not in the system, its hub driver stays a real socket: no endpoint
    assert report["endpoints"] == ["fpgaRegs", "heaterPin", "i2cBus", "sensorUart", "spiBus", "spw", "switchPin"]
    assert report["links"] == [] and report["timing"] and report["channels"] == 7 * 8 + 4
    assert report["base_id"] == result.report["control"]["control_base_id"] + sim_tlm.BASE_OFFSET
    added = result.report["dictionary"]["added"]["channels"]
    assert "Payload.wstsTlm.i2cBus_Unanswered" in added and "Payload.wstsTlm.LateTicks" in added
    assert "Simulation telemetry" in (tmp_path / "out" / "wsts-report.md").read_text()

    # Every group off, or no control component: no wstsTlm
    for changes in ({"telemetry": False}, {"control": False}):
        out = tmp_path / ("off" if "telemetry" in changes else "bare")
        result = generate(MISSION, element_manifest("payload", **changes), out)
        assert result.report["sim_telemetry"] is None and not (out / sim_tlm.DIRECTORY).exists()
