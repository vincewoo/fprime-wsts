"""MCP server for driving simulations from an agent: `fprime-wsts mcp`.

The server speaks the Model Context Protocol over stdio: an MCP client (Claude Code, for
one) starts it as a child process, and it ends with the client. It opens no port of its
own; everything it reaches is on this machine:

- runs it starts itself (`run_start`, which is `fprime-wsts run --auto-ports --json`), and
  stops when they are asked to stop or when the server exits;
- runs started elsewhere, found through `wsts-logs/wsts-run.json` of their directory;
- the bridge of each element (`WstsBridge`), and the GDS of a run started with one, joined
  as a client the way fprime-gds's test API is.

`Session` holds the logic and is used without the `mcp` package (python/tests); `serve()`
puts its methods behind MCP tools. Each tool answers with JSON, and failures that an agent
can act on come back as `{"error": ...}` rather than as protocol errors.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .fpp import WstsError

#: What the server tells a client about itself, before any tool is called
INSTRUCTIONS = """\
Drive fprime-wsts simulations of F Prime deployments: build them, run them with a GDS, act on
them through the bridge (faults, injected bytes, GPIO, registers, stepping), send flight
commands and read events and telemetry.

A run is named by its generated directory: one deployment (holds wsts-report.json) or a system
of several (holds wsts-system.json). Once one run is known, `run` may be left out. Actions for one
element of a system take `element`; faults also take a link name or "<element>.<endpoint>".

Typical loop: build -> run_start -> status / dictionary -> send_command, act -> wait_event /
read_telemetry -> tail_log or last_assert when something went wrong -> run_stop.

Things that surprise:
- Telemetry is downlinked about once a second with the latest value only. read_telemetry waits
  for a fresh sample by default.
- While a simulation is paused its rate groups, and so downlink, stop.
- Events of an element behind a hub (Payload in the example) arrive late through it.
  wait_event looks back to the last command or action by default, so an event that came before
  the call is still found.
- Elements of a system keep separate tick counts; step takes an element.
"""

EVENT_LIMIT = 20000  # events and telemetry samples each GDS feed keeps
RECORD_LIMIT = 20000  # bus records kept per bridge connection
START_TIMEOUT = 180.0  # seconds a run may take to come up
RESTORE_TIMEOUT = 900.0  # ... when it is restored from a snapshot first: its journal replays
STOP_TIMEOUT = 60.0


def _json_safe(value: Any) -> Any:
    """A value as JSON can hold it (GDS values may be enums, tuples, bytes ...)"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


# ----------------------------------------------------------------------
# Names of the dictionary
# ----------------------------------------------------------------------


def resolve_name(name: str, names, what: str) -> str:
    """The full dictionary name `name` stands for: the name itself, or the one name that ends
    with it after a dot (`CMD_NO_OP`, `cmdDisp.CMD_NO_OP`). Case is ignored when that is
    still unique. Raises WstsError naming the candidates when it is not."""
    names = list(names)
    if name in names:
        return name
    for fold in (False, True):
        wanted = name.lower() if fold else name
        found = [n for n in names if (n.lower() if fold else n).endswith("." + wanted)
                 or (fold and n.lower() == wanted)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise WstsError(f"'{name}' is ambiguous: {len(found)} {what} match ({', '.join(sorted(found)[:12])}"
                            f"{', ...' if len(found) > 12 else ''}); give more of the name")
    close = [n for n in names if name.lower() in n.lower()]
    raise WstsError(f"no {what[:-1] if what.endswith('s') else what} '{name}' in the dictionary"
                    + (f"; did you mean one of: {', '.join(sorted(close)[:12])}" if close else
                       "; list them with the dictionary tool"))


class Dictionary:
    """The JSON dictionary of a deployment or system, as `dictionary` searches it"""

    KINDS = {"commands": "commands", "events": "events", "telemetry": "telemetryChannels",
             "parameters": "parameters"}

    def __init__(self, path: Path):
        self.path = Path(path)
        content = json.loads(self.path.read_text())
        self.entries = {kind: content.get(key, []) for kind, key in self.KINDS.items()}

    def names(self, kind: str) -> List[str]:
        return [entry["name"] for entry in self.entries[kind]]

    def entry(self, kind: str, name: str) -> dict:
        for entry in self.entries[kind]:
            if entry["name"] == name:
                return entry
        raise KeyError(name)

    @staticmethod
    def _type(spec: dict) -> str:
        if spec.get("kind") == "string":
            return f"string[{spec.get('size', '')}]" if spec.get("size") else "string"
        return str(spec.get("name", spec.get("kind", "?")))

    def describe(self, kind: str, entry: dict) -> dict:
        found: Dict[str, Any] = {"name": entry["name"]}
        params = entry.get("formalParams")
        if params is not None:
            found["args"] = [f"{p['name']}: {self._type(p.get('type', {}))}" for p in params]
        if kind == "commands":
            found["opcode"] = entry.get("opcode")
            found["kind"] = entry.get("commandKind")
        elif kind == "events":
            found["severity"] = entry.get("severity")
            found["format"] = entry.get("format")
        elif kind in ("telemetry", "parameters"):
            found["type"] = self._type(entry.get("type", {}))
        if entry.get("annotation"):
            found["annotation"] = entry["annotation"]
        return found

    def search(self, kind: str, pattern: Optional[str] = None, limit: int = 50) -> dict:
        if kind not in self.KINDS:
            raise WstsError(f"kind is one of {', '.join(self.KINDS)}, not '{kind}'")
        entries = self.entries[kind]
        if pattern:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(pattern), re.IGNORECASE)
            entries = [e for e in entries if regex.search(e["name"]) or regex.search(e.get("annotation") or "")]
        return {"kind": kind, "matches": len(entries), "dictionary": str(self.path),
                "entries": [self.describe(kind, e) for e in entries[:limit]],
                **({"truncated": True} if len(entries) > limit else {})}


# ----------------------------------------------------------------------
# What the GDS receives
# ----------------------------------------------------------------------


class Feed:
    """Events or telemetry samples of the GDS as they arrive, numbered in order of arrival
    (not flight time: an element behind a hub delivers late, and its events must still be
    found after a mark taken before they came). Bounded: the oldest go first."""

    def __init__(self, limit: int = EVENT_LIMIT):
        self._items: "collections.deque" = collections.deque(maxlen=limit)
        self._next = 0
        self._changed = threading.Condition()

    def data_callback(self, data, sender=None) -> None:
        with self._changed:
            self._items.append((self._next, data))
            self._next += 1
            self._changed.notify_all()

    def mark(self) -> int:
        """The sequence number the next item gets"""
        with self._changed:
            return self._next

    def since(self, mark: int) -> List[Any]:
        with self._changed:
            return [data for number, data in self._items if number >= mark]

    def latest(self, predicate: Callable[[Any], bool]) -> Optional[Any]:
        with self._changed:
            for _, data in reversed(self._items):
                if predicate(data):
                    return data
        return None

    def wait(self, predicate: Callable[[Any], bool], mark: int, timeout: float) -> Optional[Any]:
        """The first item from `mark` on that satisfies `predicate`, waiting up to `timeout`"""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for number, data in self._items:
                    if number >= mark and predicate(data):
                        return data
                mark = max(mark, self._next)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._changed.wait(remaining)


