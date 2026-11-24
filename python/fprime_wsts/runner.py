"""Launcher for a built simulated deployment: `fprime-wsts run`.

One command brings up the simulation and, if asked, a headless GDS, with logs in a known
place and a machine-readable summary at the end. That is what CI jobs and agents need:
no terminals to juggle and an exit code that means something.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .fpp import WstsError


def find_artifacts(directory: Path):
    """Binary and dictionary of the deployment built in a generated simulation directory"""
    report = directory / "wsts-report.json"
    if not report.is_file():
        raise WstsError(f"{directory} is not a generated simulation deployment (no wsts-report.json)")
    binaries = [p for p in directory.glob("build-artifacts/*/*/bin/*") if p.is_file() and os.access(p, os.X_OK)]
    if not binaries:
        raise WstsError(f"no built deployment under {directory}/build-artifacts. Build it first:\n"
                       f"  fprime-wsts build {directory}")
    binary = binaries[0]
    dictionaries = list(binary.parent.parent.glob("dict/*Dictionary.json"))
    return binary, (dictionaries[0] if dictionaries else None), json.loads(report.read_text())


# ----------------------------------------------------------------------
# F Prime asserts in the deployment's log
# ----------------------------------------------------------------------

# With FW_ASSERT_LEVEL FW_FILEID_ASSERT a failed assert prints the hash of the file's path
# (`Assert: 0x1a2b3c4d:118`); otherwise it prints the path itself (`Assert: "Drv/X/X.cpp:118"`)
ASSERT_BY_ID = re.compile(r"Assert: 0x([0-9a-fA-F]{1,8}):(\d+)")
ASSERT_BY_NAME = re.compile(r'Assert: "([^"\n]+):(\d+)"')
SUBSTITUTE_HELP = "docs/surrogate-guide.md, 'Surrogates'"


class StartError(WstsError):
    """The deployment did not come up. `assertion` is the F Prime assert it stopped on, if
    its log ends with one (see `explain_assert`); `code` is its exit code, if it exited."""

    def __init__(self, message: str, code: Optional[int] = None, assertion: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.assertion = assertion


def find_assert(log: str) -> Optional[dict]:
    """The last F Prime assert in a deployment's log: {"line", and "id" or "file"}"""
    found = None
    for pattern, key in ((ASSERT_BY_ID, "id"), (ASSERT_BY_NAME, "file")):
        for match in pattern.finditer(log):
            if found is None or match.start() > found[0]:
                value = f"0x{int(match.group(1), 16):08x}" if key == "id" else match.group(1)
                found = (match.start(), {key: value, "line": int(match.group(2))})
    return found[1] if found else None


def read_file_ids(directory: Path) -> Dict[str, List[str]]:
    """The build's own table of assert file IDs: {"0x1a2b3c4d": [source paths]}. F Prime
    writes `<path>: 0x<id>` to hashes.txt in the build cache for every source it compiles
    (`fprime-util hash-to-file` reads the same file)."""
    ids: Dict[str, List[str]] = {}
    for table in sorted(Path(directory).glob("build-fprime-*/hashes.txt")) + sorted(Path(directory).glob("build-artifacts/hashes.txt")):
        try:
            lines = table.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            path, separator, value = line.rpartition(": 0x")
            if not separator or not path:
                continue
            try:
                key = f"0x{int(value.strip(), 16):08x}"
            except ValueError:
                continue
            if path not in ids.setdefault(key, []):
                ids[key].append(path)
    return ids


def topology_instances(directory: Path) -> List[dict]:
    """Instances of the built topology and the header of each one's class, read from the
    autocoded `<Name>TopologyAc.hpp`: [{"instance", "type", "header"}]"""
    found: List[dict] = []
    for header in sorted(Path(directory).glob("build-fprime-*/*/*/*TopologyAc.hpp")):
        try:
            text = header.read_text(errors="replace")
        except OSError:
            continue
        includes = re.findall(r'#include\s+[<"]([^">]+\.h(?:pp)?)[">]', text)
        scope: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            opened = re.fullmatch(r"namespace\s+([\w:]+)\s*\{", line)
            if opened:
                scope.append(opened.group(1))
            elif line.startswith("}") and scope:
                scope.pop()
            declared = re.fullmatch(r"extern\s+([\w:]+)\s+(\w+)\s*;", line)
            if declared:
                cls = declared.group(1).rsplit("::", 1)[-1]
                found.append({
                    "instance": ".".join([part for name in scope for part in name.split("::")] + [declared.group(2)]),
                    "type": declared.group(1),
                    "header": next((i for i in includes if Path(i).stem == cls), None),
                })
        if found:
            break
    return found


def explain_assert(directory: Path, log: str, report: Optional[dict] = None) -> Optional[dict]:
    """The F Prime assert a deployment's log ends with, resolved with what its build left
    behind: {"file", "line", "id" (when the log has a file ID), "instances", "message"}.

    `instances` are the topology's instances of the component the file belongs to. One that
    the simulation kept as it is (not substituted) is the likely cause of an assert at
    start-up: flight driver code that touches hardware the host does not have.
    """
    found = find_assert(log)
    if found is None:
        return None
    directory = Path(directory)
    result = {"file": found.get("file"), "line": found["line"]}
    if "id" in found:
        result["id"] = found["id"]
        paths = read_file_ids(directory).get(found["id"], [])
        result["file"] = paths[0] if paths else None
        if len(paths) > 1:
            result["candidates"] = paths  # different paths with one hash
    where = f"{result['file']}:{result['line']}" if result["file"] else f"file ID {found['id']}, line {result['line']}"
    message = f"F Prime assert at {where}"
    if result["file"] is None:
        message += (f" (the ID is not in the build's hashes.txt under {directory}: was the deployment built "
                    "from this directory?)")
    instances: List[dict] = []
    if result["file"]:
        report = report or {}
        substituted = {sub["instance"] for sub in report.get("substitutions", [])}
        added = {item["instance"] for item in report.get("simulators", {}).get("instances", [])}
        file_dir = Path(result["file"]).parent.as_posix()
        for item in topology_instances(directory):
            header_dir = Path(item["header"]).parent.as_posix() if item["header"] else ""
            if not header_dir or not (file_dir == header_dir or file_dir.endswith("/" + header_dir)):
                continue
            base = item["instance"].rsplit(".", 1)[-1]
            kept = item["instance"] not in substituted and item["instance"] not in added and not base.startswith("wsts")
            instances.append({"instance": item["instance"], "type": item["type"], "kept": kept})
        kept = [i for i in instances if i["kept"]]
        if kept:
            names = ", ".join(i["instance"] for i in kept)
            message += (
                f". That file belongs to {kept[0]['type']}, the component of {names}, which the simulation keeps as "
                "it is. If the component reaches hardware there (a memory map, a device file, an interrupt), "
                f"replace the instance: add\n  substitute:\n    - instance: {kept[0]['instance']}\n"
                f"      with: <surrogate component>\nto the manifest ({SUBSTITUTE_HELP})"
            )
        elif instances:
            message += f". That file belongs to {instances[0]['type']}, the component of " \
                       + ", ".join(i["instance"] for i in instances)
    result["instances"] = instances
    result["message"] = message
    return result


def with_traffic(bridge, status: dict) -> dict:
    """The final status with the bus traffic of each endpoint (`bus_traffic`), when the
    deployment answers `bus_stats` (one built with an older toolkit does not), and in stepped
    time how long its ticks took in host time (`tick_times`)"""
    try:
        status = {**status, "bus_traffic": bridge.bus_stats()}
    except Exception:
        pass
    if status.get("stepped"):
        try:
            status = {**status, "tick_times": bridge.tick_times()}
        except Exception:
            pass
    return status


def describe_tick_times(times: Optional[dict], period_ns: int = 0) -> Optional[str]:
    """One line on the slowest ticks of a stepped run, for the text summary; None when none
    were timed"""
    if not times or not times.get("ticks"):
        return None
    slowest = ", ".join(f"tick {s['tick']} {s['ms']} ms" for s in times.get("slowest", []))
    line = f"slowest ticks in host time: {slowest} (mean {times['mean_ms']} ms over {times['ticks']} ticks)"
    if times.get("over_period"):
        period = f" of {period_ns / 1e6:g} ms" if period_ns else ""
        line += (f"; {times['over_period']} took longer than the tick period{period} and could overrun on the "
                 "target (a hint: the host is not the target)")
    return line


def _wait_for_port(port: int, timeout: float, process: Optional[subprocess.Popen] = None) -> bool:
    """Wait until something listens on a local port. Gives up early when `process`, which
    was to open it, has exited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            if process is not None and process.poll() is not None:
                return False
            time.sleep(0.2)
    return False


def _wait_for_path(path: str, timeout: float, process: Optional[subprocess.Popen] = None) -> bool:
    """Wait until a Unix socket exists; gives up early when `process`, which was to make it, has exited"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def _is_listening(port: int) -> Optional[bool]:
    """Whether a local process listens on a TCP port, read from the kernel's table and not by
    connecting: the listener takes a single client, and a probe would take the GDS's place.
    None when this host cannot tell."""
    try:
        lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
    except OSError:
        lsof = shutil.which("lsof")
        if lsof is None:
            return None
        found = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
        return bool(found.stdout.strip())
    for line in lines:
        fields = line.split()
        # fields: slot, local address, remote address, state (0A = listening)
        if len(fields) > 3 and fields[3] == "0A" and fields[1].upper().endswith(f":{port:04X}"):
            return True
    return False


