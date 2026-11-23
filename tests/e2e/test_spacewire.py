"""SpaceWire: Payload's sun sensor, read over RMAP through the project's SpaceWire driver,
which Wsts.SimSpaceWire replaces. The sensor is the toolkit's RMAP target (Wsts::RmapTarget)
at logical address 0xFE (payload.yaml)."""

import time

from fprime_gds.common.testing_fw import predicates

from conftest import T
from fprime_wsts.bridge import WstsBridge

SENSOR = 0xFE
PAYLOAD = 0x67


def rmap_crc(data: bytes) -> int:
    """The RMAP CRC (ECSS-E-ST-50-52C), as the sensor and flight software compute it"""
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xE0 if value & 1 else value >> 1
    return value


def read_reply(command: bytes, data: bytes, status: int = 0) -> bytes:
    """The reply of an RMAP target to a read command without a reply address"""
    header = bytes([command[4], 0x01, command[2] & 0x3F, status, command[0], command[5], command[6], 0x00])
    header += len(data).to_bytes(3, "big")
    return header + bytes([rmap_crc(header)]) + data + bytes([rmap_crc(data)])


def test_the_sun_sensor_is_read_over_rmap(gds, payload):
    """Every read is an RMAP command to the sensor's logical address, answered by the model:
    the sun angle follows the model's signal, between 15 and 45 degrees"""
    gds.assert_telemetry("Payload.sunSensor.SunAngle", value=predicates.within_range(14.9, 45.1), timeout=T)
    replies = gds.assert_telemetry("Payload.sunSensor.Replies", value=predicates.greater_than(0), timeout=T)
    assert replies is not None
    node = payload.bus_stats()["spw"]["addresses"][hex(SENSOR)]
    assert node["transactions"] > 0 and node["unanswered"] == 0 and node["errors"] == 0
    # What flight software sent: read commands, 4 bytes at the angle's address, with a good header CRC
    payload.subscribe()
    deadline = time.monotonic() + T
    while not payload.packets_sent("spw") and time.monotonic() < deadline:
        time.sleep(0.05)
    packet = payload.packets_sent("spw")[0]
    assert packet[0] == SENSOR and packet[1] == 0x01 and packet[2] == 0x4C, packet.hex()
    assert packet[8:12] == bytes([0, 0, 0, 0x10]) and packet[12:15] == bytes([0, 0, 4])
    assert rmap_crc(packet[:15]) == packet[15]


def test_the_integration_time_is_written_and_acknowledged(gds):
    """A verified, acknowledged RMAP write, commanded from the ground"""
    gds.send_command("Payload.sunSensor.SET_INTEGRATION", [250])
    gds.assert_event("Payload.sunSensor.IntegrationSet", [250], timeout=T)


def test_a_lost_reply_and_a_wrong_key_reach_flight_software(gds, payload):
    """Faults on the SpaceWire endpoint, and a model the sensor no longer agrees with"""
    payload.fault_set("spw", "drop", direction="to_flight", count=1)
    gds.assert_event("Payload.sunSensor.NoReply", timeout=T)
    gds.assert_event("Payload.wstsControl.FaultExpired", timeout=T)
    # The sensor's key changes: it answers every command with status 3 (invalid key)
    payload.model_set("sunSensor", "key", "0x21")
    try:
        gds.assert_event("Payload.sunSensor.RmapError", [None, 3], timeout=T)
    finally:
        payload.model_set("sunSensor", "key", "0x20")
    gds.clear_histories()
    gds.assert_telemetry("Payload.sunSensor.SunAngle", value=predicates.within_range(14.9, 45.1), timeout=T)


def test_a_node_served_from_python_stands_in_for_the_model(gds, payload):
    """An external process becomes the sensor at its logical address; when it leaves, the
    model of the manifest answers again"""
    seen = []

    def sensor(packet: bytes):
        seen.append(packet)
        if len(packet) == 16 and packet[2] == 0x4C:
            return read_reply(packet, (-12345).to_bytes(4, "big", signed=True))  # -12.345 degrees
        return None

    with WstsBridge(payload.address) as model:
        model.serve_packet("spw", SENSOR, sensor)
        # An F32 on the ground: -12.345 to its precision
        gds.assert_telemetry("Payload.sunSensor.SunAngle", value=predicates.within_range(-12.3451, -12.3449), timeout=T)
        assert seen and seen[0][0] == SENSOR and seen[0][4] == PAYLOAD
    gds.assert_telemetry("Payload.sunSensor.SunAngle", value=predicates.within_range(14.9, 45.1), timeout=T)
