"""The MCP server's session: names, feeds, and tools against stand-ins for the bridge and the GDS"""

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.mcp_server import Dictionary, Feed, Session, answer, resolve_name

from test_bridge import FakeSim

NAMES = ["CdhCore.cmdDisp.CMD_NO_OP", "Payload.cmdDisp.CMD_NO_OP", "Payload.heater.HEATER_SET",
         "Cdh.wstsTlm.hub_Received", "Payload.wstsTlm.hub_Received"]


# ----------------------------------------------------------------------
# Names of the dictionary
# ----------------------------------------------------------------------


def test_a_name_is_found_by_its_end_when_that_is_unique():
    assert resolve_name("Payload.heater.HEATER_SET", NAMES, "commands") == "Payload.heater.HEATER_SET"
    assert resolve_name("HEATER_SET", NAMES, "commands") == "Payload.heater.HEATER_SET"
    assert resolve_name("heater_set", NAMES, "commands") == "Payload.heater.HEATER_SET"
    assert resolve_name("Payload.cmdDisp.CMD_NO_OP", NAMES, "commands") == "Payload.cmdDisp.CMD_NO_OP"
    # A part of a word is not an end of the name
    with pytest.raises(WstsError, match="did you mean one of: Payload.heater.HEATER_SET"):
        resolve_name("ER_SET", NAMES, "commands")


def test_an_ambiguous_name_lists_the_candidates():
    with pytest.raises(WstsError, match="ambiguous: 2 commands match .*CdhCore.cmdDisp.CMD_NO_OP, "
                                        "Payload.cmdDisp.CMD_NO_OP"):
        resolve_name("CMD_NO_OP", NAMES, "commands")
    with pytest.raises(WstsError, match="no channel 'nothing'"):
        resolve_name("nothing", NAMES, "channels")


def write_dictionary(path):
    path.write_text(json.dumps({
        "commands": [
            {"name": "Payload.heater.HEATER_SET", "opcode": 0x2000, "commandKind": "async",
             "formalParams": [{"name": "on", "type": {"name": "bool", "kind": "bool"}}],
             "annotation": "Switch the heater"},
            {"name": "CdhCore.cmdDisp.CMD_NO_OP", "opcode": 0x500, "commandKind": "async", "formalParams": []},
        ],
        "events": [
            {"name": "CdhCore.cmdDisp.OpCodeCompleted", "severity": "COMMAND", "formalParams": [
                {"name": "Opcode", "type": {"name": "U32", "kind": "integer"}}], "format": "Opcode {} completed"},
            {"name": "CdhCore.cmdDisp.OpCodeError", "severity": "COMMAND", "formalParams": []},
            {"name": "Payload.heater.HeaterOn", "severity": "ACTIVITY_HI", "formalParams": []},
        ],
        "telemetryChannels": [{"name": "Payload.heater.Temperature", "type": {"name": "F32", "kind": "float"}}],
        "parameters": [],
    }))
    return path


def test_the_dictionary_is_searched_by_name_and_annotation(tmp_path):
    dictionary = Dictionary(write_dictionary(tmp_path / "d.json"))
    found = dictionary.search("commands", "heater")
    assert found["matches"] == 1
    assert found["entries"][0] == {"name": "Payload.heater.HEATER_SET", "args": ["on: bool"], "opcode": 0x2000,
                                   "kind": "async", "annotation": "Switch the heater"}
    assert dictionary.search("commands", "switch the")["matches"] == 1
    assert dictionary.search("telemetry")["entries"][0]["type"] == "F32"
    assert dictionary.search("events", "[")["matches"] == 0  # not a regular expression: taken as text
    with pytest.raises(WstsError, match="kind is one of"):
        dictionary.search("widgets")


# ----------------------------------------------------------------------
# What the GDS receives
# ----------------------------------------------------------------------


def test_a_feed_finds_what_came_after_a_mark_in_order_of_arrival():
    feed = Feed(limit=3)
    feed.data_callback("a")
    mark = feed.mark()
    assert feed.wait(lambda item: item == "a", mark, 0) is None  # before the mark
    threading.Timer(0.05, feed.data_callback, ["b"]).start()
    assert feed.wait(lambda item: item == "b", mark, 2) == "b"
    for item in "cde":
        feed.data_callback(item)
    assert feed.since(0) == ["c", "d", "e"]  # bounded: the oldest went
    assert feed.latest(lambda item: item in "cd") == "d"


