"""Lockstep (docs/stepped-time.md, phase 4): the Mission system stepped as a whole, Cdh at
10 Hz and Payload at 20 Hz on one time base, with what the hub carries placed in time.

The system of the session runs in real time; a test switches it to lockstep and back. Whole
runs in lockstep are started here as processes of their own."""

import json
import signal
import subprocess
import sys
import time

import pytest

from conftest import T, events_seen
from fprime_wsts.bridge import WstsSystem
from fprime_wsts.runner import read_run_file


@pytest.fixture
def lockstep(wsts_system):
    """The session's system in lockstep, for the test alone"""
    wsts_system.pause()
    wsts_system.set_stepped(True)
    yield wsts_system
    wsts_system.set_stepped(False)
    wsts_system.resume()


def element_seconds(status):
    return status["tick"] * status["tick_period_ns"] / 1e9


def test_the_system_steps_as_one(lockstep):
    start = lockstep.time()
    began = time.monotonic()
    reached = lockstep.step(seconds=2.0)
    assert time.monotonic() - began < 10
    # The elements were started one after the other and ran on their own clocks: the one
    # behind caught up first. Now both are at the system's time, each at its own last tick.
    assert reached == pytest.approx(start + 2.0)
    status = lockstep.status()
    assert element_seconds(status["payload"]) == pytest.approx(reached)
    assert reached - 0.1 < element_seconds(status["cdh"]) <= reached + 1e-9
    for element in ("cdh", "payload"):
        assert status[element]["lockstep"] and not status[element].get("stuck")
        lockstep[element].wait_quiescent()
    # A step of its own would take one element out of step with the other
    with pytest.raises(Exception, match="conductor"):
        lockstep["payload"].step(1)


def test_a_command_crosses_the_hub_and_its_events_come_back_in_lockstep(gds, lockstep):
    lockstep.step(seconds=0.5)
    # Commands arrive from the ground whenever the GDS sends them: the ground link is outside
    # input. Through the hub they go in simulated time, and only while the system is stepped.
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [5])
    for _ in range(50):
        lockstep.step(seconds=0.2)
        if events_seen(gds, "SampleTaken"):
            break
    event = gds.assert_event("Payload.instrument.SampleTaken", timeout=T)
    assert event.get_display_text().endswith("taken at gain 5")


def run_system(request, tmp_path, *options, seconds=60):
    system = request.config.getoption("--wsts-system")
    command = [sys.executable, "-m", "fprime_wsts", "run", system, "--stepped", "--auto-ports",
               "--duration", str(seconds), "--log-dir", str(tmp_path / "logs"), "--json", *map(str, options)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_run_of_the_system_in_lockstep(request, tmp_path):
    summary = run_system(request, tmp_path, seconds=60)
    assert summary["lockstep"] and summary["sim_seconds"] == 60.0 and not summary.get("stuck")
    cdh, payload = summary["elements"]["cdh"]["final_status"], summary["elements"]["payload"]["final_status"]
    assert cdh["tick"] == 600 and payload["tick"] == 1200, "a minute of flight time on each, to the tick"
    assert cdh["busy_ticks"] == 0 and payload["busy_ticks"] == 0
    # Everything Payload sent through the hub arrived at Cdh
    assert payload["link_traffic"]["hub"]["sent"] == cdh["link_traffic"]["hub"]["received"] > 1000
    assert summary["speed"] > 5, f"{summary['speed']} times real time"


def test_two_runs_of_the_system_in_lockstep_are_the_same_byte_for_byte(request, tmp_path):
    """What phase 4 of stepped time is for: the whole system repeatable. Each element's
    recording holds its bus traffic, and what the hub brought it with the tick it arrived in."""
    recordings = []
    for _ in range(3):
        run_system(request, tmp_path, "--record", tmp_path / "run.jsonl", seconds=60)
        recordings.append({element: (tmp_path / f"run.{element}.jsonl").read_bytes() for element in ("cdh", "payload")})
        for element in ("cdh", "payload"):
            (tmp_path / f"run.{element}.jsonl").unlink()
    arrived = [json.loads(line) for line in recordings[0]["cdh"].splitlines()]
    assert sum(1 for m in arrived if m.get("rec") == "bs_rx" and m.get("src") == "link") > 1000, \
        "Cdh's recording has what Payload sent it"
    assert recordings[1] == recordings[0] and recordings[2] == recordings[0]


def test_a_system_scenario_places_actions_on_one_time_base(request, tmp_path):
    scenario = tmp_path / "system.jsonl"
    scenario.write_text("\n".join(json.dumps(line) for line in [
        {"time": 1.0, "element": "payload", "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\n"},
        {"time": 2.0, "link": "hub", "op": "fault_set", "kind": "drop", "direction": "b_to_a"},
        {"time": 3.0, "link": "hub", "op": "fault_clear"},
    ]) + "\n")
    run_system(request, tmp_path, "--scenario", scenario, "--record", tmp_path / "run.jsonl", seconds=4)
    payload = [json.loads(line) for line in (tmp_path / "run.payload.jsonl").read_text().splitlines()]
    cdh = [json.loads(line) for line in (tmp_path / "run.cdh.jsonl").read_text().splitlines()]
    # One second is Payload's tick 20 and Cdh's tick 10: each action at its element's own tick
    injected = [m for m in payload if m.get("op") == "inject_bytes"]
    assert [m["tick"] for m in injected] == [20]
    assert [(m["op"], m["tick"]) for m in cdh if m.get("op", "").startswith("fault_")] == \
        [("fault_set", 20), ("fault_clear", 30)]
    # While the link was cut towards Cdh, nothing of Payload's reached it
    arrived = sorted({m["tick"] for m in cdh if m.get("rec") == "bs_rx" and m.get("src") == "link"})
    assert not [tick for tick in arrived if 20 < tick <= 30]
    assert 20 in arrived and 31 in arrived


def test_a_pause_holds_the_whole_system(request, tmp_path):
    """Without a duration the conductor runs freely, until interrupted; a pause of any
    element (here through the system's client; from the GDS, SIM_PAUSE) holds it"""
    system = request.config.getoption("--wsts-system")
    logs = tmp_path / "logs"
    command = [sys.executable, "-m", "fprime_wsts", "run", system, "--stepped", "--auto-ports",
               "--log-dir", str(logs), "--json"]
    runner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while read_run_file(logs) is None:
            assert runner.poll() is None and time.monotonic() < deadline, runner.stderr.read() if runner.poll() else ""
            time.sleep(0.1)
        description = json.loads((__import__("pathlib").Path(system) / "wsts-system.json").read_text())
        running = read_run_file(logs)
        for element in description["elements"]:
            element["bridge"] = running["elements"][element["name"]]
        with WstsSystem(description) as client:
            def ticks():
                return {name: status["tick"] for name, status in client.status().items()}

            first = ticks()
            time.sleep(0.5)
            assert all(now > first[name] for name, now in ticks().items()), "running freely"
            client["cdh"].pause()  # one element: the system stops at the end of the instant
            time.sleep(0.3)
            held = ticks()
            time.sleep(0.5)
            assert ticks() == held, "the whole system holds"
            assert abs(held["payload"] * 0.05 - held["cdh"] * 0.1) <= 0.1, "at one time"
            client.resume()
            time.sleep(0.5)
            assert all(now > held[name] for name, now in ticks().items())
    finally:
        runner.send_signal(signal.SIGINT)
        out, err = runner.communicate(timeout=60)
    summary = json.loads(out)
    assert summary["interrupted"] and summary["lockstep"] and not summary.get("stuck"), err
