"""Plants in a process of their own (fprime_wsts.plant_socket, docs/dynamics.md phase 4): the
socket protocol, the reference server, and a plant served over it stepped by the conductor"""

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

from fprime_wsts import plant_socket
from fprime_wsts.dynamics import DynamicsError, load_python, parse_specs
from fprime_wsts.lockstep import Conductor, LockstepError
from fprime_wsts.manifest import TOOLKIT_ROOT
from fprime_wsts.plant_socket import SocketPlant, parse_address
from test_dynamics import PlantElement, one_element_system

THERMAL = TOOLKIT_ROOT / "examples" / "mission-sim" / "plants" / "thermal.py"
SERVE = ["python", "-m", "fprime_wsts.plant_socket", str(THERMAL), "--address", "{address}"]


def test_addresses():
    assert parse_address("tcp:6000") == ("tcp", ("127.0.0.1", 6000))
    assert parse_address("tcp:example:7") == ("tcp", ("example", 7))
    assert parse_address("unix:/tmp/p.sock") == ("unix", "/tmp/p.sock")
    for bad, message in (("tcp:host", "no port"), ("tcp:70000", "0 to 65535"), ("udp:1", "not an address")):
        with pytest.raises(ValueError, match=message):
            parse_address(bad)


def heating(plant, steps=600):
    return [plant.step(i * 0.05, 0.05, {"heater": 1.0 if 40 <= i < 400 else 0.0})["T"] for i in range(steps)]


def test_a_served_plant_computes_what_it_does_in_the_process(tmp_path):
    served = SocketPlant("tcp:127.0.0.1:0", {"C": 5.0}, name="thermal", inputs=["heater"], outputs=["T"],
                         command=SERVE, log=tmp_path / "plant.log")
    served.start(0.0)
    try:
        over_the_socket = heating(served)
    finally:
        served.stop()
    assert over_the_socket == heating(load_python(THERMAL, {"C": 5.0})), "JSON numbers carry doubles exactly"
    assert served.process is None and "[plant]" in (tmp_path / "plant.log").read_text()


def test_a_plant_on_a_unix_socket(tmp_path):
    address = f"unix:{tmp_path / 'plant.sock'}"
    thread = threading.Thread(target=plant_socket.serve, args=(THERMAL, address), daemon=True)
    thread.start()
    plant = SocketPlant(address, {}, name="thermal", inputs=["heater"], outputs=["T"])
    plant.start(0.0)
    try:
        assert plant.step(0.0, 1.0, {"heater": 1.0})["T"] == pytest.approx(23.48, abs=0.01)  # 2 K/s, less what it loses
    finally:
        plant.stop()
    thread.join(5)
    assert not thread.is_alive(), "the server ends when its client stops it"


def test_the_conductor_steps_a_served_plant_as_it_does_one_in_its_process(tmp_path):
    written = {}
    for kind, how in (("socket", {"address": "tcp:127.0.0.1:0", "command": SERVE}), ("python", str(THERMAL))):
        payload = PlantElement("payload", 50_000_000)
        payload.pins["heaterPin"] = True
        [spec] = parse_specs([{"name": "thermal", kind: how, "inputs": {"heater": {"gpio": "heaterPin"}},
                               "outputs": {"T": {"model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True}}}],
                             "wsts.yaml")
        description = {**one_element_system(payload), "directory": str(tmp_path), "log_dir": str(tmp_path / kind)}
        with Conductor(description, request_timeout=5, dynamics=[spec]) as conductor:
            conductor.run(instants=40)
        written[kind] = payload.batches
    assert written["socket"] == written["python"] and len(written["socket"]) > 30
    assert "[plant]" in (tmp_path / "socket" / "dynamics-thermal.log").read_text()


def test_what_a_server_says_reaches_the_conductor(tmp_path):
    (tmp_path / "broken.py").write_text("class P:\n    def step(self, t, dt, inputs):\n        raise ValueError('diverged')\n"
                                        "def create(params):\n    return P()\n")
    plant = SocketPlant("tcp:127.0.0.1:0", {}, name="broken", outputs=["T"],
                        command=["python", "-m", "fprime_wsts.plant_socket", str(tmp_path / "broken.py"),
                                 "--address", "{address}"])
    plant.start(0.0)
    try:
        with pytest.raises(DynamicsError, match="the server refused 'step': ValueError: diverged"):
            plant.step(0.0, 0.05, {})
    finally:
        plant.stop()


def test_a_server_that_does_not_listen(tmp_path, monkeypatch):
    monkeypatch.setattr(plant_socket, "CONNECT_LIMIT", 0.3)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(DynamicsError, match=f"nothing listens on tcp:127.0.0.1:{port}"):
        SocketPlant(f"tcp:127.0.0.1:{port}", name="p").start(0.0)
    with pytest.raises(DynamicsError, match="its server exited \\(2\\) before it listened"):
        SocketPlant("tcp:127.0.0.1:0", name="p", command=["python", "-c", "import sys; sys.exit(2)"],
                    log=tmp_path / "p.log").start(0.0)


def test_a_server_that_answers_nonsense(monkeypatch):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        connection, _ = server.accept()
        with connection:
            connection.recv(4096)
            connection.sendall(b"{\"ok\": true}\n")  # start
            connection.recv(4096)
            connection.sendall(b"not json\n")

    threading.Thread(target=answer, daemon=True).start()
    plant = SocketPlant(f"tcp:127.0.0.1:{server.getsockname()[1]}", name="odd", outputs=["T"])
    plant.start(0.0)
    with pytest.raises(DynamicsError, match="answer to 'step' is not JSON"):
        plant.step(0.0, 0.05, {})
    plant.sock.close()
    plant.sock = None
    server.close()


@pytest.mark.parametrize("how, message", [
    ({"address": "tcp:127.0.0.1:0"}, "port 0 .* needs a 'command'"),
    ({"address": "udp:7"}, "not an address"),
    ({"address": "tcp:7", "command": "python serve.py"}, "socket.command is the plant server's command line, a list"),
    ({"adress": "tcp:7"}, "did you mean 'address'"),
    (7, "an address, or a mapping"),
])
def test_what_a_manifest_says_of_a_socket(how, message):
    with pytest.raises(DynamicsError, match=message):
        parse_specs([{"name": "p", "socket": how, "outputs": {"x": {"gpio": "pin"}}}], "wsts.yaml")


def test_a_socket_plant_in_a_manifest_runs_its_command_from_the_manifest(tmp_path):
    from fprime_wsts.dynamics import relocate

    [spec] = parse_specs([{"name": "p", "socket": {"address": "tcp:127.0.0.1:0", "command": ["python", "s.py"]},
                           "outputs": {"x": {"gpio": "pin"}}}], "wsts.yaml")
    assert (spec["kind"], spec["address"], spec["command"], spec["path"]) == ("socket", "tcp:127.0.0.1:0",
                                                                               ["python", "s.py"], None)
    [moved] = relocate([spec], lambda path: (tmp_path / "sim" / path).resolve(),
                       lambda path: str(Path(path).relative_to(tmp_path)))
    assert moved["cwd"] == "sim"
    [plain] = parse_specs([{"name": "p", "socket": "tcp:6000", "outputs": {"x": {"gpio": "pin"}}}], "wsts.yaml")
    assert relocate([plain], str, str)[0] == plain
