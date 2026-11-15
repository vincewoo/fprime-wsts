"""Cdh in simulation: a VxWorks deployment, built from subtopologies, running on Linux.

The flight topology code, including its calls to the VxWorks watchdog timer, is compiled
unchanged against the surrogates, and a generated main() calls the flight entry points.
The UART driver `SensorLink.uart` is defined in the flight project's library
(lib/sensor-lib), not in the deployment's Top: the simulated build takes a patched copy of
that subtopology, whose phase code opens and starts the driver.

Behind the UART's bus endpoint is SensorSim.SerialSensorSim, a queued F Prime component of
the simulation project, connected through the Wsts.BusByteStreamDevice adapter and
scheduled by a flight rate group. SensorSim.gyroSim is stepped at 125 Hz by a rate group of
the simulation behind a Wsts.SimCycle clock. The flight time source is SensorSim.SimTime.
"""

import json
import time

from conftest import T

TEMPERATURE = "SensorLink.sensor.Temperature"
LINES = "SensorLink.sensor.LinesReceived"


def test_watchdog_timer_surrogate_cycles_rate_groups(cdh):
    before = cdh.status()["tick"]
    cdh.advance(10)
    assert cdh.status()["tick"] >= before + 10


def test_endpoints(cdh):
    # The subtopology's UART and the hub link. SensorSim.SimTime has no bus endpoint.
    assert set(cdh.status()["endpoints"]) == {"uart", "hubDriver"}


# ----------------------------------------------------------------------
# The serial sensor simulator, behind the UART of a library subtopology
# ----------------------------------------------------------------------


def test_simulator_data_reaches_the_subtopology_component(gds):
    gds.assert_telemetry(TEMPERATURE, value=21.5, timeout=T)
    gds.assert_telemetry(LINES, timeout=T)
    gds.assert_telemetry("SensorSim.sensorSim.ReportsSent", timeout=T)


def test_flight_output_reaches_the_simulator(gds, cdh):
    gds.send_and_assert_command("SensorLink.sensor.SEND_QUERY", ["ID?"], timeout=T)
    # The simulator's receive port is asynchronous: it handles the query on its rate group
    event = gds.assert_event("SensorSim.sensorSim.QueryReceived", timeout=T)
    assert event.get_display_text().endswith("ID?")
    cdh.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "uart")
    assert b"ID?\n" in cdh.bytes_sent("uart")


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


def test_drop_fault_stops_the_simulator_data_and_clear_restores_it(gds, cdh):
    gds.assert_telemetry(LINES, timeout=T)
    cdh.fault_set("uart", "drop", direction="to_flight")
    try:
        cdh.advance(15)  # let anything already on its way be downlinked
        gds.clear_histories()
        assert gds.await_telemetry(LINES, timeout=4) is None, "sensor lines still arrive through a dropped UART"
        # The simulator itself keeps running: the fault is on the link
        gds.assert_telemetry("SensorSim.sensorSim.ReportsSent", timeout=T)
    finally:
        cdh.fault_clear("uart")
    gds.assert_telemetry(LINES, timeout=T)


def test_uart_send_error(gds, cdh):
    # The UART refuses the next transmission
    cdh.fault_set("uart", "error", arg=3, count=1, direction="to_device")
    gds.send_command("SensorLink.sensor.SEND_QUERY", ["ID?"])
    gds.assert_event("SensorLink.sensor.QueryFailed", timeout=T)
    gds.assert_event("Cdh.wstsControl.FaultTriggered", timeout=T)
    gds.assert_event("Cdh.wstsControl.FaultExpired", timeout=T)


