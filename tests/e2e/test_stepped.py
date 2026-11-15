"""Stepped time (docs/stepped-time.md, phase 1): a tick is taken when flight software has
finished the last one, not when the wall clock says so.

The system of the session runs in real time. One element can be switched for a test that
pauses and steps it; a whole run in stepped time is a single deployment so far, started
here as a process of its own."""

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


def run_alone(request, tmp_path, name, seconds):
    """Payload by itself (its hub link stays down), a run of a set simulated duration"""
    system = request.config.getoption("--wsts-system")
    command = [sys.executable, "-m", "fprime_wsts", "run", f"{system}/payload", "--stepped", "--auto-ports",
               "--duration", str(seconds), "--log-dir", str(tmp_path / name), "--json"]
    began = time.monotonic()
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout), time.monotonic() - began


def test_a_whole_run_in_stepped_time(request, tmp_path):
    first, wall = run_alone(request, tmp_path, "first", 300)
    assert first["stepped"] and first["exit_code"] == 0 and not first.get("stuck")
    assert first["sim_seconds"] == 300.0, "five minutes of flight time, to the tick"
    assert first["final_status"]["tick"] == 6000 and first["final_status"]["busy_ticks"] == 0
    assert first["speed"] > 10 and wall < 60, f"{first['speed']} times real time"
    # Again: the same ticks, and flight software was given the same amount of work
    second, _ = run_alone(request, tmp_path, "second", 300)
    assert second["final_status"]["tick"] == 6000
    assert second["final_status"]["transactions"] == pytest.approx(first["final_status"]["transactions"], abs=20)


def test_stepping_a_whole_system_is_not_built_yet(request):
    system = request.config.getoption("--wsts-system")
    done = subprocess.run([sys.executable, "-m", "fprime_wsts", "run", system, "--stepped", "--duration", "1"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "single deployment so far" in done.stderr
