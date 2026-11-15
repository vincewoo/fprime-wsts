"""The `fprime-wsts run` launcher: the command line it hands to fprime-gds, and where it
says the browser GUI can be reached. The GDS itself is never started here."""

import json
from pathlib import Path

from fprime_wsts.runner import Simulation, gds_command

DICTIONARY = Path("/d/MissionDictionary.json")
LOGS = Path("/l/gds")


def test_command_is_headless_by_default():
    command = gds_command("fprime-gds", DICTIONARY, 50000, LOGS)
    assert command[:2] == ["fprime-gds", "-n"]  # -n: no deployment of its own
    assert command[-2:] == ["-g", "none"]
    assert "-g" in command and "html" not in command
    assert "--gui-port" not in command and "--skip-browser-open" not in command
    assert command[command.index("--dictionary") + 1] == str(DICTIONARY)
    assert command[command.index("--ip-port") + 1] == "50000"
    assert command[command.index("--logs") + 1] == str(LOGS)


def test_command_serves_the_browser_gui_on_its_own_port():
    command = gds_command("fprime-gds", DICTIONARY, 50000, LOGS, gui_port=8080)
    assert "none" not in command
    assert command[command.index("-g") + 1] == "html"
    assert command[command.index("--gui-port") + 1] == "8080"
    # the flight link keeps its own port
    assert command[command.index("--ip-port") + 1] == "50000"
    # the browser is left to the caller: the run may be headless
    assert "--skip-browser-open" in command


def test_command_binds_the_gui_where_asked():
    command = gds_command("fprime-gds", DICTIONARY, 50000, LOGS, gui_port=5000, gui_addr="0.0.0.0")
    assert command[command.index("--gui-addr") + 1] == "0.0.0.0"


def make_deployment(directory: Path) -> Path:
    (directory / "wsts-report.json").write_text(json.dumps({"bridge": "tcp:50100"}))
    binary = directory / "build-artifacts" / "Linux" / "sim" / "bin" / "Mission"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    binary.chmod(0o755)
    return binary


def test_gui_url_needs_a_gui(tmp_path):
    make_deployment(tmp_path)
    assert Simulation(tmp_path, gds=True).gds_gui_url is None
    assert Simulation(tmp_path, gds=True, gds_gui=5000).gds_gui_url == "http://127.0.0.1:5000/"
    assert Simulation(tmp_path, gds=True, gds_gui=8080, gds_gui_addr="0.0.0.0").gds_gui_url == "http://0.0.0.0:8080/"


# ----------------------------------------------------------------------
# Free ports, and where a run says it can be reached
# ----------------------------------------------------------------------


def test_free_ports_replace_the_fixed_ones(tmp_path):
    from fprime_wsts.runner import auto_bridge

    make_deployment(tmp_path)
    fixed = Simulation(tmp_path, gds=True, gds_gui=5000)
    assert (fixed.bridge, fixed.gds_port, fixed.gds_gui, fixed.zmq_transport) == ("tcp:50100", 50000, 5000, None)
    free = Simulation(tmp_path, gds=True, gds_gui=5000, auto_ports=True)
    try:
        ports = [int(free.bridge.rsplit(":", 1)[1]), free.gds_port, free.gds_gui]
        assert free.bridge.startswith("tcp:") and len(set(ports)) == 3 and not {50100, 50000, 5000} & set(ports)
        first, second = free.zmq_transport
        assert first.startswith("ipc://") and first.endswith("/in") and second.endswith("/out")
        assert free.addresses() == {"bridge": free.bridge, "gds_port": free.gds_port,
                                    "gds_gui": f"http://127.0.0.1:{free.gds_gui}/", "zmq_transport": free.zmq_transport}
    finally:
        free.stop()
    assert not Path(first[len("ipc://"):]).parent.exists(), "the directory of the GDS sockets goes with the run"
    # An address the caller chose stays; other kinds of bridge have no port to choose
    assert Simulation(tmp_path, bridge="tcp:6000", auto_ports=True).bridge == "tcp:6000"
    assert auto_bridge("unix:/tmp/b.sock") == "unix:/tmp/b.sock" and auto_bridge("none") == "none"
    assert auto_bridge("tcp:0.0.0.0:50100").startswith("tcp:0.0.0.0:")
    assert Simulation(tmp_path, auto_ports=True).addresses().keys() == {"bridge"}, "no GDS, no GDS ports"


