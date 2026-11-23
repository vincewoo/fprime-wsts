"""NASA GSFC's 42 as a plant (docs/dynamics.md, phase 4).

42 (github.com/ericstoneking/42) simulates spacecraft attitude and orbit dynamics, the
environment, sensors and actuators. Its socket interface (`Inp_IPC.txt`) exchanges text
messages: a `TIME` line, then one line per variable (`SC[0].qn = [q0 q1 q2 q3]`,
`SC[0].Whl[0].H = 0.1`), then `[ENDMSG]`; the receiving side answers each message with the 4
bytes `Ack\\0`. In `TXRX` mode with blocking, 42 sends its state after every step of its own
(`DTSIM`) and then waits for a message back, whose lines it applies before its next step: the
peer paces it, one step per exchange.

The `fortytwo` kind of plant is that peer. It:

- checks the case (`Inp_Sim.txt`: FAST time, no graphics; `Inp_IPC.txt`: one `TXRX` socket, a
  server, blocking, the others `OFF`), and copies it into the run's log directory, with a free
  port and echo off, so that runs do not collide and 42's output files land in the log;
- starts 42 in its own directory (where its `Model/` and `World/` are), on the copy;
- reads 42's first message (its state after its first step, before any input) when the plant
  starts, and then, at each exchange, sends the inputs, takes as many steps of 42's as the
  exchange covers (a whole number of `DTSIM`), and gives back the outputs of the last;
- checks that 42's `TIME` advances by `DTSIM` at every step.

Outputs are 42's variables, by the name it writes them with, and an index for a component of
a vector: `SC[0].svb[0]`, `SC[0].Whl[0].H`. Inputs are written the same way: the components of
a vector not given are those 42 last sent (0 if it sends none). 42 applies only the lines its
`TxRxIPC.c` reads: in a stock 42, state (`SC[0].B[0].wn`, `SC[0].qn`, `SC[0].Whl[0].H`, ...);
actuator commands need 42's IPC generated with them (its `Database`).
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .dynamics import DynamicsError
from .plant_socket import free_port

CONNECT_LIMIT = 60.0  # 42 reads its models and ephemerides before it listens
ANSWER_LIMIT = 60.0
LINE = re.compile(r"^(\S+) = (\[[^\]]*\]|\S+)$")
#: Quaternions in 42's names (qn, qrn, qrl, ...): four components; other vectors three
QUATERNION = re.compile(r"\.q[a-z]*$")
INDEXED = re.compile(r"^(.*)\[(\d+)\]$")


class FortyTwoError(DynamicsError):
    """A case of 42 that cannot be coupled, or 42 not doing what the coupling needs"""


@dataclass
class Case:
    """What the coupling needs from a case of 42"""
    path: Path
    time_mode: str
    dtsim: float
    graphics: bool
    host: str
    port: int
    socket_line: int  # the line of Inp_IPC.txt with the TXRX socket's host and port
    echo_line: int
    prefixes: List[str] = field(default_factory=list)


def _lines(path: Path) -> List[str]:
    try:
        return path.read_text().splitlines()
    except OSError as error:
        raise FortyTwoError(f"{path}: {error}") from None


def _value(line: str) -> str:
    """A line of 42's input files without its comment (after !)"""
    return line.split("!", 1)[0].strip()


