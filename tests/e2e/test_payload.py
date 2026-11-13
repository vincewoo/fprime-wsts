"""Payload in simulation: a Linux computer's drivers replaced by surrogates, devices by models.

Payload has no ground link: every command here crosses the hub from Cdh, and every event
and channel comes back the same way. The simulated hardware is driven through Payload's
own bridge, or through its WSTS commands (`Payload.wstsControl`).

Behind the surrogates:
  - i2cBus     Sim::ImuModel at 0x68 (a model of the simulation project, set in g) and the
               toolkit's Wsts::RegisterMapI2c at 0x40 (the power monitor)
  - spiBus     the toolkit's Wsts::LoopbackSpi (the instrument's converter)
  - sensorUart the toolkit's Wsts::ScriptedByteStream, reporting once a second
  - heaterPin, switchPin
               Sim.SimLinuxGpio, the simulation project's mirror of Drv.LinuxGpioDriver
  - fpgaRegs   the toolkit's Wsts::RegisterFile: the registers of the FPGA's housekeeping
               block and its frame interrupt once a second. The register interface
               component (of sensor-lib) is replaced by Wsts.SimRegister, written with that
               component's ports; the driver above it, Components.FpgaMonitor, is flight code
"""

import json
import time

import pytest

from conftest import T

IMU_ADDR = 0x68
POWER_ADDR = 0x40

# Registers of the FPGA's housekeeping block (Components/FpgaMonitor/FpgaMonitor.hpp)
REG_ID, REG_STATUS, REG_FRAME_COUNT, REG_TEMPERATURE, REG_HEARTBEAT, REG_LEDS = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14
FPGA_ID = 0xF00D0001
STATUS_FRAME_READY, STATUS_OVERRUN = 0x1, 0x2


@pytest.fixture
def quiet_sensor(payload):
    """Silence the free-running sensor model so injected readings are not overwritten
    before telemetry is downlinked (channels go down once a second, latest value only)"""
    payload.model_set("tempSensor", "period", 0)
    payload.advance(4)
    yield
    payload.model_set("tempSensor", "period", 20)


def test_surrogates_are_bound(payload):
    status = payload.status()
    assert set(status["endpoints"]) == {"i2cBus", "spiBus", "sensorUart", "heaterPin", "switchPin", "hubDriver", "fpgaRegs"}


def test_two_devices_answer_on_one_i2c_bus(payload):
    # The IMU manager reads at 10 Hz, the power monitor two registers at 1 Hz
    payload.events(clear=True)
    payload.advance(20)  # one second at 20 Hz
    addresses = [m["addr"] for m in payload.events() if m.get("rec") == "i2c" and m.get("endpoint") == "i2cBus"]
    assert addresses.count(IMU_ADDR) >= 9
    assert addresses.count(POWER_ADDR) >= 2


def test_flight_telemetry_comes_from_models(gds):
    # A project model in engineering units, the toolkit's register map, the scripted sensor
    gds.assert_telemetry("Payload.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_telemetry("Payload.power.BusVoltage", value=14.0, timeout=T)
    gds.assert_telemetry("Payload.power.Current", value=500, timeout=T)
    gds.assert_telemetry("Payload.sensor.Temperature", value=21.5, timeout=T)
    gds.assert_telemetry("Payload.adc.DetectorCounts", value=0, timeout=T)
    # A register of the FPGA block, read by the flight driver through the register interface
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)


def test_project_model_takes_engineering_units(gds, payload):
    # Sim::ImuModel starts at 1 g on Z; its parameters are in g, not register bytes
    payload.model_set("imu", "accel_x", -0.5)
    gds.assert_telemetry("Payload.imu.AccelX", value=-0x2000, timeout=T)
    payload.model_set("imu", "accel_x", 0)
    gds.assert_telemetry("Payload.imu.AccelX", value=0, timeout=T)


def test_mirror_surrogate_keeps_the_flight_dictionary(dictionary):
    events = {e["name"] for e in dictionary["events"]}
    # Drv.LinuxGpioDriver's events, present because Sim.SimLinuxGpio mirrors it
    assert {"Payload.heaterPin.OpenChip", "Payload.switchPin.OpenPinError"} <= events
    # The I2C and UART drivers' events are gone: the stock surrogates do not have them
    assert not any(name.startswith(("Payload.i2cBus.", "Payload.spiBus.", "Payload.sensorUart.")) for name in events)


