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
