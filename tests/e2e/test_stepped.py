"""Stepped time (docs/stepped-time.md, phase 1): a tick is taken when flight software has
finished the last one, not when the wall clock says so.

The system of the session runs in real time. One element can be switched for a test that
pauses and steps it; a whole run of one element in stepped time is started here as a process
of its own. The whole system stepped together is test_lockstep.py."""

import json
import subprocess
import sys
import time

import pytest

from conftest import T


@pytest.fixture
def stepped(payload):
    """Payload in stepped time and paused, for the test alone"""
    payload.pause()
    payload.set_stepped(True)
    yield payload
    payload.set_stepped(False)
    payload.resume()


def test_a_minute_of_flight_time_in_a_moment(stepped):
    start = stepped.status()
    assert start["stepped"] and start["paused"] and start["tick_period_ns"] == 50_000_000  # 20 Hz
    began = time.monotonic()
    assert stepped.step(1200, timeout=T) == start["tick"] + 1200, "exactly a minute of ticks, and finished"
    wall = time.monotonic() - began
    after = stepped.status()
    assert wall < 6, f"a minute of simulated time took {wall:.1f} s: it should not wait for the wall clock"
    assert after["sim_seconds"] - start["sim_seconds"] == pytest.approx(60.0)
    # (busy_ticks may move here: Cdh runs on in real time, and what it sends through the hub
    # can arrive just as a tick starts. A deployment by itself has none: the whole-run test.)
    stepped.wait_quiescent()
    assert not after.get("stuck")
    # The flight rate groups did run in every one of them: the IMU is read at 10 Hz
    assert after["transactions"] - start["transactions"] > 600


def test_what_a_client_sends_is_there_before_the_tick(gds, stepped):
    # Paused between two ticks: the bytes are in, and the one tick that follows reads them.
    # No timeout is needed to know that flight software has seen them.
    stepped.model_set("tempSensor", "period", 0)  # the sensor model keeps quiet meanwhile
    try:
        stepped.inject_text("sensorUart", "$TEMP,71.5\n")
        stepped.step(1)
    finally:
        stepped.model_set("tempSensor", "period", 20)
    # The event is on its way through the hub: only its arrival at the ground needs waiting
    gds.assert_telemetry("Payload.sensor.Temperature", value=pytest.approx(71.5), timeout=T)


def test_a_limit_paces_stepped_time_for_someone_who_watches(stepped):
    stepped.set_rate(1)  # as real time, though each tick still waits for the last to finish
    assert stepped.status()["rate"] == 1
    began = time.monotonic()
    stepped.step(20, timeout=T)  # one simulated second
    assert 0.7 < time.monotonic() - began < 3
    stepped.set_rate(0)
    began = time.monotonic()
    stepped.step(20, timeout=T)
    assert time.monotonic() - began < 0.7, "no limit: as fast as it goes"


def run_alone(request, tmp_path, element, seconds, record=None):
    """One element by itself (its hub link stays down), a run of a set simulated duration"""
    system = request.config.getoption("--wsts-system")
    command = [sys.executable, "-m", "fprime_wsts", "run", f"{system}/{element}", "--stepped", "--auto-ports",
               "--duration", str(seconds), "--log-dir", str(tmp_path / "logs"), "--json"]
    if record:
        command += ["--record", str(record)]
    began = time.monotonic()
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout), time.monotonic() - began


def test_a_whole_run_in_stepped_time(request, tmp_path):
    summary, wall = run_alone(request, tmp_path, "payload", 300)
    assert summary["stepped"] and summary["exit_code"] == 0 and not summary.get("stuck")
    assert summary["sim_seconds"] == 300.0, "five minutes of flight time, to the tick"
    assert summary["final_status"]["tick"] == 6000 and summary["final_status"]["busy_ticks"] == 0
    assert summary["speed"] > 10 and wall < 60, f"{summary['speed']} times real time"
    # How long the ticks took in host time: every tick timed, the slowest first
    times = summary["tick_times"]
    assert 5999 <= times["ticks"] <= 6000, "the last tick may still be finishing when the summary is read"
    assert summary["final_status"]["tick_times"] == times
    assert 1 <= len(times["slowest"]) <= 5 and times["slowest"][0]["ms"] == times["max_ms"] >= times["mean_ms"]
    assert [s["ms"] for s in times["slowest"]] == sorted((s["ms"] for s in times["slowest"]), reverse=True)
    assert all(1 <= s["tick"] <= 6000 for s in times["slowest"])


@pytest.mark.parametrize("element, seconds", [("payload", 120), ("cdh", 120)])
def test_two_runs_in_stepped_time_are_the_same_byte_for_byte(request, tmp_path, element, seconds):
    """What phase 2 of stepped time is for. Everything flight software puts on a bus is in the
    recording: every I2C, SPI and register transaction, and every packet of events and
    telemetry it sends towards the ground through the hub, with its time stamp."""
    recording = tmp_path / "run.jsonl"  # the same name both times: an event of the simulation names it
    recordings = []
    for _ in range(3):
        summary, _ = run_alone(request, tmp_path, element, seconds, recording)
        assert summary["exit_code"] == 0 and not summary.get("stuck")
        recordings.append(recording.read_bytes())
        recording.unlink()
    assert len(recordings[0].splitlines()) > 100, "there is something to compare"
    assert recordings[1] == recordings[0] and recordings[2] == recordings[0]


def test_flight_software_is_given_simulated_time(request, tmp_path):
    recording = tmp_path / "run.jsonl"
    run_alone(request, tmp_path, "payload", 10, recording)
    # Payload sends its events and telemetry to Cdh through the hub: each packet carries the
    # time flight software was given. 2000-01-01T00:00:00Z is 0x386d4380 seconds.
    sent = [json.loads(line) for line in recording.read_text().splitlines()]
    packets = [m["data"] for m in sent if m.get("rec") == "bs_tx" and m.get("endpoint") == "hubDriver"]
    stamped = [p for p in packets if "386d43" in p]
    assert len(stamped) > 50 and len(stamped) > 0.9 * len(packets)
    assert any("386d438a" in p for p in packets), "ten simulated seconds on"
