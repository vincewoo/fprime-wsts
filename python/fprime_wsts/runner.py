"""Launcher for a built simulated deployment: `fprime-wsts run`.

One command brings up the simulation and, if asked, a headless GDS, with logs in a known
place and a machine-readable summary at the end. That is what CI jobs and agents need:
no terminals to juggle and an exit code that means something.
"""

from __future__ import annotations

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
from typing import Dict, List, Optional

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
    for header in sorted(Path(directory).glob("build-fprime-*/*/Top/*TopologyAc.hpp")):
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


def gds_command(executable: str, dictionary: Path, gds_port: int, log_dir: Path,
                gui_port: Optional[int] = None, gui_addr: str = "127.0.0.1") -> List[str]:
    """The `fprime-gds` command line: headless, or with its browser GUI on `gui_port`.

    `-n` keeps fprime-gds from running a deployment of its own: the simulation is started
    separately. With a GUI the HTML app is served on `gui_addr:gui_port` (its own port, not
    `gds_port`, which carries the flight link); the browser is left unopened because the run
    may be on a headless host, so the caller prints the URL instead.
    """
    command = [executable, "-n", "--dictionary", str(dictionary), "--ip-port", str(gds_port),
               "--logs", str(log_dir), "--log-directly"]
    if gui_port is None:
        command += ["-g", "none"]
    else:
        command += ["-g", "html", "--gui-addr", gui_addr, "--gui-port", str(gui_port),
                    "--skip-browser-open"]
    return command


class Simulation:
    """A running simulated deployment, optionally with a GDS (headless, or with its browser GUI)"""

    def __init__(self, directory: Path, gds: bool = False, gds_port: int = 50000,
                 gds_gui: Optional[int] = None, gds_gui_addr: str = "127.0.0.1",
                 bridge: Optional[str] = None,
                 scenario: Optional[str] = None, scenario_mode: str = "stimulus", record: Optional[str] = None,
                 log_dir: Optional[Path] = None, files_dir: Optional[Path] = None,
                 dictionary: Optional[Path] = None, env: Optional[Dict[str, str]] = None):
        self.directory = Path(directory).resolve()
        self.binary, self.dictionary, self.report = find_artifacts(self.directory)
        if dictionary is not None:
            self.dictionary = Path(dictionary)  # for example the merged dictionary of a system
        self.extra_env = dict(env or {})
        self.bridge = bridge or self.report.get("bridge", "none")
        self.gds = gds
        self.gds_port = gds_port
        self.gds_gui = gds_gui  # browser GUI port; None keeps the GDS headless
        self.gds_gui_addr = gds_gui_addr
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
            command = gds_command(executable, self.dictionary, self.gds_port, self.log_dir / "gds",
                                  self.gds_gui, self.gds_gui_addr)
            self._processes.append(subprocess.Popen(command, stdout=gds_log, stderr=subprocess.STDOUT,
                                                    cwd=self.log_dir, start_new_session=True))
            arguments = ["-a", "127.0.0.1", "-p", str(self.gds_port)]
        env = dict(os.environ)
        # Defaults from the manifest (`run.env`), then what this run was given
        defaults = self.report.get("run", {})
        for name, value in defaults.get("env", {}).items():
            if name in defaults.get("env_paths", []):
                value = str((self.directory / value).resolve())
            env[name] = value
        env.update(self.extra_env)
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
            if port and not _wait_for_port(port, 15, self.sim):
                code = self.sim.poll()
                self.stop()
                raise self._start_error(f"the simulation did not open its bridge on {self.bridge}", code,
                                        self.log_dir / "sim.log")
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
                    raise self._start_error(f"the deployment did not connect to the GDS on port {self.gds_port}",
                                            code, self.log_dir)
                time.sleep(0.2)
            if self.gds_gui:
                # Best effort: the URL only means something once the HTML app answers, and
                # that app is a child of the GDS, so a failure shows in the GDS log
                _wait_for_port(self.gds_gui, 15)
        return self

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
        self._processes.clear()
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