def test_stub_variant_of_the_camera_runs(gds):
    # Components/Imager links the camera vendor's SDK in flight. Here it is built in the
    # variant it has for unit tests (cmake.stub_variants in payload.yaml): the frame is the
    # stub's gradient, 64 * 48 bytes with a mean level of 31. The exposure went through
    # sensor-lib's real unit conversions, not their stubs (20 ms at 48 kHz), and the board
    # revision comes from the simulation's <board/revision.h> (host.cmake).
    gds.send_and_assert_command("Payload.imager.CAPTURE", timeout=T)
    gds.assert_event("Payload.imager.FrameCaptured", [3072, 31, 960, 2], timeout=T)
    gds.assert_telemetry("Payload.imager.FramesCaptured", timeout=T)


def test_command_drives_gpio_output(gds, payload):
    gds.send_and_assert_command("Payload.heater.HEATER_SET", ["ON"], timeout=T)
    assert payload.gpio_get("heaterPin") is True
    gds.send_and_assert_command("Payload.heater.HEATER_SET", ["OFF"], timeout=T)
    assert payload.gpio_get("heaterPin") is False


def test_gpio_interrupt_reaches_flight_software(gds, payload):
    gds.send_and_assert_command("Payload.heater.HEATER_SET", ["ON"], timeout=T)
    payload.gpio_force("switchPin", False)
    payload.gpio_force("switchPin", True)  # rising edge: the over-temperature switch trips
    gds.assert_event("Payload.heater.OverTemperature", timeout=T)
    assert payload.gpio_get("heaterPin") is False
    payload.gpio_force("switchPin", False)


def test_injected_serial_input(gds, payload, quiet_sensor):
    payload.inject_text("sensorUart", "$TEMP,48.25\n")
    gds.assert_telemetry("Payload.sensor.Temperature", value=48.25, timeout=T)
    payload.inject_text("sensorUart", "garbage\n")
    gds.assert_event("Payload.sensor.UnrecognizedLine", timeout=T)


def test_flight_serial_output_is_observable(gds, payload):
    gds.send_and_assert_command("Payload.sensor.SEND_QUERY", ["STATUS?"], timeout=T)
    payload.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "sensorUart")
    assert b"STATUS?\n" in payload.bytes_sent("sensorUart")


def test_i2c_fault_and_recovery(gds, payload):
    # I2C_READ_ERR for the next five transactions on the bus, whichever device they are for
    payload.fault_set("i2cBus", "error", arg=3, count=5)
    gds.assert_event("Payload.imu.ImuReadError", timeout=T)
    gds.assert_event("Payload.wstsControl.FaultTriggered", timeout=T)
    gds.assert_event("Payload.wstsControl.FaultExpired", timeout=T)
    gds.assert_event("Payload.imu.ImuRecovered", timeout=T)
    assert payload.status()["active_faults"] == 0


def test_serial_fault_send_error(gds, payload):
    payload.fault_set("sensorUart", "error", arg=3, direction="to_device")
    gds.send_command("Payload.sensor.SEND_QUERY", ["PING"])
    gds.assert_event("Payload.sensor.QueryFailed", timeout=T)
    payload.fault_clear("sensorUart")
    gds.clear_histories()
    gds.send_and_assert_command("Payload.sensor.SEND_QUERY", ["PING"], timeout=T)