def _wait_until_listening(port: int, timeout: float, process: Optional[subprocess.Popen] = None) -> bool:
    """Wait until something listens on a local port. Gives up early when `process`, which was
    to open it, has exited. On a host that cannot tell, waits a moment and goes on: the GDS
    retries its connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listening = _is_listening(port)
        if listening is None:
            time.sleep(1)
            return True
        if listening:
            return True
        if process is not None and process.poll() is not None:
            return False
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


RUN_FILE = "wsts-run.json"  # in the log directory while a run is up: where its bridge and GDS are


def free_port() -> int:
    """A TCP port nothing listens on at the moment, from the range the system hands out"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def auto_bridge(bridge: str) -> str:
    """A TCP bridge address on a free port of the same host; other kinds stay as they are"""
    if not bridge.startswith("tcp:"):
        return bridge
    host = bridge[4:].rpartition(":")[0]
    return f"tcp:{host + ':' if host else ''}{free_port()}"


def write_run_file(log_dir: Path, content: dict) -> None:
    (log_dir / RUN_FILE).write_text(json.dumps({"pid": os.getpid(), **content}, indent=2) + "\n")


def read_run_file(directory: Path) -> Optional[dict]:
    """What a run of a generated directory left in its log directory, while it is up: the
    addresses it really uses. None when nothing is running there (the file is removed when
    a run stops, and one left by a run that was killed names a process that is gone)."""
    for path in (Path(directory) / "wsts-logs" / RUN_FILE, Path(directory) / RUN_FILE):
        try:
            content = json.loads(path.read_text())
            os.kill(int(content["pid"]), 0)
        except (OSError, ValueError, KeyError):
            continue
        return content
    return None


def group_members(group: int) -> Optional[List[int]]:
    """The running processes of a process group (not those that have ended and wait to be
    collected), or None where the kernel's process table cannot be read"""
    found: List[int] = []
    try:
        entries = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return None
    for entry in entries:
        try:
            # pid (name) state ppid pgrp ...: the name may hold spaces and parentheses
            fields = Path(f"/proc/{entry}/stat").read_text().rpartition(")")[2].split()
        except OSError:
            continue
        if len(fields) > 2 and fields[0] != "Z" and fields[2] == str(group):
            found.append(int(entry))
    return found


def end_group(group: int, grace: float = 2.0) -> bool:
    """Make sure no process of a process group is left: ask (SIGTERM), wait a little, then
    force (SIGKILL). True if there was anything left to end."""
    def alive() -> bool:
        try:
            os.killpg(group, 0)
        except (ProcessLookupError, PermissionError):
            return False
        # A process that has ended and has not been collected yet still answers: look closer
        members = group_members(group)
        return True if members is None else bool(members)

    if not alive():
        return False
    for sign, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(group, sign)
        except (ProcessLookupError, PermissionError):
            return True
        deadline = time.monotonic() + wait
        while alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not alive():
            break
    return True


def gds_command(executable: str, dictionary: Path, gds_port: int, log_dir: Path,
                gui_port: Optional[int] = None, gui_addr: str = "127.0.0.1",
                zmq_transport: Optional[List[str]] = None, client: bool = False,
                extra: Optional[List[str]] = None) -> List[str]:
    """The `fprime-gds` command line: headless, or with its browser GUI on `gui_port`.

    `-n` keeps fprime-gds from running a deployment of its own: the simulation is started
    separately. With a GUI the HTML app is served on `gui_addr:gui_port` (its own port, not
    `gds_port`, which carries the flight link); the browser is left unopened because the run
    may be on a headless host, so the caller prints the URL instead.

    The GDS listens on `gds_port` and the deployment connects to it, as a `Drv.TcpClient` does.
    With `client` it is the other way round, for a deployment whose ground link is a
    `Drv.TcpServer`: the deployment listens on `gds_port` and the GDS connects to it.

    `extra` is added at the end: what the manifest says under `run.gds_args` (a deployment that does not
    frame its ground link as the GDS does by default says which framing it uses).
    """
    command = [executable, "-n", "--dictionary", str(dictionary), "--ip-port", str(gds_port),
               "--logs", str(log_dir), "--log-directly"]
    if client:
        command += ["--ip-client", "--ip-address", "127.0.0.1"]
    if zmq_transport:
        # The GDS's own processes meet on two sockets that are otherwise the same for every
        # GDS of a user: a second GDS on the machine needs a pair of its own
        command += ["--zmq-transport", *zmq_transport]
    if gui_port is None:
        command += ["-g", "none"]
    else:
        command += ["-g", "html", "--gui-addr", gui_addr, "--gui-port", str(gui_port),
                    "--skip-browser-open"]
    return command + list(extra or [])


def sandbox_directory(report: dict, directory: Path) -> Optional[Path]:
    """The sandbox of the files of flight software a run exports as WSTS_FILES_DIR: `files.sandbox`
    of the manifest, carried by the report as written. A relative one is relative to the generated
    directory (`directory`, the one that holds wsts-report.json; an element's own directory in a
    system), `~` is the user's home. None without the key: the files directory of the run
    (WSTS_DIR) is the sandbox.
    """
    sandbox = report.get("files", {}).get("sandbox")
    if not sandbox:
        return None
    expanded = Path(os.path.expanduser(sandbox))
    return (expanded if expanded.is_absolute() else Path(directory) / expanded).resolve()


