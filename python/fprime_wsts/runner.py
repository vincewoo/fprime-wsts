"""Launcher for a built simulated deployment: `fprime-wsts run`.

One command brings up the simulation and, if asked, a headless GDS, with logs in a known
place and a machine-readable summary at the end. That is what CI jobs and agents need:
no terminals to juggle and an exit code that means something.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from .fpp import WstsError


def find_artifacts(directory: Path):
    """Binary and dictionary of the deployment built in a generated simulation directory"""
    report = directory / "wsts-report.json"
    if not report.is_file():
        raise WstsError(f"{directory} is not a generated simulation deployment (no wsts-report.json)")
    binaries = [p for p in directory.glob("build-artifacts/*/*/bin/*") if p.is_file() and os.access(p, os.X_OK)]
    if not binaries:
        raise WstsError(f"no built deployment under {directory}/build-artifacts. Build it first:\n"
                       f"  cd {directory} && fprime-util generate && fprime-util build")
    binary = binaries[0]
    dictionaries = list(binary.parent.parent.glob("dict/*Dictionary.json"))
    return binary, (dictionaries[0] if dictionaries else None), json.loads(report.read_text())


def _wait_for_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def _link_established(port: int) -> bool:
    """True once some local TCP connection to `port` is established.

    Read from the kernel's connection table rather than by connecting: the GDS accepts a
    single client on this port, and a probe connection would take the deployment's place.
    """
    try:
        lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
    except OSError:
        return True  # cannot tell on this host: do not hold the run up
    for line in lines:
        fields = line.split()
        # fields: slot, local address, remote address, state (01 = established)
        if len(fields) > 3 and fields[3] == "01" and fields[2].upper() == f"0100007F:{port:04X}":
            return True
    return False


class Simulation:
    """A running simulated deployment, optionally with a headless GDS"""

    def __init__(self, directory: Path, gds: bool = False, gds_port: int = 50000, bridge: Optional[str] = None,
                 scenario: Optional[str] = None, scenario_mode: str = "stimulus", record: Optional[str] = None,
                 log_dir: Optional[Path] = None, files_dir: Optional[Path] = None):
        self.directory = Path(directory).resolve()
        self.binary, self.dictionary, self.report = find_artifacts(self.directory)
        self.bridge = bridge or self.report.get("bridge", "none")
        self.gds = gds
        self.gds_port = gds_port
        self.scenario = scenario
        self.scenario_mode = scenario_mode
        self.record = record
        self.log_dir = Path(log_dir).resolve() if log_dir else self.directory / "wsts-logs"
        # Relative scenario and recording paths given to the simulation resolve against this
        self.files_dir = Path(files_dir).resolve() if files_dir else self.log_dir
        self._processes: List[subprocess.Popen] = []
        self.sim: Optional[subprocess.Popen] = None
        self.started = 0.0

    def start(self) -> "Simulation":
        self.log_dir.mkdir(parents=True, exist_ok=True)
        arguments: List[str] = []
        if self.gds:
            executable = shutil.which("fprime-gds")
            if executable is None:
                raise WstsError("fprime-gds was not found on PATH (pip install fprime-gds)")
            if self.dictionary is None:
                raise WstsError(f"no dictionary found next to {self.binary}")
            # fprime-gds refuses to start over the logs of an earlier run
            shutil.rmtree(self.log_dir / "gds", ignore_errors=True)
            gds_log = open(self.log_dir / "gds.log", "w")
            command = [executable, "-n", "-g", "none", "--dictionary", str(self.dictionary),
                       "--ip-port", str(self.gds_port), "--logs", str(self.log_dir / "gds"), "--log-directly"]
            self._processes.append(subprocess.Popen(command, stdout=gds_log, stderr=subprocess.STDOUT,
                                                    cwd=self.log_dir, start_new_session=True))
            arguments = ["-a", "127.0.0.1", "-p", str(self.gds_port)]
        env = dict(os.environ)
        env["WSTS_BRIDGE"] = self.bridge
        env.setdefault("WSTS_DIR", str(self.files_dir))
        if self.scenario:
            env["WSTS_SCENARIO"] = str(Path(self.scenario).resolve())
            env["WSTS_SCENARIO_MODE"] = self.scenario_mode
        if self.record:
            env["WSTS_RECORD"] = str(Path(self.record).resolve())
        sim_log = open(self.log_dir / "sim.log", "w")
        self.sim = subprocess.Popen([str(self.binary), *arguments], stdout=sim_log, stderr=subprocess.STDOUT,
                                    cwd=self.log_dir, env=env)
        self._processes.append(self.sim)
        self.started = time.monotonic()
        if self.bridge.startswith("tcp:"):
            port = int(self.bridge.rsplit(":", 1)[-1])
            if port and not _wait_for_port(port, 15):
                code = self.sim.poll()
                self.stop()
                raise WstsError(f"the simulation did not open its bridge on {self.bridge}"
                               + (f" (it exited with code {code})" if code is not None else "")
                               + f"; see {self.log_dir / 'sim.log'}")
        if self.gds:
            # The deployment's TCP client retries until the GDS is listening
            deadline = time.monotonic() + 30
            gds_process = self._processes[0]
            while not _link_established(self.gds_port):
                if gds_process.poll() is not None:
                    self.stop()
                    raise WstsError(f"fprime-gds exited during start-up; see {self.log_dir / 'gds.log'}")
                if self.sim.poll() is not None or time.monotonic() >= deadline:
                    code = self.sim.poll()
                    self.stop()
                    raise WstsError(f"the deployment did not connect to the GDS on port {self.gds_port}"
                                   + (f" (it exited with code {code})" if code is not None else "")
                                   + f"; see {self.log_dir}")
                time.sleep(0.2)
        return self

    def connect(self):
        """Bridge client for this simulation"""
        from .bridge import WstsBridge

        return WstsBridge(self.bridge)

    def stop(self) -> Optional[int]:
        code = None
        for process in reversed(self._processes):
            if process.poll() is None:
                try:
                    if process is self.sim:
                        process.send_signal(signal.SIGINT)
                    else:
                        os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process is self.sim:
                code = process.returncode
        self._processes.clear()
        return code

    def __enter__(self) -> "Simulation":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def run_cli(args) -> int:
    simulation = Simulation(Path(args.directory), gds=args.gds, gds_port=args.gds_port, bridge=args.bridge,
                            scenario=args.scenario, scenario_mode=args.scenario_mode, record=args.record,
                            log_dir=Path(args.log_dir) if args.log_dir else None,
                            files_dir=Path(args.files_dir) if args.files_dir else None)
    simulation.start()
    if not args.json:
        print(f"simulation running: {simulation.binary.name} (bridge {simulation.bridge}, logs {simulation.log_dir})")
        if args.gds:
            print(f"GDS listening for the deployment on port {args.gds_port}")
    status = {}
    crashed = False
    interrupted = False
    try:
        deadline = time.monotonic() + args.duration if args.duration else None
        while simulation.sim.poll() is None:
            if deadline and time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        crashed = simulation.sim.poll() is not None
    except KeyboardInterrupt:
        interrupted = True
    if not crashed and simulation.bridge != "none":
        try:
            with simulation.connect() as bridge:
                status = bridge.status()
        except Exception:
            status = {}
    code = simulation.stop()
    summary = {
        "deployment": simulation.binary.name,
        "exit_code": code,
        "exited_early": crashed,
        "interrupted": interrupted,
        "seconds": round(time.monotonic() - simulation.started, 2),
        "logs": str(simulation.log_dir),
        "final_status": status,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"simulation stopped after {summary['seconds']} s, exit code {code}")
    if crashed:
        print(f"the simulation exited on its own; see {simulation.log_dir / 'sim.log'}", file=sys.stderr)
        return 1
    return 0 if code in (0, None) else 1
