"""Couplings to dynamics simulators (docs/dynamics.md): the `dynamics` section of the
manifests, the conversions of inputs and outputs, and a plant stepped by the conductor
between instants, against stand-ins for the elements"""

import json
import textwrap

import pytest

from fprime_wsts import dynamics
from fprime_wsts.dynamics import DynamicsError, Output, check_targets, input_value, parse_specs
from fprime_wsts.lockstep import Conductor, LockstepError
from fprime_wsts.manifest import parse_manifest
from fprime_wsts.system import parse_system
from test_lockstep import FakeElement

THERMAL = {
    "name": "thermal", "python": "plants/thermal.py", "params": {"C": 5.0},
    "inputs": {"heater": {"element": "payload", "gpio": "heaterPin"}},
    "outputs": {"T": [{"element": "payload", "model": "tempSensor", "key": "text", "format": "$TEMP,{:.1f}\n"},
                      {"element": "payload", "model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True},
                      {"element": "payload", "gpio": "switchPin", "above": 45, "hysteresis": 5}]},
}


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------

def test_a_plant_of_a_system_is_checked_and_normalised():
    [spec] = parse_specs([THERMAL], "sys.yaml", elements=["cdh", "payload"])
    assert spec["kind"] == "python" and spec["path"] == "plants/thermal.py" and spec["step"] is None
    assert spec["inputs"]["heater"] == {"element": "payload", "gpio": "heaterPin"}
    assert len(spec["outputs"]["T"]) == 3 and spec["outputs"]["T"][1]["scale"] == 10.0


def test_one_output_may_be_a_mapping_and_one_element_needs_no_name():
    [spec] = parse_specs([{"name": "p", "fmu": "p.fmu", "step": 0.1, "outputs": {"x": {"gpio": "pin"}}}],
                         "sys.yaml", elements=["only"])
    assert spec["outputs"]["x"] == [{"element": "only", "gpio": "pin"}] and spec["step"] == 0.1


@pytest.mark.parametrize("entry, message", [
    ({"python": "p.py", "outputs": {"x": {"gpio": "pin"}}}, "a plant has a name"),
    ({"name": "p", "outputs": {"x": {"gpio": "pin"}}}, "one of python, fmu"),
    ({"name": "p", "python": "p.py", "fmu": "p.fmu", "outputs": {"x": {"gpio": "pin"}}}, "one of python, fmu"),
    ({"name": "p", "python": "p.txt", "outputs": {"x": {"gpio": "pin"}}}, "names a Python file"),
    ({"name": "p", "python": "p.py"}, "no outputs"),
    ({"name": "p", "python": "p.py", "outputz": {}, "outputs": {"x": {"gpio": "pin"}}}, "did you mean 'outputs'"),
    ({"name": "p", "python": "p.py", "step": 0, "outputs": {"x": {"gpio": "pin"}}}, "positive number"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin", "model": "m"}}}, "not gpio and model"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"model": "m"}}}, "model needs 'key'"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin", "above": 1, "below": 2}}}, "one threshold"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin", "hysteresis": 1}}}, "goes with a threshold"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"bytes": "uart"}}}, "bytes needs 'format'"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin", "scale": "2"}}}, "'scale' is a number"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"register": "r", "offset": "twelve"}}}, "'offset' is a number"),
    ({"name": "p", "python": "p.py", "inputs": {"a": {"i2c": "bus"}}, "outputs": {"x": {"gpio": "pin"}}},
     "name one of gpio, model, register"),
    ({"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin"}, "y": {"gpio": "pin"}}}, "write the same place"),
])
def test_what_a_deployment_manifest_refuses(entry, message):
    with pytest.raises(DynamicsError, match=message):
        parse_specs([entry], "wsts.yaml")


def test_a_system_says_which_element_and_a_deployment_does_not():
    with pytest.raises(DynamicsError, match="say which element"):
        parse_specs([{"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin"}}}], "s", elements=["a", "b"])
    with pytest.raises(DynamicsError, match="say which element"):
        parse_specs([{"name": "p", "python": "p.py", "outputs": {"x": {"element": "c", "gpio": "pin"}}}], "s",
                    elements=["a", "b"])
    with pytest.raises(DynamicsError, match="belongs in a system manifest"):
        parse_specs([{"name": "p", "python": "p.py", "outputs": {"x": {"element": "a", "gpio": "pin"}}}], "wsts.yaml")
    with pytest.raises(DynamicsError, match="two plants of that name"):
        parse_specs([THERMAL, THERMAL], "s", elements=["payload"])


