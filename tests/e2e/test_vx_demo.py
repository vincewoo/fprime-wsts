"""VxDemo in simulation: a VxWorks deployment running on Linux.

The flight topology code, including its calls to the VxWorks UART driver and watchdog
timer, is compiled unchanged against the surrogates.
"""

T = 10


def test_watchdog_timer_surrogate_cycles_rate_groups(sse):
    before = sse.status()["tick"]
    sse.advance(10)
    assert sse.status()["tick"] >= before + 10


def test_serial_sensor_through_uart_surrogate(gds, sse):
    gds.assert_telemetry("VxDemo.sensor.Temperature", value=-12.25, timeout=T)
    gds.send_and_assert_command("VxDemo.sensor.SEND_QUERY", ["ID?"], timeout=T)
    sse.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "sensorUart")
    assert b"ID?\n" in sse.bytes_sent("sensorUart")


def test_uart_fault(gds, sse):
    # The UART refuses the next transmission
    sse.fault_set("sensorUart", "error", arg=3, count=1, direction="to_device")
    gds.send_command("VxDemo.sensor.SEND_QUERY", ["ID?"])
    gds.assert_event("VxDemo.sensor.QueryFailed", timeout=T)
    gds.assert_event("VxDemo.sseControl.FaultTriggered", timeout=T)
    gds.assert_event("VxDemo.sseControl.FaultExpired", timeout=T)