def test_recording_shows_simulator_replies_as_model_traffic(gds, cdh, tmp_path):
    recording = tmp_path / "run.jsonl"
    cdh.record_start(recording)
    gds.send_and_assert_command("SensorLink.sensor.SEND_QUERY", ["ID?"], timeout=T)
    cdh.advance(25)
    cdh.record_stop()
    records = [json.loads(line) for line in recording.read_text().splitlines()]
    replies = [r for r in records if r.get("rec") == "bs_rx" and r.get("endpoint") == "uart"]
    assert replies and all(r["src"] == "model" for r in replies)
    assert any(r.get("rec") == "bs_tx" and r.get("endpoint") == "uart" and r["src"] == "flight" for r in records)


def test_bridge_injection_still_reaches_flight_software(gds, cdh):
    cdh.inject_text("uart", "not a reading\n")
    gds.assert_event("SensorLink.sensor.UnrecognizedLine", timeout=T)


def test_messages_waiting_for_a_queued_component_do_not_hold_a_tick_up(cdh):
    # Cdh's health component is queued: ping replies wait on its queue until its rate group
    # dispatches it on a later tick. A paused Cdh is quiescent all the same.
    assert cdh.status()["counting"]
    cdh.pause()
    cdh.wait_quiescent()
    for _ in range(10):
        cdh.step(1)  # raises, naming the queues, if the tick's work is not finished
    cdh.resume()


def test_pause_stops_the_simulator_with_the_flight_rate_groups(cdh):
    # The bus counters include the hub link, whose far end keeps running: count the
    # simulator's replies on the UART instead
    def replies():
        return [m for m in cdh.events() if m.get("rec") == "bs_rx" and m.get("endpoint") == "uart"]

    cdh.pause()
    try:
        time.sleep(0.3)
        cdh.events(clear=True)
        time.sleep(2.5)  # the simulator reports once a second when it runs
        assert not replies()
    finally:
        cdh.resume()
    cdh.advance(15)
    assert replies()


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


def test_clock_stops_with_pause_and_restarts_with_resume(gds, cdh):
    steps_before, _ = sample(gds)
    tick_before = cdh.status()["tick"]
    cdh.pause()
    try:
        time.sleep(3.0)  # 375 cycles if the clock ran on
        assert cdh.status()["tick"] <= tick_before + 1
    finally:
        cdh.resume()
    cdh.advance(20)
    steps_after, _ = sample(gds)
    ticks = cdh.status()["tick"] - tick_before
    expected = ticks * CLOCK_HZ / 10.0
    # The step sample and the tick count are read up to about a second apart
    assert steps_after > steps_before, "the clock restarts"
    assert abs((steps_after - steps_before) - expected) < 1.5 * CLOCK_HZ, \
        f"{steps_after - steps_before} steps over {ticks} ticks: the clock ran while paused, or did not restart"


def test_clock_scales_with_the_simulation_rate(gds, cdh):
    cdh.set_rate(2.0)
    try:
        cdh.advance(10)
        fast = measured_rate(gds, seconds=3.0)
    finally:
        cdh.set_rate(1.0)
    assert abs(fast - 2 * CLOCK_HZ) < 0.08 * 2 * CLOCK_HZ
    cdh.advance(10)
    assert abs(measured_rate(gds, seconds=3.0) - CLOCK_HZ) < 0.05 * CLOCK_HZ


def test_environment_variable_reaches_the_simulator(gds):
    # run.env in cdh.yaml names profiles/calm.txt; the gyro simulator's phase code reads
    # the variable and reports what it loaded
    gds.assert_telemetry("SensorSim.gyroSim.Profile", value="CALM", timeout=T)


def test_time_comes_from_the_surrogate_without_an_endpoint(gds):
    # Cdh.chronoTime is SensorSim.SimTime: no bus endpoint, and time stamps advance
    _, first = sample(gds)
    time.sleep(1.5)
    _, second = sample(gds)
    assert second > first > 1_600_000_000


# ----------------------------------------------------------------------
# VxWorks code through the API shim: Cdh.heartbeat (Cdh/Heartbeat) spawns a task with
# taskSpawn, which takes a binary semaphore that a watchdog (wdStart) gives every six ticks
# of the system clock (60 Hz), counts under a mutex semaphore, and takes a new period from
# a message queue. Nothing in it is guarded for simulation: cdh.yaml has `shims: [vxworks]`.
# ----------------------------------------------------------------------