def test_wsts_commands_through_gds(gds, payload):
    # The same control surface, used the way an operator would, through the hub
    gds.send_and_assert_command("Payload.wstsControl.I2C_REG_SET", ["i2cBus", IMU_ADDR, 0x3B, 0x12], timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.I2C_REG_SET", ["i2cBus", IMU_ADDR, 0x3C, 0x34], timeout=T)
    gds.assert_telemetry("Payload.imu.AccelX", value=0x1234, timeout=T)
    # A register of the other device on the bus: 12.0 V
    gds.send_and_assert_command("Payload.wstsControl.I2C_REG_SET", ["i2cBus", POWER_ADDR, 0x02, 0x5D], timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.I2C_REG_SET", ["i2cBus", POWER_ADDR, 0x03, 0xC0], timeout=T)
    gds.assert_telemetry("Payload.power.BusVoltage", value=12.0, timeout=T)

    gds.send_and_assert_command(
        "Payload.wstsControl.FAULT_SET", ["i2cBus", "STUCK", "TO_FLIGHT", 0x7F, 0, 0], timeout=T
    )
    gds.assert_event("Payload.wstsControl.FaultArmed", timeout=T)
    gds.assert_telemetry("Payload.imu.AccelX", value=0x7F7F, timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.FAULT_CLEAR", ["*"], timeout=T)
    gds.assert_telemetry("Payload.imu.AccelX", value=0x1234, timeout=T)
    gds.assert_telemetry("Payload.wstsControl.SimTicks", timeout=T)

    # An action the simulation refuses fails the command and says why
    gds.send_command("Payload.wstsControl.INJECT_BYTES", ["noSuchEndpoint", "00"])
    gds.assert_event("Payload.wstsControl.ActionFailed", timeout=T)
    # Restore the manifest's values for the tests that follow
    payload.model_set("imu", "accel_x", 0)
    payload.i2c_reg_set("i2cBus", POWER_ADDR, 0x02, 0x6D)
    payload.i2c_reg_set("i2cBus", POWER_ADDR, 0x03, 0x60)


def test_pause_and_resume_by_command(gds, payload):
    # Pausing Payload stops its rate groups, but not the hub: its events still reach the
    # ground through Cdh, which keeps running, and commands still reach it
    gds.send_command("Payload.wstsControl.SIM_PAUSE")
    gds.assert_event("Payload.wstsControl.SimPaused", timeout=T)
    assert payload.status()["paused"]
    tick = payload.status()["tick"]
    time.sleep(0.5)
    assert payload.status()["tick"] == tick
    # Events arrive in order through the hub: anything older than SimPaused is in already
    gds.clear_histories()
    gds.send_command("Payload.wstsControl.SIM_RESUME")
    gds.assert_event("Payload.wstsControl.SimResumed", timeout=T)
    assert not payload.status()["paused"]
    payload.advance(10)


def test_time_scale(payload):
    payload.set_rate(4.0)
    start = time.monotonic()
    payload.advance(80)  # four seconds of simulated time at 20 Hz
    elapsed = time.monotonic() - start
    payload.set_rate(1.0)
    assert elapsed < 2.5


def test_scenario_file(gds, payload, wsts_system_simulation, quiet_sensor):
    # Command strings are short (FW_CMD_STRING_MAX_SIZE), so the operator names a file in
    # the element's files directory rather than a full path
    scenario = wsts_system_simulation.elements["payload"].files_dir / "overtemp.jsonl"
    scenario.write_text(
        "# Sensor reports a rising temperature, then the over-temperature switch trips\n"
        '{"tick": 4, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,70.0\\n"}\n'
        '{"tick": 8, "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\\n"}\n'
        '{"tick": 10, "op": "gpio_force", "endpoint": "switchPin", "level": true}\n'
        '{"tick": 16, "op": "gpio_force", "endpoint": "switchPin", "level": false}\n'
        '{"tick": 18, "op": "log", "text": "overtemp scenario done"}\n'
    )
    gds.send_and_assert_command("Payload.heater.HEATER_SET", ["ON"], timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.SCENARIO_LOAD", [scenario.name, "STIMULUS"], timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.SCENARIO_START", timeout=T)
    gds.assert_event("Payload.heater.OverTemperature", timeout=T)
    gds.assert_event("Payload.wstsControl.ScenarioCompleted", timeout=T)
    gds.assert_telemetry("Payload.sensor.Temperature", value=95.5, timeout=T)


def test_record_then_replay_reproduces_the_run(gds, payload, tmp_path, quiet_sensor):
    recording = tmp_path / "run.jsonl"

    payload.record_start(recording)
    # Over a second of undisturbed readings first: Payload's telemetry reaches the ground
    # once a second, latest value only, and the response replay below has to be seen
    payload.advance(30)
    payload.inject_text("sensorUart", "$TEMP,33.0\n")
    payload.advance(6)
    payload.fault_set("i2cBus", "error", arg=3, count=3)
    payload.advance(12)
    payload.inject_text("sensorUart", "not a reading\n")
    payload.advance(6)
    payload.record_stop()
    gds.assert_event("Payload.imu.ImuRecovered", timeout=T)
    gds.assert_event("Payload.sensor.UnrecognizedLine", timeout=T)

    lines = [json.loads(line) for line in recording.read_text().splitlines()]
    assert [l["op"] for l in lines if "op" in l] == ["inject_bytes", "fault_set", "inject_bytes"]
    assert any(l.get("rec") == "i2c" and l["status"] == 3 for l in lines)

    # Replay the recording as a scenario against the live models: same flight behaviour
    gds.clear_histories()
    payload.run_scenario(recording, mode="stimulus")
    gds.assert_telemetry("Payload.sensor.Temperature", value=33.0, timeout=T)
    gds.assert_event("Payload.imu.ImuReadError", timeout=T)
    gds.assert_event("Payload.imu.ImuRecovered", timeout=T)
    gds.assert_event("Payload.sensor.UnrecognizedLine", timeout=T)

    # Replay recorded device responses with the models bypassed: change the live model and
    # check flight software still sees what was recorded
    payload.model_set("imu", "accel_z", 1.328125)  # 0x5500
    gds.assert_telemetry("Payload.imu.AccelZ", value=0x5500, timeout=T)
    gds.clear_histories()
    payload.scenario_load(recording, mode="responses")
    payload.scenario_start()
    gds.assert_telemetry("Payload.imu.AccelZ", value=0x4000, timeout=T)
    gds.assert_event("Payload.imu.ImuReadError", timeout=T)
    while payload.status()["scenario_state"] == "running":
        time.sleep(0.05)
    payload.model_set("imu", "accel_z", 1)


def test_python_device_model(gds, payload):
    """An external process takes over as the IMU; the power monitor at another address
    stays with its in-process model"""
    from fprime_wsts.bridge import WstsBridge

    registers = {0x75: 0x68, 0x3B: 0x0A, 0x3C: 0xBC}
    pointer = [0]

    def imu(write: bytes, length: int):
        if write:
            pointer[0] = write[0]
        data = bytes(registers.get((pointer[0] + i) & 0xFF, 0) for i in range(length))
        return 0, data

    with WstsBridge(payload.address) as model:
        model.serve_i2c("i2cBus", IMU_ADDR, imu)
        gds.assert_telemetry("Payload.imu.AccelX", value=0x0ABC, timeout=T)
        gds.assert_telemetry("Payload.power.BusVoltage", value=14.0, timeout=T)
    # The external model has gone away: the bus reports that nothing answers
    gds.assert_event("Payload.imu.ImuReadError", timeout=T)


def test_spi_faults_then_a_device_model_in_python(gds, payload):
    """The bus fails a transfer; then an external process becomes the converter"""
    from fprime_wsts.bridge import WstsBridge

    payload.fault_set("spiBus", "error", arg=4, count=1)  # SPI_WRITE_ERR
    gds.assert_event("Payload.adc.SampleError", timeout=T)
    gds.assert_event("Payload.wstsControl.FaultExpired", timeout=T)

    commands = []

    def adc(write: bytes, length: int):
        # MCP3008: the conversion comes back in the last ten bits of the three bytes
        commands.append(write)
        return 0, bytes([0x00, 0x02, 0xA5])[:length]

    with WstsBridge(payload.address) as model:
        model.serve_spi("spiBus", adc)
        gds.assert_telemetry("Payload.adc.DetectorCounts", value=0x2A5, timeout=T)
    assert commands and all(command == bytes([0x01, 0x80, 0x00]) for command in commands)
    # The external model has gone away. Unlike I2C, nothing reports it: with no device on
    # the bus MISO floats, and the transfer reads zeros
    gds.assert_telemetry("Payload.adc.DetectorCounts", value=0, timeout=T)


# ----------------------------------------------------------------------
# The register block behind fpgaRegs
# ----------------------------------------------------------------------


def test_flight_driver_reads_and_writes_the_register_block(gds, payload):
    # At 10 Hz the driver reads the temperature register and writes the heartbeat
    payload.advance(10)
    payload.events(clear=True)
    payload.advance(20)  # one second at 20 Hz
    accesses = payload.register_accesses("fpgaRegs")
    reads = [a for a in accesses if a["rec"] == "reg_rd" and a["offset"] == REG_TEMPERATURE]
    assert len(reads) >= 9 and all(a["value"] == 215 and a["status"] == 0 for a in reads)
    beats = [a["value"] for a in accesses if a["rec"] == "reg_wr" and a["offset"] == REG_HEARTBEAT]
    assert len(beats) >= 9 and beats == list(range(beats[0], beats[0] + len(beats)))
    assert not [a for a in accesses if a.get("offset") == REG_ID], "the identity is read until it is right, then no more"

    # The register follows the model, and the telemetry the register
    payload.reg_set("fpgaRegs", REG_TEMPERATURE, 305)
    gds.assert_telemetry("Payload.fpga.Temperature", value=30.5, timeout=T)
    payload.model_set("fpga", "reg.0x0C", 215)
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)

    # A flight command becomes a register write
    payload.events(clear=True)
    gds.send_and_assert_command("Payload.fpga.FPGA_SET_LEDS", [0xA5], timeout=T)
    gds.assert_event("Payload.fpga.LedsSet", [0xA5], timeout=T)
    assert [a["value"] for a in payload.register_accesses("fpgaRegs") if a.get("offset") == REG_LEDS] == [0xA5]


def test_register_interrupt_is_serviced_by_flight_software(gds, payload):
    # The model sets "frame ready" and raises interrupt 1 once a second. The driver's
    # handler queues the service; its own thread reads the status and clears it.
    payload.events(clear=True)
    payload.advance(45)
    accesses = payload.register_accesses("fpgaRegs")
    raised = [a for a in accesses if a["rec"] == "reg_irq"]
    assert len(raised) >= 2 and all(a["number"] == 1 and a["src"] == "model" for a in raised)
    status_reads = [a["value"] for a in accesses if a["rec"] == "reg_rd" and a["offset"] == REG_STATUS]
    status_clears = [a["value"] for a in accesses if a["rec"] == "reg_wr" and a["offset"] == REG_STATUS]
    assert len(status_reads) >= 2 and set(status_reads) == {STATUS_FRAME_READY}
    assert status_clears == status_reads, "write 1 to clear what was read"
    gds.assert_telemetry("Payload.fpga.Interrupts", timeout=T)

    # Another cause in the status register, and an interrupt raised by hand
    payload.reg_set("fpgaRegs", REG_FRAME_COUNT, 1234)
    payload.reg_set("fpgaRegs", REG_STATUS, STATUS_FRAME_READY | STATUS_OVERRUN)
    payload.raise_irq("fpgaRegs", 1)
    gds.assert_event("Payload.fpga.FrameOverrun", timeout=T)
    gds.assert_telemetry("Payload.fpga.FrameCount", value=1234, timeout=T)
    payload.reg_set("fpgaRegs", REG_FRAME_COUNT, 0)


def test_lost_and_duplicated_interrupts(payload):
    # The interrupts of a register endpoint are a fault target of their own
    payload.fault_set("fpgaRegs:irq", "drop")
    payload.advance(5)
    payload.events(clear=True)
    payload.advance(45)
    accesses = payload.register_accesses("fpgaRegs")
    assert not [a for a in accesses if a["rec"] == "reg_irq"], "nothing reaches flight software"
    assert not [a for a in accesses if a.get("offset") == REG_STATUS], "so the status is never serviced"
    assert [a for a in accesses if a.get("offset") == REG_TEMPERATURE], "register traffic is not affected"
    payload.fault_clear("fpgaRegs:irq")

    payload.fault_set("fpgaRegs:irq", "duplicate", count=1)
    payload.events(clear=True)
    payload.raise_irq("fpgaRegs", 1)
    payload.advance(4)
    assert len([a for a in payload.register_accesses("fpgaRegs") if a["rec"] == "reg_irq" and a["src"] == "inject"]) == 2
    assert payload.status()["active_faults"] == 0


def test_register_faults_show_in_flight_telemetry_and_events(gds, payload):
    # Stuck: every read returns the value, whatever the model holds. 50.0 degrees.
    payload.fault_set("fpgaRegs", "stuck", arg=500, direction="to_flight")
    gds.assert_event("Payload.wstsControl.FaultTriggered", timeout=T)
    gds.assert_telemetry("Payload.fpga.Temperature", value=50.0, timeout=T)
    payload.fault_clear("fpgaRegs")
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)

    # Error: the reads fail. The register ports have no status, so flight software reads
    # all ones, which the driver reports itself; the bus counts the failures.
    errors = payload.status()["errors"]
    gds.clear_histories()
    payload.fault_set("fpgaRegs", "error", arg=3, count=4, direction="to_flight")
    gds.assert_event("Payload.fpga.FpgaReadInvalid", [REG_TEMPERATURE], timeout=T)
    gds.assert_event("Payload.fpga.FpgaIdMismatch", [0xFFFFFFFF], timeout=T)
    gds.assert_event("Payload.wstsControl.FaultExpired", timeout=T)
    gds.assert_event("Payload.fpga.FpgaIdentified", [FPGA_ID], timeout=T)
    assert payload.status()["errors"] >= errors + 4
    gds.assert_telemetry("Payload.wstsControl.BusErrors", timeout=T)
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)


