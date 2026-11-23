"""A plant in the loop (docs/dynamics.md): the thermal model of Payload's heated panel
(examples/mission-sim/plants), stepped with the system in lockstep. The heater flight
software drives warms it; the serial sensor, the FPGA's temperature register and the
over-temperature switch read it; HeaterControl turns the heater off when the switch trips.

The first test closes the loop on the session's system, with a ground command. The others
start runs of their own, without a GDS, and compare their recordings."""

import json
import shutil
import time
from pathlib import Path

import pytest

from conftest import T, channels_seen, events_seen
from fprime_wsts.dynamics import parse_specs
from fprime_wsts.lockstep import Conductor
from fprime_wsts.runner import SystemSimulation

PLANTS = Path(__file__).resolve().parents[2] / "examples" / "mission-sim" / "plants"


@pytest.fixture
def coupled(wsts_system):
    """The session's system in lockstep, its plant stepped with it, for the test alone. The
    models the plant writes are given back their manifest's values afterwards."""
    wsts_system.pause()
    wsts_system.set_stepped(True)
    yield wsts_system
    payload = wsts_system["payload"]
    wsts_system.set_stepped(False)
    payload.model_set("tempSensor", "text", "$TEMP,21.5\n")
    payload.model_set("fpga", "reg.0x0C", "215")
    payload.gpio_force("switchPin", False)
    wsts_system.resume()


def test_the_heater_loop_closes_through_flight_software_and_the_plant(gds, coupled):
    payload = coupled["payload"]
    coupled.step(seconds=1.0)
    assert coupled.dynamics("thermal")["outputs"]["T"] == pytest.approx(21.5), "at its surroundings' temperature"
    gds.send_command("Payload.heater.HEATER_SET", ["ON"])
    for _ in range(100):  # the command reaches Payload through the hub, in simulated time
        coupled.step(seconds=0.1)
        if payload.gpio_get("heaterPin"):
            break
    assert payload.gpio_get("heaterPin"), "flight software turned the heater on"
    heated_at = coupled.time()
    for _ in range(400):  # 2 K/s from 21.5 C: the switch closes at 45 C, some 12 s later
        coupled.step(instants=1)  # one at a time: the last exchange is the one that closed the switch
        if not payload.gpio_get("heaterPin"):
            break
    tripped_at = coupled.time()
    state = coupled.dynamics("thermal")
    assert not payload.gpio_get("heaterPin"), "HeaterControl turned the heater off when the switch tripped"
    assert 10.0 < tripped_at - heated_at < 15.0
    # The exchange that closed the switch read the heater on; the next ones read it off
    assert 45.0 <= state["outputs"]["T"] < 45.5 and state["inputs"]["heater"] == 1.0
    # What flight software was given is the plant's temperature
    assert payload.model_get("fpga", "reg.0x0C") == str(int(state["outputs"]["T"] * 10 + 0.5))
    assert payload.model_get("tempSensor", "text") == f"$TEMP,{state['outputs']['T']:.1f}\n"
    # The panel cools; its temperature reaches the ground as flight software read it
    coupled.step(seconds=3.0)
    cooled = coupled.dynamics("thermal")
    assert cooled["inputs"]["heater"] == 0.0 and cooled["outputs"]["T"] < state["outputs"]["T"]
    gds.assert_event("Payload.heater.OverTemperature", timeout=T)
    for channel in ("Payload.fpga.Temperature", "Payload.sensor.Temperature"):
        for _ in range(50):
            if any(sample.get_val() > 40.0 for sample in channels_seen(gds, channel)):
                break
            coupled.step(seconds=0.2)
        assert any(sample.get_val() > 40.0 for sample in channels_seen(gds, channel)), channel


