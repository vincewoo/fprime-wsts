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


def test_unreachable_bridge():
    with pytest.raises(BridgeError, match="cannot connect"):
        WstsBridge("tcp:1", connect_timeout=0.3)
    with pytest.raises(BridgeError, match="unrecognized"):
        WstsBridge("carrier-pigeon", connect_timeout=0.3)
