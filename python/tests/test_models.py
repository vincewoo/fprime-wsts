"""Python device models: the handlers of the bridge client, the files `run.models` names,
and the module that serves them for a run"""

import json
import time

import pytest

from fprime_wsts import models
from fprime_wsts.bridge import BridgeError
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import parse_manifest

from mission import MISSION, SIM, copy_sim, element_data, needs_fpp
from test_bridge import bridge, sim  # noqa: F401  (fixtures: a stand-in for the simulation)


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_a_byte_stream_model_answers_what_flight_sends(sim, bridge):  # noqa: F811
    seen = []

    def sensor(data):
        seen.append(data)
        return b"$TEMP,21.5\n" if data == b"TEMP?\n" else None

    bridge.serve_bytestream("sensorUart", sensor)
    assert sim.requests[-1]["op"] == "subscribe"
    sim.push({"ev": "rec", "rec": "bs_tx", "endpoint": "sensorUart", "src": "flight", "data": b"TEMP?\n".hex()})
    assert wait_for(lambda: sim.requests[-1]["op"] == "inject_bytes")
    assert sim.requests[-1]["endpoint"] == "sensorUart" and bytes.fromhex(sim.requests[-1]["data"]) == b"$TEMP,21.5\n"
    # Nothing to say, another endpoint, and the model's own bytes coming back: no answer
    count = len(sim.requests)
    sim.push({"ev": "rec", "rec": "bs_tx", "endpoint": "sensorUart", "src": "flight", "data": b"PING".hex()})
    sim.push({"ev": "rec", "rec": "bs_tx", "endpoint": "gpsUart", "src": "flight", "data": b"TEMP?\n".hex()})
    sim.push({"ev": "rec", "rec": "bs_rx", "endpoint": "sensorUart", "src": "inject", "data": b"TEMP?\n".hex()})
    assert wait_for(lambda: len(seen) == 2) and seen == [b"TEMP?\n", b"PING"]
    time.sleep(0.1)
    assert len(sim.requests) == count


def test_a_gpio_model_follows_an_output_and_a_failing_one_does_not_end_the_rest(sim, bridge, capsys):  # noqa: F811
    levels = []

    def heater(level):
        if not levels:
            levels.append(level)
            raise RuntimeError("model bug")
        levels.append(level)
        bridge.gpio_force("switchPin", level)

    bridge.serve_gpio("heaterPin", heater)
    sim.push({"ev": "rec", "rec": "gpio_out", "endpoint": "heaterPin", "src": "flight", "level": True})
    sim.push({"ev": "rec", "rec": "gpio_out", "endpoint": "heaterPin", "src": "flight", "level": False})
    assert wait_for(lambda: sim.requests[-1]["op"] == "gpio_force")
    assert levels == [True, False] and sim.requests[-1] == {"id": 2, "op": "gpio_force", "endpoint": "switchPin", "level": False}
    assert "model bug" in capsys.readouterr().err


def test_a_model_file_defines_serve(tmp_path, sim):  # noqa: F811
    good = tmp_path / "thermo.py"
    (tmp_path / "shared.py").write_text("ADDRESS = 0x48\n")
    good.write_text("import shared\n\ndef serve(sim):\n    sim.serve_i2c('bus', shared.ADDRESS, lambda w, n: (0, bytes(n)))\n")
    bad = tmp_path / "empty.py"
    bad.write_text("x = 1\n")
    with pytest.raises(BridgeError, match=r"does not define serve\(sim\)"):
        models.load(bad)
    assert callable(models.load(good)), "and it may import what lies beside it"
    assert models.main(["--bridge", f"tcp:{sim.port}", str(bad)]) == 2


@pytest.mark.parametrize("value", ["thermo.py", ["thermo.txt"], [3]])
def test_run_models_is_a_list_of_python_files(value):
    with pytest.raises(WstsError, match="run.models must be a list of Python files"):
        parse_manifest({"deployment": "D", "run": {"models": value}})
    assert parse_manifest({"deployment": "D", "run": {"models": ["m/a.py"]}}).run_models == ["m/a.py"]


@needs_fpp
def test_model_files_reach_the_report_relative_to_the_overlay(tmp_path):
    project = copy_sim(tmp_path)
    (project / "pymodels").mkdir()
    (project / "pymodels" / "power.py").write_text("def serve(sim):\n    pass\n")
    data = {**element_data("payload"), "project": str(MISSION), "run": {"models": ["pymodels/power.py"]}}
    result = generate(MISSION, parse_manifest(data, project / "payload.yaml"), tmp_path / "out")
    assert result.report["run"]["models"] == ["../sim/pymodels/power.py"] and result.report["run"]["env"] == {}
    assert "Python device models started with the run: `../sim/pymodels/power.py`" in (tmp_path / "out" / "wsts-report.md").read_text()
    with pytest.raises(WstsError, match="run.models: nope.py not found"):
        generate(MISSION, parse_manifest({**data, "run": {"models": ["nope.py"]}}, project / "payload.yaml"), tmp_path / "out2")
    with pytest.raises(WstsError, match="run.models needs the bridge"):
        generate(MISSION, parse_manifest({**data, "bridge": "none"}, project / "payload.yaml"), tmp_path / "out3")