class Simulation:
    """A running simulated deployment, optionally with a GDS (headless, or with its browser GUI)"""

    def __init__(self, directory: Path, gds: bool = False, gds_port: int = 50000,
                 gds_gui: Optional[int] = None, gds_gui_addr: str = "127.0.0.1",
                 bridge: Optional[str] = None,
                 scenario: Optional[str] = None, scenario_mode: str = "stimulus", record: Optional[str] = None,
                 log_dir: Optional[Path] = None, files_dir: Optional[Path] = None,
                 dictionary: Optional[Path] = None, env: Optional[Dict[str, str]] = None,
                 auto_ports: bool = False, zmq_transport: Optional[List[str]] = None,
                 stepped: Optional[bool] = None, journal: Optional[bool] = None, restore=None):
        self.directory = Path(directory).resolve()
        self.binary, self.dictionary, self.report = find_artifacts(self.directory)
        # Snapshots (docs/snapshots.md): a journal of the run's inputs, and a snapshot to start
        # from (a snapshots.Snapshot), whose run is restored before anything else happens.
        # A restored run keeps a journal of its own, and is stepped.
        self.restore = restore
        if journal is None:
            journal = bool(self.report.get("time", {}).get("journal"))
        self.journal = journal or restore is not None
        if restore is not None:
            stepped = True
        self.restored_status: Optional[dict] = None
        if dictionary is not None:
            self.dictionary = Path(dictionary)  # for example the merged dictionary of a system
        self.extra_env = dict(env or {})
        # Stepped time for this run; None leaves it to the manifest (time.mode)
        self.stepped = stepped
        # Start paused and stay so: the caller decides how far time goes (a stepped run of
        # a set duration is one step)
        self.hold = False
        self.bridge = bridge or self.report.get("bridge", "none")
        self.gds = gds
        self.gds_port = gds_port
        self.gds_gui = gds_gui  # browser GUI port; None keeps the GDS headless
        self.zmq_transport = zmq_transport
        self._zmq_dir: Optional[str] = None
        self.owns_run_file = True  # an element of a system leaves the run file to the system
        if auto_ports:
            # Free ports in place of the fixed ones, so that runs on one machine do not meet.
            # A bridge address the caller gave is used as given.
            if bridge is None:
                self.bridge = auto_bridge(self.bridge)
            if gds:
                self.gds_port = free_port()
                self.gds_gui = free_port() if gds_gui else None
                if self.zmq_transport is None:
                    self._zmq_dir = tempfile.mkdtemp(prefix="wsts-gds-")
                    self.zmq_transport = [f"ipc://{self._zmq_dir}/in", f"ipc://{self._zmq_dir}/out"]
        self.gds_gui_addr = gds_gui_addr
        # The deployment's ground link listens (Drv.TcpServer): the GDS is the client of it
        self.gds_is_client = "ground_link" in self.report
        self.scenario = scenario
        self.scenario_mode = scenario_mode
        self.record = record
        self.log_dir = Path(log_dir).resolve() if log_dir else self.directory / "wsts-logs"
        # Relative scenario and recording paths given to the simulation resolve against this
        self.files_dir = Path(files_dir).resolve() if files_dir else self.log_dir
        self._processes: List[subprocess.Popen] = []
        self.sim: Optional[subprocess.Popen] = None
        self.started = 0.0

    def _start_gds(self, command: List[str], log) -> None:
        self._gds_process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                             cwd=self.log_dir, start_new_session=True)
        self._processes.append(self._gds_process)

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
            command = gds_command(executable, self.dictionary, self.gds_port, self.log_dir / "gds",
                                  self.gds_gui, self.gds_gui_addr, self.zmq_transport, client=self.gds_is_client,
                                  extra=self.report.get("run", {}).get("gds_args"))
            arguments = ["-a", "127.0.0.1", "-p", str(self.gds_port)]
            if not self.gds_is_client:
                self._start_gds(command, gds_log)
        env = dict(os.environ)
        # Defaults from the manifest (`run.env`), then what this run was given
        defaults = self.report.get("run", {})
        for name, value in defaults.get("env", {}).items():
            if name in defaults.get("env_paths", []):
                value = str((self.directory / value).resolve())
            env[name] = value
        env.update(self.extra_env)
        env["WSTS_BRIDGE"] = self.bridge
        if self.stepped is not None:
            env["WSTS_STEPPED"] = "1" if self.stepped else "0"
        # Python device models of the manifest (`run.models`): the deployment starts paused,
        # and is resumed once they are on their endpoints
        models = [str((self.directory / file).resolve()) for file in defaults.get("models", [])]
        if models:
            if not self.bridge.startswith(("tcp:", "unix:")):
                raise WstsError(f"run.models needs the bridge, and this run has none (bridge {self.bridge})")
            env["WSTS_START_PAUSED"] = "1"
        if self.hold:
            env["WSTS_START_PAUSED"] = "1"
        env.setdefault("WSTS_DIR", str(self.files_dir))
        # The sandbox of the files of flight software (`files.sandbox` in the manifest): where
        # target paths and relative names of flight software land on the host
        sandbox = sandbox_directory(self.report, self.directory)
        if self.journal:
            from . import snapshots

            stepped = self.stepped if self.stepped is not None else self.report.get("time", {}).get("mode") == "stepped"
            if not stepped:
                raise WstsError("a journal replays only in stepped time: run with --stepped (or time.mode: stepped "
                                "in the manifest)")
            if self.restore is not None:
                snapshots.prepare_restore(self, self.restore)
                env["WSTS_START_PAUSED"] = "1"  # held until the journal replays
            # The run's own sandbox, from the files it starts with: a snapshot's, or the manifest's
            sandbox = snapshots.prepare_journal(self, sandbox)
        if sandbox is not None:
            env["WSTS_FILES_DIR"] = str(sandbox)
            sandbox.mkdir(parents=True, exist_ok=True)
        if self.scenario:
            env["WSTS_SCENARIO"] = str(Path(self.scenario).resolve())
            env["WSTS_SCENARIO_MODE"] = self.scenario_mode
        if self.record:
            env["WSTS_RECORD"] = str(Path(self.record).resolve())
        if self.journal:
            snapshots.write_journal_meta(self, env.get("WSTS_START_PAUSED") == "1", self.extra_env)
        sim_log = open(self.log_dir / "sim.log", "w")
        self.sim = subprocess.Popen([str(self.binary), *arguments], stdout=sim_log, stderr=subprocess.STDOUT,
                                    cwd=self.log_dir, env=env)
        self._processes.append(self.sim)
        self.started = time.monotonic()
        if self.bridge.startswith("tcp:"):
            port = int(self.bridge.rsplit(":", 1)[-1])
            if port and not _wait_for_port(port, 15, self.sim):
                code = self.sim.poll()
                self.stop()
                raise self._start_error(f"the simulation did not open its bridge on {self.bridge}", code,
                                        self.log_dir / "sim.log")
        if models:
            self._start_models(models)
        if self.gds and self.gds_is_client:
            # The deployment listens, and the GDS connects: the GDS retries, but the deployment
            # takes a single client, so it starts once the port is open
            if not _wait_until_listening(self.gds_port, 30, self.sim):
                code = self.sim.poll()
                self.stop()
                raise self._start_error(f"the deployment did not listen for the GDS on port {self.gds_port}",
                                        code, self.log_dir)
            self._start_gds(command, gds_log)
        if self.gds:
            # The deployment's TCP client retries until the GDS is listening
            deadline = time.monotonic() + 30
            gds_process = self._gds_process
            while not _link_established(self.gds_port):
                if gds_process.poll() is not None:
                    self.stop()
                    raise WstsError(f"fprime-gds exited during start-up; see {self.log_dir / 'gds.log'}")
                if self.sim.poll() is not None or time.monotonic() >= deadline:
                    code = self.sim.poll()
                    self.stop()
                    raise self._start_error(f"the deployment did not connect to the GDS on port {self.gds_port}",
                                            code, self.log_dir)
                time.sleep(0.2)
            if self.gds_gui:
                # Best effort: the URL only means something once the HTML app answers, and
                # that app is a child of the GDS, so a failure shows in the GDS log
                _wait_for_port(self.gds_gui, 15)
        if self.restore is not None:
            from . import snapshots

            try:
                snapshots.start_replay(self, self.restore)
                # An element of a system in lockstep replays under the system's conductor
                if self.extra_env.get("WSTS_LOCKSTEP") != "1":
                    self.restored_status = snapshots.complete_restore(self, self.restore)
            except BaseException:
                self.stop()
                raise
        if self.owns_run_file:
            write_run_file(self.log_dir, self.addresses())
        return self

    def addresses(self) -> dict:
        """Where this run can be reached: its bridge, and its GDS when it has one"""
        found: dict = {"bridge": self.bridge, "directory": str(self.directory)}
        if getattr(self, "conducted", False):
            found["conducted"] = True  # in lockstep, stepped with its plants by a conductor
        if self.gds:
            found.update(gds_port=self.gds_port, gds_gui=self.gds_gui_url, zmq_transport=self.zmq_transport,
                         dictionary=str(self.dictionary) if self.dictionary else None)
        return found

    def _start_models(self, files: List[str], timeout: float = 20.0) -> None:
        """Start the Python device models, wait until they are on their endpoints, then let
        the deployment run"""
        from .bridge import BridgeError, WstsBridge
        from .models import READY

        if self.bridge.startswith("unix:") and not _wait_for_path(self.bridge[5:], 15, self.sim):
            code = self.sim.poll()
            self.stop()
            raise self._start_error(f"the simulation did not open its bridge on {self.bridge}", code,
                                    self.log_dir / "sim.log")
        log_path = self.log_dir / "models.log"
        log = open(log_path, "w")
        process = subprocess.Popen([sys.executable, "-u", "-m", "fprime_wsts.models", "--bridge", self.bridge, *files],
                                   stdout=log, stderr=subprocess.STDOUT, cwd=self.log_dir, start_new_session=True)
        self._processes.insert(0, process)  # stopped after the deployment, which it serves
        deadline = time.monotonic() + timeout
        while READY not in log_path.read_text(errors="replace"):
            if process.poll() is not None or time.monotonic() >= deadline:
                why = "did not attach in time" if process.poll() is None else f"stopped (exit code {process.returncode})"
                said = [line for line in log_path.read_text(errors="replace").splitlines() if line.strip()]
                self.stop()
                raise WstsError(f"the Python device models {why}" + (f": {said[-1]}" if said else "")
                                + f"; see {log_path}")
            time.sleep(0.05)
        if self.hold:
            return
        try:
            with WstsBridge(self.bridge) as bridge:
                bridge.resume()
        except BridgeError as error:
            self.stop()
            raise WstsError(f"could not resume the simulation after attaching its Python models: {error}") from None

    def assertion(self) -> Optional[dict]:
        """The F Prime assert the deployment's log ends with, if any (see `explain_assert`)"""
        try:
            log = (self.log_dir / "sim.log").read_text(errors="replace")
        except OSError:
            return None
        return explain_assert(self.directory, log, self.report)

    def _start_error(self, what: str, code: Optional[int], see: Path) -> StartError:
        assertion = self.assertion() if code is not None else None
        message = what + (f" (it exited with code {code})" if code is not None else "")
        if assertion:
            message += f": {assertion['message']}"
        return StartError(f"{message}; see {see}", code, assertion)

    def connect(self):
        """Bridge client for this simulation"""
        from .bridge import WstsBridge

        return WstsBridge(self.bridge)

    @property
    def gds_gui_url(self) -> Optional[str]:
        """Where the browser GUI is served, when this simulation runs one"""
        return f"http://{self.gds_gui_addr}:{self.gds_gui}/" if self.gds_gui else None

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
            else:
                # The GDS and the model server lead a process group of their own. Whether
                # the leader went by itself, on the interrupt or by force, nothing of its
                # group may outlive the run: a GDS child left behind keeps a processor busy.
                end_group(process.pid)
        self._processes.clear()
        if self.owns_run_file:
            (self.log_dir / RUN_FILE).unlink(missing_ok=True)
        if self._zmq_dir:
            shutil.rmtree(self._zmq_dir, ignore_errors=True)
            self._zmq_dir = None
        return code

    def __enter__(self) -> "Simulation":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def parse_env(entries, elements: Optional[List[str]] = None) -> Dict[Optional[str], Dict[str, str]]:
    """`--env NAME=VALUE` options. For a system, `<element>.NAME=VALUE` sets a variable for
    one element only. Returns {element or None: {name: value}}."""
    found: Dict[Optional[str], Dict[str, str]] = {}
    for entry in entries or []:
        name, separator, value = str(entry).partition("=")
        element = None
        if "." in name:
            element, name = name.split(".", 1)
            if elements is None or element not in elements:
                raise WstsError(
                    f"--env {entry}: '{element}' is not an element"
                    + (f" of this system ({', '.join(elements)})" if elements else "; this is a single deployment")
                )
        if not separator or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise WstsError(f"--env {entry}: expected NAME=VALUE")
        found.setdefault(element, {})[name] = value
    return found