class FakeEvent:
    def __init__(self, name, *values):
        self.template = SimpleNamespace(get_full_name=lambda: name)
        self.args = [SimpleNamespace(val=value) for value in values]

    def get_dict(self):
        return {"name": self.template.get_full_name(), "raw_time": "x", "args": self.args, "display_text": "t"}


class FakeGds:
    """GdsLink as the session uses it: commands up, events down"""

    def __init__(self, answer=None):
        self.events = Feed()
        self.telemetry = Feed()
        self.sent = []
        self.answer = answer

    def send_command(self, name, args):
        self.sent.append((name, args))
        if self.answer:
            for event in self.answer(name, args):
                threading.Timer(0.05, self.events.data_callback, [event]).start()

    def close(self):
        pass


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------


def make_deployment(directory, bridge, dictionary=None, gds=True):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "wsts-report.json").write_text(json.dumps({"bridge": "tcp:50100"}))
    (directory / "wsts-logs").mkdir(exist_ok=True)
    content = {"pid": os.getpid(), "bridge": bridge}
    if gds:
        content.update(gds_port=50000, zmq_transport=None, dictionary=str(dictionary) if dictionary else None)
    (directory / "wsts-logs" / "wsts-run.json").write_text(json.dumps(content))
    return directory


@pytest.fixture
def session(tmp_path):
    session = Session(cwd=tmp_path)
    yield session
    session.close()


def test_a_running_deployment_is_found_and_acted_on(tmp_path, session):
    sim = FakeSim()
    make_deployment(tmp_path / "dep", f"tcp:{sim.port}")
    status = session.status("dep")
    assert status["state"] == "running" and status["status"]["endpoints"] == ["a", "b"]
    # Once one run is known it need not be named
    session.fault_set("imuBus", "error", arg=3, count=2)
    assert sim.requests[-2] == {"id": 2, "op": "fault_set", "endpoint": "imuBus", "kind": "error", "arg": 3,
                                "count": 2, "ticks": 0, "dir": "both"}
    assert sim.requests[-1]["op"] == "status", "an action answers with where the simulation is now"
    session.inject("uart", hex="41 42")
    assert sim.requests[-1]["data"] == "4142"
    assert session.gpio("pin") == {"endpoint": "pin", "level": True}
    session.gpio("pin", level=False)
    assert sim.requests[-1] == {"id": sim.requests[-1]["id"], "op": "gpio_force", "endpoint": "pin", "level": False}
    session.register_set("i2cBus", 0x10, 0xAB, i2c_address=0x48)
    assert sim.requests[-1]["op"] == "i2c_reg_set" and sim.requests[-1]["addr"] == 0x48
    session.action("log", {"text": "hello"})
    assert sim.requests[-1]["op"] == "log" and sim.requests[-1]["text"] == "hello"
    assert answer(session.inject, "uart") == {"error": "give either text or hex"}
    assert "a single deployment" in answer(session.status, element="other")["error"]


def test_a_run_that_is_not_up_says_so(tmp_path, session):
    directory = make_deployment(tmp_path / "dep", "tcp:1")
    (directory / "wsts-logs" / "wsts-run.json").unlink()
    assert "is not running" in answer(session.status, "dep")["error"]
    assert "not a generated simulation" in answer(session.status, str(tmp_path))["error"]
    assert session.runs_list()["runs"][0]["state"] == "not running"


def test_a_run_started_elsewhere_is_stopped_only_when_forced(tmp_path, session):
    make_deployment(tmp_path / "dep", "tcp:1")  # its run file names this process: pytest
    assert "started elsewhere, by process" in answer(session.run_stop, "dep")["error"]


