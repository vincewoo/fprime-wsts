"""SubtopDemo with a device simulator written as an F Prime component.

The flight UART driver is replaced by the stock surrogate. Behind its bus endpoint is
SensorSim.SerialSensorSim, a queued component of the simulation project, connected through
the Wsts.BusByteStreamDevice adapter and scheduled by a flight rate group. It is commanded
through the same GDS as the flight software.
"""

import json
import time

T = 10

TEMPERATURE = "SensorLink.sensor.Temperature"
LINES = "SensorLink.sensor.LinesReceived"


def test_simulator_data_reaches_the_flight_component(gds, wsts):
    assert wsts.status()["endpoints"] == ["uart"]
    gds.assert_telemetry(TEMPERATURE, value=21.5, timeout=T)
    gds.assert_telemetry("SensorSim.sensorSim.ReportsSent", timeout=T)


def test_flight_output_reaches_the_simulator(gds, wsts):
    gds.send_and_assert_command("SensorLink.sensor.SEND_QUERY", ["ID?"], timeout=T)
    # The simulator's receive port is asynchronous: it handles the query on its rate group
    event = gds.assert_event("SensorSim.sensorSim.QueryReceived", timeout=T)
    assert event.get_display_text().endswith("ID?")
    assert b"ID?\n" in wsts.bytes_sent("uart")


def test_simulator_parameter_changes_what_flight_reports(gds):
    gds.send_and_assert_command("SensorSim.sensorSim.TEMPERATURE_PRM_SET", [30.0], timeout=T)
    try:
        gds.assert_telemetry(TEMPERATURE, value=30.0, timeout=T)
        # The simulator's own command, alongside the parameter
        gds.send_and_assert_command("SensorSim.sensorSim.SET_BIAS", [-5.0], timeout=T)
        gds.assert_telemetry(TEMPERATURE, value=25.0, timeout=T)
    finally:
        gds.send_and_assert_command("SensorSim.sensorSim.SET_BIAS", [0.0], timeout=T)
        gds.send_and_assert_command("SensorSim.sensorSim.TEMPERATURE_PRM_SET", [21.5], timeout=T)
    gds.assert_telemetry(TEMPERATURE, value=21.5, timeout=T)


def test_drop_fault_stops_the_simulator_data_and_clear_restores_it(gds, wsts):
    gds.assert_telemetry(LINES, timeout=T)
    wsts.fault_set("uart", "drop", direction="to_flight")
    try:
        wsts.advance(15)  # let anything already on its way be downlinked
        gds.clear_histories()
        assert gds.await_telemetry(LINES, timeout=4) is None, "sensor lines still arrive through a dropped UART"
        # The simulator itself keeps running: the fault is on the link
        gds.assert_telemetry("SensorSim.sensorSim.ReportsSent", timeout=T)
    finally:
        wsts.fault_clear("uart")
    gds.assert_telemetry(LINES, timeout=T)


def test_recording_shows_simulator_replies_as_model_traffic(gds, wsts, tmp_path):
    recording = tmp_path / "run.jsonl"
    wsts.record_start(recording)
    gds.send_and_assert_command("SensorLink.sensor.SEND_QUERY", ["ID?"], timeout=T)
    wsts.advance(25)
    wsts.record_stop()
    records = [json.loads(line) for line in recording.read_text().splitlines()]
    replies = [r for r in records if r.get("rec") == "bs_rx" and r.get("endpoint") == "uart"]
    assert replies and all(r["src"] == "model" for r in replies)
    assert any(r.get("rec") == "bs_tx" and r.get("endpoint") == "uart" and r["src"] == "flight" for r in records)


def test_bridge_injection_still_reaches_flight_software(gds, wsts):
    wsts.inject_text("uart", "not a reading\n")
    gds.assert_event("SensorLink.sensor.UnrecognizedLine", timeout=T)


def test_pause_stops_the_simulator_with_the_flight_rate_groups(gds, wsts):
    wsts.pause()
    try:
        time.sleep(0.3)
        before = wsts.status()["transactions"]
        time.sleep(2.5)  # the simulator reports once a second when it runs
        assert wsts.status()["transactions"] == before
    finally:
        wsts.resume()
    wsts.advance(15)
    assert wsts.status()["transactions"] > before


# ----------------------------------------------------------------------
# A simulator on a clock of its own: SensorSim.simClock at 125 Hz cycles a passive rate
# group of the simulation, which steps SensorSim.gyroSim. The flight tick is 10 Hz.
# ----------------------------------------------------------------------

STEPS = "SensorSim.gyroSim.Steps"
CLOCK_HZ = 125.0


def sample(gds):
    """A (steps, seconds) pair: the step count and the time it was written at"""
    gds.clear_histories()
    item = gds.await_telemetry(STEPS, timeout=T)
    assert item is not None, "no gyro simulator telemetry"
    time_tag = item.get_time()
    return item.get_val(), time_tag.seconds + time_tag.useconds / 1e6


def measured_rate(gds, seconds=4.0):
    steps_a, time_a = sample(gds)
    time.sleep(seconds)
    steps_b, time_b = sample(gds)
    assert time_b > time_a
    return (steps_b - steps_a) / (time_b - time_a)


def test_fast_simulator_runs_at_its_own_rate(gds):
    # 12.5 cycles per flight tick: the count is exact in simulated time, and the tick is
    # paced by the host, so a few percent either way over some seconds
    assert abs(measured_rate(gds) - CLOCK_HZ) < 0.05 * CLOCK_HZ


def test_clock_stops_with_pause_and_restarts_with_resume(gds, wsts):
    steps_before, _ = sample(gds)
    tick_before = wsts.status()["tick"]
    wsts.pause()
    try:
        time.sleep(3.0)  # 375 cycles if the clock ran on
        assert wsts.status()["tick"] <= tick_before + 1
    finally:
        wsts.resume()
    wsts.advance(20)
    steps_after, _ = sample(gds)
    ticks = wsts.status()["tick"] - tick_before
    expected = ticks * CLOCK_HZ / 10.0
    # The step sample and the tick count are read up to about a second apart
    assert steps_after > steps_before, "the clock restarts"
    assert abs((steps_after - steps_before) - expected) < 1.5 * CLOCK_HZ, \
        f"{steps_after - steps_before} steps over {ticks} ticks: the clock ran while paused, or did not restart"


def test_clock_scales_with_the_simulation_rate(gds, wsts):
    wsts.set_rate(2.0)
    try:
        wsts.advance(10)
        fast = measured_rate(gds, seconds=3.0)
    finally:
        wsts.set_rate(1.0)
    assert abs(fast - 2 * CLOCK_HZ) < 0.08 * 2 * CLOCK_HZ
    wsts.advance(10)
    assert abs(measured_rate(gds, seconds=3.0) - CLOCK_HZ) < 0.05 * CLOCK_HZ


def test_environment_variable_reaches_the_simulator(gds):
    # run.env in the manifest names profiles/calm.txt; the gyro simulator's phase code reads
    # the variable and reports what it loaded with its first step
    assert gds.await_telemetry("SensorSim.gyroSim.Profile", timeout=T) is not None
    gds.assert_telemetry("SensorSim.gyroSim.Profile", value="CALM", timeout=T)


def test_time_comes_from_the_surrogate_without_an_endpoint(gds, wsts):
    # SubtopDemo.chronoTime is SensorSim.SimTime: no bus endpoint, and time stamps advance
    assert wsts.status()["endpoints"] == ["uart"]
    _, first = sample(gds)
    time.sleep(1.5)
    _, second = sample(gds)
    assert second > first > 1_600_000_000