def per_element(option: str, values, elements: List[str], spread: bool = False) -> Dict[str, str]:
    """`--scenario` and `--record` of a system run: `<element>=<file>`, once per element.
    With `spread`, a plain `<file>` stands for one file per element, `<stem>.<element><suffix>`."""
    found: Dict[str, str] = {}
    # An element's own file first, wherever it is on the command line: it wins over a plain one
    for value in sorted(values or [], key=lambda v: "=" not in str(v)):
        element, separator, path = str(value).partition("=")
        if separator and element in elements and path:
            if element in found:
                raise WstsError(f"--{option} is given twice for element '{element}'")
            found[element] = path
        elif spread and not separator:
            target = Path(value)
            for name in elements:
                found.setdefault(name, str(target.with_name(f"{target.stem}.{name}{target.suffix}")))
        else:
            raise WstsError(f"--{option} {value}: for a system, say which element it is for: "
                            f"--{option} <element>=<file>, with one of the elements {', '.join(elements)}"
                            + ("" if not spread else f"; a plain --{option} <file> records every element, each to "
                                                     "<file stem>.<element>.<suffix>"))
    return found


def one_value(option: str, values) -> Optional[str]:
    """`--scenario` and `--record` of a single deployment: one file"""
    if not values:
        return None
    if isinstance(values, str):
        return values
    if len(values) > 1:
        raise WstsError(f"--{option} is given {len(values)} times: a single deployment plays and records one file")
    return values[0]


#: The `element` a merged recording tags each line with, at the start of the object
MERGED_ELEMENT = re.compile(r'^\s*\{\s*"element"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,')


def merge_recordings(sources: Dict[str, Path], target: Path) -> int:
    """One file holding every element's recording (`--record-merged` of a system run).

    Each line keeps its fields and gains `"element"`; the lines come in the order of
    `sources` (the system's elements) and, within one, in the order the element recorded
    them. Ticks are relative to each element's own recording start, so they are not
    comparable across elements and the lines are not re-sorted. Blank lines contribute
    nothing. Returns the line count of `target`.
    """
    missing = [f"{name} ({path})" for name, path in sources.items() if not Path(path).is_file()]
    if missing:
        raise WstsError("no recording to merge for " + ", ".join(missing)
                        + ": the element(s) did not leave a file to merge")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = 0
    with open(target, "w", newline="\n") as merged:
        for name, path in sources.items():
            for number, line in enumerate(Path(path).read_text().splitlines(), 1):
                text = line.strip()
                if not text:
                    continue
                if not text.startswith("{"):
                    raise WstsError(f"{path}:{number}: not a line of a recording")
                merged.write('{"element": ' + json.dumps(name) + "," + text[1:] + "\n")
                lines += 1
    return lines


