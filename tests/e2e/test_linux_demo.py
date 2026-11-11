"""LinuxDemo in simulation: flight software, surrogates, models, faults, scenarios.

Each test drives the deployment the way an operator or an agent would: flight commands
and telemetry through the GDS, the simulated hardware through the SSE bridge or the SSE
commands.
"""

import json
import time

import pytest

IMU_ADDR = 0x68
T = 10  # seconds allowed for an event or channel to arrive


@pytest.fixture
def quiet_sensor(sse):
    """Silence the free-running sensor model so injected readings are not overwritten
    before telemetry is downlinked (channels go down once a second, latest value only)"""
    sse.model_set("tempSensor", "period", 0)
    sse.advance(2)
    yield
    sse.model_set("tempSensor", "period", 10)


def test_surrogates_are_bound(sse):
    status = sse.status()
    assert set(status["endpoints"]) >= {"imuBus", "sensorUart", "heaterPin", "switchPin"}
    before = status["transactions"]
    sse.advance(10)
    # The IMU manager polls the bus at 10 Hz through the I2C surrogate
    assert sse.status()["transactions"] >= before + 10


def test_flight_telemetry_comes_from_models(gds):
    # In-process models from the manifest: IMU registers and the scripted serial sensor
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=21.5, timeout=T)


def test_command_drives_gpio_output(gds, sse):
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["ON"], timeout=T)
    assert sse.gpio_get("heaterPin") is True
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["OFF"], timeout=T)
    assert sse.gpio_get("heaterPin") is False


def test_gpio_interrupt_reaches_flight_software(gds, sse):
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["ON"], timeout=T)
    sse.gpio_force("switchPin", False)
    sse.gpio_force("switchPin", True)  # rising edge: the over-temperature switch trips
    gds.assert_event("LinuxDemo.heater.OverTemperature", timeout=T)
    assert sse.gpio_get("heaterPin") is False
    sse.gpio_force("switchPin", False)


def test_injected_serial_input(gds, sse, quiet_sensor):
    sse.inject_text("sensorUart", "$TEMP,48.25\n")
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=48.25, timeout=T)
    sse.inject_text("sensorUart", "garbage\n")
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)


def test_flight_serial_output_is_observable(gds, sse):
    gds.send_and_assert_command("LinuxDemo.sensor.SEND_QUERY", ["STATUS?"], timeout=T)
    sse.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "sensorUart")
    assert b"STATUS?\n" in sse.bytes_sent("sensorUart")


