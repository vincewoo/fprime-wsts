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
        self.answer_status = None
        self.answers = {}
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
            if message["op"] == "status" and self.answer_status:
                response = self.answer_status(message)
            elif message["op"] == "status":
                response.update(tick=len(self.requests), endpoints="a,b", paused=False)
            elif message["op"] in self.answers:
                response.update(self.answers[message["op"]])
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


def test_serving_a_packet_node_and_injecting_packets(sim, bridge):
    bridge.serve_packet("spw", 0x44, lambda packet: bytes([0x67, packet[1] + 1]))
    assert sim.requests[-1] == {"id": sim.requests[-1]["id"], "op": "attach", "endpoint": "spw", "kind": "packet",
                                "addr": 0x44}
    bridge.serve_packet("spw", "any", lambda packet: None)
    assert sim.requests[-1]["addr"] == "any"
    sim.push({"ev": "pkt_req", "rid": 3, "endpoint": "spw", "addr": 0x44, "data": "4410", "rlen": 0})
    sim.push({"ev": "pkt_req", "rid": 4, "endpoint": "spw", "addr": 0x50, "data": "5001", "rlen": 0})
    deadline = time.monotonic() + 2
    while len(sim.replies) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sim.replies[0] == {"op": "reply", "rid": 3, "status": 0, "data": "6711"}
    assert sim.replies[1] == {"op": "reply", "rid": 4, "status": 0, "data": ""}, "the node at any: no reply"
    bridge.inject_packet("spw", bytes([0x67, 0x01]))
    assert sim.requests[-1]["op"] == "inject_packet" and sim.requests[-1]["data"] == "6701"


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


# ----------------------------------------------------------------------
# The bridge from the command line: fprime-wsts status, fprime-wsts ctl
# ----------------------------------------------------------------------


def test_step_asks_for_ticks_and_waits_for_the_pause(sim, bridge):
    def paused_after_three(message):
        return {"id": message["id"], "ok": True, "tick": 40 + len(sim.requests), "endpoints": "",
                "paused": len(sim.requests) >= 4, "stepping": 0 if len(sim.requests) >= 4 else 2}
    sim.answer_status = paused_after_three
    assert bridge.step(5, wait=False) == 41 + 5
    assert sim.requests[-1] == {"id": 2, "op": "step", "ticks": 5}
    assert bridge.step(2) >= 44, "the tick at which it had paused"
    assert [r["op"] for r in sim.requests].count("step") == 2


def test_wait_quiescent_names_what_holds_work_up(sim, bridge):
    bridge.wait_quiescent()  # the stand-in answers ok: nothing outstanding
    assert sim.requests[-1]["op"] == "wait_quiescent" and sim.requests[-1]["timeout_ms"] >= 1
    sim.answers["wait_quiescent"] = {"quiescent": False, "activity": 2, "holding": "CdhCore.cmdDisp: 2"}
    with pytest.raises(BridgeError, match=r"2 piece\(s\) outstanding \(CdhCore.cmdDisp: 2\)"):
        bridge.wait_quiescent(timeout=0.05)


def test_stepped_time_is_asked_for_by_name(sim, bridge):
    bridge.set_stepped()
    assert sim.requests[-1] == {"id": 1, "op": "set_stepped", "stepped": True}
    bridge.set_stepped(False)
    assert sim.requests[-1]["stepped"] is False
    bridge.set_rate(0)  # in stepped time: no limit
    assert sim.requests[-1] == {"id": 3, "op": "set_rate", "rate": 0}


def generated(directory, sim):
    directory.mkdir()
    (directory / "wsts-report.json").write_text(json.dumps({"bridge": f"tcp:{sim.port}"}))
    return directory


def test_ctl_sends_one_action(tmp_path, sim, capsys):
    from fprime_wsts.cli import main

    out = generated(tmp_path / "Dep", sim)
    assert main(["ctl", str(out), "fault_set", "endpoint=i2cBus", "kind=error", "arg=3", "count=5"]) == 0
    assert sim.requests[-1] == {"id": 1, "op": "fault_set", "endpoint": "i2cBus", "kind": "error", "arg": 3, "count": 5}
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_ctl_text_values_and_refusals(tmp_path, sim, capsys):
    from fprime_wsts.cli import main

    out = generated(tmp_path / "Dep", sim)
    assert main(["ctl", str(out), "bogus", r"text=$TEMP,48.0\n", "level=true", "value=0x10"]) == 2
    assert sim.requests[-1] == {"id": 1, "op": "bogus", "text": "$TEMP,48.0\n", "level": True, "value": "0x10"}
    assert "unknown op 'bogus'" in capsys.readouterr().err
    assert main(["ctl", str(out), "pause", "nonsense"]) == 2
    assert "expected FIELD=VALUE" in capsys.readouterr().err
    assert main(["ctl", str(out), "pause", "--element", "cdh"]) == 2
    assert "is a single deployment" in capsys.readouterr().err


