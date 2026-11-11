"""LinuxDemo in simulation: flight software, surrogates, models, faults, scenarios.

Each test drives the deployment the way an operator or an agent would: flight commands
and telemetry through the GDS, the simulated hardware through the WSTS bridge or the WSTS
commands.
"""

import json
import time

import pytest

IMU_ADDR = 0x68
T = 10  # seconds allowed for an event or channel to arrive


@pytest.fixture
def quiet_sensor(wsts):
    """Silence the free-running sensor model so injected readings are not overwritten
    before telemetry is downlinked (channels go down once a second, latest value only)"""
    wsts.model_set("tempSensor", "period", 0)
    wsts.advance(2)
    yield
    wsts.model_set("tempSensor", "period", 10)


def test_surrogates_are_bound(wsts):
    status = wsts.status()
    assert set(status["endpoints"]) >= {"imuBus", "sensorUart", "heaterPin", "switchPin"}
    before = status["transactions"]
    wsts.advance(10)
    # The IMU manager polls the bus at 10 Hz through the I2C surrogate
    assert wsts.status()["transactions"] >= before + 10


def test_flight_telemetry_comes_from_models(gds):
    # In-process models from the manifest: IMU registers and the scripted serial sensor
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=21.5, timeout=T)


def test_command_drives_gpio_output(gds, wsts):
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["ON"], timeout=T)
    assert wsts.gpio_get("heaterPin") is True
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["OFF"], timeout=T)
    assert wsts.gpio_get("heaterPin") is False


def test_gpio_interrupt_reaches_flight_software(gds, wsts):
    gds.send_and_assert_command("LinuxDemo.heater.HEATER_SET", ["ON"], timeout=T)
    wsts.gpio_force("switchPin", False)
    wsts.gpio_force("switchPin", True)  # rising edge: the over-temperature switch trips
    gds.assert_event("LinuxDemo.heater.OverTemperature", timeout=T)
    assert wsts.gpio_get("heaterPin") is False
    wsts.gpio_force("switchPin", False)


def test_injected_serial_input(gds, wsts, quiet_sensor):
    wsts.inject_text("sensorUart", "$TEMP,48.25\n")
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=48.25, timeout=T)
    wsts.inject_text("sensorUart", "garbage\n")
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)


def test_flight_serial_output_is_observable(gds, wsts):
    gds.send_and_assert_command("LinuxDemo.sensor.SEND_QUERY", ["STATUS?"], timeout=T)
    wsts.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "sensorUart")
    assert b"STATUS?\n" in wsts.bytes_sent("sensorUart")


def test_i2c_fault_and_recovery(gds, wsts):
    # I2C_READ_ERR for the next five transactions
    wsts.fault_set("imuBus", "error", arg=3, count=5)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    gds.assert_event("LinuxDemo.wstsControl.FaultTriggered", timeout=T)
    gds.assert_event("LinuxDemo.wstsControl.FaultExpired", timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    assert wsts.status()["active_faults"] == 0


def test_serial_fault_send_error(gds, wsts):
    wsts.fault_set("sensorUart", "error", arg=3, direction="to_device")
    gds.send_command("LinuxDemo.sensor.SEND_QUERY", ["PING"])
    gds.assert_event("LinuxDemo.sensor.QueryFailed", timeout=T)
    wsts.fault_clear("sensorUart")
    gds.clear_histories()
    gds.send_and_assert_command("LinuxDemo.sensor.SEND_QUERY", ["PING"], timeout=T)


def test_wsts_commands_through_gds(gds, wsts):
    # The same control surface, used the way an operator would
    gds.send_and_assert_command("LinuxDemo.wstsControl.I2C_REG_SET", ["imuBus", IMU_ADDR, 0x3B, 0x12], timeout=T)
    gds.send_and_assert_command("LinuxDemo.wstsControl.I2C_REG_SET", ["imuBus", IMU_ADDR, 0x3C, 0x34], timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x1234, timeout=T)

    gds.send_and_assert_command(
        "LinuxDemo.wstsControl.FAULT_SET", ["imuBus", "STUCK", "TO_FLIGHT", 0x7F, 0, 0], timeout=T
    )
    gds.assert_event("LinuxDemo.wstsControl.FaultArmed", timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x7F7F, timeout=T)
    gds.send_and_assert_command("LinuxDemo.wstsControl.FAULT_CLEAR", ["*"], timeout=T)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x1234, timeout=T)
    gds.assert_telemetry("LinuxDemo.wstsControl.SimTicks", timeout=T)

    # An action the simulation refuses fails the command and says why
    gds.send_command("LinuxDemo.wstsControl.FAULT_CLEAR", ["*"])
    gds.send_command("LinuxDemo.wstsControl.INJECT_BYTES", ["noSuchEndpoint", "00"])
    gds.assert_event("LinuxDemo.wstsControl.ActionFailed", timeout=T)
    # Restore the manifest's register values for the tests that follow
    wsts.i2c_reg_set("imuBus", IMU_ADDR, 0x3B, 0x00)
    wsts.i2c_reg_set("imuBus", IMU_ADDR, 0x3C, 0x10)


