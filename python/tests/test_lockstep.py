"""Lockstep (docs/stepped-time.md, phase 4): the conductor of a system, against stand-ins for
the elements that answer as an element in lockstep does"""

import json
import socket
import threading
import time

import pytest

from fprime_wsts.bridge import WstsSystem
from fprime_wsts.lockstep import Conductor, LockstepError, Stuck, SystemScenario


class FakeElement:
    """An element in lockstep: takes a tick per grant, and sends `per_tick` messages on its
    link each tick"""

    def __init__(self, name, period_ns, per_tick=0, link="hub"):
        self.name = name
        self.period_ns = period_ns
        self.per_tick = per_tick
        self.link = link
        self.tick = 0
        self.sent = 0
        self.received = 0
        self.lockstep = True
        self.paused = False
        self.stuck_at = None  # tick whose work does not finish
        self.advances = []  # (system time of the tick, the request)
        self.actions = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            connection, _ = self.server.accept()
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def status(self):
        return {"tick": self.tick, "tick_period_ns": self.period_ns, "lockstep": self.lockstep, "settled": True,
                "paused": self.paused, "stepped": self.lockstep, "endpoints": "",
                f"link.{self.link}.sent": self.sent, f"link.{self.link}.received": self.received}

    def _serve(self, connection):
        for line in connection.makefile("r"):
            message = json.loads(line)
            response = {"id": message["id"], "ok": True}
            op = message["op"]
            if op == "status":
                response.update(self.status())
            elif op == "advance":
                self.tick += message["ticks"]
                self.advances.append((self.tick * self.period_ns, message))
                self.sent += self.per_tick
                response.update(self.status(), done=True)
                if self.tick == self.stuck_at:
                    response.update(stuck="Payload.camera: 1", paused=True)
            else:
                self.actions.append(message)
            connection.sendall((json.dumps(response) + "\n").encode())


def describe(elements):
    names = list(elements)
    return {
        "system": "Demo", "ground": names[0],
        "elements": [{"name": name, "bridge": f"tcp:{element.port}"} for name, element in elements.items()],
        "links": [{"name": "hub", "a": {"element": names[0], "endpoint": "hubDriver"},
                   "b": {"element": names[1], "endpoint": "hubDriver"}}],
    }


@pytest.fixture
def elements():
    # 10 Hz and 20 Hz, as the example's Cdh and Payload; Payload sends two messages a tick
    return {"cdh": FakeElement("cdh", 100_000_000), "payload": FakeElement("payload", 50_000_000, per_tick=2)}


@pytest.fixture
def conductor(elements):
    with Conductor(describe(elements), request_timeout=2) as conductor:
        yield conductor


def test_instants_are_the_times_at_which_any_element_has_a_tick(elements, conductor):
    assert conductor.run(until_ns=1_000_000_000) == 1_000_000_000
    assert elements["cdh"].tick == 10 and elements["payload"].tick == 20, "one simulated second each"
    assert conductor.instants == 20, "every 50 ms one, Cdh in every other"
    times = [at for at, _ in elements["cdh"].advances]
    assert times == [100_000_000 * n for n in range(1, 11)]
    assert all(request["ticks"] == 1 for _, request in elements["payload"].advances)
    assert conductor.time_ns == 1_000_000_000


def test_a_tick_waits_for_what_the_other_end_had_sent_by_the_last_instant(elements, conductor):
    conductor.run(until_ns=300_000_000)
    expected = [(at, request.get("expect.hub")) for at, request in elements["cdh"].advances]
    # Payload had sent 2 messages a tick: by 50 ms 2, by 150 ms 6. What it sends at 100 ms,
    # in the same instant as Cdh's tick, is not waited for: it is for the next tick
    assert expected == [(100_000_000, 2), (200_000_000, 6), (300_000_000, 10)]
    assert all(request.get("expect.hub") == 0 for _, request in elements["payload"].advances), "Cdh sends nothing"


def test_a_number_of_instants_and_the_system_client(elements):
    with WstsSystem(describe(elements), request_timeout=2) as system:
        assert system.step() == pytest.approx(0.05), "one instant: Payload alone"
        assert system.step(instants=3) == pytest.approx(0.2)
        assert system.step(seconds=0.5) == pytest.approx(0.7)
        assert system.time() == pytest.approx(0.7)
        assert elements["cdh"].tick == 7 and elements["payload"].tick == 14
        system.set_stepped(False)
        assert elements["cdh"].actions[-1] == {"id": elements["cdh"].actions[-1]["id"], "op": "set_stepped",
                                                "stepped": False, "lockstep": False}