def test_status_of_a_running_simulation_and_of_none(tmp_path, sim, capsys):
    from fprime_wsts.cli import main

    out = generated(tmp_path / "Dep", sim)
    assert main(["status", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Dep: running at tick 1" in printed and "endpoints: a, b" in printed
    (out / "wsts-report.json").write_text(json.dumps({"bridge": "tcp:1"}))
    assert main(["status", str(out), "--timeout", "0.2"]) == 2
    assert "is the simulation running?" in capsys.readouterr().err
    assert main(["status", str(tmp_path)]) == 2


def test_status_counts_a_started_link_as_not_up(tmp_path, sim, capsys):
    """A datagram link is started and counted in `links`, but never comes up: the status
    line says both, rather than reading as a link that failed"""
    from fprime_wsts.cli import main

    sim.answer_status = lambda message: {"id": message["id"], "ok": True, "links": 2, "links_up": 1}
    out = generated(tmp_path / "Dep", sim)
    assert main(["status", str(out)]) == 0
    assert "links 2 started, 1 up" in capsys.readouterr().out


def test_links_up_does_not_wait_for_a_datagram_link():
    """`links_up` counts the link ends that have a connection: a datagram end is started
    but never up, so a system with one must not be reported as not yet linked"""
    from fprime_wsts.bridge import links_expected_started, links_expected_up

    description = {
        "system": "Demo", "ground": "cdh", "elements": [],
        "links": [
            {"name": "hub", "kind": "bytestream",
             "a": {"element": "cdh", "endpoint": "hubDriver"}, "b": {"element": "payload", "endpoint": "hubDriver"}},
            {"name": "telem", "kind": "datagram",
             "a": {"element": "cdh", "endpoint": "tlmDriver"}, "b": {"element": "payload", "endpoint": "tlmDriver"}},
        ],
    }
    assert links_expected_up(description) == {"cdh": 1, "payload": 1}, "only the stream link has a connection"
    assert links_expected_started(description) == {"cdh": 2, "payload": 2}, "a run waits for both to be started"


def test_an_action_for_one_element_of_a_system_needs_its_name(tmp_path, capsys):
    from fprime_wsts.cli import main

    (tmp_path / "wsts-system.json").write_text(json.dumps({"system": "S", "ground": "cdh", "elements": [
        {"name": "cdh", "directory": "cdh", "bridge": "tcp:1"}, {"name": "payload", "directory": "payload", "bridge": "tcp:2"}]}))
    assert main(["ctl", str(tmp_path), "gpio_force", "endpoint=pin", "level=true"]) == 2
    assert "add --element with one of cdh, payload" in capsys.readouterr().err
    assert main(["ctl", str(tmp_path), "pause", "--element", "radio"]) == 2
    assert "not an element" in capsys.readouterr().err


def test_bus_traffic_is_nested_by_endpoint_and_address():
    from fprime_wsts.bridge import bus_traffic
    from fprime_wsts.cli import render_traffic

    flat = {"id": 3, "bus.i2cBus.transactions": 12, "bus.i2cBus.to_device": 12, "bus.i2cBus.unanswered": 2,
            "bus.i2cBus.at.0x68.transactions": 10, "bus.i2cBus.at.0x68.unanswered": 0,
            "bus.i2cBus.at.0x40.transactions": 2, "bus.i2cBus.at.0x40.unanswered": 2,
            "bus.spiBus.transactions": 3, "bus.spiBus.unanswered": 3, "bus.heaterPin.transactions": 0}
    traffic = bus_traffic(flat)
    assert set(traffic) == {"i2cBus", "spiBus", "heaterPin"}
    assert traffic["i2cBus"]["transactions"] == 12
    assert traffic["i2cBus"]["addresses"] == {"0x68": {"transactions": 10, "unanswered": 0},
                                              "0x40": {"transactions": 2, "unanswered": 2}}
    assert traffic["heaterPin"] == {"addresses": {}, "transactions": 0}
    lines = render_traffic(traffic)
    assert lines[0].split() == ["endpoint", "from", "flight", "to", "flight", "bytes", "out", "bytes", "in", "errors",
                                "unanswered", "fault", "hits", "last", "tick"]
    assert lines[1].split()[0] == "heaterPin" and lines[2].split()[:2] == ["i2cBus", "12"]
    assert "  nothing answered on i2cBus at: 0x40 (2)" in lines
    assert "  nothing answered on spiBus: no device is attached" in lines
    assert render_traffic({}) == ["  bus traffic: no endpoints"]


def test_tick_times_are_nested_and_described():
    from fprime_wsts.bridge import tick_times
    from fprime_wsts.runner import describe_tick_times, with_traffic

    flat = {"id": 4, "ticks": 120, "mean_us": 812.5, "max_us": 12345.6, "over_period": 2,
            "slowest.0.tick": 37, "slowest.0.us": 12345.6, "slowest.1.tick": 90, "slowest.1.us": 9100.0}
    times = tick_times(flat)
    assert times == {"ticks": 120, "mean_ms": 0.812, "max_ms": 12.346, "over_period": 2,
                     "slowest": [{"tick": 37, "ms": 12.346}, {"tick": 90, "ms": 9.1}]}
    line = describe_tick_times(times, 8_000_000)
    assert line.startswith("slowest ticks in host time: tick 37 12.346 ms, tick 90 9.1 ms (mean 0.812 ms over 120")
    assert "2 took longer than the tick period of 8 ms" in line
    assert "longer than" not in describe_tick_times({**times, "over_period": 0})
    assert describe_tick_times(None) is None and describe_tick_times(tick_times({})) is None

    class Bridge:
        def bus_stats(self):
            return {}

        def tick_times(self):
            return times

    assert with_traffic(Bridge(), {"stepped": True})["tick_times"] == times
    assert "tick_times" not in with_traffic(Bridge(), {"stepped": False}), "real time is not timed"