def make_system(directory, sims):
    directory.mkdir(parents=True)
    elements = [{"name": name, "directory": name, "bridge": f"tcp:{sim.port}"} for name, sim in sims.items()]
    (directory / "wsts-system.json").write_text(json.dumps({
        "system": "Mission", "ground": "cdh", "elements": elements,
        "links": [{"name": "hub", "kind": "bytestream", "a": {"element": "cdh", "endpoint": "hubDriver"},
                   "b": {"element": "payload", "endpoint": "hubDriver"}}]}))
    (directory / "wsts-logs").mkdir()
    (directory / "wsts-logs" / "wsts-run.json").write_text(json.dumps({
        "pid": os.getpid(), "elements": {name: f"tcp:{sim.port}" for name, sim in sims.items()}}))
    return directory


def test_a_system_is_addressed_by_element_and_link(tmp_path, session):
    sims = {"cdh": FakeSim(), "payload": FakeSim()}
    make_system(tmp_path / "sys", sims)
    assert set(session.status("sys")["elements"]) == {"cdh", "payload"}
    session.fault_set("hub", "drop", direction="b_to_a")
    assert [r for r in sims["cdh"].requests if r["op"] == "fault_set"][-1]["dir"] == "to_flight"
    session.fault_set("payload.i2cBus", "error")
    assert [r for r in sims["payload"].requests if r["op"] == "fault_set"][-1]["endpoint"] == "i2cBus"
    session.pause()
    assert all(any(r["op"] == "pause" for r in sim.requests) for sim in sims.values())
    assert "give element" in answer(session.step, 2)["error"]
    assert "give element" in answer(session.inject, "uart", "x")["error"]
    assert "no element 'imu'" in answer(session.gpio, "pin", element="imu")["error"]


def test_a_command_waits_for_its_dispatcher(tmp_path, session):
    dictionary = write_dictionary(tmp_path / "d.json")
    sim = FakeSim()
    make_deployment(tmp_path / "dep", f"tcp:{sim.port}", dictionary)
    run = session.run("dep")

    def dispatcher(name, args):
        if name == "Payload.heater.HEATER_SET":
            return [FakeEvent("Payload.heater.HeaterOn"), FakeEvent("CdhCore.cmdDisp.OpCodeCompleted", 0x2000)]
        return [FakeEvent("CdhCore.cmdDisp.OpCodeError", 0x500, "EXECUTION_ERROR")]

    run.gds = FakeGds(dispatcher)
    done = session.send_command("HEATER_SET", [True])
    assert run.gds.sent == [("Payload.heater.HEATER_SET", [True])]
    assert done["result"] == "completed"
    assert [event["name"] for event in done["events"]] == ["Payload.heater.HeaterOn", "CdhCore.cmdDisp.OpCodeCompleted"]
    assert done["events"][1]["args"] == [0x2000] and "raw_time" not in done["events"][1]
    assert session.send_command("CMD_NO_OP")["result"] == "error"
    assert "takes 1 argument(s) (on: bool), 0 given" in answer(session.send_command, "HEATER_SET")["error"]
    run.gds.answer = lambda name, args: []
    assert session.send_command("CMD_NO_OP", timeout=0.2)["result"] == "timeout"


def test_an_event_that_came_before_the_wait_is_found(tmp_path, session):
    dictionary = write_dictionary(tmp_path / "d.json")
    sim = FakeSim()
    make_deployment(tmp_path / "dep", f"tcp:{sim.port}", dictionary)
    run = session.run("dep")
    run.gds = FakeGds()
    run.gds.events.data_callback(FakeEvent("Payload.heater.HeaterOn"))  # before any action: not looked at
    session.inject("uart", text="x")
    run.gds.events.data_callback(FakeEvent("Payload.heater.HeaterOn"))  # late, after the action
    assert session.wait_event("HeaterOn", timeout=0)["found"] is True
    assert session.wait_event("HeaterOn", since="now", timeout=0.1)["found"] is False
    assert session.recent_events(search="heater")["total"] == 2


def test_a_tool_reads_the_logs_and_the_assert(tmp_path, session):
    sim = FakeSim()
    directory = make_deployment(tmp_path / "dep", f"tcp:{sim.port}")
    (directory / "wsts-logs" / "sim.log").write_text("one\ntwo\nthree\n")
    assert session.tail_log("sim", "dep", lines=2)["tail"] == "two\nthree"
    assert session.tail_log("sim", "dep", grep="^t")["lines"] == 2
    assert "does not exist" in answer(session.tail_log, "gds", "dep")["error"]
    assert session.last_assert("dep") == {"assert": None}