BEATS = "Cdh.heartbeat.Beats"
SYSTEM_TICKS = "Cdh.heartbeat.SystemTicks"
PERIOD = "Cdh.heartbeat.PeriodTicks"


def heartbeat_samples(gds, count, timeout=T):
    """(system ticks, beats) of the first `count` reports of the heartbeat since the histories
    were cleared. The two channels of one report are written one after the other: paired by
    time tag, which is the same in stepped time and microseconds apart in real time."""
    def seconds(item):
        tag = item.get_time()
        return tag.seconds + tag.useconds / 1e6

    beats = gds.await_telemetry_count(count, BEATS, start=0, timeout=timeout)
    ticks = gds.await_telemetry_count(count, SYSTEM_TICKS, start=0, timeout=timeout)
    pairs = []
    for beat in beats:
        nearest = min(ticks, key=lambda t: abs(seconds(t) - seconds(beat)), default=None)
        if nearest is not None and abs(seconds(nearest) - seconds(beat)) < 0.05:
            pairs.append((nearest.get_val(), beat.get_val()))
    return pairs


def test_vxworks_heartbeat_beats_on_the_system_clock(gds):
    gds.assert_telemetry(PERIOD, value=6, timeout=T)
    gds.clear_histories()
    first = min(heartbeat_samples(gds, 3))  # a clear can fall between the channels of one report
    time.sleep(3)
    gds.clear_histories()
    last = max(heartbeat_samples(gds, 3))
    ticks, beats = last[0] - first[0], last[1] - first[1]
    assert ticks >= 120, "60 ticks of the system clock a second"
    # In real time the task may count the last beat just after the report
    assert abs(beats - ticks / 6) <= 1.5, f"{beats} beats in {ticks} system clock ticks"


def test_a_command_reaches_the_vxworks_task_through_a_message_queue(gds):
    try:
        gds.send_and_assert_command("Cdh.heartbeat.HEARTBEAT_SET_PERIOD", [3], timeout=T)
        # Taken by the task between two beats, which says so itself
        gds.assert_event("Cdh.heartbeat.PeriodSet", args=[3], timeout=T)
        gds.assert_telemetry(PERIOD, value=3, timeout=T)
    finally:
        gds.send_and_assert_command("Cdh.heartbeat.HEARTBEAT_SET_PERIOD", [6], timeout=T)
    gds.assert_telemetry(PERIOD, value=6, timeout=T)
    gds.send_command("Cdh.heartbeat.HEARTBEAT_SET_PERIOD", [0])
    gds.assert_event("Cdh.heartbeat.PeriodRejected", args=[0], timeout=T)


def test_in_stepped_time_every_beat_is_counted_in_its_tick(gds, cdh):
    """The watchdog runs in the tick its system clock tick falls in, and the tick is not over
    until the task it woke has counted the beat: each report, a simulated second after the
    last, is exactly 60 system clock ticks and 10 beats on"""
    gds.assert_telemetry(PERIOD, value=6, timeout=T)
    cdh.pause()
    cdh.set_stepped(True)
    try:
        time.sleep(0.5)  # what real time had sent is in
        gds.clear_histories()
        cdh.step(100)  # ten simulated seconds: ten reports, the last still on its way down
        samples = heartbeat_samples(gds, 7)
    finally:
        cdh.set_stepped(False)
        cdh.resume()
    # In the order they were written (the history is in the order of time tags: simulated
    # time in stepped time, the host's clock in real time), from the second on: the first
    # may have been written in real time, before the pause
    samples = sorted(samples)
    steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(samples[1:], samples[2:])]
    assert len(steps) >= 5 and all(step == (60, 10) for step in steps), f"{samples}"
