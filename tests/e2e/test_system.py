"""The Mission system: two flight computers joined by an F Prime hub, as one simulation.

`Cdh` talks to the ground; `Payload` is reached only through the hub. Each runs as its own
simulated deployment, and the hub link between their Ethernet drivers is carried by
WSTS. One GDS is connected to `Cdh` with the merged dictionary.
"""

import time

from conftest import QUIET, T, channels_seen, events_seen


def received(system, element, endpoint="hubDriver"):
    """Bus records of bytes delivered to flight software on an element's endpoint"""
    return [m for m in system[element].events() if m.get("rec") == "bs_rx" and m.get("endpoint") == endpoint]


def test_system_is_up(wsts_system):
    assert set(wsts_system) == {"cdh", "payload"}
    assert wsts_system.links_up()
    status = wsts_system.status()
    for element in ("cdh", "payload"):
        assert status[element]["links"] == 1
        assert "hubDriver" in status[element]["endpoints"]


def test_elements_keep_their_own_time(wsts_system):
    # Cdh's watchdog timer ticks at 10 Hz, Payload's Linux timer at 20 Hz
    before = wsts_system.status()
    time.sleep(2.5)
    after = wsts_system.status()
    cdh = after["cdh"]["tick"] - before["cdh"]["tick"]
    payload = after["payload"]["tick"] - before["payload"]["tick"]
    assert cdh >= 15
    assert 1.6 * cdh <= payload <= 2.4 * cdh


def test_merged_dictionary_has_both_elements_and_the_project_ids(gds):
    # The flight project's configuration overrides place CdhCore (a framework subtopology)
    # and SensorLink (a library subtopology); the simulation's overrides do not move them
    dictionary = gds.pipeline.dictionaries
    assert dictionary.command_name["CdhCore.cmdDisp.CMD_NO_OP"].get_op_code() == 0x05000000
    assert dictionary.command_name["SensorLink.sensor.SEND_QUERY"].get_op_code() == 0x06000000
    assert dictionary.command_name["Payload.instrument.TAKE_SAMPLE"].get_op_code() >= 0x20000000


def test_remote_telemetry_reaches_the_ground(gds):
    # Produced on Payload, carried by the hub, downlinked by Cdh, decoded with the merged dictionary
    first = gds.assert_telemetry("Payload.instrument.Uptime", timeout=T).get_val()
    gds.clear_histories()
    assert gds.assert_telemetry("Payload.instrument.Uptime", timeout=T).get_val() > first


def test_command_is_routed_through_the_hub(gds, wsts_system):
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [7])
    event = gds.assert_event("Payload.instrument.SampleTaken", timeout=T)
    count, gain = event.get_display_text().split("Sample ")[1].split(" taken at gain ")
    assert int(gain) == 7
    # The remote dispatcher's own events come back the same way
    gds.assert_event("Payload.cmdDisp.OpCodeCompleted", timeout=T)
    gds.assert_telemetry("Payload.instrument.Samples", value=int(count), timeout=T)
    # The command crossed the link: Payload's hub driver surrogate delivered bytes to flight software
    assert received(wsts_system, "payload")


def test_local_commands_stay_on_the_ground_element(gds, wsts_system):
    wsts_system.fault_set("hub", "drop", direction="a_to_b")  # anything Cdh sends to Payload is counted
    hits = wsts_system["cdh"].status()["fault_hits"]
    gds.send_and_assert_command("CdhCore.cmdDisp.CMD_NO_OP", timeout=T)
    gds.assert_event("CdhCore.cmdDisp.NoOpReceived", timeout=T)
    # Nothing went onto the link: the command splitter kept the command on Cdh
    assert wsts_system["cdh"].status()["fault_hits"] == hits


def test_cut_uplink_loses_commands_until_restored(gds, wsts_system):
    wsts_system.fault_set("hub", "drop", direction="a_to_b")
    hits = wsts_system["cdh"].status()["fault_hits"]
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [1])
    time.sleep(QUIET)
    assert not events_seen(gds, "SampleTaken")
    assert wsts_system["cdh"].status()["fault_hits"] == hits + 1
    # Telemetry still flows the other way
    gds.clear_histories()
    gds.assert_telemetry("Payload.instrument.Uptime", timeout=T)

    wsts_system.fault_clear("hub")
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [2])
    gds.assert_event("Payload.instrument.SampleTaken", timeout=T)
    assert len(events_seen(gds, "SampleTaken")) == 1


def test_cut_downlink_silences_the_remote_element(gds, wsts_system):
    gds.assert_telemetry("Payload.instrument.Uptime", timeout=T)
    wsts_system.fault_set("hub", "drop", direction="b_to_a")
    time.sleep(2.5)  # telemetry already on its way to the ground arrives
    gds.clear_histories()
    time.sleep(QUIET)
    assert channels_seen(gds, "SensorLink."), "Cdh's own telemetry keeps coming"
    assert not channels_seen(gds, "Payload.")
    wsts_system.fault_clear("hub")
    gds.assert_telemetry("Payload.instrument.Uptime", timeout=T)
    assert channels_seen(gds, "Payload.")


def test_fault_on_one_element_endpoint(gds, wsts_system):
    # The same cut, made at the far end of the link and addressed as <element>.<endpoint>
    wsts_system.fault_set("payload.hubDriver", "drop", direction="to_flight")
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [3])
    time.sleep(QUIET)
    assert not events_seen(gds, "SampleTaken")
    assert wsts_system["payload"].status()["active_faults"] == 1
    assert wsts_system["cdh"].status()["active_faults"] == 0
    wsts_system.fault_clear("payload.hubDriver")
    assert wsts_system["payload"].status()["active_faults"] == 0


def test_wsts_commands_reach_the_remote_element(gds, wsts_system):
    # Each element has its own wstsControl, in its own ID window, so the flight routing
    # delivers WSTS commands like any others and the merged dictionary keeps them apart
    gds.send_command("Payload.wstsControl.FAULT_SET", ["hubDriver", "DELAY", "TO_FLIGHT", 1, 0, 0])
    gds.assert_event("Payload.wstsControl.FaultArmed", timeout=T)
    assert wsts_system["payload"].status()["active_faults"] == 1
    assert wsts_system["cdh"].status()["active_faults"] == 0

    gds.send_command("Cdh.wstsControl.FAULT_SET", ["hubDriver", "DELAY", "TO_FLIGHT", 1, 0, 0])
    gds.assert_event("Cdh.wstsControl.FaultArmed", timeout=T)
    assert wsts_system["cdh"].status()["active_faults"] == 1


def test_pause_and_resume_the_whole_system(wsts_system):
    wsts_system.pause()
    time.sleep(0.5)  # a tick already under way completes
    paused = wsts_system.status()
    assert all(status["paused"] for status in paused.values())
    time.sleep(2.5)
    still = wsts_system.status()
    assert {name: status["tick"] for name, status in still.items()} == {name: status["tick"] for name, status in paused.items()}
    wsts_system.resume()
    wsts_system["cdh"].advance(5)
    wsts_system["payload"].advance(10)


def test_link_recovers_after_everything(gds, wsts_system):
    assert wsts_system.links_up()
    gds.send_command("Payload.instrument.TAKE_SAMPLE", [9])
    gds.assert_event("Payload.instrument.SampleTaken", timeout=T)