def test_an_element_behind_catches_up_first(elements, conductor):
    # Switched from real time, the elements are where their own clocks took them
    elements["payload"].tick = 40  # two seconds
    elements["cdh"].tick = 15  # one and a half
    conductor.run(instants=5)
    assert elements["cdh"].tick == 20 and elements["payload"].tick == 40, "Cdh alone, to Payload's time"
    conductor.run(instants=1)
    assert elements["payload"].tick == 41 and elements["cdh"].tick == 20


def test_an_element_that_does_not_finish_stops_the_system(elements, conductor):
    elements["payload"].stuck_at = 3
    with pytest.raises(Stuck) as caught:
        conductor.run(until_ns=1_000_000_000)
    assert caught.value.stuck == {"payload": "Payload.camera: 1"}
    assert caught.value.time_ns == 150_000_000
    assert "payload: Payload.camera: 1 (at 0.15 s" in str(caught.value)


def test_every_element_must_be_in_lockstep(elements, conductor):
    elements["cdh"].lockstep = False
    with pytest.raises(LockstepError, match="cdh is not in lockstep"):
        conductor.run(instants=1)
    elements["cdh"].lockstep = True
    elements["payload"].period_ns = 0
    with pytest.raises(LockstepError, match="did not settle"):
        conductor.wait_ready(timeout=0.1)


def test_every_element_needs_a_bridge(elements):
    description = describe(elements)
    description["elements"].append({"name": "dark", "bridge": "none"})
    with pytest.raises(LockstepError, match="dark have no bridge"):
        Conductor(description)


def test_a_pause_holds_a_conductor_that_runs_freely(elements, conductor):
    elements["cdh"].paused = True

    def resume():
        time.sleep(0.3)
        elements["cdh"].paused = False

    threading.Thread(target=resume, daemon=True).start()
    began = time.monotonic()
    conductor.run(until_ns=200_000_000, hold_while_paused=True)
    assert time.monotonic() - began >= 0.25, "nothing while paused"
    assert elements["payload"].tick == 4
    # A step is taken from a paused system: that is what it is for
    elements["cdh"].paused = True
    conductor.run(instants=1)
    assert elements["payload"].tick == 5


def test_a_system_scenario_places_actions_on_one_time_base(tmp_path, elements, conductor):
    path = tmp_path / "system.jsonl"
    path.write_text("\n".join([
        "# a comment",
        json.dumps({"time": 0.1, "element": "payload", "op": "inject_bytes", "endpoint": "sensorUart", "text": "x"}),
        "",
        json.dumps({"time": 0.0, "link": "hub", "op": "fault_set", "kind": "drop", "direction": "a_to_b"}),
        json.dumps({"time": 0.12, "link": "hub", "op": "fault_clear"}),
    ]))
    scenario = SystemScenario.load(path, ["cdh", "payload"], ["hub"])
    assert scenario.end_ns == 120_000_000
    conductor.run(until_ns=scenario.end_ns, scenario=scenario)
    assert scenario.done
    fault, clear = [{k: v for k, v in a.items() if k != "id"} for a in elements["cdh"].actions]
    assert fault == {"op": "fault_set", "endpoint": "hubDriver", "kind": "drop", "dir": "to_device"}
    assert clear == {"op": "fault_clear", "endpoint": "hubDriver"}
    injected = elements["payload"].actions[0]
    assert injected["op"] == "inject_bytes" and injected["text"] == "x"
    # Applied between two instants, once the system's time had reached theirs: the injection
    # after the instant at 100 ms, the clear (at 120 ms) after the one at 150 ms
    assert conductor.time_ns == 150_000_000, "the last action needs an instant at or after its time"


@pytest.mark.parametrize("line, message", [
    ({"element": "payload", "op": "log"}, "no 'time'"),
    ({"time": 1, "op": "log"}, "say which element"),
    ({"time": 1, "element": "nowhere", "op": "log"}, "say which element"),
    ({"time": 1, "link": "hub", "op": "inject_bytes"}, "fault_set and fault_clear only"),
    ({"time": 1, "link": "uart", "op": "fault_clear"}, "no link 'uart'"),
    ({"time": -1, "element": "cdh", "op": "log"}, "not negative"),
    ({"time": "soon", "element": "cdh", "op": "log"}, "number of seconds"),
    ({"time": 1, "element": "cdh"}, "with an 'op'"),
])
def test_system_scenario_errors(tmp_path, line, message):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(line) + "\n")
    with pytest.raises(LockstepError, match=message):
        SystemScenario.load(path, ["cdh", "payload"], ["hub"])
