"""SubtopDemo in simulation: the UART driver is inside a library subtopology.

The driver instance `SensorLink.uart` is defined in lib/sensor-lib, not in the deployment's
Top. The simulated build takes a patched copy of that subtopology in place of the
original; its phase code, which opens and starts the VxWorks driver, runs against the
surrogate.
"""

T = 10

LINES = "SensorLink.sensor.LinesReceived"


def test_subtopology_uart_is_bound(wsts):
    assert wsts.status()["endpoints"] == ["uart"]


def test_model_bytes_reach_the_subtopology_component(gds):
    gds.assert_telemetry("SensorLink.sensor.Temperature", value=21.5, timeout=T)
    gds.assert_telemetry(LINES, timeout=T)


def test_flight_output_leaves_through_the_subtopology_uart(gds, wsts):
    gds.send_and_assert_command("SensorLink.sensor.SEND_QUERY", ["ID?"], timeout=T)
    wsts.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "uart")
    assert b"ID?\n" in wsts.bytes_sent("uart")


def test_project_configuration_overrides_are_in_force(gds):
    # config/project replaces CdhCoreConfig.fpp (framework) and SensorLinkConfig.fpp
    # (library): the IDs in the dictionary are the project's
    dictionary = gds.pipeline.dictionaries
    assert dictionary.command_name["CdhCore.cmdDisp.CMD_NO_OP"].get_op_code() == 0x05000000
    assert dictionary.command_name["SensorLink.sensor.SEND_QUERY"].get_op_code() == 0x06000000


def test_drop_fault_stops_the_sensor_data(gds, wsts):
    gds.assert_telemetry(LINES, timeout=T)
    wsts.fault_set("uart", "drop", direction="to_flight")
    try:
        wsts.advance(15)  # let anything already on its way be downlinked
        gds.clear_histories()
        assert gds.await_telemetry(LINES, timeout=4) is None, "sensor lines still arrive through a dropped UART"
    finally:
        wsts.fault_clear("uart")
    gds.assert_telemetry(LINES, timeout=T)