def event_dict(event) -> dict:
    """An EventData as a tool shows it"""
    try:
        found = event.get_dict()
    except Exception:
        found = {"name": event.template.get_full_name(), "display_text": str(event)}
    found.pop("raw_time", None)
    # The values, not their types
    found["args"] = _arg_values(event)
    return _json_safe(found)


def _arg_values(event) -> list:
    return [arg.val if hasattr(arg, "val") else arg for arg in (getattr(event, "args", None) or [])]


def sample_dict(sample) -> dict:
    """A ChData as a tool shows it: its value too, as JSON holds it"""
    try:
        found = sample.get_dict()
    except Exception:
        found = {"name": sample.template.get_full_name()}
    found.pop("raw_time", None)
    try:
        found["value"] = sample.get_val()
    except Exception:
        pass
    return _json_safe(found)


def _name_of(data) -> str:
    return data.template.get_full_name()


def _args_match(event, expected: Optional[list]) -> bool:
    """Expected arguments: a value each, compared as text, or None for any"""
    if not expected:
        return True
    values = _arg_values(event)
    for index, wanted in enumerate(expected):
        if wanted is None:
            continue
        if index >= len(values) or str(values[index]) != str(wanted):
            return False
    return True


class GdsLink:
    """A client of a running GDS: commands up, events and telemetry down. Joins the GDS's
    own sockets (`--zmq-transport`) as fprime-gds's test API does."""

    def __init__(self, dictionary: Path, zmq_transport: Optional[List[str]], logs: Path):
        from fprime_gds.common.handlers import DataHandler
        from fprime_gds.executables.cli import ConfigDrivenParser, StandardPipelineParser

        arguments = ["--dictionary", str(dictionary), "--logs", str(logs)]
        if zmq_transport:
            arguments += ["--zmq-transport", *zmq_transport]
        logs.mkdir(parents=True, exist_ok=True)
        parser = StandardPipelineParser()
        namespace, _, _ = ConfigDrivenParser.parse_known_args([StandardPipelineParser], arguments=arguments,
                                                              client=True)
        self.pipeline = parser.pipeline_factory(namespace, None)
        self.events = Feed()
        self.telemetry = Feed()

        class Consumer(DataHandler):  # the pipeline takes only DataHandlers
            def __init__(self, feed: Feed):
                self.feed = feed

            def data_callback(self, data, sender=None):
                self.feed.data_callback(data, sender)

        self.pipeline.coders.register_event_consumer(Consumer(self.events))
        self.pipeline.coders.register_channel_consumer(Consumer(self.telemetry))
        self.connected = time.monotonic()

    def send_command(self, name: str, args: List[Any]) -> None:
        self.pipeline.send_command(name, args)

    def close(self) -> None:
        try:
            self.pipeline.disconnect()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------


class Run:
    """A simulation the server knows: started by it (`process`), or found running"""

    def __init__(self, directory: Path):
        from .system import SYSTEM_FILE

        self.directory = Path(directory).resolve()
        if (self.directory / SYSTEM_FILE).is_file():
            self.description = json.loads((self.directory / SYSTEM_FILE).read_text())
            self.is_system = True
            self.elements = [e["name"] for e in self.description["elements"]]
            self.ground: Optional[str] = self.description.get("ground")
        elif (self.directory / "wsts-report.json").is_file():
            self.description = None
            self.is_system = False
            self.elements = [self.directory.name]
            self.ground = None
        else:
            raise WstsError(f"{self.directory} is not a generated simulation deployment or system "
                            "(no wsts-report.json or wsts-system.json)")
        self.log_dir = self.directory / "wsts-logs"
        self.process: Optional[subprocess.Popen] = None
        self.output: Optional[Path] = None  # stdout (the JSON summary) of a run started here
        self.errors: Optional[Path] = None
        self.started: Optional[float] = None
        self.summary: Optional[dict] = None  # what a run started here printed when it ended
        self.stepped: Optional[bool] = None
        self.client = None  # WstsBridge or WstsSystem
        self.gds: Optional[GdsLink] = None
        self.mark = {"events": 0, "telemetry": 0}  # GDS feeds at the last command or action
        self._dictionary: Optional[Dictionary] = None

    @property
    def name(self) -> str:
        return str(self.directory)

    @property
    def owned(self) -> bool:
        return self.process is not None

    def run_file(self) -> Optional[dict]:
        from .runner import read_run_file

        return read_run_file(self.directory)

    def state(self) -> str:
        if self.process is not None:
            if self.process.poll() is None:
                return "running" if self.run_file() else "starting"
            return "stopped" if self.summary is not None and self.summary.get("interrupted") else "exited"
        return "running" if self.run_file() else "not running"

    def dictionary_path(self) -> Optional[Path]:
        running = self.run_file() or {}
        if running.get("dictionary"):
            return Path(running["dictionary"])
        if self.is_system:
            merged = self.log_dir / f"{self.description['system']}Dictionary.json"
            if merged.is_file():
                return merged
            ground = next(e for e in self.description["elements"] if e["name"] == self.ground)
            directory = self.directory / ground["directory"]
        else:
            directory = self.directory
        found = sorted(directory.glob("build-artifacts/*/*/dict/*Dictionary.json"))
        return found[0] if found else None

    def dictionary(self) -> Dictionary:
        path = self.dictionary_path()
        if path is None or not path.is_file():
            raise WstsError(f"no dictionary for {self.name}: build it first"
                            + (", and start it once with a GDS so the system's dictionary is merged"
                               if self.is_system else ""))
        if self._dictionary is None or self._dictionary.path != path:
            self._dictionary = Dictionary(path)
        return self._dictionary

    def element_log_dir(self, element: Optional[str]) -> Path:
        if not self.is_system:
            return self.log_dir
        return self.log_dir / self.element(element)

    def element(self, element: Optional[str]) -> str:
        if not self.is_system:
            if element not in (None, self.elements[0]):
                raise WstsError(f"{self.name} is a single deployment: leave element out")
            return self.elements[0]
        if element is None:
            raise WstsError(f"{self.name} is a system: give element, one of {', '.join(self.elements)}")
        if element not in self.elements:
            raise WstsError(f"no element '{element}' in {self.name} (elements: {', '.join(self.elements)})")
        return element

    def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        if self.gds is not None:
            self.gds.close()
            self.gds = None