class SystemSimulation:
    """A running system: one simulated deployment per element, joined by their links, with
    an optional GDS on the ground element using the merged dictionary (headless, or with its
    browser GUI)"""

    def __init__(self, directory: Path, gds: bool = False, gds_port: int = 50000, log_dir: Optional[Path] = None,
                 env: Optional[List[str]] = None, gds_gui: Optional[int] = None, gds_gui_addr: str = "127.0.0.1"):
        from .system import load_description

        self.directory = Path(directory).resolve()
        self.description = load_description(self.directory)
        self.name = self.description["system"]
        self.ground = self.description["ground"]
        self.gds = gds
        self.gds_port = gds_port
        self.gds_gui = gds_gui  # browser GUI port; None keeps the GDS headless
        self.gds_gui_addr = gds_gui_addr
        self.log_dir = Path(log_dir).resolve() if log_dir else self.directory / "wsts-logs"
        self.dictionary: Optional[Path] = None
        self.elements: Dict[str, Simulation] = {}
        self.started = 0.0
        self._link_dir: Optional[str] = None
        # NAME=VALUE for every element, <element>.NAME=VALUE for one
        self.env = parse_env(env, [element["name"] for element in self.description["elements"]])

    def merge_dictionaries(self) -> Path:
        """One dictionary for the whole system, as the ground system uses for deployments
        joined by a hub: the ground element's merged with each other element's"""
        executable = shutil.which("fprime-merge-dictionary")
        if executable is None:
            raise WstsError("fprime-merge-dictionary was not found on PATH (it comes with fprime-gds)")
        dictionaries = {}
        for element in self.description["elements"]:
            _, dictionary, _ = find_artifacts(self.directory / element["directory"])
            if dictionary is None:
                raise WstsError(f"no dictionary found for element '{element['name']}': has it been built?")
            dictionaries[element["name"]] = dictionary
        merged = dictionaries.pop(self.ground)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, dictionary) in enumerate(dictionaries.items()):
            last = index == len(dictionaries) - 1
            target = self.log_dir / (f"{self.name}Dictionary.json" if last else f"{self.name}Dictionary.{index}.json")
            command = [executable, "--name", self.name, "--output", str(target), str(merged), str(dictionary)]
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
                    dictionary=self.dictionary if is_ground else None,
                    env={**self.env.get(None, {}), **self.env.get(name, {}), "WSTS_LINK_DIR": self._link_dir},
                )
                self.elements[name] = simulation
                try:
                    simulation.start()
                except WstsError as error:
                    raise WstsError(f"element '{name}': {error}") from None
            self._wait_for_links()
        except BaseException:
            self.stop()
            raise
        return self

    def _wait_for_links(self, timeout: float = 20.0) -> None:
        from .bridge import BridgeError, WstsBridge

        deadline = time.monotonic() + timeout
        for name, simulation in self.elements.items():
            if simulation.bridge == "none":
                continue
            try:
                with WstsBridge(simulation.bridge) as bridge:
                    while True:
                        status = bridge.status()
                        if status.get("links_up", 0) >= status.get("links", 0):
                            break
                        if time.monotonic() >= deadline:
                            raise WstsError(
                                f"element '{name}': {status.get('links_up', 0)} of {status.get('links', 0)} link(s) "
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

    def stop(self) -> Dict[str, Optional[int]]:
        codes = {name: simulation.stop() for name, simulation in reversed(list(self.elements.items()))}
        if self._link_dir:
            shutil.rmtree(self._link_dir, ignore_errors=True)
            self._link_dir = None
        return codes

    def __enter__(self) -> "SystemSimulation":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def run_system_cli(args) -> int:
    for option in ("bridge", "scenario", "record", "files_dir"):
        if getattr(args, option):
            raise WstsError(f"--{option.replace('_', '-')} applies to a single deployment and is not supported when "
                            "running a system; drive the elements through their bridges instead")
    gds = args.gds or args.gds_gui
    system = SystemSimulation(Path(args.directory), gds=gds, gds_port=args.gds_port,
                              gds_gui=args.gds_gui_port if args.gds_gui else None,
                              gds_gui_addr=args.gds_gui_addr,
                              log_dir=Path(args.log_dir) if args.log_dir else None, env=getattr(args, "env", None))
    system.start()
    if not args.json:
        bridges = ", ".join(f"{name} {simulation.bridge}" for name, simulation in system.elements.items())
        print(f"system {system.name} running: bridges {bridges}; logs {system.log_dir}")
        if gds:
            print(f"GDS listening for element '{system.ground}' on port {args.gds_port}, dictionary {system.dictionary}")
        if args.gds_gui:
            print(f"GDS browser GUI for element '{system.ground}' at {system.gds_gui_url}")
    statuses: Dict[str, dict] = {}
    interrupted = False
    try:
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
                statuses[name] = bridge.status()
        except Exception:
            pass
    codes = system.stop()
    summary = {
        "system": system.name,
        "exited_early": crashed,
        "interrupted": interrupted,
        "seconds": round(time.monotonic() - system.started, 2),
        "logs": str(system.log_dir),
        "dictionary": str(system.dictionary) if system.dictionary else None,
        "gds_gui": system.gds_gui_url,
        "elements": {
            name: {
                "deployment": simulation.binary.name,
                "exit_code": codes.get(name),
                "exited_early": name in crashed,
                "logs": str(simulation.log_dir),
                "final_status": statuses.get(name, {}),
                **({"assert": _assert_summary(asserts[name])} if asserts.get(name) else {}),
            }
            for name, simulation in system.elements.items()
        },
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"system stopped after {summary['seconds']} s, exit codes "
              + ", ".join(f"{name} {code}" for name, code in codes.items()))
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


def run_cli(args) -> int:
    from .system import SYSTEM_FILE

    if (Path(args.directory) / SYSTEM_FILE).is_file():
        return run_system_cli(args)
    gds = args.gds or args.gds_gui
    simulation = Simulation(Path(args.directory), gds=gds, gds_port=args.gds_port,
                            gds_gui=args.gds_gui_port if args.gds_gui else None,
                            gds_gui_addr=args.gds_gui_addr, bridge=args.bridge,
                            scenario=args.scenario, scenario_mode=args.scenario_mode, record=args.record,
                            log_dir=Path(args.log_dir) if args.log_dir else None,
                            files_dir=Path(args.files_dir) if args.files_dir else None,
                            env=parse_env(getattr(args, "env", None)).get(None, {}))
    try:
        simulation.start()
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
        if gds:
            print(f"GDS listening for the deployment on port {args.gds_port}")
        if args.gds_gui:
            print(f"GDS browser GUI at {simulation.gds_gui_url}")
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
    assertion = simulation.assertion() if crashed else None
    summary = {
        "deployment": simulation.binary.name,
        "exit_code": code,
        "exited_early": crashed,
        "interrupted": interrupted,
        "seconds": round(time.monotonic() - simulation.started, 2),
        "logs": str(simulation.log_dir),
        "gds_gui": simulation.gds_gui_url,
        "final_status": status,
        **({"assert": _assert_summary(assertion)} if assertion else {}),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"simulation stopped after {summary['seconds']} s, exit code {code}")
    if crashed:
        print("the simulation exited on its own" + (f": {assertion['message']}" if assertion else "")
              + f"; see {simulation.log_dir / 'sim.log'}", file=sys.stderr)
        return 1
    return 0 if code in (0, None) else 1
