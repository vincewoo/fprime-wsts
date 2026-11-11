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