def test_both_manifests_take_dynamics():
    manifest = parse_manifest({"deployment": "D", "dynamics": [
        {"name": "p", "python": "p.py", "outputs": {"x": {"gpio": "pin"}}}]})
    assert manifest.dynamics[0]["name"] == "p"
    system = parse_system({"system": "S", "elements": {"cdh": "cdh.yaml", "payload": "payload.yaml"},
                           "dynamics": [THERMAL]})
    assert system.dynamics[0]["outputs"]["T"][2]["above"] == 45.0


def test_the_generator_checks_the_models_and_endpoints_a_plant_names():
    report = {"models": [{"name": "fpga", "endpoint": "fpgaRegs"}, {"name": "tempSensor", "endpoint": "sensorUart"}],
              "substitutions": [{"endpoint": "heaterPin"}, {"endpoint": "switchPin"}, {"endpoint": None}]}
    [spec] = parse_specs([THERMAL], "s", elements=["payload"])
    check_targets([spec], "s", {"payload": report})
    wrong = json.loads(json.dumps(spec))
    wrong["outputs"]["T"][0]["model"] = "tempSensr"
    with pytest.raises(DynamicsError, match="no model 'tempSensr' on element payload"):
        check_targets([wrong], "s", {"payload": report})
    wrong = json.loads(json.dumps(spec))
    wrong["inputs"]["heater"]["gpio"] = "heater"
    with pytest.raises(DynamicsError, match="no endpoint 'heater'.*heaterPin"):
        check_targets([wrong], "s", {"payload": report})


def test_the_plant_file_must_exist_and_its_path_follows_the_output(tmp_path):
    (tmp_path / "plants").mkdir()
    (tmp_path / "plants" / "thermal.py").write_text("")
    [spec] = parse_specs([THERMAL], "s", elements=["payload"])
    out = dynamics.relocate([spec], lambda path: tmp_path / path, lambda path: str(path.relative_to(tmp_path)))
    assert out[0]["path"] == "plants/thermal.py"
    with pytest.raises(DynamicsError, match="not found"):
        dynamics.relocate([spec], lambda path: tmp_path / "elsewhere" / path, str)


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------

def test_inputs_are_numbers_where_they_can_be():
    assert input_value({"gpio": "pin"}, True) == 1.0 and input_value({"gpio": "pin"}, False) == 0.0
    assert input_value({"register": "r", "offset": 0, "scale": 0.5}, 10) == 5.0
    assert input_value({"model": "m", "key": "k"}, "215") == 215
    assert input_value({"model": "m", "key": "k", "add": -1}, "2.5") == 1.5
    assert input_value({"model": "m", "key": "k"}, "$TEMP,21.5\n") == "$TEMP,21.5\n"