def test_the_gds_gets_sockets_of_its_own_when_asked():
    command = gds_command("fprime-gds", DICTIONARY, 50000, LOGS, zmq_transport=["ipc:///t/in", "ipc:///t/out"])
    assert command[command.index("--zmq-transport") + 1:][:2] == ["ipc:///t/in", "ipc:///t/out"]
    assert "--zmq-transport" not in gds_command("fprime-gds", DICTIONARY, 50000, LOGS)


def test_a_run_says_where_it_is_while_it_is_up(tmp_path):
    from fprime_wsts.bridge import bridge_address
    from fprime_wsts.runner import read_run_file, write_run_file

    make_deployment(tmp_path)
    assert read_run_file(tmp_path) is None and bridge_address(tmp_path) == "tcp:50100"
    (tmp_path / "wsts-logs").mkdir()
    write_run_file(tmp_path / "wsts-logs", {"bridge": "tcp:41000"})
    assert read_run_file(tmp_path)["bridge"] == "tcp:41000" and bridge_address(tmp_path) == "tcp:41000"
    # A file left by a run that was killed names a process that is gone
    content = json.loads((tmp_path / "wsts-logs" / "wsts-run.json").read_text())
    (tmp_path / "wsts-logs" / "wsts-run.json").write_text(json.dumps({**content, "pid": 2 ** 22 + 12345}))
    assert read_run_file(tmp_path) is None and bridge_address(tmp_path) == "tcp:50100"


def test_scenarios_and_recordings_of_a_system_are_per_element():
    import pytest

    from fprime_wsts.fpp import WstsError
    from fprime_wsts.runner import one_value, per_element

    names = ["cdh", "payload"]
    assert per_element("scenario", ["payload=over.jsonl"], names) == {"payload": "over.jsonl"}
    assert per_element("record", ["runs/r1.jsonl"], names, spread=True) == {
        "cdh": "runs/r1.cdh.jsonl", "payload": "runs/r1.payload.jsonl"}
    for order in (["r.jsonl", "cdh=c.jsonl"], ["cdh=c.jsonl", "r.jsonl"]):
        assert per_element("record", order, names, spread=True) == {"cdh": "c.jsonl", "payload": "r.payload.jsonl"}, \
            "an element's own file wins over the plain one"
    with pytest.raises(WstsError, match="say which element it is for"):
        per_element("scenario", ["over.jsonl"], names)
    with pytest.raises(WstsError, match="given twice"):
        per_element("scenario", ["cdh=a", "cdh=b"], names)
    assert one_value("scenario", None) is None and one_value("record", ["a.jsonl"]) == "a.jsonl"
    with pytest.raises(WstsError, match="one file"):
        one_value("record", ["a", "b"])


def test_elements_built_from_different_states_are_named_before_the_merge(tmp_path):
    from fprime_wsts.runner import dictionary_mismatch

    def dictionary(name, project, framework="f22ce8f"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"metadata": {"projectVersion": project, "frameworkVersion": framework}}))
        return path

    same = {"cdh": dictionary("cdh", "d366f51"), "payload": dictionary("payload", "d366f51")}
    assert dictionary_mismatch(same) is None
    message = dictionary_mismatch({**same, "payload": dictionary("payload", "d366f51-dirty")}, tmp_path / "Mission")
    assert "project version d366f51 in cdh, d366f51-dirty in payload" in message
    assert f"fprime-wsts build {tmp_path / 'Mission'} --fresh" in message
    assert "framework version" in dictionary_mismatch({**same, "payload": dictionary("payload", "d366f51", "v4.4.2")})
    assert dictionary_mismatch({"cdh": tmp_path / "missing.json", "payload": same["payload"]}) is None


