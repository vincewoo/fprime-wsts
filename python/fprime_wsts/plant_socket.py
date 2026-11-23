"""Plants in a process of their own, stepped over a socket (docs/dynamics.md, phase 4).

The `socket` kind of plant: the conductor is a client of a plant server, which may be written
in any language. The protocol is line-delimited JSON, one request and one answer at a time:

    -> {"op": "start", "plant": "thermal", "t0": 0.0, "params": {...}, "inputs": ["heater"], "outputs": ["T"]}
    <- {"ok": true}
    -> {"op": "step", "t": 0.0, "dt": 0.05, "inputs": {"heater": 1.0}}
    <- {"ok": true, "outputs": {"T": 21.6}}
    -> {"op": "stop"}
    <- {"ok": true}

A failure is {"ok": false, "error": "..."}. Numbers are JSON numbers: a double written with its
shortest representation (as Python and most JSON writers do) reads back as the same double, so
a plant behind a socket gives the same bits as in the process.

`serve()` hosts any Python plant (a file that defines create(params)) this way: the reference
server, and a way to keep a plant out of the conductor's process:

    python -m fprime_wsts.plant_socket plants/thermal.py --address tcp:127.0.0.1:6000

In a manifest:

    dynamics:
      - name: thermal
        socket:
          address: tcp:127.0.0.1:0          # 0: a free port, chosen at run time
          command: [python, -m, fprime_wsts.plant_socket, plants/thermal.py, --address, "{address}"]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .dynamics import DynamicsError

#: Wall-clock seconds a plant server may take to start listening, and to answer a request
CONNECT_LIMIT = 30.0
ANSWER_LIMIT = 60.0


def parse_address(address: str) -> Tuple[str, object]:
    """("tcp", (host, port)) or ("unix", path)"""
    if address.startswith("unix:") and len(address) > 5:
        return "unix", address[5:]
    if address.startswith("tcp:"):
        rest = address[4:]
        host, _, port = rest.rpartition(":")
        try:
            number = int(port)
        except ValueError:
            raise ValueError(f"'{address}' has no port: tcp:<host>:<port> or tcp:<port>") from None
        if not 0 <= number <= 65535:
            raise ValueError(f"'{address}': a port is 0 to 65535")
        return "tcp", (host or "127.0.0.1", number)
    raise ValueError(f"'{address}' is not an address: tcp:<host>:<port>, tcp:<port> or unix:<path>")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class SocketPlant:
    """A plant served over the protocol above. With `command`, the server is started (in
    `cwd`, its output to `log`) and stopped with the plant."""

    def __init__(self, address: str, params: Optional[Dict[str, Any]] = None, name: str = "plant",
                 inputs: Sequence[str] = (), outputs: Sequence[str] = (), command: Optional[List[str]] = None,
                 cwd: Optional[Path] = None, log: Optional[Path] = None):
        self.name = name
        self.params = dict(params or {})
        self.inputs, self.outputs = list(inputs), list(outputs)
        self.command, self.cwd, self.log = command, cwd, log
        kind, where = parse_address(address)
        if kind == "tcp" and where[1] == 0:
            if not command:
                raise DynamicsError(f"plant '{name}': port 0 needs a command that starts the server on the port")
            where = (where[0], free_port())
        self.kind, self.where = kind, where
        self.address = f"tcp:{where[0]}:{where[1]}" if kind == "tcp" else f"unix:{where}"
        self.process: Optional[subprocess.Popen] = None
        self.sock: Optional[socket.socket] = None
        self._file = None

    # ---- The connection ----

    def _launch(self) -> None:
        port = str(self.where[1]) if self.kind == "tcp" else ""
        argv = [arg.replace("{address}", self.address).replace("{port}", port) for arg in self.command]
        if argv[0] in ("python", "python3"):
            argv[0] = sys.executable  # the conductor's: fprime_wsts is importable from it
        out = open(self.log, "w") if self.log else subprocess.DEVNULL
        try:
            self.process = subprocess.Popen(argv, cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.STDOUT)
        except OSError as error:
            raise DynamicsError(f"plant '{self.name}': cannot start its server ({' '.join(argv)}): {error}") from None
        finally:
            if self.log:
                out.close()

    def _connect(self) -> None:
        deadline = time.monotonic() + CONNECT_LIMIT
        while True:
            try:
                if self.kind == "tcp":
                    sock = socket.create_connection(self.where, timeout=1.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                else:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(self.where)
                break
            except OSError as error:
                if self.process is not None and self.process.poll() is not None:
                    raise DynamicsError(f"plant '{self.name}': its server exited ({self.process.returncode}) before "
                                        f"it listened on {self.address}" + self._see()) from None
                if time.monotonic() >= deadline:
                    raise DynamicsError(f"plant '{self.name}': nothing listens on {self.address} after "
                                        f"{CONNECT_LIMIT:g} s ({error})" + self._see()) from None
                time.sleep(0.05)
        sock.settimeout(ANSWER_LIMIT)
        self.sock = sock
        self._file = sock.makefile("r", encoding="utf-8", newline="\n")

    def _see(self) -> str:
        return f"; see {self.log}" if self.log else ""

    def request(self, op: str, **fields) -> dict:
        try:
            self.sock.sendall((json.dumps({"op": op, **fields}, separators=(",", ":")) + "\n").encode())
            line = self._file.readline()
        except OSError as error:
            raise DynamicsError(f"plant '{self.name}': {op}: {error}" + self._see()) from None
        if not line:
            raise DynamicsError(f"plant '{self.name}': the server at {self.address} closed the connection during "
                                f"'{op}'" + self._see())
        try:
            answer = json.loads(line)
        except ValueError:
            raise DynamicsError(f"plant '{self.name}': the server's answer to '{op}' is not JSON: {line[:200]!r}") \
                from None
        if not isinstance(answer, dict) or not answer.get("ok"):
            error = answer.get("error") if isinstance(answer, dict) else answer
            raise DynamicsError(f"plant '{self.name}': the server refused '{op}': {error}")
        return answer

    # ---- The plant ----

    def start(self, t0: float) -> None:
        if self.command:
            self._launch()
        self._connect()
        self.request("start", plant=self.name, t0=t0, params=self.params, inputs=self.inputs, outputs=self.outputs)

    def step(self, t: float, dt: float, inputs: Dict[str, Any]) -> Dict[str, Any]:
        answer = self.request("step", t=t, dt=dt, inputs=inputs)
        outputs = answer.get("outputs")
        if not isinstance(outputs, dict):
            raise DynamicsError(f"plant '{self.name}': the server's answer to 'step' has no outputs")
        return outputs

    def stop(self) -> None:
        try:
            if self.sock is not None:
                try:
                    self.request("stop")
                except DynamicsError:
                    pass
                self._file.close()
                self.sock.close()
                self.sock = None
        finally:
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                self.process = None


# ---------------------------------------------------------------------------
# The reference server
# ---------------------------------------------------------------------------

def handle(plant_file: Path, request: dict, state: dict) -> dict:
    """One request to a Python plant hosted by `serve`"""
    from .dynamics import load_python

    op = request.get("op")
    if op == "start":
        state["plant"] = load_python(plant_file, request.get("params") or {})
        start = getattr(state["plant"], "start", None)
        if callable(start):
            start(request.get("t0", 0.0))
        return {"ok": True}
    if op == "step":
        if "plant" not in state:
            return {"ok": False, "error": "step before start"}
        return {"ok": True, "outputs": state["plant"].step(request["t"], request["dt"], request.get("inputs") or {})}
    if op == "stop":
        stop = getattr(state.get("plant"), "stop", None)
        if callable(stop):
            stop()
        state["done"] = True
        return {"ok": True}
    return {"ok": False, "error": f"unknown op {op!r}"}


def serve(plant_file: Path, address: str, once: bool = True) -> None:
    """Host a Python plant on `address` (one client at a time; with `once`, until the first
    client stops it or goes)"""
    kind, where = parse_address(address)
    server = socket.socket(socket.AF_INET if kind == "tcp" else socket.AF_UNIX, socket.SOCK_STREAM)
    if kind == "tcp":
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    elif os.path.exists(where):
        os.unlink(where)
    server.bind(where)
    server.listen(1)
    print(f"[plant] {plant_file} on {address}", flush=True)
    try:
        while True:
            connection, _ = server.accept()
            if kind == "tcp":
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            state: dict = {}
            with connection, connection.makefile("r", encoding="utf-8") as lines:
                for line in lines:
                    try:
                        answer = handle(plant_file, json.loads(line), state)
                    except Exception as error:  # the plant's own failure goes back to the conductor
                        answer = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                    connection.sendall((json.dumps(answer, separators=(",", ":")) + "\n").encode())
                    if state.get("done"):
                        break
            if once:
                return
    finally:
        server.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fprime_wsts.plant_socket",
                                     description="Serve a Python plant (create(params)) over the plant socket protocol")
    parser.add_argument("plant", help="a Python file that defines create(params)")
    parser.add_argument("--address", default="tcp:127.0.0.1:6000", help="tcp:<host>:<port> or unix:<path>")
    parser.add_argument("--forever", action="store_true", help="serve one client after another")
    args = parser.parse_args(argv)
    serve(Path(args.plant).resolve(), args.address, once=not args.forever)
    return 0


if __name__ == "__main__":
    sys.exit(main())