def test_outputs_become_the_actions_that_give_flight_software_the_value():
    text = Output("T", {"model": "tempSensor", "key": "text", "format": "$TEMP,{:.1f}\n"}, "thermal")
    assert text.action(21.54) == {"op": "model_set", "model": "tempSensor", "key": "text", "value": "$TEMP,21.5\n"}
    reg = Output("T", {"model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True}, "thermal")
    assert reg.action(21.55)["value"] == "216"
    block = Output("T", {"register": "fpgaRegs", "offset": 12, "scale": 10}, "thermal")
    assert block.action(-1.0) == {"op": "reg_set", "endpoint": "fpgaRegs", "offset": "0xc",
                                  "value": hex(0xFFFFFFFFFFFFFFF6)}
    i2c = Output("g", {"i2c": "i2cBus", "addr": 0x68, "reg": 0x3B}, "imu")
    assert i2c.action(300) == {"op": "i2c_reg_set", "endpoint": "i2cBus", "addr": 0x68, "reg": 0x3B, "value": 44}
    sent = Output("T", {"bytes": "sensorUart", "format": "$T,{:.0f}\n"}, "thermal")
    assert sent.action(20.4) == {"op": "inject_bytes", "endpoint": "sensorUart", "text": "$T,20\n"}
    with pytest.raises(DynamicsError, match="output 'T' is nan"):
        reg.action(float("nan"))


def test_a_threshold_with_hysteresis_closes_and_opens_once():
    switch = Output("T", {"gpio": "switchPin", "above": 45.0, "hysteresis": 5.0}, "thermal")
    levels = [switch.action(t)["level"] for t in (44.0, 45.5, 44.0, 40.5, 39.9, 44.0, 45.1)]
    assert levels == [False, True, True, True, False, False, True]
    low = Output("V", {"gpio": "lowBattery", "below": 7.0}, "power")
    assert [low.action(v)["level"] for v in (7.5, 6.9, 7.0)] == [False, True, False]


# ---------------------------------------------------------------------------
# The coupling, stepped by the conductor
# ---------------------------------------------------------------------------

class PlantElement(FakeElement):
    """An element in lockstep that also answers what a coupling asks: pins, models and
    registers to read, and batches of actions to apply"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pins = {"heaterPin": False}
        self.models = {("tempSensor", "text"): "$TEMP,21.5\n", ("fpga", "reg.0x0C"): "215"}
        self.batches = []  # (tick, [actions])
        self.reads = []

    def _serve(self, connection):
        for line in connection.makefile("r"):
            message = json.loads(line)
            op = message["op"]
            response = {"id": message["id"], "ok": True}
            if op == "gpio_get":
                self.reads.append((self.tick, op))
                response["level"] = self.pins[message["endpoint"]]
            elif op == "model_get":
                self.reads.append((self.tick, op))
                response["value"] = self.models[(message["model"], message["key"])]
            elif op == "reg_get":
                response["value"] = "0x2a"
            elif op == "batch":
                actions = [json.loads(text) for text in message["actions"].split("\n")]
                self.batches.append((self.tick, actions))
                for action in actions:
                    if action["op"] == "model_set":
                        self.models[(action["model"], action["key"])] = action["value"]
                response["applied"] = len(actions)
            elif op in ("status", "advance"):
                response.update(self.status())
                if op == "advance":
                    self.tick += message["ticks"]
                    response.update(self.status(), done=True)
            connection.sendall((json.dumps(response) + "\n").encode())


PLANT = textwrap.dedent('''
    class Heated:
        def __init__(self, rate):
            self.rate, self.temperature, self.calls = rate, 21.5, []
        def start(self, t0):
            self.t0 = t0
        def step(self, t, dt, inputs):
            self.calls.append((round(t, 9), round(dt, 9), inputs["heater"]))
            self.temperature += self.rate * dt * inputs["heater"]
            return {"T": self.temperature}
    def create(params):
        return Heated(**params)
''')


def one_element_system(element):
    return {"system": "Demo", "ground": "payload", "elements": [{"name": "payload", "bridge": f"tcp:{element.port}"}],
            "links": []}


@pytest.fixture
def plant_dir(tmp_path):
    (tmp_path / "heated.py").write_text(PLANT)
    return tmp_path


def spec(**extra):
    entry = {"name": "heated", "python": "heated.py", "params": {"rate": 2.0},
             "inputs": {"heater": {"gpio": "heaterPin"}},
             "outputs": {"T": [{"model": "tempSensor", "key": "text", "format": "$TEMP,{:.1f}\n"},
                               {"model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True}]}, **extra}
    return parse_specs([entry], "wsts.yaml")


def test_the_conductor_steps_a_plant_between_instants(plant_dir):
    payload = PlantElement("payload", 50_000_000)
    description = {**one_element_system(payload), "directory": str(plant_dir), "log_dir": str(plant_dir / "logs")}
    with Conductor(description, request_timeout=2, dynamics=spec()) as conductor:
        conductor.run(instants=2)  # the heater off: the first exchange writes, the second has nothing new
        payload.pins["heaterPin"] = True
        conductor.run(instants=3)
        coupling = conductor.couplings[0]
        assert [call[:2] for call in coupling.plant.calls] == [(0.0, 0.05), (0.05, 0.05), (0.1, 0.05), (0.15, 0.05),
                                                               (0.2, 0.05)], "from the last instant to the next"
        assert [call[2] for call in coupling.plant.calls] == [0.0, 0.0, 1.0, 1.0, 1.0]
        state = conductor.dynamics("heated")
        assert state["inputs"] == {"heater": 1.0} and state["outputs"]["T"] == pytest.approx(21.8)
        assert state["time"] == pytest.approx(0.25)
    # Written before the instant they are for, only when they changed, marked as the plant's
    assert [tick for tick, _ in payload.batches] == [0, 2, 3, 4]
    first = payload.batches[0][1]
    assert first == [{"op": "model_set", "model": "tempSensor", "key": "text", "value": "$TEMP,21.5\n", "via": "heated"},
                     {"op": "model_set", "model": "fpga", "key": "reg.0x0C", "value": "215", "via": "heated"}]
    assert [action["value"] for action in payload.batches[1][1]] == ["$TEMP,21.6\n", "216"]
    assert payload.models[("tempSensor", "text")] == "$TEMP,21.8\n"
    assert [tick for tick, op in payload.reads] == [0, 1, 2, 3, 4], "inputs read after each instant"
    lines = [json.loads(line) for line in (plant_dir / "logs" / "dynamics-heated.jsonl").read_text().splitlines()]
    assert [line["t"] for line in lines] == pytest.approx([0.05, 0.1, 0.15, 0.2, 0.25])
    assert lines[-1]["in"] == {"heater": 1.0} and lines[-1]["out"]["T"] == pytest.approx(21.8)


def test_a_plant_with_a_step_of_its_own_exchanges_every_few_instants(plant_dir):
    payload = PlantElement("payload", 50_000_000)
    payload.pins["heaterPin"] = True
    description = {**one_element_system(payload), "directory": str(plant_dir)}
    with Conductor(description, request_timeout=2, dynamics=spec(step=0.2)) as conductor:
        conductor.run(instants=8)
        calls = conductor.couplings[0].plant.calls
    assert [call[:2] for call in calls] == [(0.0, 0.2), (0.2, 0.2)], "at whole steps from where it started"
    assert [tick for tick, _ in payload.reads] == [3, 7], "read after the instant before each step's end"


def test_a_conductor_without_dynamics_leaves_the_plants_out(plant_dir):
    payload = PlantElement("payload", 50_000_000)
    description = {**one_element_system(payload), "directory": str(plant_dir), "dynamics": spec()}
    with Conductor(description, request_timeout=2, dynamics=False) as conductor:
        conductor.run(instants=2)
        assert conductor.couplings == []
    assert payload.batches == [] and payload.reads == []
    with Conductor(description, request_timeout=2) as conductor:  # the description's, by default
        conductor.run(instants=1)
    assert len(payload.batches) == 1


@pytest.mark.parametrize("source, message", [
    ("def create(params):\n    return object()\n", "has no step"),
    ("x = 1\n", "does not define create"),
    ("class P:\n    def step(self, t, dt, inputs):\n        return {}\ndef create(params):\n    return P()\n",
     "no value for output"),
    ("class P:\n    def step(self, t, dt, inputs):\n        raise ValueError('diverged')\n"
     "def create(params):\n    return P()\n", "failed to step from 0 s by 0.05 s: ValueError: diverged"),
])
def test_a_plant_that_cannot_step_stops_the_conductor(tmp_path, source, message):
    (tmp_path / "heated.py").write_text(source)
    payload = PlantElement("payload", 50_000_000)
    description = {**one_element_system(payload), "directory": str(tmp_path)}
    with Conductor(description, request_timeout=2, dynamics=spec()) as conductor:
        with pytest.raises(LockstepError, match=message):
            conductor.run(instants=1)
    assert payload.tick == 0, "the instant was not taken"


def test_a_plant_names_elements_that_have_bridges(plant_dir):
    payload = PlantElement("payload", 50_000_000)
    [entry] = spec()
    entry["inputs"]["heater"]["element"] = "cdh"
    with pytest.raises(DynamicsError, match="element\\(s\\) cdh have no bridge"):
        Conductor({**one_element_system(payload), "directory": str(plant_dir)}, dynamics=[entry])


def test_the_example_plant_trips_the_switch_when_heated():
    from fprime_wsts.manifest import TOOLKIT_ROOT

    plant = dynamics.load_python(TOOLKIT_ROOT / "examples" / "mission-sim" / "plants" / "thermal.py",
                                 {"C": 5.0, "P": 10.0, "G": 0.1, "T_env": 21.5, "T0": 21.5})
    temperature, t = 21.5, 0.0
    while temperature < 45.0:
        temperature = plant.step(t, 0.05, {"heater": 1.0})["T"]
        t += 0.05
    assert 13.0 < t < 15.0
    assert plant.step(t, 0.05, {"heater": 0.0})["T"] < temperature
