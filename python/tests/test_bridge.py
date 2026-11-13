"""Bridge client against a stand-in for the simulation side of the protocol"""

import json
import socket
import threading
import time

import pytest

from fprime_wsts.bridge import BridgeError, WstsBridge


class FakeSim:
    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.requests = []
        self.replies = []
        self.connection = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.connection, _ = self.server.accept()
        for line in self.connection.makefile("r"):
            message = json.loads(line)
            if message["op"] == "reply":
                self.replies.append(message)
                continue
            self.requests.append(message)
            response = {"id": message["id"], "ok": True}
            if message["op"] == "status":
                response.update(tick=len(self.requests), endpoints="a,b", paused=False)
            elif message["op"] == "bogus":
                response = {"id": message["id"], "ok": False, "error": "unknown op 'bogus'"}
            elif message["op"] == "gpio_get":
                response["level"] = True
            self.push(response)

    def push(self, message):
        self.connection.sendall((json.dumps(message) + "\n").encode())


@pytest.fixture
def sim():
    return FakeSim()


@pytest.fixture
def bridge(sim):
    with WstsBridge(f"tcp:{sim.port}", request_timeout=2) as client:
        yield client


def test_requests_and_errors(sim, bridge):
    assert bridge.status()["endpoints"] == ["a", "b"]
    bridge.fault_set("imuBus", "error", arg=3, count=2, direction="to_flight")
    assert sim.requests[-1] == {"id": 2, "op": "fault_set", "endpoint": "imuBus", "kind": "error", "arg": 3,
                                "count": 2, "ticks": 0, "dir": "to_flight"}
    bridge.inject_text("uart", "hi\n")
    assert sim.requests[-1]["data"] == "68690a"
    assert bridge.gpio_get("pin") is True
    with pytest.raises(BridgeError, match="unknown op"):
        bridge.request("bogus")


def test_wait_for_tick(bridge):
    assert bridge.wait_for_tick(4) >= 4


def test_events_and_bytes_sent(sim, bridge):
    bridge.subscribe()
    sim.push({"ev": "rec", "rec": "bs_tx", "endpoint": "uart", "data": "4142"})
    sim.push({"ev": "rec", "rec": "bs_tx", "endpoint": "other", "data": "ff"})
    sim.push({"ev": "sim_event", "event": "fault_triggered", "endpoint": "uart"})
    event = bridge.wait_for(lambda m: m.get("event") == "fault_triggered", timeout=2)
    assert event["endpoint"] == "uart"
    assert bridge.bytes_sent("uart") == b"AB"
    with pytest.raises(BridgeError):
        bridge.wait_for(lambda m: False, timeout=0.2)


def test_serving_an_i2c_device(sim, bridge):
    bridge.serve_i2c("bus", 0x68, lambda write, length: (0, bytes([write[0] + 1] * length)))
    assert sim.requests[-1]["op"] == "attach" and sim.requests[-1]["addr"] == 0x68
    sim.push({"ev": "i2c_req", "rid": 9, "endpoint": "bus", "addr": 0x68, "write": "10", "rlen": 2})
    sim.push({"ev": "i2c_req", "rid": 10, "endpoint": "bus", "addr": 0x10, "write": "", "rlen": 1})
    deadline = time.monotonic() + 2
    while len(sim.replies) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sim.replies[0] == {"op": "reply", "rid": 9, "status": 0, "data": "1111"}
    assert sim.replies[1]["status"] == 5, "no handler for that address"


def test_serving_a_register_block(sim, bridge):
    registers = {0x10: 0x1234, 0xFFFFFFFF00000000: 7}
    writes = []

    def read(offset, width):
        if offset not in registers:
            return 1, 0
        return registers[offset]

    bridge.serve_register("fpga", read, lambda offset, width, value: writes.append((offset, width, value)))
    assert sim.requests[-1] == {"id": 1, "op": "attach", "endpoint": "fpga", "kind": "register"}
    sim.push({"ev": "reg_req", "rid": 1, "endpoint": "fpga", "access": "read", "offset": "0x10", "width": 4, "rlen": 4})
    sim.push({"ev": "reg_req", "rid": 2, "endpoint": "fpga", "access": "read", "offset": "0xffffffff00000000",
              "width": 8, "rlen": 8})
    sim.push({"ev": "reg_req", "rid": 3, "endpoint": "fpga", "access": "read", "offset": "0x20", "width": 4, "rlen": 4})
    sim.push({"ev": "reg_req", "rid": 4, "endpoint": "fpga", "access": "write", "offset": "0x14", "width": 4,
              "value": "0xfeedf00d", "rlen": 0})
    sim.push({"ev": "reg_req", "rid": 5, "endpoint": "other", "access": "read", "offset": "0x0", "width": 4, "rlen": 4})
    deadline = time.monotonic() + 2
    while len(sim.replies) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sim.replies[0] == {"op": "reply", "rid": 1, "status": 0, "data": "34120000"}
    assert sim.replies[1] == {"op": "reply", "rid": 2, "status": 0, "data": "0700000000000000"}
    assert sim.replies[2]["status"] == 1, "the handler's own status"
    assert sim.replies[3] == {"op": "reply", "rid": 4, "status": 0, "data": ""}
    assert writes == [(0x14, 4, 0xFEEDF00D)]
    assert sim.replies[4]["status"] == 5, "no handler for that endpoint"


def test_register_actions_and_records(sim, bridge):
    bridge.reg_set("fpga", 0x10, 0xFFFFFFFF00000001)
    assert sim.requests[-1] == {"id": 1, "op": "reg_set", "endpoint": "fpga", "offset": "0x10",
                                "value": "0xffffffff00000001"}
    bridge.raise_irq("fpga", 3)
    assert sim.requests[-1] == {"id": 2, "op": "irq_raise", "endpoint": "fpga", "number": 3}
    bridge.subscribe()
    sim.push({"ev": "rec", "rec": "reg_rd", "endpoint": "fpga", "offset": "0x10", "width": 4, "read": "78563412",
              "status": 0, "src": "flight", "tick": 4})
    sim.push({"ev": "rec", "rec": "reg_wr", "endpoint": "fpga", "offset": "0x14", "width": 4, "write": "01000000",
              "status": 0, "src": "flight", "tick": 4})
    sim.push({"ev": "rec", "rec": "reg_irq", "endpoint": "fpga", "number": 3, "src": "inject", "tick": 5})
    sim.push({"ev": "rec", "rec": "reg_rd", "endpoint": "elsewhere", "offset": "0x0", "width": 4, "read": "00000000",
              "status": 0, "src": "flight", "tick": 5})
    bridge.wait_for(lambda m: m.get("endpoint") == "elsewhere", timeout=2)
    assert bridge.register_accesses("fpga") == [
        {"rec": "reg_rd", "tick": 4, "src": "flight", "offset": 0x10, "status": 0, "value": 0x12345678},
        {"rec": "reg_wr", "tick": 4, "src": "flight", "offset": 0x14, "status": 0, "value": 1},
        {"rec": "reg_irq", "tick": 5, "src": "inject", "number": 3},
    ]


def test_unreachable_bridge():
    with pytest.raises(BridgeError, match="cannot connect"):
        WstsBridge("tcp:1", connect_timeout=0.3)
    with pytest.raises(BridgeError, match="unrecognized"):
        WstsBridge("carrier-pigeon", connect_timeout=0.3)