def read_case(path: Path) -> Case:
    """Read and check a case's Inp_Sim.txt and Inp_IPC.txt"""
    sim = _lines(path / "Inp_Sim.txt")
    try:
        time_mode = _value(sim[2]).split()[0]
        duration, dtsim = (float(x) for x in _value(sim[3]).split()[:2])
        graphics = _value(sim[6]).split()[0].upper() == "TRUE"
    except (IndexError, ValueError):
        raise FortyTwoError(f"{path / 'Inp_Sim.txt'}: not 42's Inp_Sim.txt (time mode on line 3, duration and step "
                            "on line 4, graphics on line 7)") from None
    ipc = _lines(path / "Inp_IPC.txt")
    try:
        count = int(_value(ipc[1]).split()[0])
        found: List[Tuple[int, List[str]]] = []
        row = 2
        for index in range(count):
            mode = _value(ipc[row + 1]).split()[0].upper()
            role = _value(ipc[row + 3]).split()[0].upper()
            host_port = _value(ipc[row + 4]).split()
            blocking = _value(ipc[row + 5]).split()[0].upper() == "TRUE"
            prefixes = int(_value(ipc[row + 7]).split()[0])
            names = [_value(ipc[row + 8 + k]).strip('"') for k in range(prefixes)]
            if mode == "TXRX":
                if role != "SERVER" or not blocking:
                    raise FortyTwoError(f"{path / 'Inp_IPC.txt'}: IPC {index} is TXRX but "
                                        + ("not a SERVER" if role != "SERVER" else "does not block (Allow Blocking: "
                                           "FALSE)") + ": the coupling connects to 42, and 42 must wait for it at "
                                        "every step")
                found.append((row, [host_port[0], host_port[1], str(row + 6), *names]))
            elif mode != "OFF":
                raise FortyTwoError(f"{path / 'Inp_IPC.txt'}: IPC {index} is {mode}: the coupling is 42's one peer; "
                                    "set the others OFF")
            row += 8 + prefixes
    except (IndexError, ValueError):
        raise FortyTwoError(f"{path / 'Inp_IPC.txt'}: not 42's Inp_IPC.txt (a number of sockets, then a block of "
                            "lines per socket)") from None
    if len(found) != 1:
        raise FortyTwoError(f"{path / 'Inp_IPC.txt'}: the coupling needs one TXRX socket (SERVER, Allow Blocking "
                            f"TRUE); it has {len(found)}")
    row, (host, port, echo_line, *names) = found[0]
    if time_mode.upper() != "FAST":
        raise FortyTwoError(f"{path / 'Inp_Sim.txt'}: time mode {time_mode}: the coupling paces 42, which must run "
                            "as fast as it is let (FAST)")
    if graphics:
        raise FortyTwoError(f"{path / 'Inp_Sim.txt'}: Graphics Front End is TRUE: a coupled 42 runs headless (FALSE)")
    if dtsim <= 0:
        raise FortyTwoError(f"{path / 'Inp_Sim.txt'}: a step of {dtsim} s")
    return Case(path, time_mode, dtsim, graphics, host, int(port), row + 4, int(echo_line), names)


def copy_case(case: Case, target: Path, port: int) -> Path:
    """The case in `target`, with its TXRX socket on `port` and its echo off"""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(case.path, target)
    lines = (target / "Inp_IPC.txt").read_text().splitlines()
    lines[case.socket_line] = f"localhost     {port}                     ! Server Host Name, Port"
    lines[case.echo_line] = "FALSE                                   ! Echo to stdout"
    (target / "Inp_IPC.txt").write_text("\n".join(lines) + "\n")
    return target


def parse_message(text: str) -> Tuple[Optional[float], Dict[str, Any]]:
    """(the TIME, in seconds of its day of year, or None; {variable: float or [floats]})"""
    seconds = None
    values: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("TIME "):
            # TIME 2014-172-00:00:00.050000000: year, day of year, hours, minutes, seconds
            year, doy, clock = line[5:].split("-", 2)
            hours, minutes, secs = clock.split(":")
            seconds = ((int(doy) * 24 + int(hours)) * 60 + int(minutes)) * 60 + float(secs)
            continue
        match = LINE.match(line)
        if not match:
            continue
        name, value = match.groups()
        try:
            values[name] = [float(x) for x in value[1:-1].split()] if value.startswith("[") else float(value)
        except ValueError:
            continue
    return seconds, values


def component(values: Dict[str, Any], name: str) -> Optional[float]:
    """A variable of 42's message, or a component of one (`SC[0].svb[1]`)"""
    if name in values and not isinstance(values[name], list):
        return values[name]
    match = INDEXED.match(name)
    if match and isinstance(values.get(match.group(1)), list):
        vector = values[match.group(1)]
        index = int(match.group(2))
        return vector[index] if index < len(vector) else None
    return None


def find_executable(given: Optional[str]) -> str:
    candidate = given or os.environ.get("FORTYTWO") or shutil.which("42")
    if not candidate:
        raise FortyTwoError("42's executable is not known: give fortytwo.executable in the manifest, set FORTYTWO, "
                            "or put 42 on the PATH")
    path = Path(os.path.expanduser(candidate))
    if path.is_dir():
        path = path / "42"
    if not path.is_file() or not os.access(path, os.X_OK):
        found = shutil.which(candidate)
        if not found:
            raise FortyTwoError(f"42's executable {candidate} is not an executable file")
        path = Path(found)
    return str(path.resolve())