def split_recording(source: Path, elements: List[str],
                    directory: Optional[Path] = None) -> Dict[str, str]:
    """The inverse of `merge_recordings` (`--scenario-merged` of a system run): each
    element's lines go to a file of its own, with the `"element"` key removed, byte for byte
    as they were recorded. Elements that have no lines get no file. The files are written to
    `directory` (default: a new temporary directory) and returned as element -> path.
    A line whose element is not one of `elements` is an error naming the line."""
    source = Path(source)
    directory = Path(directory) if directory is not None else Path(tempfile.mkdtemp(prefix="wsts-merged-"))
    directory.mkdir(parents=True, exist_ok=True)
    handles: Dict[str, tuple] = {}
    try:
        for number, line in enumerate(source.read_text().splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            match = MERGED_ELEMENT.match(text)
            if not match:
                raise WstsError(f"{source}:{number}: not a line of a merged recording "
                                '(each line starts with {"element": "<element>", …)')
            name = json.loads('"' + match.group(1) + '"')
            if name not in elements:
                raise WstsError(f"{source}:{number}: element '{name}' is not one of this system's "
                                f"({', '.join(elements)})")
            if name not in handles:
                path = directory / f"{name}.jsonl"
                handles[name] = (open(path, "w", newline="\n"), path)
            handle, _ = handles[name]
            handle.write("{" + text[match.end():] + "\n")
    finally:
        for handle, _ in handles.values():
            handle.close()
    return {name: str(handles[name][1]) for name in elements if name in handles}


def in_gds_dictionary(element: dict) -> bool:
    """Whether an element of a system description has its dictionary merged into the GDS's
    (`gds: false` in the system manifest leaves it out)"""
    return element.get("gds", True)


def dictionary_mismatch(dictionaries: Dict[str, Path], directory: Optional[Path] = None) -> Optional[str]:
    """Why the dictionaries of a system's elements cannot be merged into one, if they
    cannot: F Prime writes the version of the checkout into each when its build is
    configured, and the merge refuses dictionaries whose project or framework versions
    differ. That happens when the elements were built at different times: one before a
    commit and one after, or one from a clean tree and one with changes (`-dirty`)."""
    versions: Dict[str, Dict[str, str]] = {}
    for name, path in dictionaries.items():
        try:
            metadata = json.loads(Path(path).read_text()).get("metadata", {})
        except (OSError, ValueError):
            continue
        versions[name] = {field: str(metadata.get(field)) for field in ("projectVersion", "frameworkVersion")}
    for field, what in (("projectVersion", "project"), ("frameworkVersion", "framework")):
        found = {name: version[field] for name, version in versions.items()}
        if len(set(found.values())) > 1:
            if field == "frameworkVersion":
                return (f"the elements are built on different F Prime framework versions ("
                        + ", ".join(f"{version} in {name}" for name, version in found.items())
                        + "), and the GDS cannot merge their dictionaries. An element whose items the ground "
                        "does not decode by their own IDs can be left out of the merge with 'gds: false' under "
                        "its entry in the system manifest; otherwise build the elements on one framework.")
            return (f"the elements were built from different states of the checkout ({what} version "
                    + ", ".join(f"{version} in {name}" for name, version in found.items())
                    + "), and the GDS cannot merge their dictionaries. The version is written when a build is "
                    "configured: build them again from one state with\n  fprime-wsts build "
                    + (str(directory) if directory else "<system directory>") + " --fresh")
    return None


class SystemSimulation:
    """A running system: one simulated deployment per element, joined by their links, with
    an optional GDS on the ground element using the merged dictionary (headless, or with its
    browser GUI)"""

    def __init__(self, directory: Path, gds: bool = False, gds_port: int = 50000, log_dir: Optional[Path] = None,
                 env: Optional[List[str]] = None, gds_gui: Optional[int] = None, gds_gui_addr: str = "127.0.0.1",
                 auto_ports: bool = False, zmq_transport: Optional[List[str]] = None,
                 scenarios: Optional[Dict[str, str]] = None, scenario_mode: str = "stimulus",
                 records: Optional[Dict[str, str]] = None, stepped: Optional[bool] = None,
                 record_merged: Optional[str] = None, scenario_merged: Optional[str] = None,
                 journal: Optional[bool] = None, restore=None):
        from .system import load_description

        self.directory = Path(directory).resolve()
        # Snapshots (docs/snapshots.md): every element keeps a journal, and a system snapshot
        # restores every element at the instant it was taken, in lockstep
        self.journal = journal
        self.restore = restore
        if restore is not None:
            stepped = True
        self.description = load_description(self.directory)
        self.name = self.description["system"]
        self.ground = self.description["ground"]
        self.gds = gds
        self.gds_port = gds_port
        self.gds_gui = gds_gui  # browser GUI port; None keeps the GDS headless
        self.gds_gui_addr = gds_gui_addr
        self.log_dir = Path(log_dir).resolve() if log_dir else self.directory / "wsts-logs"
        self.description["log_dir"] = str(self.log_dir)  # where a conductor writes its plants' traces
        self.dictionary: Optional[Path] = None
        self.elements: Dict[str, Simulation] = {}
        self.started = 0.0
        self._link_dir: Optional[str] = None
        # NAME=VALUE for every element, <element>.NAME=VALUE for one
        self.env = parse_env(env, [element["name"] for element in self.description["elements"]])
        self.auto_ports = auto_ports
        self.zmq_transport = zmq_transport
        self.scenarios = dict(scenarios or {})  # element -> scenario file played from start-up
        self.scenario_mode = scenario_mode
        self.records = dict(records or {})  # element -> file its bus traffic and actions are recorded to
        # One file for the whole system: the elements record to temp files under the log
        # directory, merged when the run stops, and a replay is split back into one file per
        # element (see merge_recordings / split_recording)
        self.record_merged = Path(record_merged).resolve() if record_merged else None
        self.scenario_merged = scenario_merged
        self._merged_dir: Optional[Path] = None
        self._scenario_dir: Optional[Path] = None
        self._merge_pending = False
        self.merged_lines: Optional[int] = None
        self.merge_error: Optional[str] = None
        # Lockstep: every element in stepped time, its ticks granted by a conductor. None
        # leaves it to the manifests: lockstep if any element's asks for stepped time.
        if stepped is None:
            stepped = bool(self.stepped_by_manifest()) or None
        self.stepped = stepped
        if auto_ports:
            # The description is what clients connect by: it gets the addresses of this run
            for element in self.description["elements"]:
                element["bridge"] = auto_bridge(element["bridge"])

    def stepped_by_manifest(self) -> List[str]:
        """The elements whose manifest asks for stepped time (time.mode: stepped)"""
        found = []
        for element in self.description["elements"]:
            try:
                report = json.loads((self.directory / element["directory"] / "wsts-report.json").read_text())
            except (OSError, ValueError):
                continue
            if report.get("time", {}).get("mode") == "stepped":
                found.append(element["name"])
        return found

    def merge_dictionaries(self) -> Path:
        """One dictionary for the whole system, as the ground system uses for deployments
        joined by a hub: the ground element's merged with each other element's"""
        executable = shutil.which("fprime-merge-dictionary")
        if executable is None:
            raise WstsError("fprime-merge-dictionary was not found on PATH (it comes with fprime-gds)")
        dictionaries = {}
        for element in self.description["elements"]:
            if not in_gds_dictionary(element):
                continue
            _, dictionary, _ = find_artifacts(self.directory / element["directory"])
            if dictionary is None:
                raise WstsError(f"no dictionary found for element '{element['name']}': has it been built?")
            dictionaries[element["name"]] = dictionary
        options = list(self.description.get("merge", []))
        mismatch = dictionary_mismatch(dictionaries, self.directory)
        if mismatch:
            if "--permissive" not in options:
                raise WstsError(mismatch)
            print(f"fprime-wsts: warning: {mismatch.splitlines()[0]}", file=sys.stderr)
        merged = dictionaries.pop(self.ground)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, dictionary) in enumerate(dictionaries.items()):
            last = index == len(dictionaries) - 1
            target = self.log_dir / (f"{self.name}Dictionary.json" if last else f"{self.name}Dictionary.{index}.json")
            command = [executable, "--name", self.name, "--output", str(target), *options, str(merged), str(dictionary)]
            done = subprocess.run(command, capture_output=True, text=True)
            if done.returncode != 0:
                raise WstsError(f"could not merge the dictionary of element '{name}' into the system's:\n"
                                + (done.stderr or done.stdout).strip())
            merged = target
        return merged

    def start(self) -> "SystemSimulation":
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.gds:
            self.dictionary = self.merge_dictionaries()
        names = [element["name"] for element in self.description["elements"]]
        if self.scenario_merged:
            # One file for the whole system: split by element, and play each element's part
            self._scenario_dir = self.log_dir / "scenario-merged"
            shutil.rmtree(self._scenario_dir, ignore_errors=True)
            self.scenarios.update(split_recording(Path(self.scenario_merged), names, self._scenario_dir))
        if self.record_merged:
            # Each element records to a file of its own under the log directory; they are
            # merged into the one file when the run stops
            self._merged_dir = self.log_dir / "record-merged"
            shutil.rmtree(self._merged_dir, ignore_errors=True)
            self._merged_dir.mkdir(parents=True, exist_ok=True)
            self.records = {name: str(self._merged_dir / f"{name}.jsonl") for name in names}
        # Link sockets live in a short path of their own: Unix socket paths are limited to
        # about a hundred characters
        self._link_dir = tempfile.mkdtemp(prefix="wsts-links-")
        self.started = time.monotonic()
        try:
            # The ground element last: by the time the GDS is talking to it, the rest of the
            # system is running
            order = sorted(self.description["elements"], key=lambda element: element["name"] == self.ground)
            for element in order:
                name = element["name"]
                is_ground = name == self.ground
                simulation = Simulation(
                    self.directory / element["directory"], gds=self.gds and is_ground, gds_port=self.gds_port,
                    gds_gui=self.gds_gui if is_ground else None, gds_gui_addr=self.gds_gui_addr,
                    bridge=element["bridge"], log_dir=self.log_dir / name,
                    scenario=self.scenarios.get(name), scenario_mode=self.scenario_mode,
                    record=self.records.get(name),
                    dictionary=self.dictionary if is_ground else None,
                    env={**self._restored_env(name), **self.env.get(None, {}), **self.env.get(name, {}),
                         "WSTS_LINK_DIR": self._link_dir,
                         **({"WSTS_LOCKSTEP": "1"} if self.stepped else {})},
                    auto_ports=self.auto_ports,  # the GDS's ports; the bridge is chosen already
                    zmq_transport=self.zmq_transport if is_ground else None,
                    stepped=self.stepped,
                    journal=self.journal,
                    restore=self.restore.elements[name] if self.restore is not None else None,
                )
                simulation.owns_run_file = False
                self.elements[name] = simulation
                try:
                    simulation.start()
                except WstsError as error:
                    raise WstsError(f"element '{name}': {error}") from None
            self._wait_for_links()
            write_run_file(self.log_dir, self.addresses())
            self._merge_pending = bool(self.record_merged)
        except BaseException:
            self.stop()
            raise
        return self

    def _restored_env(self, name: str) -> Dict[str, str]:
        """What the element's environment was when it was snapshotted, but what the system sets"""
        if self.restore is None:
            return {}
        env = self.restore.elements[name].meta.get("env", {})
        return {key: value for key, value in env.items() if key not in ("WSTS_LINK_DIR", "WSTS_LOCKSTEP")}

    def complete_restore(self, conductor, keep_going: Callable[[], bool] = lambda: True) -> None:
        """Bring a restored system back to the instant it was snapshotted at: every element
        replays its journal while the conductor grants each the ticks it lacks; then each is
        checked, record by record"""
        from . import snapshots

        targets = {name: element.tick for name, element in self.restore.elements.items()}
        if conductor.couplings:
            # Plants (docs/dynamics.md) re-driven along their traces, compared, not written
            conductor.shadow_plants(snapshots.load_traces(self.restore))
        conductor.advance_to(targets, keep_going)
        # The journals end with the pauses of the snapshot: applied before each element's next tick
        deadline = time.monotonic() + 30
        while keep_going():
            statuses = conductor.refresh()
            if all(snapshots.replay_done(statuses[name], self.restore.elements[name]) for name in targets):
                break
            if time.monotonic() >= deadline:
                states = ", ".join(f"{name}: tick {s.get('tick')}, paused {s.get('paused')}, scenario "
                                   f"{s.get('scenario_state')}" for name, s in statuses.items())
                raise WstsError(f"the restored system did not come to rest at its snapshot ({states})")
            time.sleep(0.02)
        for name, simulation in self.elements.items():
            with simulation.connect() as bridge:
                bridge.wait_quiescent(timeout=30)
            snapshots.check_replay(simulation, self.restore.elements[name], name)
        conductor.end_shadow()

    def addresses(self) -> dict:
        """Where this run can be reached: each element's bridge, and the GDS on the ground element"""
        found: dict = {"elements": {name: simulation.bridge for name, simulation in self.elements.items()},
                       "directory": str(self.directory), "log_dir": str(self.log_dir)}
        ground = self.elements.get(self.ground)
        if self.gds and ground:
            found.update(gds_port=ground.gds_port, gds_gui=ground.gds_gui_url, zmq_transport=ground.zmq_transport,
                         dictionary=str(self.dictionary) if self.dictionary else None)
        return found

    def _wait_for_links(self, timeout: float = 20.0) -> None:
        from .bridge import BridgeError, WstsBridge, links_expected_started, links_expected_up

        # A datagram link has nothing to connect: it is started (its socket bound, so it can
        # receive) and never "comes up". A stream link has to be connected before the
        # system's first instant, which in lockstep must not race the other end's start-up.
        started = links_expected_started(self.description)
        up = links_expected_up(self.description)
        deadline = time.monotonic() + timeout
        for name, simulation in self.elements.items():
            if simulation.bridge == "none":
                continue
            try:
                with WstsBridge(simulation.bridge) as bridge:
                    while True:
                        status = bridge.status()
                        if (status.get("links", 0) >= started.get(name, 0)
                                and status.get("links_up", 0) >= up.get(name, 0)):
                            break
                        if time.monotonic() >= deadline:
                            raise WstsError(
                                f"element '{name}': {status.get('links_up', 0)} of {up.get(name, 0)} link(s) "
                                f"came up; see {simulation.log_dir / 'sim.log'}"
                            )
                        time.sleep(0.1)
            except BridgeError as error:
                raise WstsError(f"element '{name}': {error}; see {simulation.log_dir / 'sim.log'}") from None

    def exited(self) -> List[str]:
        """Elements whose deployment is no longer running"""
        return [name for name, simulation in self.elements.items() if simulation.sim and simulation.sim.poll() is not None]

    def connect(self):
        """Client for the whole system: one bridge per element"""
        from .bridge import WstsSystem

        return WstsSystem(self.description)

    @property
    def gds_gui_url(self) -> Optional[str]:
        """Where the ground element's browser GUI is served, when the system runs one"""
        element = self.elements.get(self.ground)
        return element.gds_gui_url if element else None

    def _pause_everything(self) -> None:
        """Pause every element before any is stopped, so that nothing is being sent while the first one
        shuts down. The elements stop one after another, and one that is still running keeps sending
        what its tick makes it send into the queues of the one that has stopped its own: flight
        software that asserts on a full queue (Svc-style `FW_ASSERT(send == OP_OK)`) then aborts in
        its teardown, with an exit code that says the run failed. Best effort: an element that has no
        bridge, or does not answer, is stopped as it is."""
        from .bridge import BridgeError, WstsBridge

        for simulation in self.elements.values():
            if simulation.sim is None or simulation.sim.poll() is not None or not simulation.bridge.startswith(
                    ("tcp:", "unix:")):
                continue
            try:
                with WstsBridge(simulation.bridge, connect_timeout=1.0, request_timeout=2.0) as bridge:
                    bridge.pause()
            except (BridgeError, OSError):
                pass

    def stop(self) -> Dict[str, Optional[int]]:
        self._pause_everything()
        codes = {name: simulation.stop() for name, simulation in reversed(list(self.elements.items()))}
        if self._merge_pending:
            self._merge_pending = False
            self._merge()
        (self.log_dir / RUN_FILE).unlink(missing_ok=True)
        if self._link_dir:
            shutil.rmtree(self._link_dir, ignore_errors=True)
            self._link_dir = None
        return codes

    def _merge(self) -> None:
        """The one file of `--record-merged`, from the elements' own recordings, in the
        order of the system's elements. The temp files go once the merge has succeeded; a
        failed merge leaves them in place, to be looked at."""
        sources = {element["name"]: Path(self.records[element["name"]])
                   for element in self.description["elements"] if element["name"] in self.records}
        try:
            self.merged_lines = merge_recordings(sources, self.record_merged)
        except WstsError as error:
            self.merge_error = str(error)
            return
        self.merge_error = None
        if self._merged_dir:
            shutil.rmtree(self._merged_dir, ignore_errors=True)

    def __enter__(self) -> "SystemSimulation":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