def test_pause_and_resume(gds, wsts):
    # Pausing stops the tick that cycles the flight rate groups, and with them the
    # downlink: the response to SIM_PAUSE itself only arrives after the resume.
    gds.send_command("LinuxDemo.wstsControl.SIM_PAUSE")
    deadline = time.monotonic() + T
    while not wsts.status()["paused"]:
        assert time.monotonic() < deadline, "simulation did not pause"
        time.sleep(0.05)
    tick = wsts.status()["tick"]
    time.sleep(0.5)
    assert wsts.status()["tick"] == tick
    # Uplink does not depend on the rate groups, so the simulation can be resumed by command
    gds.send_command("LinuxDemo.wstsControl.SIM_RESUME")
    gds.assert_event("LinuxDemo.wstsControl.SimPaused", timeout=T)
    gds.assert_event("LinuxDemo.wstsControl.SimResumed", timeout=T)
    assert not wsts.status()["paused"]
    wsts.advance(5)


def test_time_scale(wsts):
    wsts.set_rate(4.0)
    start = time.monotonic()
    wsts.advance(40)  # four seconds of simulated time at 10 Hz
    elapsed = time.monotonic() - start
    wsts.set_rate(1.0)
    assert elapsed < 2.5


def test_scenario_file(gds, wsts, wsts_simulation, quiet_sensor):
    # Command strings are short (FW_CMD_STRING_MAX_SIZE), so the operator names a file in
    # the simulation's files directory rather than a full path
    scenario = wsts_simulation.files_dir / "overtemp.jsonl"
    scenario.write_text(
        "# Sensor reports a rising temperature, then the over-temperature switch trips\n"
        '{"tick": 2, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,70.0\\n"}\n'
        '{"tick": 4, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\\n"}\n'
        '{"tick": 5, "op": "gpio_force", "endpoint": "switchPin", "level": true}\n'
        '{"tick": 8, "op": "gpio_force", "endpoint": "switchPin", "level": false}\n'
        '{"tick": 9, "op": "log", "text": "overtemp scenario done"}\n'
    )
    gds.send_and_assert_command("LinuxDemo.wstsControl.SCENARIO_LOAD", [scenario.name, "STIMULUS"], timeout=T)
    gds.send_and_assert_command("LinuxDemo.wstsControl.SCENARIO_START", timeout=T)
    gds.assert_event("LinuxDemo.heater.OverTemperature", timeout=T)
    gds.assert_event("LinuxDemo.wstsControl.ScenarioCompleted", timeout=T)
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=95.5, timeout=T)


def test_record_then_replay_reproduces_the_run(gds, wsts, tmp_path, quiet_sensor):
    recording = tmp_path / "run.jsonl"

    wsts.record_start(recording)
    wsts.advance(2)
    wsts.inject_text("sensorUart", "$TEMP,33.0\n")
    wsts.advance(3)
    wsts.fault_set("imuBus", "error", arg=3, count=3)
    wsts.advance(6)
    wsts.inject_text("sensorUart", "not a reading\n")
    wsts.advance(3)
    wsts.record_stop()
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)

    lines = [json.loads(line) for line in recording.read_text().splitlines()]
    assert [l["op"] for l in lines if "op" in l] == ["inject_bytes", "fault_set", "inject_bytes"]
    assert any(l.get("rec") == "i2c" and l["status"] == 3 for l in lines)

    # Replay the recording as a scenario against the live models: same flight behaviour
    gds.clear_histories()
    wsts.run_scenario(recording, mode="stimulus")
    gds.assert_telemetry("LinuxDemo.sensor.Temperature", value=33.0, timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
    gds.assert_event("LinuxDemo.sensor.UnrecognizedLine", timeout=T)

    # Replay recorded device responses with the models bypassed: change the live model and
    # check flight software still sees what was recorded
    wsts.i2c_reg_set("imuBus", IMU_ADDR, 0x3F, 0x55)
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x5500, timeout=T)
    gds.clear_histories()
    wsts.scenario_load(recording, mode="responses")
    wsts.scenario_start()
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    while wsts.status()["scenario_state"] == "running":
        time.sleep(0.05)
    wsts.i2c_reg_set("imuBus", IMU_ADDR, 0x3F, 0x40)


def test_python_device_model(gds, wsts):
    """An external process takes over as the IMU"""
    from fprime_wsts.bridge import WstsBridge

    registers = {0x75: 0x68, 0x3B: 0x0A, 0x3C: 0xBC}
    pointer = [0]

    def imu(write: bytes, length: int):
        if write:
            pointer[0] = write[0]
        data = bytes(registers.get((pointer[0] + i) & 0xFF, 0) for i in range(length))
        return 0, data

    with WstsBridge(wsts.address) as model:
        model.serve_i2c("imuBus", IMU_ADDR, imu)
        gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0x0ABC, timeout=T)
    # The external model has gone away: the bus reports that nothing answers
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