def test_register_commands_through_gds(gds, payload):
    # REG_SET and IRQ_RAISE, as an operator sends them through the hub. 64-bit arguments.
    gds.send_and_assert_command("Payload.wstsControl.REG_SET", ["fpgaRegs", REG_TEMPERATURE, 335], timeout=T)
    gds.assert_telemetry("Payload.fpga.Temperature", value=33.5, timeout=T)
    gds.send_and_assert_command("Payload.wstsControl.REG_SET", ["fpgaRegs", REG_TEMPERATURE, 215], timeout=T)
    payload.events(clear=True)
    gds.send_and_assert_command("Payload.wstsControl.IRQ_RAISE", ["fpgaRegs", 7], timeout=T)
    payload.wait_for(lambda m: m.get("rec") == "reg_irq" and m.get("number") == 7 and m.get("src") == "inject")
    # A register the block cannot take: the command fails and says why
    gds.send_command("Payload.wstsControl.REG_SET", ["i2cBus", 0, 1])
    gds.assert_event("Payload.wstsControl.ActionFailed", timeout=T)


def test_register_traffic_is_recorded_and_replayed(gds, payload, tmp_path):
    recording = tmp_path / "registers.jsonl"
    payload.record_start(recording)
    payload.advance(30)
    payload.record_stop()
    lines = [json.loads(line) for line in recording.read_text().splitlines()]
    reads = [l for l in lines if l.get("rec") == "reg_rd" and l["endpoint"] == "fpgaRegs"]
    assert {"offset": "0xc", "width": 4, "read": "d7000000", "status": 0} == {
        key: reads[0][key] for key in ("offset", "width", "read", "status")}
    assert any(l.get("rec") == "reg_wr" and l["offset"] == "0x10" for l in lines)
    assert any(l.get("rec") == "reg_irq" and l["number"] == 1 for l in lines)

    # Responses replay: the model is bypassed, flight software reads what was recorded
    payload.model_set("fpga", "reg.0x0C", 400)
    gds.assert_telemetry("Payload.fpga.Temperature", value=40.0, timeout=T)
    gds.clear_histories()
    payload.events(clear=True)
    payload.scenario_load(recording, mode="responses")
    payload.scenario_start()
    payload.wait_for(lambda m: m.get("rec") == "reg_rd" and m.get("src") == "replay" and m.get("read") == "d7000000")
    payload.wait_for(lambda m: m.get("rec") == "reg_irq" and m.get("src") == "replay")
    while payload.status()["scenario_state"] == "running":
        time.sleep(0.05)
    gds.assert_telemetry("Payload.fpga.Temperature", value=40.0, timeout=T)
    payload.model_set("fpga", "reg.0x0C", 215)
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)


