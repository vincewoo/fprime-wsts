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