def test_nothing_of_a_process_group_outlives_a_run(tmp_path):
    """The GDS starts children of its own. When it goes without taking them along (killed,
    or gone before the run stops), they must not be left running."""
    import subprocess
    import sys
    import time

    from fprime_wsts.runner import end_group

    # A leader with a child that ignores the interrupt and the request to end, as a busy GDS child does
    child = "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
    leader = subprocess.Popen(
        [sys.executable, "-c", f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(120)"],
        start_new_session=True)
    time.sleep(0.5)
    leader.kill()  # what stop() does to a leader that does not answer the interrupt
    leader.wait()
    from fprime_wsts.runner import group_members

    assert len(group_members(leader.pid)) == 1, "the child is still there: this is the leak"
    assert end_group(leader.pid, grace=0.3)
    assert group_members(leader.pid) == [], "only force ended it: it ignored the request"
    assert not end_group(leader.pid), "nothing left: nothing to do"


def test_stepped_time_is_chosen_per_run(tmp_path, capsys):
    from fprime_wsts.cli import main

    make_deployment(tmp_path)
    assert Simulation(tmp_path).stepped is None, "left to the manifest's time.mode"
    assert Simulation(tmp_path, stepped=True).stepped is True and not Simulation(tmp_path, stepped=True).hold
    with __import__("pytest").raises(SystemExit):
        main(["run", str(tmp_path), "--stepped", "--realtime"])


def test_a_system_is_stepped_in_lockstep_when_asked_or_when_an_element_asks(tmp_path, capsys):
    from fprime_wsts.cli import main
    from fprime_wsts.runner import SystemSimulation

    system = tmp_path / "S"
    for name in ("a", "b"):
        (system / name).mkdir(parents=True)
        (system / name / "wsts-report.json").write_text(json.dumps({"time": {"mode": "realtime"}}))
    (system / "wsts-system.json").write_text(json.dumps({"system": "S", "ground": "a", "links": [], "elements": [
        {"name": "a", "directory": "a", "bridge": "tcp:1"}, {"name": "b", "directory": "b", "bridge": "none"}]}))
    assert SystemSimulation(system).stepped is None, "real time, as the manifests say"
    assert SystemSimulation(system, stepped=True).stepped is True
    (system / "b" / "wsts-report.json").write_text(json.dumps({"time": {"mode": "stepped"}}))
    assert SystemSimulation(system).stepped is True, "one stepped element alone would run away from the others"
    assert SystemSimulation(system, stepped=False).stepped is False
    # Lockstep goes through every element's bridge
    assert main(["run", str(system), "--stepped"]) == 2
    assert "b has none (bridge: none)" in capsys.readouterr().err
    # A scenario for the whole system needs one time base
    assert main(["run", str(system), "--realtime", "--scenario", "all.jsonl"]) == 2
    assert "needs lockstep" in capsys.readouterr().err


def test_a_stepped_run_waits_for_the_tick_source_to_say_how_long_a_tick_is(tmp_path, monkeypatch, capsys):
    """On a loaded host the deployment's start-up code may start its timer, which declares
    the tick period, a while after the bridge answers: the run waits for it"""
    from fprime_wsts import runner
    from fprime_wsts.cli import main

    make_deployment(tmp_path)
    steps = []

    class Process:
        def poll(self):
            return None

    class Bridge:
        statuses = 0

        def status(self):
            Bridge.statuses += 1
            if not steps:  # the timer is started on the fifth look
                period = 50_000_000 if Bridge.statuses >= 5 else 0
                return {"tick": 0, "tick_period_ns": period, "paused": True}
            return {"tick": steps[0], "tick_period_ns": 50_000_000, "paused": True, "sim_seconds": steps[0] / 20}

        def request(self, op, **fields):
            assert op == "step"
            steps.append(fields["ticks"])

        def wait_quiescent(self, timeout):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def start(simulation):
        simulation.sim = Process()
        simulation.started = runner.time.monotonic()
        return simulation

    monkeypatch.setattr(Simulation, "start", start)
    monkeypatch.setattr(Simulation, "connect", lambda simulation: Bridge())
    monkeypatch.setattr(Simulation, "stop", lambda simulation: 0)
    assert main(["run", str(tmp_path), "--stepped", "--duration", "5", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert steps == [100] and summary["sim_seconds"] == 5.0
    # A tick source that never says fails with a message, after the wait
    steps.clear()
    Bridge.statuses = -10**9
    monkeypatch.setattr(runner, "TICK_SOURCE_WAIT", 0.1)
    assert main(["run", str(tmp_path), "--stepped", "--duration", "5"]) == 2
    assert "did not say how long a tick is within 0.1 s" in capsys.readouterr().err and steps == []