class FortyTwoPlant:
    def __init__(self, case: Path, executable: Optional[str] = None, name: str = "plant",
                 inputs: Sequence[str] = (), outputs: Sequence[str] = (), params: Optional[dict] = None,
                 log: Optional[Path] = None):
        if params:
            raise FortyTwoError(f"plant '{name}': 42 takes its parameters from its case's files, not params")
        self.name = name
        self.case = read_case(Path(case))
        self.executable = find_executable(executable)
        self.inputs, self.outputs = list(inputs), list(outputs)
        self.log = log
        self.process: Optional[subprocess.Popen] = None
        self.sock: Optional[socket.socket] = None
        self._buffer = b""
        self.last: Dict[str, Any] = {}
        self.time: Optional[float] = None
        self.steps = 0
        bases = {}
        for input_name in self.inputs:
            match = INDEXED.match(input_name)
            base, index = (match.group(1), int(match.group(2))) if match else (input_name, None)
            if base in bases and (bases[base] is None) != (index is None):
                raise FortyTwoError(f"plant '{name}': input {input_name}: {base} is given both whole and by component")
            bases[base] = index
        self._bases = list(dict.fromkeys(INDEXED.match(n).group(1) if INDEXED.match(n) else n for n in self.inputs))

    # ---- 42's side of the protocol ----

    def _read_message(self) -> str:
        while b"[ENDMSG]\n" not in self._buffer:
            try:
                chunk = self.sock.recv(65536)
            except OSError as error:
                raise FortyTwoError(f"plant '{self.name}': reading from 42: {error}" + self._see()) from None
            if not chunk:
                raise FortyTwoError(f"plant '{self.name}': 42 closed its socket (did it reach its Sim Duration, or "
                                    "fail?)" + self._see())
            self._buffer += chunk
        text, _, self._buffer = self._buffer.partition(b"[ENDMSG]\n")
        self.sock.sendall(b"Ack\0")
        return text.decode(errors="replace")

    def _receive(self) -> None:
        seconds, values = parse_message(self._read_message())
        if seconds is not None:
            if self.time is not None and abs(seconds - self.time - self.case.dtsim) > 1e-6:
                raise FortyTwoError(f"plant '{self.name}': 42's time moved {seconds - self.time:.9f} s in one step, "
                                    f"not its step of {self.case.dtsim:g} s")
            self.time = seconds
        self.last.update(values)
        self.steps += 1

    def _send(self, inputs: Dict[str, Any]) -> None:
        lines = []
        for base in self._bases:
            if base in inputs:
                value = inputs[base]
                lines.append(f"{base} = {repr(float(value))}")
                continue
            vector = list(self.last.get(base) or [0.0] * (4 if QUATERNION.search(base) else 3))
            for name, value in inputs.items():
                match = INDEXED.match(name)
                if match and match.group(1) == base:
                    index = int(match.group(2))
                    vector += [0.0] * (index + 1 - len(vector))
                    vector[index] = float(value)
            lines.append(f"{base} = [{' '.join(repr(float(x)) for x in vector)}]")
        message = "".join(line + "\n" for line in lines) + "[ENDMSG]\n"
        try:
            self.sock.sendall(message.encode())
            ack = b""
            while len(ack) < 4:
                chunk = self.sock.recv(4 - len(ack))
                if not chunk:
                    raise OSError("42 closed its socket")
                ack += chunk
        except OSError as error:
            raise FortyTwoError(f"plant '{self.name}': writing to 42: {error}" + self._see()) from None
        if ack != b"Ack\0":
            raise FortyTwoError(f"plant '{self.name}': 42 answered {ack!r}, not Ack")

    def _see(self) -> str:
        return f"; see {self.log}" if self.log else ""

    # ---- The plant ----

    def start(self, t0: float) -> None:
        port = free_port()
        where = (self.log.parent / f"dynamics-{self.name}-case") if self.log else None
        if where is None:
            import tempfile

            where = Path(tempfile.mkdtemp(prefix="wsts-42-")) / "case"
        case = copy_case(self.case, where, port)
        out = open(self.log, "w") if self.log else subprocess.DEVNULL
        try:
            self.process = subprocess.Popen([self.executable, str(case.resolve())], cwd=Path(self.executable).parent,
                                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        except OSError as error:
            raise FortyTwoError(f"plant '{self.name}': cannot start 42 ({self.executable}): {error}") from None
        finally:
            if self.log:
                out.close()
        deadline = time.monotonic() + CONNECT_LIMIT
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                break
            except OSError:
                if self.process.poll() is not None:
                    raise FortyTwoError(f"plant '{self.name}': 42 exited ({self.process.returncode}) before it listened"
                                        + self._see()) from None
                if time.monotonic() >= deadline:
                    raise FortyTwoError(f"plant '{self.name}': 42 did not listen on port {port} within "
                                        f"{CONNECT_LIMIT:g} s" + self._see()) from None
                time.sleep(0.05)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(ANSWER_LIMIT)
        self._receive()  # its state after its first step, which it takes before it waits for anyone
        missing = [name for name in self.outputs if component(self.last, name) is None]
        if missing:
            have = ", ".join(sorted(self.last)) or "nothing"
            raise FortyTwoError(f"plant '{self.name}': 42 does not send {', '.join(missing)} (it sends {have}; its "
                                f"TX prefixes are {', '.join(self.case.prefixes) or 'none'})")

    def step(self, t: float, dt: float, inputs: Dict[str, Any]) -> Dict[str, Any]:
        count = dt / self.case.dtsim
        whole = int(round(count))
        if whole < 1 or abs(count - whole) > 1e-6:
            raise FortyTwoError(f"plant '{self.name}': a step of {dt:g} s is not a whole number of 42's steps of "
                                f"{self.case.dtsim:g} s: make 42's step divide the plant's (step, or the instants)")
        for _ in range(whole):
            self._send(inputs)
            self._receive()
        return {name: component(self.last, name) for name in self.outputs}

    def stop(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None


# ---------------------------------------------------------------------------
# Preparing a case
# ---------------------------------------------------------------------------

def prepare_case(source: Path, target: Path, prefixes: Sequence[str] = ("SC",), step: Optional[float] = None,
                 port: int = 10001) -> Case:
    """Copy a case of 42 (one of 42's own, such as InOut or Tx) to `target`, made one the
    coupling can drive: FAST time, no graphics, and one TXRX socket (a blocking server) that
    sends the variables whose names start with `prefixes`. `step` sets 42's DTSIM."""
    source, target = Path(source), Path(target)
    if not (source / "Inp_Sim.txt").is_file():
        raise FortyTwoError(f"{source} is not a case of 42 (no Inp_Sim.txt)")
    if target.exists():
        raise FortyTwoError(f"{target} exists already")
    shutil.copytree(source, target)
    sim = (target / "Inp_Sim.txt").read_text().splitlines()
    duration, dtsim = _value(sim[3]).split()[:2]
    sim[2] = "FAST                            !  Time Mode (FAST, REAL, EXTERNAL, or NOS3)"
    sim[3] = f"{duration:<11} {step if step is not None else dtsim:<16}!  Sim Duration, Step Size [sec]"
    sim[6] = "FALSE                           !  Graphics Front End?"
    (target / "Inp_Sim.txt").write_text("\n".join(sim) + "\n")
    lines = [
        "<<<<<<<<<<<<<<< 42: InterProcess Comm Configuration File >>>>>>>>>>>>>>>>",
        "1                                       ! Number of Sockets",
        "**********************************  IPC 0   *****************************",
        "TXRX                                    ! IPC Mode (OFF,TX,RX,TXRX,WRITEFILE,READFILE)",
        '"State00.42"                            ! File name for WRITE or READ',
        "SERVER                                  ! Socket Role (SERVER,CLIENT,GMSEC_CLIENT)",
        f"localhost     {port}                     ! Server Host Name, Port",
        "TRUE                                    ! Allow Blocking (i.e. wait on RX)",
        "FALSE                                   ! Echo to stdout",
        f"{len(prefixes):<40}! Number of TX prefixes",
        *[f'{chr(34) + prefix + chr(34):<40}! Prefix {index}' for index, prefix in enumerate(prefixes)],
    ]
    (target / "Inp_IPC.txt").write_text("\n".join(lines) + "\n")
    return read_case(target)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m fprime_wsts.fortytwo",
                                     description="Make a case of 42 one a plant of the `fortytwo` kind can drive")
    parser.add_argument("source", help="a case directory of 42 (one with Inp_Sim.txt), e.g. 42/Tx")
    parser.add_argument("target", help="where to write the coupled case (a new directory)")
    parser.add_argument("--prefix", action="append", dest="prefixes",
                        help="the names of the variables 42 sends start with this (repeatable; default SC)")
    parser.add_argument("--step", type=float, help="42's step (DTSIM), seconds")
    args = parser.parse_args(argv)
    try:
        case = prepare_case(Path(args.source), Path(args.target), args.prefixes or ["SC"], args.step)
    except FortyTwoError as error:
        print(f"error: {error}")
        return 2
    print(f"{case.path}: FAST, steps of {case.dtsim:g} s, sends {', '.join(case.prefixes)}. Its spacecraft files still "
          "say which flight software 42 runs itself (PASSIVE_FSW leaves control to the coupled flight software)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