@contextlib.contextmanager
def terminate_as_interrupt():
    """Turn SIGTERM and SIGHUP into the interrupt that Ctrl-C is, for the length of a run.

    A run starts processes (the deployment, the GDS, the device models) and stops them when it ends or is
    interrupted. A plain `kill`, or a terminal that goes away, ended it at once and left them running, holding
    their ports. As an interrupt, the same orderly stop happens. SIGHUP stays ignored where it was ignored
    already (`nohup`). Only in the main thread, where Python allows signal handlers; elsewhere, nothing changes.
    """
    def interrupt(number, frame):
        raise KeyboardInterrupt

    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGHUP):
            if signal.getsignal(number) is signal.SIG_IGN:
                continue
            previous[number] = signal.signal(number, interrupt)
    except ValueError:  # not the main thread
        pass
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_system_cli(args) -> int:
    with terminate_as_interrupt():
        return _run_system_cli(args)


def _run_system_cli(args) -> int:
    from .system import load_description

    for option in ("bridge", "files_dir"):
        if getattr(args, option):
            raise WstsError(f"--{option.replace('_', '-')} applies to a single deployment and is not supported when "
                            "running a system; drive the elements through their bridges instead")
    names = [element["name"] for element in load_description(Path(args.directory).resolve())["elements"]]
    record_merged = getattr(args, "record_merged", None)
    scenario_merged = getattr(args, "scenario_merged", None)
    # One file per kind: a merged file and the per-element form say the same thing twice
    if record_merged and args.record:
        raise WstsError("--record-merged holds every element's recording in one file, so --record cannot be given "
                        "with it: use one or the other")
    if scenario_merged and args.scenario:
        raise WstsError("--scenario-merged holds every element's scenario in one file, so --scenario cannot be "
                        "given with it: use one or the other")
    stepped = getattr(args, "stepped", None)
    gds = args.gds or args.gds_gui
    system = SystemSimulation(Path(args.directory), gds=gds, gds_port=args.gds_port,
                              gds_gui=args.gds_gui_port if args.gds_gui else None,
                              gds_gui_addr=args.gds_gui_addr,
                              log_dir=Path(args.log_dir) if args.log_dir else None, env=getattr(args, "env", None),
                              auto_ports=getattr(args, "auto_ports", False), stepped=stepped,
                              record_merged=record_merged, scenario_merged=scenario_merged,
                              journal=getattr(args, "journal", None), restore=getattr(args, "restore", None))
    lockstep = bool(system.stepped)
    if system.journal and not lockstep:
        raise WstsError("a system keeps a journal in lockstep only: run it with --stepped")
    if system.restore is not None and (record_merged or scenario_merged or args.scenario or args.record):
        raise WstsError("--scenario and --record (merged or not) do not go with --from: each restored element "
                        "plays its journal, and records one of its own")
    # In lockstep a scenario without an element is the system's: actions at simulated times
    system_scenario = None
    plain = [value for value in (args.scenario or []) if "=" not in value]
    if lockstep and len(plain) == 1:
        system_scenario = plain[0]
        args.scenario = [value for value in args.scenario if "=" in value]
    elif plain and not lockstep:
        raise WstsError(f"--scenario {plain[0]}: for a system, say which element it is for: <element>=<file>. "
                        "A scenario for the whole system, on one time base, needs lockstep (--stepped)")
    system.scenarios = per_element("scenario", args.scenario, names)
    system.scenario_mode = args.scenario_mode
    records = {} if record_merged else per_element("record", args.record, names, spread=True)
    system.records.update(records)
    scenario = None
    if system_scenario:
        from .lockstep import SystemScenario

        description = system.description
        scenario = SystemScenario.load(system_scenario, names, [link["name"] for link in description.get("links", [])])
    if lockstep:
        unbridged = [element["name"] for element in system.description["elements"] if element["bridge"] == "none"]
        if unbridged:
            raise WstsError(f"a system is stepped through the bridges of its elements, and {', '.join(unbridged)} "
                            "has none (bridge: none)")
    system.start()
    if not args.json:
        bridges = ", ".join(f"{name} {simulation.bridge}" for name, simulation in system.elements.items())
        print(f"system {system.name} running{' in lockstep' if lockstep else ''}: bridges {bridges}; "
              f"logs {system.log_dir}")
        if gds:
            print(f"GDS listening for element '{system.ground}' on port {system.elements[system.ground].gds_port}, "
                  f"dictionary {system.dictionary}")
        if args.gds_gui:
            print(f"GDS browser GUI for element '{system.ground}' at {system.gds_gui_url}")
    statuses: Dict[str, dict] = {}
    interrupted = False
    stuck: Dict[str, str] = {}
    lockstep_error = None
    sim_ns = 0
    conducted = 0.0
    try:
        if lockstep:
            from .lockstep import Conductor, LockstepError, Stuck

            conductor = Conductor(system.description)
            try:
                conductor.wait_ready(TICK_SOURCE_WAIT, alive=lambda: not system.exited())
                began = time.monotonic()
                from_ns = 0
                if system.restore is not None:
                    try:
                        system.complete_restore(conductor, keep_going=lambda: not system.exited())
                    except BaseException:
                        conductor.close()
                        system.stop()
                        raise
                    from_ns = conductor.time_ns
                    if not args.json:
                        ticks = ", ".join(f"{n} at tick {t}" for n, t in system.restore.meta["elements"].items())
                        print(f"restored from {system.restore.path}: {ticks}, every bus record the same as when it "
                              "was taken; paused there" + ("" if args.duration else
                                                           " (resume it through the bridges or the GDS)"))
                    if args.duration:
                        for bridge in conductor.bridges.values():
                            bridge.resume()
                # Simulated seconds; without a duration, until interrupted. A pause of any
                # element (the GDS, a client) holds the whole system. After a restore, the
                # duration counts from the snapshot.
                conductor.run(until_ns=from_ns + int(round(args.duration * 1e9)) if args.duration else None,
                              scenario=scenario, hold_while_paused=True, keep_going=lambda: not system.exited())
                conducted = time.monotonic() - began
            except Stuck as error:
                stuck = error.stuck
            except LockstepError as error:
                if not system.exited():
                    lockstep_error = str(error)
            finally:
                sim_ns = conductor.time_ns if conductor.state else 0
                conductor.close()
        else:
            deadline = time.monotonic() + args.duration if args.duration else None
            while not system.exited():
                if deadline and time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        interrupted = True
    crashed = system.exited()
    asserts = {name: system.elements[name].assertion() for name in crashed}
    for name, simulation in system.elements.items():
        if name in crashed or simulation.bridge == "none":
            continue
        try:
            with simulation.connect() as bridge:
                statuses[name] = with_traffic(bridge, bridge.status())
        except Exception:
            pass
    codes = system.stop()
    wall = round(time.monotonic() - system.started, 2)
    timing = {}
    if lockstep:
        simulated = round(sim_ns / 1e9, 3)
        timing = {"stepped": True, "lockstep": True, "sim_seconds": simulated,
                  "speed": round(simulated / conducted, 1) if conducted > 0 else None,
                  **({"dynamics": [spec["name"] for spec in system.description["dynamics"]]}
                     if system.description.get("dynamics") else {}),
                  **({"stuck": stuck} if stuck else {}), **({"error": lockstep_error} if lockstep_error else {})}
    summary = {
        "system": system.name,
        **({"restored": {"snapshot": str(system.restore.path), "elements": system.restore.meta["elements"]}}
           if system.restore is not None else {}),
        "exited_early": crashed,
        "interrupted": interrupted,
        "seconds": wall,
        "logs": str(system.log_dir),
        "dictionary": str(system.dictionary) if system.dictionary else None,
        "gds_gui": system.gds_gui_url,
        "gds_port": system.elements[system.ground].gds_port if gds else None,
        **timing,
        "elements": {
            name: {
                "deployment": simulation.binary.name,
                "bridge": simulation.bridge,
                **({"record": str(Path(records[name]).resolve())} if name in records else {}),
                "exit_code": codes.get(name),
                "exited_early": name in crashed,
                "logs": str(simulation.log_dir),
                "final_status": statuses.get(name, {}),
                **({"assert": _assert_summary(asserts[name])} if asserts.get(name) else {}),
            }
            for name, simulation in system.elements.items()
        },
        **({"record_merged": {"path": str(system.record_merged), "lines": system.merged_lines}}
           if system.record_merged else {}),
        **({"merge_error": system.merge_error} if system.merge_error else {}),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"system stopped after {summary['seconds']} s, exit codes "
              + ", ".join(f"{name} {code}" for name, code in codes.items())
              + (f"; {timing['sim_seconds']} s simulated in lockstep, {timing['speed']} times real time"
                 if timing and timing.get("speed") else ""))
        for name, status in statuses.items():
            line = describe_tick_times(status.get("tick_times"), int(status.get("tick_period_ns", 0) or 0))
            if line:
                print(f"{name}: {line}")
        if system.record_merged and system.merged_lines is not None:
            print(f"merged recording: {system.merged_lines} lines of {len(system.records)} element(s) in "
                  f"{system.record_merged}")
    if system.merge_error:
        print(f"the recordings of the elements were not merged: {system.merge_error}. They are in "
              f"{system.log_dir / 'record-merged'}", file=sys.stderr)
        return 1
    if stuck:
        for name, why in stuck.items():
            print(f"lockstep: a tick of element '{name}' did not finish, and the system stopped at "
                  f"{timing['sim_seconds']} s. Work outstanding: {why}; see {system.elements[name].log_dir / 'sim.log'}",
                  file=sys.stderr)
        return 1
    if lockstep_error:
        print(f"lockstep: {lockstep_error}", file=sys.stderr)
        return 1
    if crashed:
        for name in crashed:
            print(f"element '{name}' exited on its own"
                  + (f": {asserts[name]['message']}" if asserts.get(name) else "")
                  + f"; see {system.elements[name].log_dir / 'sim.log'}", file=sys.stderr)
        return 1
    return 0 if all(code in (0, None) for code in codes.values()) else 1