def test_python_register_model(gds, payload):
    """An external process takes over as the FPGA block, and gives it back when it leaves"""
    from fprime_wsts.bridge import WstsBridge

    registers = {REG_ID: FPGA_ID, REG_STATUS: 0, REG_FRAME_COUNT: 77, REG_TEMPERATURE: 995}
    writes = []

    def read(offset, width):
        return registers[offset] if offset in registers else (1, 0)

    def write(offset, width, value):
        writes.append((offset, value))
        if offset == REG_STATUS:
            registers[REG_STATUS] &= ~value

    with WstsBridge(payload.address) as model:
        model.serve_register("fpgaRegs", read, write)
        gds.assert_telemetry("Payload.fpga.Temperature", value=99.5, timeout=T)
        # Its interrupt, with a cause in its status register
        registers[REG_STATUS] = STATUS_FRAME_READY
        model.raise_irq("fpgaRegs", 1)
        gds.assert_telemetry("Payload.fpga.FrameCount", value=77, timeout=T)
        assert (REG_STATUS, STATUS_FRAME_READY) in writes
        assert any(offset == REG_HEARTBEAT for offset, _ in writes)
    # The external model has gone away: the block of the manifest answers again
    gds.assert_telemetry("Payload.fpga.Temperature", value=21.5, timeout=T)
