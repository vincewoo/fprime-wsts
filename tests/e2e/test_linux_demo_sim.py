"""LinuxDemo built from a standalone simulation project (examples/linux-demo-sim).

The flight project is untouched; the manifest, a mirror surrogate for the GPIO driver and
a device model for the IMU live in the simulation project, and the generated deployment
is not checked in. These tests check that the project's own surrogate and model are the
ones running, and that the flight software behaves the same through them.
"""

import json

import pytest

T = 10  # seconds allowed for an event or channel to arrive


@pytest.fixture(scope="module")
def dictionary(request):
    path = request.config.getoption("--dictionary")
    return json.loads(open(path).read())


def test_mirror_surrogate_keeps_the_flight_dictionary(dictionary):
    events = {e["name"] for e in dictionary["events"]}
    # Drv.LinuxGpioDriver's events, present because Sim.SimLinuxGpio mirrors it
    assert {"LinuxDemo.heaterPin.OpenChip", "LinuxDemo.switchPin.OpenPinError"} <= events


def test_project_surrogate_is_bound(wsts):
    assert {"heaterPin", "switchPin"} <= set(wsts.status()["endpoints"])


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


def test_project_model_takes_engineering_units(gds, wsts):
    # Sim::ImuModel starts at 1 g on Z; its parameters are in g, not register bytes
    gds.assert_telemetry("LinuxDemo.imu.AccelZ", value=0x4000, timeout=T)
    wsts.model_set("imu", "accel_x", -0.5)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=-0x2000, timeout=T)
    wsts.model_set("imu", "accel_x", 0)
    gds.assert_telemetry("LinuxDemo.imu.AccelX", value=0, timeout=T)


def test_faults_reach_a_project_model(gds, wsts):
    # The model sits on the bus, so fault injection works on it unchanged
    wsts.fault_set("imuBus", "error", arg=3, count=5)
    gds.assert_event("LinuxDemo.imu.ImuReadError", timeout=T)
    gds.assert_event("LinuxDemo.imu.ImuRecovered", timeout=T)