class Session:
    """What one MCP server holds: the runs it knows, and their connections"""

    def __init__(self, cwd: Optional[Path] = None, executable: Optional[List[str]] = None):
        self.cwd = Path(cwd or os.getcwd()).resolve()
        # How `fprime-wsts` is run: this Python, this package (a project that pins another
        # toolkit hands the command on to it, as on the command line)
        self.executable = executable or [sys.executable, "-c",
                                         "import sys; from fprime_wsts.cli import main; sys.exit(main())"]
        self.runs: Dict[str, Run] = {}
        self.workdir = Path(tempfile.mkdtemp(prefix="wsts-mcp-"))
        self._lock = threading.RLock()

    # ---- Finding runs ----

    def _path(self, text: str) -> Path:
        path = Path(os.path.expanduser(text))
        return (path if path.is_absolute() else self.cwd / path).resolve()

    def run(self, target: Optional[str]) -> Run:
        """The run a tool is about: the one named, else the only one known"""
        with self._lock:
            if target is None:
                live = [run for run in self.runs.values() if run.state() in ("running", "starting")]
                candidates = live or list(self.runs.values())
                if len(candidates) == 1:
                    return candidates[0]
                if not candidates:
                    raise WstsError("no run is known yet: start one with run_start, or name the generated "
                                    "directory of one that is running (run=...)")
                raise WstsError("several runs are known; say which with run=: "
                                + ", ".join(run.name for run in candidates))
            path = self._path(target)
            if path.is_file():  # wsts-system.json or wsts-report.json named directly
                path = path.parent
            run = self.runs.get(str(path))
            if run is None:
                run = Run(path)
                self.runs[run.name] = run
            return run

    def _client(self, run: Run):
        """The bridge client of a run: WstsBridge for a deployment, WstsSystem for a system"""
        from .bridge import BridgeError, WstsBridge, WstsSystem

        with self._lock:
            client = run.client
            if client is not None:
                bridges = client.elements.values() if run.is_system else [client]
                if not any(getattr(b, "_closed", False) for b in bridges):
                    return client
                run.disconnect()
            running = run.run_file()
            if running is None:
                raise WstsError(f"{run.name} is not running ({run.state()}): start it with run_start"
                                + (f"; it ended: {self._ending(run)}" if run.summary else ""))
            try:
                if run.is_system:
                    client = WstsSystem(str(run.directory), connect_timeout=5.0)
                else:
                    address = running.get("bridge", "none")
                    if address == "none":
                        raise WstsError(f"{run.name} runs without a bridge (bridge: none in the manifest)")
                    client = WstsBridge(address, connect_timeout=5.0)
            except BridgeError as error:
                raise WstsError(f"cannot reach the bridge of {run.name}: {error}") from None
            run.client = client
            return client

    def _bridge(self, run: Run, element: Optional[str]):
        client = self._client(run)
        if not run.is_system:
            run.element(element)
            return client
        name = run.element(element)
        if name not in client.elements:
            raise WstsError(f"element '{name}' of {run.name} has no bridge (bridge: none)")
        return client.elements[name]

    def _bridges(self, run: Run, element: Optional[str]) -> Dict[str, Any]:
        """{element: bridge} for every element, or the one asked for"""
        client = self._client(run)
        if not run.is_system:
            return {run.element(element): client}
        if element is not None:
            return {run.element(element): self._bridge(run, element)}
        return dict(client.elements)

    def _gds(self, run: Run) -> GdsLink:
        with self._lock:
            if run.gds is not None:
                return run.gds
            running = run.run_file()
            if running is None:
                raise WstsError(f"{run.name} is not running: start it with run_start")
            if "gds_port" not in running:
                raise WstsError(f"{run.name} runs without a GDS: stop it and start it again with gds=true")
            dictionary = run.dictionary_path()
            if dictionary is None:
                raise WstsError(f"no dictionary found for {run.name}")
            try:
                run.gds = GdsLink(dictionary, running.get("zmq_transport"), self.workdir / "gds" / run.directory.name)
            except ImportError:
                raise WstsError("fprime-gds is not installed in this Python (pip install fprime-gds)") from None
            # Something from before this connection is not seen; give the feed a moment to fill
            time.sleep(0.5)
            return run.gds

    def _touch(self, run: Run) -> None:
        """A command or action was sent: wait_event looks back to here"""
        if run.gds is not None:
            run.mark = {"events": run.gds.events.mark(), "telemetry": run.gds.telemetry.mark()}

    def _brief(self, run: Run, element: Optional[str] = None) -> dict:
        """Where each element is now, after an action"""
        found = {}
        try:
            bridges = self._bridges(run, element)
        except WstsError as error:
            return {"note": str(error)}
        for name, bridge in bridges.items():
            try:
                status = bridge.status()
            except Exception as error:  # noqa: BLE001
                found[name] = {"error": str(error)}
                continue
            found[name] = {key: status.get(key) for key in ("tick", "sim_seconds", "paused", "stepped", "lockstep",
                                                            "active_faults", "stuck") if key in status}
        return found if run.is_system else next(iter(found.values()))

    # ---- Lifecycle ----

    def build(self, target: str, element: Optional[List[str]] = None, fresh: bool = False,
              define: Optional[List[str]] = None, timeout: float = 3600) -> dict:
        command = [*self.executable, "build", str(self._path(target)), "--json"]
        for name in element or []:
            command += ["--element", name]
        if fresh:
            command.append("--fresh")
        for value in define or []:
            command += ["-D", value]
        try:
            done = subprocess.run(command, cwd=self.cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise WstsError(f"the build did not finish within {timeout} s") from None
        try:
            result = json.loads(done.stdout)
        except ValueError:
            result = {"output": done.stdout[-4000:]}
        result["exit_code"] = done.returncode
        if done.stderr.strip():
            result["stderr"] = done.stderr.strip()[-4000:]
        if done.returncode != 0:
            result["hint"] = "tail_log(log='build') shows the build log of the element that failed"
        return result

    def run_start(self, directory: str, gds: bool = True, stepped: Optional[bool] = None,
                  duration: Optional[float] = None, scenario: Optional[List[str]] = None,
                  record: Optional[List[str]] = None, env: Optional[List[str]] = None,
                  gds_gui: bool = False, timeout: Optional[float] = None, journal: bool = False,
                  from_snapshot: Optional[str] = None) -> dict:
        if from_snapshot:
            from . import snapshots

            snapshot = snapshots.load(self._path(from_snapshot))
            directory = directory or str(snapshot.directory)
        if timeout is None:
            timeout = RESTORE_TIMEOUT if from_snapshot else START_TIMEOUT
        run = self.run(directory)
        with self._lock:
            if run.state() in ("running", "starting"):
                raise WstsError(f"{run.name} is running already ({'started here' if run.owned else 'started elsewhere'})"
                                ": act on it, or run_stop it first")
            run.disconnect()
            command = [*self.executable, "run", run.name, "--auto-ports", "--json"]
            if gds_gui:
                command.append("--gds-gui")
            elif gds:
                command.append("--gds")
            if stepped is not None:
                command.append("--stepped" if stepped else "--realtime")
            if duration is not None:
                command += ["--duration", str(duration)]
            for value in scenario or []:
                command += ["--scenario", value]
            for value in record or []:
                command += ["--record", value]
            for value in env or []:
                command += ["--env", value]
            if journal:
                command.append("--journal")
            if from_snapshot:
                command += ["--from", str(self._path(from_snapshot))]
            stamp = time.strftime("%Y%m%d-%H%M%S")
            run.output = self.workdir / f"{run.directory.name}-{stamp}.json"
            run.errors = self.workdir / f"{run.directory.name}-{stamp}.log"
            run.summary = None
            run.stepped = stepped
            with open(run.output, "w") as out, open(run.errors, "w") as err:
                run.process = subprocess.Popen(command, cwd=self.cwd, stdout=out, stderr=err,
                                               stdin=subprocess.DEVNULL, start_new_session=True)
            run.started = time.monotonic()
        deadline = time.monotonic() + timeout
        while True:
            running = run.run_file()
            if running is not None and int(running.get("pid", -1)) == run.process.pid:
                break
            if run.process.poll() is not None:
                self._collect(run)
                raise WstsError(f"the run did not come up: {self._ending(run)}")
            if time.monotonic() >= deadline:
                self._stop_process(run)
                raise WstsError(f"the run did not come up within {timeout} s; see {run.errors}")
            time.sleep(0.25)
        return {"run": run.name, "state": run.state(), "pid": run.process.pid, "logs": str(run.log_dir),
                "elements": run.elements, **{k: v for k, v in running.items() if k != "pid"},
                "status": self._brief(run)}

    def _collect(self, run: Run) -> None:
        """What a run started here printed when it ended"""
        if run.output is None or run.summary is not None:
            return
        try:
            run.summary = json.loads(run.output.read_text())
        except (OSError, ValueError):
            run.summary = {}
        if run.errors is not None:
            try:
                said = run.errors.read_text(errors="replace").strip()
            except OSError:
                said = ""
            if said:
                run.summary["stderr"] = said[-4000:]
        if run.process is not None:
            run.summary["exit_code"] = run.process.returncode

    def _ending(self, run: Run) -> str:
        summary = run.summary or {}
        if summary.get("stderr"):
            return summary["stderr"].splitlines()[-1]
        return f"exit code {summary.get('exit_code')}"

    def _stop_process(self, run: Run, timeout: float = STOP_TIMEOUT) -> None:
        process = run.process
        if process is None or process.poll() is not None:
            return
        # `fprime-wsts run` stops in order on an interrupt: the deployment, the GDS, the models
        try:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def snapshot(self, path: str, target: Optional[str] = None, resume: Optional[bool] = None) -> dict:
        """Snapshot a run that keeps a journal (docs/snapshots.md)"""
        from . import snapshots

        run = self.run(target)
        return snapshots.take(run.directory, self._path(path), resume=resume).describe()

    def run_stop(self, target: Optional[str] = None, force: bool = False, timeout: float = STOP_TIMEOUT) -> dict:
        run = self.run(target)
        run.disconnect()
        if run.owned:
            self._stop_process(run, timeout)
            self._collect(run)
            return {"run": run.name, "state": run.state(), "summary": run.summary}
        running = run.run_file()
        if running is None:
            return {"run": run.name, "state": run.state(), "note": "it was not running"}
        # Started elsewhere: by `fprime-wsts run`, which takes SIGTERM as an interrupt and stops
        # in order, or by a test session, which the signal would end as a whole
        pid = int(running["pid"])
        if not force:
            raise WstsError(f"{run.name} was started elsewhere, by process {pid} (a `fprime-wsts run`, or a test "
                            "session that the signal would end): stop it there, or pass force=true to send that "
                            "process SIGTERM")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while run.run_file() is not None and time.monotonic() < deadline:
            time.sleep(0.25)
        return {"run": run.name, "state": run.state(),
                "note": "started elsewhere: its summary went to wherever it was started"}

    def runs_list(self) -> dict:
        found = []
        for run in list(self.runs.values()):
            if run.process is not None and run.process.poll() is not None:
                self._collect(run)
            entry: Dict[str, Any] = {"run": run.name, "kind": "system" if run.is_system else "deployment",
                                     "elements": run.elements, "state": run.state(),
                                     "started_here": run.owned}
            running = run.run_file()
            if running:
                entry.update({k: v for k, v in running.items() if k != "pid"})
            if run.owned and run.started is not None and run.state() == "running":
                entry["seconds"] = round(time.monotonic() - run.started, 1)
            if run.summary is not None:
                entry["summary"] = run.summary
            found.append(entry)
        return {"runs": found}

    # ---- The simulation ----

    def status(self, target: Optional[str] = None, element: Optional[str] = None, bus: bool = False) -> dict:
        run = self.run(target)
        found = {}
        for name, bridge in self._bridges(run, element).items():
            status = bridge.status()
            status.pop("id", None)
            status.pop("ok", None)
            if bus:
                status["bus_traffic"] = bridge.bus_stats()
            if status.get("stepped"):
                try:
                    status["tick_times"] = bridge.tick_times()
                except BridgeError:
                    pass  # a deployment built with an older toolkit
            found[name] = status
        return {"run": run.name, "state": run.state(),
                **({"elements": found} if run.is_system else {"status": next(iter(found.values()))})}

    def pause(self, target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        for bridge in self._bridges(run, element).values():
            bridge.pause()
        return {"status": self._brief(run, element)}

    def resume(self, target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        for bridge in self._bridges(run, element).values():
            bridge.resume()
        return {"status": self._brief(run, element)}

    def set_rate(self, rate: float, target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        for bridge in self._bridges(run, element).values():
            bridge.set_rate(rate)
        return {"status": self._brief(run, element)}

    def step(self, ticks: int = 1, target: Optional[str] = None, element: Optional[str] = None,
             timeout: float = 60.0) -> dict:
        run = self.run(target)
        if run.is_system and element is None:
            raise WstsError("the elements of a system keep their own ticks: give element, one of "
                            + ", ".join(run.elements))
        bridge = self._bridge(run, element)
        if bridge.status().get("lockstep"):
            raise WstsError("this system runs in lockstep, conducted by its `fprime-wsts run`: pause and resume "
                            "it, or start it in real time or with plain stepped time to step one element")
        self._touch(run)
        tick = bridge.step(int(ticks), timeout=timeout)
        return {"tick": tick, "status": self._brief(run, element)}

    def wait_quiescent(self, target: Optional[str] = None, element: Optional[str] = None,
                       timeout: float = 5.0) -> dict:
        run = self.run(target)
        for bridge in self._bridges(run, element).values():
            bridge.wait_quiescent(timeout)
        return {"quiescent": True, "status": self._brief(run, element)}

    def _fault_target(self, run: Run, endpoint: str, element: Optional[str], direction: str):
        """(bridge, endpoint, direction): a link name or "<element>.<endpoint>" on a system"""
        if run.is_system and element is None:
            client = self._client(run)
            name, endpoint, direction = client.resolve(endpoint, direction)
            return client[name], endpoint, direction
        return self._bridge(run, element), endpoint, direction

    def fault_set(self, endpoint: str, kind: str, arg: int = 0, count: int = 0, ticks: int = 0,
                  direction: str = "both", target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        bridge, endpoint, direction = self._fault_target(run, endpoint, element, direction)
        self._touch(run)
        bridge.fault_set(endpoint, kind, arg=arg, count=count, ticks=ticks, direction=direction)
        return {"armed": {"endpoint": endpoint, "kind": kind, "arg": arg, "count": count, "ticks": ticks,
                          "direction": direction}, "status": self._brief(run)}

    def fault_clear(self, endpoint: str = "*", target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        if endpoint == "*":
            for bridge in self._bridges(run, element).values():
                bridge.fault_clear("*")
        else:
            bridge, endpoint, _ = self._fault_target(run, endpoint, element, "both")
            bridge.fault_clear(endpoint)
        return {"cleared": endpoint, "status": self._brief(run)}

    def inject(self, endpoint: str, text: Optional[str] = None, hex: Optional[str] = None,
               target: Optional[str] = None, element: Optional[str] = None, packet: bool = False) -> dict:
        if (text is None) == (hex is None):
            raise WstsError("give either text or hex")
        data = text.encode("utf-8") if text is not None else bytes.fromhex(hex.replace(" ", ""))
        run = self.run(target)
        self._touch(run)
        bridge = self._bridge(run, element)
        if packet:
            bridge.inject_packet(endpoint, data)
        else:
            bridge.inject_bytes(endpoint, data)
        return {"injected": len(data), "endpoint": endpoint, **({"packet": True} if packet else {})}

    def gpio(self, endpoint: str, level: Optional[bool] = None, target: Optional[str] = None,
             element: Optional[str] = None) -> dict:
        run = self.run(target)
        bridge = self._bridge(run, element)
        if level is None:
            return {"endpoint": endpoint, "level": bridge.gpio_get(endpoint)}
        self._touch(run)
        bridge.gpio_force(endpoint, bool(level))
        return {"endpoint": endpoint, "forced": bool(level)}

    def register_set(self, endpoint: str, offset: int, value: int, i2c_address: Optional[int] = None,
                     target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        bridge = self._bridge(run, element)
        self._touch(run)
        if i2c_address is not None:
            bridge.i2c_reg_set(endpoint, int(i2c_address), int(offset), int(value))
        else:
            bridge.reg_set(endpoint, int(offset), int(value))
        return {"endpoint": endpoint, "offset": hex(int(offset)), "value": hex(int(value)),
                **({"i2c_address": hex(int(i2c_address))} if i2c_address is not None else {})}

    def raise_irq(self, endpoint: str, number: int = 0, target: Optional[str] = None,
                  element: Optional[str] = None) -> dict:
        run = self.run(target)
        self._touch(run)
        self._bridge(run, element).raise_irq(endpoint, int(number))
        return {"endpoint": endpoint, "irq": int(number)}

    def model_set(self, model: str, key: str, value: str, target: Optional[str] = None,
                  element: Optional[str] = None) -> dict:
        run = self.run(target)
        self._touch(run)
        self._bridge(run, element).model_set(model, key, value)
        return {"model": model, key: value}

    def scenario_run(self, path: str, mode: str = "stimulus", wait: bool = True, timeout: float = 120.0,
                     target: Optional[str] = None, element: Optional[str] = None) -> dict:
        run = self.run(target)
        bridge = self._bridge(run, element)
        file = str(self._path(path))
        self._touch(run)
        if wait:
            bridge.run_scenario(file, mode, timeout=timeout)
        else:
            bridge.scenario_load(file, mode)
            bridge.scenario_start()
        return {"scenario": file, "completed": wait, "status": self._brief(run, element)}

    def action(self, op: str, fields: Optional[dict] = None, target: Optional[str] = None,
               element: Optional[str] = None) -> dict:
        from .cli import SYSTEM_WIDE

        run = self.run(target)
        fields = dict(fields or {})
        for key in ("path",):
            if isinstance(fields.get(key), str):
                fields[key] = str(self._path(fields[key]))
        if run.is_system and element is None and op not in SYSTEM_WIDE:
            raise WstsError(f"'{op}' goes to one element of a system: give element, one of {', '.join(run.elements)}")
        self._touch(run)
        answers = {}
        for name, bridge in self._bridges(run, element).items():
            answer = bridge.request(op, **fields)
            answer.pop("id", None)
            answers[name] = answer
        return answers if len(answers) > 1 else next(iter(answers.values()))

    def bus_records(self, target: Optional[str] = None, element: Optional[str] = None,
                    endpoint: Optional[str] = None, kinds: Optional[List[str]] = None,
                    limit: int = 50, clear: bool = False) -> dict:
        run = self.run(target)
        bridge = self._bridge(run, element)
        subscribed = getattr(bridge, "_wsts_mcp_subscribed", False)
        if not subscribed:
            bridge.subscribe()
            bridge._wsts_mcp_subscribed = True
        records = bridge.events(clear=clear)
        bridge.trim_events(RECORD_LIMIT)
        found = [r for r in records
                 if (endpoint is None or r.get("endpoint") == endpoint)
                 and (not kinds or r.get("rec", r.get("ev")) in kinds)]
        return {"total": len(found), "records": found[-limit:],
                **({} if subscribed else {"note": "subscribed now: records are kept from this call on; call again "
                                                  "after the traffic you want to see"})}

    # ---- The GDS ----

    def dictionary(self, kind: str = "commands", search: Optional[str] = None, limit: int = 50,
                   target: Optional[str] = None) -> dict:
        return self.run(target).dictionary().search(kind, search, limit)

    def send_command(self, command: str, args: Optional[List[Any]] = None, wait: bool = True,
                     timeout: float = 10.0, target: Optional[str] = None) -> dict:
        run = self.run(target)
        dictionary = run.dictionary()
        name = resolve_name(command, dictionary.names("commands"), "commands")
        entry = dictionary.entry("commands", name)
        args = list(args or [])
        params = entry.get("formalParams", [])
        if len(args) != len(params):
            raise WstsError(f"{name} takes {len(params)} argument(s) "
                            f"({', '.join(dictionary.describe('commands', entry).get('args', [])) or 'none'}), "
                            f"{len(args)} given")
        gds = self._gds(run)
        self._touch(run)
        mark = gds.events.mark()
        # The GDS takes text, numbers and booleans as they are, and arrays and structs as JSON text
        values = [json.dumps(a) if isinstance(a, (list, dict)) else a for a in args]
        try:
            gds.send_command(name, values)
        except Exception as error:  # noqa: BLE001 - a value the GDS cannot encode
            raise WstsError(f"{name} was not sent: {error}") from None
        result: Dict[str, Any] = {"command": name, "opcode": entry.get("opcode"), "args": args}
        if not wait:
            result["result"] = "sent"
            return result
        opcode = entry.get("opcode")

        def done(event) -> bool:
            if not _name_of(event).endswith((".OpCodeCompleted", ".OpCodeError")):
                return False
            values = _arg_values(event)
            return bool(values) and str(values[0]) == str(opcode)

        ending = gds.events.wait(done, mark, timeout)
        seen = [event_dict(e) for e in gds.events.since(mark)][:30]
        if ending is None:
            result["result"] = "timeout"
            result["note"] = (f"no OpCodeCompleted or OpCodeError for opcode {opcode} within {timeout} s. A command "
                              "for an element behind a hub travels through it; is the run paused?")
        elif _name_of(ending).endswith(".OpCodeError"):
            result["result"] = "error"
            result["error"] = event_dict(ending).get("display_text")
        else:
            result["result"] = "completed"
        result["events"] = seen
        return result

    def wait_event(self, event: str, args: Optional[List[Any]] = None, since: str = "last_action",
                   timeout: float = 10.0, target: Optional[str] = None) -> dict:
        run = self.run(target)
        name = resolve_name(event, run.dictionary().names("events"), "events")
        gds = self._gds(run)
        mark = {"last_action": run.mark["events"], "now": gds.events.mark(), "all": 0}.get(since)
        if mark is None:
            raise WstsError("since is one of last_action, now, all")
        found = gds.events.wait(lambda e: _name_of(e) == name and _args_match(e, args), mark, timeout)
        if found is None:
            recent = [event_dict(e) for e in gds.events.since(mark)][-10:]
            return {"event": name, "found": False, "timeout": timeout, "recent_events": recent}
        return {"event": name, "found": True, **event_dict(found)}

    def recent_events(self, limit: int = 20, search: Optional[str] = None, severity: Optional[str] = None,
                      target: Optional[str] = None) -> dict:
        run = self.run(target)
        gds = self._gds(run)
        events = [event_dict(e) for e in gds.events.since(0)]
        if search:
            regex = re.compile(search, re.IGNORECASE)
            events = [e for e in events if regex.search(e.get("name", "")) or regex.search(str(e.get("display_text")))]
        if severity:
            events = [e for e in events if severity.upper() in str(e.get("severity", "")).upper()]
        return {"total": len(events), "events": events[-limit:]}

    def read_telemetry(self, channels: List[str], fresh: bool = True, timeout: float = 5.0,
                       target: Optional[str] = None) -> dict:
        run = self.run(target)
        names = run.dictionary().names("telemetry")
        wanted = [resolve_name(channel, names, "channels") for channel in channels]
        gds = self._gds(run)
        mark = gds.telemetry.mark()
        deadline = time.monotonic() + timeout
        found: Dict[str, Any] = {}
        for name in wanted:
            sample = None
            if not fresh:
                sample = gds.telemetry.latest(lambda s, n=name: _name_of(s) == n)
            if sample is None:
                sample = gds.telemetry.wait(lambda s, n=name: _name_of(s) == n, mark,
                                            max(0.0, deadline - time.monotonic()))
            if sample is None:
                last = gds.telemetry.latest(lambda s, n=name: _name_of(s) == n)
                found[name] = {"fresh": False, "note": f"no sample within {timeout} s"
                               + (" (is the run paused?)" if last is not None else
                                  " and none before: the channel may not be downlinked"),
                               **({"last": sample_dict(last)} if last is not None else {})}
            else:
                found[name] = sample_dict(sample)
        return {"channels": found}

    # ---- Diagnosis ----

    LOGS = {"sim": "sim.log", "gds": "gds.log", "models": "models.log", "build": "build.log"}

    def tail_log(self, log: str = "sim", target: Optional[str] = None, element: Optional[str] = None,
                 lines: int = 60, grep: Optional[str] = None) -> dict:
        run = self.run(target)
        if log == "run":
            if run.errors is None:
                raise WstsError("log 'run' is what a run started by this server said; this one was started elsewhere")
            path = run.errors
        elif log in self.LOGS:
            if log == "gds" and run.is_system and element is None:
                element = run.ground
            path = run.element_log_dir(element) / self.LOGS[log]
        else:
            raise WstsError(f"log is one of {', '.join([*self.LOGS, 'run'])}")
        if not path.is_file():
            raise WstsError(f"{path} does not exist (yet)")
        text = path.read_text(errors="replace").splitlines()
        if grep:
            regex = re.compile(grep, re.IGNORECASE)
            text = [line for line in text if regex.search(line)]
        return {"path": str(path), "lines": len(text), "tail": "\n".join(text[-lines:])}

    def last_assert(self, target: Optional[str] = None, element: Optional[str] = None) -> dict:
        from .runner import explain_assert

        run = self.run(target)
        names = [run.element(element)] if element is not None or not run.is_system else run.elements
        found = {}
        for name in names:
            log = run.element_log_dir(name if run.is_system else None) / "sim.log"
            if not log.is_file():
                found[name] = None
                continue
            directory = run.directory
            if run.is_system:
                directory = run.directory / next(e["directory"] for e in run.description["elements"]
                                                 if e["name"] == name)
            found[name] = explain_assert(directory, log.read_text(errors="replace"))
        if not run.is_system:
            return {"assert": found[run.elements[0]]}
        return {"asserts": found}

    # ---- Ending ----

    def close(self) -> None:
        """Stop the runs this server started, and drop every connection"""
        for run in list(self.runs.values()):
            run.disconnect()
            if run.owned:
                self._stop_process(run)
        shutil.rmtree(self.workdir / "gds", ignore_errors=True)


# ----------------------------------------------------------------------
# The MCP server
# ----------------------------------------------------------------------


def _tools(session: Session) -> List[Callable]:
    """The tools, as plain functions whose signatures and docstrings make their schemas"""
    s = session

    def build(target: str, element: Optional[List[str]] = None, fresh: bool = False,
              define: Optional[List[str]] = None) -> dict:
        """Configure and build a generated deployment or system directory (or generate and build from a
        manifest): `fprime-wsts build --json`. element: build only these elements of a system. fresh:
        configure again from nothing. define: CMake options NAME=VALUE. Takes minutes the first time."""
        return s.build(target, element, fresh, define)

    def run_start(directory: str = "", gds: bool = True, stepped: Optional[bool] = None,
                  duration: Optional[float] = None, scenario: Optional[List[str]] = None,
                  record: Optional[List[str]] = None, env: Optional[List[str]] = None,
                  gds_gui: bool = False, journal: bool = False, from_snapshot: Optional[str] = None) -> dict:
        """Start a built simulation (a generated deployment or system directory) on free ports, with a
        headless GDS unless gds=false, and return once it is up. It runs until run_stop, until duration
        seconds have passed, or until this server exits. stepped: true for stepped time (lockstep for a
        system), false for real time, omitted for the manifest's choice. scenario/record/env take the
        values of `fprime-wsts run --scenario/--record/--env` ([element=]file, NAME=VALUE). gds_gui: also
        serve the GDS web UI (its URL is returned). journal: keep a journal of the run's inputs (stepped
        time), so that it can be snapshotted. from_snapshot: start from a snapshot instead (directory may
        then be omitted): its journal replays and is checked, and the run is held paused at its tick."""
        return s.run_start(directory, gds, stepped, duration, scenario, record, env, gds_gui,
                           journal=journal, from_snapshot=from_snapshot)

    def snapshot(path: str, run: Optional[str] = None, resume: Optional[bool] = None) -> dict:
        """Snapshot a run started with journal=true (and stepped time; a system in lockstep, as a whole): it is
        paused between two ticks and its journal so far saved at path, a new directory. resume: let it go on
        afterwards (default: if it was running). Branch runs from it with run_start(from_snapshot=path)."""
        return s.snapshot(path, run, resume)

    def run_stop(run: Optional[str] = None, force: bool = False) -> dict:
        """Stop a run in order (deployment, GDS, models) and return its summary: exit codes, final ticks,
        bus counters, and the F Prime assert it stopped on, if any. A run started elsewhere is stopped only
        with force=true, which sends the process that started it SIGTERM."""
        return s.run_stop(run, force)

    def runs_list() -> dict:
        """The runs this server knows: started here or attached to, their state, addresses, and the
        summary of those that ended."""
        return s.runs_list()

    def status(run: Optional[str] = None, element: Optional[str] = None, bus: bool = False) -> dict:
        """Where a run is: tick, simulated seconds, paused, stepped, faults, scenario, recording, links,
        endpoint names. bus=true adds the traffic of each endpoint, with what nothing answered (a missing
        device model or a wrong address). In stepped time, tick_times: the slowest ticks in host time and
        over_period, how many took longer than the tick period (a hint at what could overrun on the target)."""
        return s.status(run, element, bus)

    def pause(run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Pause the simulation (every element of a system unless element is given). Downlink stops too."""
        return s.pause(run, element)

    def resume(run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Resume the simulation (every element of a system unless element is given)."""
        return s.resume(run, element)

    def set_rate(rate: float, run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Simulated time per wall-clock time (1 is real time). In stepped time a limit; 0 removes it."""
        return s.set_rate(rate, run, element)

    def step(ticks: int = 1, run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Run exactly `ticks` ticks from a paused simulation, then pause again, and wait until the work
        they caused has finished. On a system, give element."""
        return s.step(ticks, run, element)

    def wait_quiescent(run: Optional[str] = None, element: Optional[str] = None, timeout: float = 5.0) -> dict:
        """Wait until flight software has finished all the work it was given (while paused); on timeout the
        error names the queues that still hold work."""
        return s.wait_quiescent(run, element, timeout)

    def fault_set(endpoint: str, kind: str, arg: int = 0, count: int = 0, ticks: int = 0,
                  direction: str = "both", run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Arm a fault on a bus endpoint. kind: drop, corrupt, delay, duplicate, error, stuck; arg is its
        parameter (an error status, a delay in ticks ...); count: transactions it hits (0: no limit);
        ticks: how long it lasts (0: until cleared); direction: both, to_device, to_flight. On a system,
        endpoint may be a link name (direction a_to_b, b_to_a or both) or "<element>.<endpoint>"."""
        return s.fault_set(endpoint, kind, arg, count, ticks, direction, run, element)

    def fault_clear(endpoint: str = "*", run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Clear the faults of an endpoint or link; "*" clears every fault (of every element)."""
        return s.fault_clear(endpoint, run, element)

    def inject(endpoint: str, text: Optional[str] = None, hex: Optional[str] = None,
               run: Optional[str] = None, element: Optional[str] = None, packet: bool = False) -> dict:
        """Deliver bytes to flight software on a byte stream endpoint, as if the device had sent them:
        text (UTF-8) or hex. packet=true: on a packet (SpaceWire) endpoint, the bytes as one whole packet,
        as if a node had sent it."""
        return s.inject(endpoint, text, hex, run, element, packet)

    def gpio(endpoint: str, level: Optional[bool] = None, run: Optional[str] = None,
             element: Optional[str] = None) -> dict:
        """Drive a GPIO input to level, or without level read the level flight software last wrote."""
        return s.gpio(endpoint, level, run, element)

    def register_set(endpoint: str, offset: int, value: int, i2c_address: Optional[int] = None,
                     run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Set a register without a flight write: of the register block on an endpoint, or with
        i2c_address of the I2C register map device at that address (offset is then the register)."""
        return s.register_set(endpoint, offset, value, i2c_address, run, element)

    def raise_irq(endpoint: str, number: int = 0, run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Raise an interrupt of the register block on an endpoint."""
        return s.raise_irq(endpoint, number, run, element)

    def model_set(model: str, key: str, value: str, run: Optional[str] = None,
                  element: Optional[str] = None) -> dict:
        """Set a parameter of a device model (a number, or a signal such as 'sine(offset=25, amplitude=5,
        period=60)')."""
        return s.model_set(model, key, value, run, element)

    def scenario_run(path: str, mode: str = "stimulus", wait: bool = True, timeout: float = 120.0,
                     run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """Play a scenario file (JSON lines of actions keyed on ticks) on a running simulation; with wait,
        return when it has completed. mode: stimulus, or responses to replay a recording's answers."""
        return s.scenario_run(path, mode, wait, timeout, run, element)

    def action(op: str, fields: Optional[dict] = None, run: Optional[str] = None,
               element: Optional[str] = None) -> dict:
        """Any bridge action with its fields, as in scenario files and `fprime-wsts ctl` (docs/reference.md,
        'Actions'): record_start {path}, record_stop, scenario_load, scenario_start, scenario_stop,
        set_stepped {stepped}, log {text}, bus_stats ..."""
        return s.action(op, fields, run, element)

    def bus_records(run: Optional[str] = None, element: Optional[str] = None, endpoint: Optional[str] = None,
                    kinds: Optional[List[str]] = None, limit: int = 50, clear: bool = False) -> dict:
        """Bus records (what flight software sent and received on each endpoint: rec bs_tx, bs_rx, i2c,
        spi, gpio_out, reg_rd, reg_wr ...) and simulation events, since the first call (which subscribes).
        Filter by endpoint and kinds; clear empties the buffer after reading."""
        return s.bus_records(run, element, endpoint, kinds, limit, clear)

    def dictionary(kind: str = "commands", search: Optional[str] = None, limit: int = 50,
                   run: Optional[str] = None) -> dict:
        """Search the flight dictionary: kind commands, events, telemetry or parameters; search is a regular
        expression over names and annotations. Commands show their arguments and opcode."""
        return s.dictionary(kind, search, limit, run)

    def send_command(command: str, args: Optional[List[Any]] = None, wait: bool = True, timeout: float = 10.0,
                     run: Optional[str] = None) -> dict:
        """Send a flight command through the GDS. command: its full name or a unique end of it
        (NO_OP is ambiguous on a system; Payload.cmdDisp.CMD_NO_OP is not). args: one value per argument,
        enums by name. With wait, return once the dispatcher reports it completed or failed, with the
        events seen meanwhile."""
        return s.send_command(command, args, wait, timeout, run)

    def wait_event(event: str, args: Optional[List[Any]] = None, since: str = "last_action",
                   timeout: float = 10.0, run: Optional[str] = None) -> dict:
        """Wait for a flight event, by name (or a unique end of it) and optionally argument values (null for
        any). since: last_action (the default: from the last command or action this server sent, so
        late events through a hub are found), now, or all."""
        return s.wait_event(event, args, since, timeout, run)

    def recent_events(limit: int = 20, search: Optional[str] = None, severity: Optional[str] = None,
                      run: Optional[str] = None) -> dict:
        """The latest flight events the GDS received since this server connected to it; search is a regular
        expression over names and text, severity e.g. WARNING_HI."""
        return s.recent_events(limit, search, severity, run)

    def read_telemetry(channels: List[str], fresh: bool = True, timeout: float = 5.0,
                       run: Optional[str] = None) -> dict:
        """Read telemetry channels (full names or unique ends of them). fresh (default): wait for a sample
        downlinked after this call, about a second away; fresh=false: the latest one received."""
        return s.read_telemetry(channels, fresh, timeout, run)

    def tail_log(log: str = "sim", run: Optional[str] = None, element: Optional[str] = None,
                 lines: int = 60, grep: Optional[str] = None) -> dict:
        """The end of a log: sim (the deployment's output), gds, models (Python device models), build, or
        run (what `fprime-wsts run` said, for a run started here). grep: keep matching lines only."""
        return s.tail_log(log, run, element, lines, grep)

    def last_assert(run: Optional[str] = None, element: Optional[str] = None) -> dict:
        """The F Prime assert a deployment's log ends with, if any: file, line, the instances that use that
        file, and which kept instance to substitute with a surrogate."""
        return s.last_assert(run, element)

    return [build, run_start, run_stop, runs_list, snapshot, status, pause, resume, set_rate, step, wait_quiescent,
            fault_set, fault_clear, inject, gpio, register_set, raise_irq, model_set, scenario_run, action,
            bus_records, dictionary, send_command, wait_event, recent_events, read_telemetry, tail_log,
            last_assert]


def answer(function: Callable, *args, **kwargs) -> dict:
    """Call a tool; what an agent can act on comes back as {"error": ...}"""
    from .bridge import BridgeError

    try:
        return _json_safe(function(*args, **kwargs))
    except (WstsError, BridgeError, KeyError, ValueError, OSError) as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args else str(error)
        return {"error": str(message)}


def create_server(session: Session):
    """The MCP server for a session (FastMCP, from the `mcp` package)"""
    import functools

    import anyio
    from mcp.server.fastmcp import FastMCP

    # Logs go to stderr, which the client shows or keeps; only what needs attention
    server = FastMCP("fprime-wsts", instructions=INSTRUCTIONS, log_level="WARNING")
    for tool in _tools(session):
        # Bridge and GDS calls block: off the event loop, so that the server keeps answering
        def bind(function):
            @functools.wraps(function)
            async def call(*args, **kwargs):
                return await anyio.to_thread.run_sync(functools.partial(answer, function, *args, **kwargs))
            return call
        server.add_tool(bind(tool), name=tool.__name__, description=tool.__doc__)
    return server


def _mcp_version() -> Optional[str]:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mcp")
    except PackageNotFoundError:
        return None


def check_mcp_version(installed: Optional[str]) -> None:
    """The server is written for mcp 1.x: 2.0 renamed FastMCP to MCPServer and changed other
    APIs (docs/user-guide/agents.md, "The MCP server")"""
    if installed and installed.split(".")[0].isdigit() and int(installed.split(".")[0]) >= 2:
        raise WstsError(f"the MCP server needs mcp 1.x, and mcp {installed} is installed: 2.0 renamed FastMCP, which "
                        "the server is built on, and changed other APIs. pip install 'mcp>=1.2,<2', or "
                        "pip install -e './python[mcp]', which pins it")


def serve(cwd: Optional[Path] = None) -> int:
    """Run the server on stdio until the client goes away"""
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise WstsError("the MCP server needs the mcp package: pip install 'mcp>=1.2,<2' (Python 3.10 or later), "
                        "or pip install -e './python[mcp]'") from None
    check_mcp_version(_mcp_version())
    import logging

    session = Session(cwd)
    server = create_server(session)
    # The SDK and the GDS client log progress at INFO on stderr: only what needs attention
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    from .runner import terminate_as_interrupt

    try:
        # SIGTERM and SIGHUP too: the runs started here are in sessions of their own, and are
        # stopped below rather than left behind
        with terminate_as_interrupt():
            server.run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
        shutil.rmtree(session.workdir, ignore_errors=True)
    # The GDS client leaves threads behind that would hold the process open
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):  # closed by the client going away
            pass
    os._exit(0)