def test_the_server_offers_every_tool_with_a_schema(session):
    pytest.importorskip("mcp")
    import asyncio

    from fprime_wsts.mcp_server import create_server

    tools = {tool.name: tool for tool in asyncio.run(create_server(session).list_tools())}
    assert {"run_start", "run_stop", "runs_list", "build", "status", "fault_set", "send_command", "wait_event",
            "read_telemetry", "dictionary", "tail_log", "last_assert"} <= set(tools)
    schema = tools["fault_set"].inputSchema
    assert schema["required"] == ["endpoint", "kind"] and "run" in schema["properties"]
    assert all(tool.description for tool in tools.values())


def test_the_session_starts_and_stops_a_run(tmp_path):
    """run_start runs `fprime-wsts run` and returns once its run file names it; here a stand-in
    for that command writes the file and waits for its interrupt"""
    directory = tmp_path / "dep"
    directory.mkdir()
    (directory / "wsts-report.json").write_text(json.dumps({"bridge": "none"}))
    script = tmp_path / "fake_run.py"
    script.write_text(
        "import json, os, sys, time\n"
        "logs = os.path.join(sys.argv[2], 'wsts-logs'); os.makedirs(logs, exist_ok=True)\n"
        "path = os.path.join(logs, 'wsts-run.json')\n"
        "json.dump({'pid': os.getpid(), 'bridge': 'none'}, open(path, 'w'))\n"
        "try:\n"
        "    time.sleep(60)\n"
        "except KeyboardInterrupt:\n"
        "    os.unlink(path); print(json.dumps({'interrupted': True, 'args': sys.argv[1:]}))\n")
    import sys

    session = Session(cwd=tmp_path, executable=[sys.executable, str(script)])
    try:
        started = session.run_start("dep", stepped=True, duration=5)
        assert started["state"] == "running" and started["pid"] > 0
        assert "running already" in answer(session.run_start, "dep")["error"]
        stopped = session.run_stop()
        assert stopped["state"] == "stopped"
        assert stopped["summary"]["args"][2:] == ["--auto-ports", "--json", "--gds", "--stepped", "--duration", "5"]
        assert session.runs_list()["runs"][0]["summary"]["exit_code"] == 0
    finally:
        session.close()
    # One that does not come up says why
    script.write_text("import sys; print('no build here', file=sys.stderr); sys.exit(2)\n")
    session = Session(cwd=tmp_path, executable=[sys.executable, str(script)])
    try:
        started = time.monotonic()
        assert answer(session.run_start, "dep") == {"error": "the run did not come up: no build here"}
        assert time.monotonic() - started < 10
    finally:
        session.close()


def test_status_in_stepped_time_has_the_slowest_ticks(tmp_path, session):
    sim = FakeSim()
    sim.answer_status = lambda message: {"id": message["id"], "ok": True, "tick": 40, "endpoints": "", "stepped": True}
    sim.answers["tick_times"] = {"ticks": 40, "mean_us": 500.0, "max_us": 2500.0, "over_period": 1,
                                 "slowest.0.tick": 12, "slowest.0.us": 2500.0}
    make_deployment(tmp_path / "dep", f"tcp:{sim.port}")
    times = session.status("dep")["status"]["tick_times"]
    assert times == {"ticks": 40, "mean_ms": 0.5, "max_ms": 2.5, "over_period": 1, "slowest": [{"tick": 12, "ms": 2.5}]}


def test_the_server_refuses_mcp_2_with_the_reason():
    from fprime_wsts.mcp_server import check_mcp_version

    for supported in ("1.2.0", "1.30.0", None):
        check_mcp_version(supported)
    with pytest.raises(WstsError, match=r"needs mcp 1\.x, and mcp 2\.3\.0 is installed: 2\.0 renamed FastMCP.*<2"):
        check_mcp_version("2.3.0")