def hot_start(kind, path):
    """The example's plant, but starting hot: the switch closes at the first exchange, and
    flight software reacts to it without being commanded"""
    [spec] = parse_specs([{
        "name": "thermal", kind: str(path), "params": {"T0": 50.0},
        "inputs": {"heater": {"element": "payload", "gpio": "heaterPin"}},
        "outputs": {"T": [{"element": "payload", "model": "tempSensor", "key": "text", "format": "$TEMP,{:.1f}\n"},
                          {"element": "payload", "model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True},
                          {"element": "payload", "gpio": "switchPin", "above": 45, "hysteresis": 5}]},
    }], "test", elements=["cdh", "payload"])
    return spec


def coupled_run(request, directory: Path, spec, seconds=30.0):
    """A run of the system in lockstep with a plant, recorded from start-up"""
    sim = SystemSimulation(Path(request.config.getoption("--wsts-system")), auto_ports=True, stepped=True,
                           log_dir=directory / "logs",
                           records={name: str(directory / f"{name}.jsonl") for name in ("cdh", "payload")})
    sim.start()
    try:
        with Conductor(sim.description, dynamics=[spec]) as conductor:
            conductor.wait_ready(20.0, alive=lambda: not sim.exited())
            began = time.monotonic()
            conductor.run(until_ns=int(seconds * 1e9))
            wall = time.monotonic() - began
            state = conductor.dynamics("thermal")
    finally:
        sim.stop()
    return state, wall


@pytest.fixture(scope="module")
def runs(request, tmp_path_factory):
    """Two runs with the Python plant and one with the same plant as an FMU"""
    from fprime_wsts.fmi import build_fmu

    if not (shutil.which("cc") or shutil.which("gcc")):
        pytest.skip("needs a C compiler for the FMU")
    base = tmp_path_factory.mktemp("dynamics")
    fmu = build_fmu([PLANTS / "thermal_fmu" / "thermal.c"], PLANTS / "thermal_fmu" / "modelDescription.xml",
                    base / "Thermal.fmu")
    found = {}
    for name, kind, path in (("python-1", "python", PLANTS / "thermal.py"), ("python-2", "python", PLANTS / "thermal.py"),
                             ("fmu", "fmu", fmu)):
        found[name] = (base / name, *coupled_run(request, base / name, hot_start(kind, path)))
    return found


def records(path: Path):
    import json

    return [json.loads(line) for line in path.read_text().splitlines()]


def test_flight_software_reacts_to_the_plant_without_being_commanded(runs):
    directory, state, wall = runs["python-1"]
    payload = records(directory / "payload.jsonl")
    switch = [(r["tick"], r["level"]) for r in payload if r.get("op") == "gpio_force" and r["endpoint"] == "switchPin"]
    assert [level for _, level in switch] == [True, False], "closed hot, opened again below 40 C"
    assert 20.0 < (switch[1][0] - switch[0][0]) / 20 < 23.0, "C/G = 50 s: from 50 C to 40 C in about 21.6 s"
    heater = [r for r in payload if r.get("rec") == "gpio_out" and r["endpoint"] == "heaterPin"]
    # The edge, given between two ticks, ran HeaterControl's handler at once: its write is
    # recorded at the last tick taken, and the action at the tick it takes effect in
    assert heater and heater[0]["tick"] == switch[0][0] - 1, "HeaterControl wrote the heater off when the switch tripped"
    assert all(r.get("via") == "thermal" for r in payload if r.get("op") in ("model_set", "gpio_force"))
    assert 30.0 < state["outputs"]["T"] < 40.0
    log = (directory / "logs" / "dynamics-thermal.jsonl").read_text().splitlines()
    assert len(log) == 600, "an exchange before every instant of Payload's 20 Hz"


def test_two_coupled_runs_and_the_fmu_record_the_same_bytes(runs):
    first, second, fmu = (runs[name][0] for name in ("python-1", "python-2", "fmu"))
    for element in ("payload", "cdh"):
        reference = (first / f"{element}.jsonl").read_bytes()
        assert reference, element
        assert (second / f"{element}.jsonl").read_bytes() == reference, f"{element}: two runs of the Python plant"
        assert (fmu / f"{element}.jsonl").read_bytes() == reference, f"{element}: the FMU computes the same bits"


def test_a_restored_run_goes_on_coupled_as_if_it_had_not_stopped(request, runs, tmp_path):
    """Phase 3: a journaled run with the plant, snapshotted at 10 s, restored (the plant
    re-driven along its trace, compared exchange by exchange) and run on to 30 s, is the run
    that never stopped: the same bus records on both elements, the same trace, the same plant.
    The switch opens again at about 21.6 s, after the restore: the threshold's state came back."""
    from fprime_wsts import snapshots

    spec = hot_start("python", PLANTS / "thermal.py")
    system_dir = Path(request.config.getoption("--wsts-system"))
    first = SystemSimulation(system_dir, auto_ports=True, stepped=True, journal=True, log_dir=tmp_path / "first")
    first.start()
    try:
        with Conductor(first.description, dynamics=[spec]) as conductor:
            conductor.wait_ready(20.0, alive=lambda: not first.exited())
            conductor.run(until_ns=10_000_000_000)
            snap = snapshots.take(first.log_dir, tmp_path / "at-10s")
    finally:
        first.stop()
    assert snap.meta["plants"] == ["thermal"]
    assert json.loads((snap.path / "dynamics-thermal.jsonl").read_text().splitlines()[-1])["t"] == 10.0

    restored = SystemSimulation(snap.directory, auto_ports=True, restore=snap, log_dir=tmp_path / "restored")
    restored.start()
    try:
        with Conductor(restored.description, dynamics=[spec]) as conductor:
            conductor.wait_ready(20.0, alive=lambda: not restored.exited())
            restored.complete_restore(conductor, keep_going=lambda: not restored.exited())
            assert conductor.dynamics("thermal")["time"] == 10.0
            conductor.run(until_ns=30_000_000_000)
            state = conductor.dynamics("thermal")
    finally:
        restored.stop()

    reference, reference_state, _ = runs["python-1"]
    assert state == reference_state
    assert (tmp_path / "restored" / "dynamics-thermal.jsonl").read_text() == \
        (reference / "logs" / "dynamics-thermal.jsonl").read_text()
    for element in ("payload", "cdh"):
        assert snapshots.bus_records(tmp_path / "restored" / element / "journal.jsonl") == \
            snapshots.bus_records(reference / f"{element}.jsonl"), element


def test_a_plant_that_differs_from_the_snapshot_fails_its_restore(request, tmp_path):
    """The same snapshot, restored with the plant changed (a heater twice as strong does not
    matter while it is off; a panel that cools faster does): the first exchange that differs
    stops the restore, and says where"""
    from fprime_wsts import snapshots
    from fprime_wsts.dynamics import PlantMismatch

    spec = hot_start("python", PLANTS / "thermal.py")
    system_dir = Path(request.config.getoption("--wsts-system"))
    first = SystemSimulation(system_dir, auto_ports=True, stepped=True, journal=True, log_dir=tmp_path / "first")
    first.start()
    try:
        with Conductor(first.description, dynamics=[spec]) as conductor:
            conductor.wait_ready(20.0, alive=lambda: not first.exited())
            conductor.run(until_ns=2_000_000_000)
            snap = snapshots.take(first.log_dir, tmp_path / "at-2s")
    finally:
        first.stop()
    changed = json.loads(json.dumps(spec))
    changed["params"]["G"] = 0.2
    restored = SystemSimulation(snap.directory, auto_ports=True, restore=snap, log_dir=tmp_path / "restored")
    restored.start()
    try:
        with Conductor(restored.description, dynamics=[changed]) as conductor:
            conductor.wait_ready(20.0, alive=lambda: not restored.exited())
            with pytest.raises(PlantMismatch, match="exchange 0") as raised:
                restored.complete_restore(conductor, keep_going=lambda: not restored.exited())
    finally:
        restored.stop()
    assert raised.value.expected["out"]["T"] > raised.value.found["out"]["T"], "it cooled faster"