def test_i2c_fault_and_recovery(gds, sse):
    # I2C_READ_ERR for the next five transactions
    sse.fault_set("imuBus", "error", arg=3, count=5)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    gds.assert_event("LinuxDemo.sseControl.FaultTriggered", timeout=T)
    gds.assert_event("LinuxDemo.sseControl.FaultExpired", timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    assert sse.status()["active_faults"] == 0


def test_serial_fault_send_error(gds, sse):
    sse.fault_set("sensorUart", "error", arg=3, direction="to_device")
    gds.send_command("LinuxDemo.sensor.SEND_QUERY", ["PING"])
    gds.assert_event("LinuxDemo.sensor.QueryFailed", timeout=T)
    sse.fault_clear("sensorUart")
    gds.clear_histories()
    gds.send_and_assert_command("LinuxDemo.sensor.SEND_QUERY", ["PING"], timeout=T)


def test_sse_commands_through_gds(gds, sse):
    # The same control surface, used the way an operator would
    gds.send_and_assert_command("LinuxDemo.sseControl.I2C_REG_SET", ["imuBus", IMU_ADDR, 0x3B, 0x12], timeout=T)
    gds.send_and_assert_command("LinuxDemo.sseControl.I2C_REG_SET", ["imuBus", IMU_ADDR, 0x3C, 0x34], timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x1234, timeout=T)

    gds.send_and_assert_command(
        "LinuxDemo.sseControl.FAULT_SET", ["imuBus", "STUCK", "TO_FLIGHT", 0x7F, 0, 0], timeout=T
    )
    gds.assert_event("LinuxDemo.sseControl.FaultArmed", timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x7F7F, timeout=T)
    gds.send_and_assert_command("LinuxDemo.sseControl.FAULT_CLEAR", ["*"], timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x1234, timeout=T)
    gds.assert_telemetry("LinuxDemo.sseControl.SimTicks", timeout=T)

    # An action the simulation refuses fails the command and says why
    gds.send_command("LinuxDemo.sseControl.FAULT_CLEAR", ["*"])
    gds.send_command("LinuxDemo.sseControl.INJECT_BYTES", ["noSuchEndpoint", "00"])
    gds.assert_event("LinuxDemo.sseControl.ActionFailed", timeout=T)
    # Restore the manifest's register values for the tests that follow
    sse.i2c_reg_set("imuBus", IMU_ADDR, 0x3B, 0x00)
    sse.i2c_reg_set("imuBus", IMU_ADDR, 0x3C, 0x10)


def test_pause_and_resume(gds, sse):
    # Pausing stops the tick that cycles the flight rate groups, and with them the
    # downlink: the response to SIM_PAUSE itself only arrives after the resume.
    gds.send_command("LinuxDemo.sseControl.SIM_PAUSE")
    deadline = time.monotonic() + T
    while not sse.status()["paused"]:
        assert time.monotonic() < deadline, "simulation did not pause"
        time.sleep(0.05)
    tick = sse.status()["tick"]
    time.sleep(0.5)
    assert sse.status()["tick"] == tick
    # Uplink does not depend on the rate groups, so the simulation can be resumed by command
    gds.send_command("LinuxDemo.sseControl.SIM_RESUME")
    gds.assert_event("LinuxDemo.sseControl.SimPaused", timeout=T)
    gds.assert_event("LinuxDemo.sseControl.SimResumed", timeout=T)
    assert not sse.status()["paused"]
    sse.advance(5)


def test_time_scale(sse):
    sse.set_rate(4.0)
    start = time.monotonic()
    sse.advance(40)  # four seconds of simulated time at 10 Hz
    elapsed = time.monotonic() - start
    sse.set_rate(1.0)
    assert elapsed < 2.5


def test_scenario_file(gds, sse, sse_simulation, quiet_sensor):
    # Command strings are short (FW_CMD_STRING_MAX_SIZE), so the operator names a file in
    # the simulation's files directory rather than a full path
    scenario = sse_simulation.files_dir / "overtemp.jsonl"
    scenario.write_text(
        "# Sensor reports a rising temperature, then the over-temperature switch trips\n"
        '{"tick": 2, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,70.0\\n"}\n'
        '{"tick": 4, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\\n"}\n'
        '{"tick": 5, "op": "gpio_force", "endpoint": "switchPin", "level": true}\n'
        '{"tick": 8, "op": "gpio_force", "endpoint": "switchPin", "level": false}\n'
        '{"tick": 9, "op": "log", "text": "overtemp scenario done"}\n'
    )
    gds.send_and_assert_command("LinuxDemo.sseControl.SCENARIO_LOAD", [scenario.name, "STIMULUS"], timeout=T)
    gds.send_and_assert_command("LinuxDemo.sseControl.SCENARIO_START", timeout=T)
    gds.assert_event("LinuxDemo.heater.OverTemperature", timeout=T)
    gds.assert_event("LinuxDemo.sseControl.ScenarioCompleted", timeout=T)
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=95.5, timeout=T)


def test_record_then_replay_reproduces_the_run(gds, sse, tmp_path, quiet_sensor):
    recording = tmp_path / "run.jsonl"

    sse.record_start(recording)
    sse.advance(2)
    sse.inject_text("sensorUart", "$TEMP,33.0\n")
    sse.advance(3)
    sse.fault_set("imuBus", "error", arg=3, count=3)
    sse.advance(6)
    sse.inject_text("sensorUart", "not a reading\n")
    sse.advance(3)
    sse.record_stop()
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)

    lines = [json.loads(line) for line in recording.read_text().splitlines()]
    assert [l["op"] for l in lines if "op" in l] == ["inject_bytes", "fault_set", "inject_bytes"]
    assert any(l.get("rec") == "i2c" and l["status"] == 3 for l in lines)

    # Replay the recording as a scenario against the live models: same flight behaviour
    gds.clear_histories()
    sse.run_scenario(recording, mode="stimulus")
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=33.0, timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)

    # Replay recorded device responses with the models bypassed: change the live model and
    # check flight software still sees what was recorded
    sse.i2c_reg_set("imuBus", IMU_ADDR, 0x3F, 0x55)
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x5500, timeout=T)
    gds.clear_histories()
    sse.scenario_load(recording, mode="responses")
    sse.scenario_start()
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    while sse.status()["scenario_state"] == "running":
        time.sleep(0.05)
    sse.i2c_reg_set("imuBus", IMU_ADDR, 0x3F, 0x40)


def test_python_device_model(gds, sse):
    """An external process takes over as the IMU"""
    from fprime_sse.bridge import SseBridge

    registers = {0x75: 0x68, 0x3B: 0x0A, 0x3C: 0xBC}
    pointer = [0]

    def imu(write: bytes, length: int):
        if write:
            pointer[0] = write[0]
        data = bytes(registers.get((pointer[0] + i) & 0xFF, 0) for i in range(length))
        return 0, data

    with SseBridge(sse.address) as model:
        model.serve_i2c("imuBus", IMU_ADDR, imu)
        gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x0ABC, timeout=T)
    # The external model has gone away: the bus reports that nothing answers
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