def _assert_summary(assertion: dict) -> dict:
    """An assert as the JSON summary shows it"""
    return {key: value for key, value in assertion.items() if key != "message"}


#: How long a stepped run of a set duration waits for the tick source to say how long a tick is
TICK_SOURCE_WAIT = 20.0
#: Seconds a stepped run under a running scenario must stay paused, unchanged, before it has ended
HELD_PAUSE = 0.2


def run_cli(args) -> int:
    with terminate_as_interrupt():
        return _run_cli(args)


def _run_cli(args) -> int:
    from .system import SYSTEM_FILE

    restore = None
    if getattr(args, "from_snapshot", None):
        from . import snapshots

        restore = snapshots.load(args.from_snapshot)
        if args.directory in (None, "."):
            args.directory = str(restore.directory)
        if Path(args.directory).resolve() != restore.directory.resolve():
            raise WstsError(f"{restore.path} is a snapshot of {restore.directory}, not of {args.directory}")
        if getattr(args, "stepped", None) is False:
            raise WstsError("a snapshot is restored in stepped time: --realtime does not go with --from")
        for option in ("scenario", "record"):
            if getattr(args, option, None):
                raise WstsError(f"--{option} does not go with --from: the restored run plays the snapshot's "
                                "journal, and records one of its own (journal.jsonl in its log directory)")
    args.restore = restore
    if (Path(args.directory) / SYSTEM_FILE).is_file():
        if restore is not None and not restore.is_system:
            raise WstsError(f"{restore.path} is a snapshot of one deployment, and {args.directory} is a system")
        return _run_system_cli(args)
    if restore is not None and restore.is_system:
        raise WstsError(f"{restore.path} is a snapshot of a system, and {args.directory} is one deployment")
    for merged, option, single in (("record_merged", "--record-merged", "--record"),
                                   ("scenario_merged", "--scenario-merged", "--scenario")):
        if getattr(args, merged, None):
            raise WstsError(f"{option} is for a system, where it holds every element's file in one; a single "
                            f"deployment plays and records one file already: use {single}")
    gds = args.gds or args.gds_gui
    simulation = Simulation(Path(args.directory), gds=gds, gds_port=args.gds_port,
                            gds_gui=args.gds_gui_port if args.gds_gui else None,
                            gds_gui_addr=args.gds_gui_addr, bridge=args.bridge,
                            scenario=one_value("scenario", args.scenario), scenario_mode=args.scenario_mode,
                            record=one_value("record", args.record),
                            log_dir=Path(args.log_dir) if args.log_dir else None,
                            files_dir=Path(args.files_dir) if args.files_dir else None,
                            env={**(restore.meta.get("env", {}) if restore is not None else {}),
                                 **parse_env(getattr(args, "env", None)).get(None, {})},
                            auto_ports=getattr(args, "auto_ports", False), stepped=getattr(args, "stepped", None),
                            journal=getattr(args, "journal", None), restore=restore)
    is_stepped = simulation.stepped if simulation.stepped is not None else \
        simulation.report.get("time", {}).get("mode") == "stepped"
    if is_stepped and args.duration and simulation.bridge == "none":
        raise WstsError("--duration counts simulated seconds in stepped time, which are read through the bridge: "
                        "this deployment has none (bridge: none)")
    # A stepped run of a set duration is one step from a paused start: it ends at exactly
    # that simulated time, whatever the host's speed
    simulation.hold = bool(is_stepped and args.duration)
    # Plants (docs/dynamics.md) are stepped between instants by a conductor: the deployment
    # runs in lockstep, as a system of one. A restore replays what they gave (it is in the
    # journal) without them.
    plants = simulation.report.get("dynamics") or []
    restore_conducted = restore is None or bool(restore.meta.get("conducted"))
    conducted = bool(plants and is_stepped and restore_conducted and simulation.bridge != "none")
    if plants and not restore_conducted and not args.json:
        print(f"{restore.path} was taken of a run without plants: restored, and run, without them")
    if conducted:
        simulation.extra_env["WSTS_LOCKSTEP"] = "1"
        simulation.conducted = True
        simulation.hold = False
    try:
        simulation.start()
    except (KeyboardInterrupt, SystemExit):
        simulation.stop()  # interrupted while starting: what was started goes with it
        raise
    except StartError as error:
        if args.json:
            # The same summary as for a run that ended, so that a caller reads one shape
            print(json.dumps({
                "deployment": simulation.binary.name,
                "exit_code": error.code,
                "exited_early": error.code is not None,
                "interrupted": False,
                "seconds": round(time.monotonic() - simulation.started, 2) if simulation.started else 0.0,
                "logs": str(simulation.log_dir),
                "gds_gui": None,
                "final_status": {},
                "error": str(error),
                **({"assert": _assert_summary(error.assertion)} if error.assertion else {}),
            }, indent=2))
        raise
    if not args.json:
        print(f"simulation running: {simulation.binary.name} (bridge {simulation.bridge}, logs {simulation.log_dir})")
        if simulation.restore is not None and conducted:
            print(f"restoring from {simulation.restore.path} to tick {simulation.restore.tick}, its plants re-driven "
                  "along their traces")
        elif simulation.restore is not None:
            print(f"restored from {simulation.restore.path}: tick {simulation.restore.tick}, "
                  f"{simulation.restore.meta.get('records', 0)} bus records the same as when it was taken; paused there"
                  + ("" if args.duration else " (resume it through the bridge or the GDS)"))
        if gds:
            print(f"GDS connecting to the deployment on port {simulation.gds_port}" if simulation.gds_is_client
                  else f"GDS listening for the deployment on port {simulation.gds_port}")
        if args.gds_gui:
            print(f"GDS browser GUI at {simulation.gds_gui_url}")
    status = {}
    crashed = False
    interrupted = False
    stuck = None
    lockstep_error = None
    try:
        if conducted:
            if not args.json:
                print("plants " + ", ".join(spec["name"] for spec in plants) + " stepped with the deployment, in "
                      "lockstep")
            try:
                stuck, lockstep_error = conduct_deployment(simulation, plants, args.duration,
                                                           keep_going=lambda: simulation.sim.poll() is None,
                                                           restore=simulation.restore)
            except WstsError:
                simulation.stop()  # a restore that differs from its snapshot: nothing is handed over
                raise
        deadline = time.monotonic() + args.duration if args.duration else None
        watch = simulation.connect() if is_stepped and simulation.bridge != "none" and not conducted else None
        if watch is not None and simulation.hold:
            # The tick source declares its period when the deployment's start-up code
            # starts it, after the bridge is up: on a loaded host that can take a while.
            # Waited for on a connection of its own: the step is recorded with the number
            # of its request, which must not depend on how long that took.
            waited_until = time.monotonic() + TICK_SOURCE_WAIT
            with simulation.connect() as probe:
                while (not probe.status().get("tick_period_ns") and time.monotonic() < waited_until
                       and simulation.sim.poll() is None):
                    time.sleep(0.02)
            now = watch.status()
            period = now.get("tick_period_ns") or 0
            if not period:
                raise WstsError(f"the deployment's tick source did not say how long a tick is within "
                                f"{TICK_SOURCE_WAIT:g} s of start, so a duration in simulated seconds cannot be turned "
                                "into ticks: run without --duration and stop it through the bridge, or in real time")
            # From the start; after a restore, from the snapshot's tick
            ticks = max(1, -(-int(args.duration * 1e9) // period) - (0 if simulation.restore is not None else now["tick"]))
            watch.request("step", ticks=ticks)
        paused_at = None  # (tick, scenario step) a held run was first seen paused at, and when
        while simulation.sim.poll() is None and not conducted:
            if watch is not None:
                # Stepped time: the duration is simulated seconds, and a tick that cannot
                # finish ends the run
                try:
                    now = watch.status()
                except Exception:
                    break  # the deployment is going
                if now.get("stuck"):
                    stuck = now["stuck"]
                    break
                if simulation.hold and now["paused"] and not now.get("stepping"):
                    # The step is taken; its work is finished when wait_quiescent returns. A
                    # scenario that pauses and steps the run itself (a replay of a run a client
                    # drove) passes through this between two of its own actions: while one runs,
                    # the run has ended only if it stays so, at the same tick and step
                    if now.get("scenario_state") != "running":
                        break
                    held = (now.get("tick"), now.get("scenario_step"))
                    if paused_at is not None and paused_at[0] == held and time.monotonic() - paused_at[1] >= HELD_PAUSE:
                        break
                    if paused_at is None or paused_at[0] != held:
                        paused_at = (held, time.monotonic())
                else:
                    paused_at = None
                time.sleep(0.02)
                continue
            if deadline and time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if watch is not None:
            if simulation.hold and not stuck and simulation.sim.poll() is None:
                try:
                    watch.wait_quiescent(timeout=15)
                except Exception:
                    pass
            watch.close()
        crashed = simulation.sim.poll() is not None
    except KeyboardInterrupt:
        interrupted = True
    if not crashed and simulation.bridge != "none":
        try:
            with simulation.connect() as bridge:
                status = with_traffic(bridge, bridge.status())
        except Exception:
            status = {}
    code = simulation.stop()
    assertion = simulation.assertion() if crashed else None
    wall = round(time.monotonic() - simulation.started, 2)
    timing = {}
    if is_stepped:
        simulated = round(float(status.get("sim_seconds", 0.0)), 3)
        timing = {"stepped": True, "sim_seconds": simulated, "speed": round(simulated / wall, 1) if wall > 0 else None,
                  **({"lockstep": True, "dynamics": [spec["name"] for spec in plants]} if conducted else {}),
                  **({"stuck": stuck} if stuck else {}),
                  **({"error": lockstep_error} if lockstep_error else {}),
                  **({"tick_times": status["tick_times"]} if status.get("tick_times") else {})}
    summary = {
        "deployment": simulation.binary.name,
        "exit_code": code,
        "exited_early": crashed,
        "interrupted": interrupted,
        "seconds": round(time.monotonic() - simulation.started, 2),
        "logs": str(simulation.log_dir),
        "bridge": simulation.bridge,
        "gds_port": simulation.gds_port if gds else None,
        "gds_gui": simulation.gds_gui_url,
        "final_status": status,
        **({"restored": {"snapshot": str(simulation.restore.path), "tick": simulation.restore.tick,
                         "records": simulation.restore.meta.get("records", 0)}} if simulation.restore is not None else {}),
        **({"journal": str(simulation.log_dir / "journal.json")} if simulation.journal else {}),
        **timing,
        **({"assert": _assert_summary(assertion)} if assertion else {}),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"simulation stopped after {summary['seconds']} s, exit code {code}"
              + (f"; {timing['sim_seconds']} s simulated, {timing['speed']} times real time" if timing else ""))
        line = describe_tick_times(timing.get("tick_times"), int(status.get("tick_period_ns", 0) or 0))
        if line:
            print(line)
    if stuck:
        print(f"stepped time: a tick did not finish, and the simulation paused. Work outstanding: {stuck}. A component "
              "that waits on the host (sleeps, polls, blocks in a handler) cannot be stepped: give it a surrogate, or "
              f"run in real time; see {simulation.log_dir / 'sim.log'}", file=sys.stderr)
        return 1
    if lockstep_error:
        print(f"lockstep: {lockstep_error}", file=sys.stderr)
        return 1
    if crashed:
        print("the simulation exited on its own" + (f": {assertion['message']}" if assertion else "")
              + f"; see {simulation.log_dir / 'sim.log'}", file=sys.stderr)
        return 1
    return 0 if code in (0, None) else 1


def deployment_description(simulation: "Simulation", plants: List[dict]) -> dict:
    """A running deployment as a system of one element, for a conductor that steps its plants"""
    return {"system": simulation.binary.name, "ground": "deployment",
            "elements": [{"name": "deployment", "directory": ".", "bridge": simulation.bridge}], "links": [],
            "dynamics": plants, "directory": str(simulation.directory), "log_dir": str(simulation.log_dir)}


def restore_deployment(simulation: "Simulation", conductor, keep_going: Callable[[], bool] = lambda: True) -> None:
    """Bring a restored deployment that has plants back to its snapshot under its conductor:
    the journal replays while the conductor grants the ticks and re-drives the plants along
    their traces; then the replay is checked, record by record"""
    from . import snapshots

    snapshot = simulation.restore
    conductor.shadow_plants(snapshots.load_traces(snapshot))
    conductor.advance_to({"deployment": snapshot.tick}, keep_going)
    deadline = time.monotonic() + 30
    with simulation.connect() as bridge:
        while not snapshots.replay_done(bridge.status(), snapshot):  # its journal ends with the pause
            if time.monotonic() >= deadline or not keep_going():
                raise WstsError(f"the restored run did not come to rest at its snapshot (tick {snapshot.tick})")
            time.sleep(0.02)
        bridge.wait_quiescent(timeout=30)
    snapshots.check_replay(simulation, snapshot, snapshot.meta["deployment"])
    conductor.end_shadow()


def conduct_deployment(simulation: "Simulation", plants: List[dict], duration: Optional[float],
                       keep_going: Callable[[], bool], restore=None) -> Tuple[Optional[str], Optional[str]]:
    """Step a deployment started in lockstep, with its plants, for `duration` simulated seconds,
    or until it is interrupted (a pause holds it). A restored one is first brought back to its
    snapshot, and its duration counts from there. Returns (stuck, error)."""
    from .lockstep import Conductor, LockstepError, Stuck

    conductor = Conductor(deployment_description(simulation, plants))
    try:
        conductor.wait_ready(TICK_SOURCE_WAIT, alive=keep_going)
        from_ns = 0
        if restore is not None:
            restore_deployment(simulation, conductor, keep_going)
            from_ns = conductor.time_ns
            if duration:
                for bridge in conductor.bridges.values():
                    bridge.resume()
        conductor.run(until_ns=from_ns + int(round(duration * 1e9)) if duration else None, hold_while_paused=True,
                      keep_going=keep_going)
    except Stuck as error:
        return error.stuck.get("deployment", str(error)), None
    except LockstepError as error:
        return None, (str(error) if keep_going() else None)
    finally:
        conductor.close()
    return None, None
