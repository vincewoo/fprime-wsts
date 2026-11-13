#!/usr/bin/env python3
"""Probe a real device by a plan, and capture what it does as a WSTS recording.

The plan (YAML) lists exchanges on one interface: UART, I2C, SPI or GPIO. Each step has a
class that says what it does to the device:

    read        no lasting effect on the device (a query, a register read, a pin read)
    write       changes configuration or state the device keeps (a mode, a setpoint)
    hazardous   resets, erases, writes non-volatile memory, actuates, or is not known

Only `read` steps run by default. `write` steps run when named with --allow, or all of
them with --allow-class write. `hazardous` steps run only when named with --allow. A step
without a class is hazardous. The user, not the agent, decides what to allow.

The captures are JSON lines in the WSTS recording format (bs_tx, bs_rx, i2c, spi,
gpio_out, gpio_in), with the step's name, compare mode and model parameters added, so
conform.py can replay them against a model and fprime-wsts can load them as a recording.

    probe.py plan.yaml --dry-run                      # show what would be sent, and the gate
    probe.py plan.yaml --out captures/sensor.jsonl    # run the read steps
    probe.py plan.yaml --out captures/cfg.jsonl --allow set_rate --only set_rate --only status

Plan keys: see references/probing.md in the skill.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import errno
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CLASSES = ("read", "write", "hazardous")


class PlanError(Exception):
    pass


# ----------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------

def load_plan(path: Path) -> dict:
    text = path.read_text()
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore
    except ImportError:
        raise PlanError("PyYAML is needed for a YAML plan (pip install pyyaml), or write the plan as .json")
    return yaml.safe_load(text) or {}


def to_bytes(spec, what: str) -> bytes:
    """{text: "..."} | {hex: "..."} | "hex string" -> bytes"""
    if spec is None or spec == "":
        return b""
    if isinstance(spec, dict):
        if "text" in spec:
            return str(spec["text"]).encode("latin-1")
        if "hex" in spec:
            spec = spec["hex"]
        else:
            raise PlanError(f"{what}: give text or hex")
    if isinstance(spec, int):
        return bytes([spec])
    if isinstance(spec, list):
        return bytes(int(str(b), 0) for b in spec)
    cleaned = re.sub(r"0x|[\s,:]", "", str(spec))
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        raise PlanError(f"{what}: not hex: {spec!r}")


def step_class(step: dict) -> str:
    cls = step.get("class", "hazardous")
    if cls not in CLASSES:
        raise PlanError(f"step {step.get('name')}: class must be one of {CLASSES}")
    return cls


def check_step(step: dict, interface: str) -> List[str]:
    """Errors for a step that is classified as read but cannot be one"""
    errors = []
    name = step.get("name", "?")
    cls = step_class(step)
    if interface == "gpio" and "set" in step and cls == "read":
        errors.append(f"{name}: driving a pin is not a read; class it write or hazardous")
    if interface == "i2c" and cls == "read":
        write = to_bytes(step.get("write"), name)
        if int(step.get("read", 0)) == 0 and write and not step.get("pointer_only"):
            errors.append(f"{name}: an I2C write with nothing read changes the device; class it write, "
                          "or set pointer_only: true if it only sets the register pointer")
    return errors


def gate(step: dict, allow: List[str], allow_classes: List[str]) -> Tuple[bool, str]:
    cls = step_class(step)
    name = step.get("name", "")
    if cls == "read":
        return True, "read"
    if name in allow:
        return True, f"{cls}, allowed by name"
    if cls == "write" and "write" in allow_classes:
        return True, "write, allowed by class"
    return False, f"{cls}: needs --allow {name}" + (" or --allow-class write" if cls == "write" else "")


# ----------------------------------------------------------------------
# Capture file
# ----------------------------------------------------------------------

class Capture:
    def __init__(self, path: Optional[Path], plan: dict, plan_path: Path):
        self.endpoint = plan.get("endpoint") or plan.get("interface", "dev")
        self.tick_hz = float(plan.get("tick_hz", 10))
        self.start = time.monotonic()
        self.file = None
        self.count = 0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = path.open("w")
            header = {
                "device": plan.get("device", ""),
                "interface": plan.get("interface"),
                "config": plan.get(plan.get("interface", ""), {}),
                "endpoint": self.endpoint,
                "tick_hz": self.tick_hz,
                "plan": str(plan_path),
                "started": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            self.file.write("# wsts-probe " + json.dumps(header) + "\n")

    def now(self) -> float:
        return time.monotonic() - self.start

    def write(self, rec: str, step: dict, t: float, endpoint: Optional[str] = None, **fields) -> dict:
        line = {"rec": rec, "tick": int(t * self.tick_hz), "endpoint": endpoint or self.endpoint,
                "src": "flight" if rec in ("bs_tx", "i2c", "spi", "gpio_out") else "model"}
        line.update(fields)
        line["t"] = round(t, 4)
        if step.get("name"):
            line["step"] = step["name"]
        if step.get("compare"):
            line["compare"] = step["compare"]
        if step.get("model_params"):
            line["model_params"] = {str(k): str(v) for k, v in step["model_params"].items()}
        if self.file:
            self.file.write(json.dumps(line) + "\n")
            self.file.flush()
        self.count += 1
        return line

    def close(self) -> None:
        if self.file:
            self.file.close()


def show(data: bytes, limit: int = 64) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:limit])
    more = "…" if len(data) > limit else ""
    return f"{data[:limit].hex(' ')}{more} |{text}{more}|" if data else "(nothing)"


# ----------------------------------------------------------------------
# UART
# ----------------------------------------------------------------------

class Uart:
    def __init__(self, cfg: dict):
        self.port = cfg.get("port")
        if not self.port:
            raise PlanError("uart.port is required")
        baud = int(cfg.get("baud", 115200))
        self.ser = None
        self.fd = None
        try:
            import serial  # type: ignore
            self.ser = serial.Serial(
                self.port, baudrate=baud, bytesize=int(cfg.get("bytesize", 8)),
                parity=str(cfg.get("parity", "N"))[0].upper(), stopbits=float(cfg.get("stopbits", 1)),
                rtscts=bool(cfg.get("rtscts", False)), xonxoff=bool(cfg.get("xonxoff", False)),
                timeout=0, exclusive=True)
        except ImportError:
            self.fd = self._open_termios(cfg, baud)

    def _open_termios(self, cfg: dict, baud: int) -> int:
        import termios
        name = f"B{baud}"
        if not hasattr(termios, name):
            raise PlanError(f"baud {baud} needs pyserial (pip install pyserial)")
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, termios.TIOCEXCL)  # nobody else opens it while we probe
        except OSError:
            pass
        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag = 0, 0, termios.CREAD | termios.CLOCAL, 0
        cflag |= {5: termios.CS5, 6: termios.CS6, 7: termios.CS7, 8: termios.CS8}[int(cfg.get("bytesize", 8))]
        parity = str(cfg.get("parity", "N"))[0].upper()
        if parity in ("E", "O"):
            cflag |= termios.PARENB | (termios.PARODD if parity == "O" else 0)
        if float(cfg.get("stopbits", 1)) == 2:
            cflag |= termios.CSTOPB
        if cfg.get("rtscts"):
            cflag |= getattr(termios, "CRTSCTS", 0)
        if cfg.get("xonxoff"):
            iflag |= termios.IXON | termios.IXOFF
        speed = getattr(termios, name)
        attrs[0:6] = [iflag, oflag, cflag, lflag, speed, speed]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd

    def write(self, data: bytes) -> None:
        if self.ser:
            self.ser.write(data)
            self.ser.flush()
        else:
            view = memoryview(data)
            while view:
                _, ready, _ = select.select([], [self.fd], [], 1.0)
                if ready:
                    view = view[os.write(self.fd, view):]
            import termios
            termios.tcdrain(self.fd)

    def read_available(self, wait: float) -> bytes:
        if self.ser:
            deadline = time.monotonic() + wait
            while True:
                n = self.ser.in_waiting
                if n:
                    return self.ser.read(n)
                if time.monotonic() >= deadline:
                    return b""
                time.sleep(0.002)
        ready, _, _ = select.select([self.fd], [], [], max(0.0, wait))
        if not ready:
            return b""
        try:
            return os.read(self.fd, 4096)
        except BlockingIOError:
            return b""

    def collect(self, timeout: float, quiet: float, terminator: bytes, length: int) -> Tuple[bytes, Optional[float]]:
        """Read until the terminator, the length, `quiet` seconds without a byte after the
        first, or `timeout`. Returns the bytes and the seconds to the first byte."""
        start = time.monotonic()
        data = bytearray()
        first = None
        last = start
        while True:
            now = time.monotonic()
            if now - start >= timeout:
                break
            if data and quiet and now - last >= quiet:
                break
            chunk = self.read_available(min(0.01, timeout - (now - start)))
            if chunk:
                if first is None:
                    first = time.monotonic() - start
                data.extend(chunk)
                last = time.monotonic()
                if terminator and terminator in data:
                    break
                if length and len(data) >= length:
                    break
        return bytes(data), first

    def close(self) -> None:
        if self.ser:
            self.ser.close()
        elif self.fd is not None:
            os.close(self.fd)


def run_uart_step(dev: Uart, step: dict, cap: Capture, defaults: dict) -> str:
    timeout = float(step.get("timeout_ms", defaults.get("timeout_ms", 1000))) / 1000
    until = step.get("until") or {}
    quiet = float(until.get("quiet_ms", defaults.get("quiet_ms", 100))) / 1000
    terminator = to_bytes(until.get("terminator"), step.get("name", "")) if isinstance(until.get("terminator"), dict) \
        else str(until.get("terminator", "")).encode("latin-1")
    length = int(until.get("length", 0))
    # Whatever the device sent on its own since the last step is kept, not flushed away
    pending = dev.read_available(0)
    if pending:
        cap.write("bs_rx", {"name": "_unsolicited", "compare": "ignore"}, cap.now(), data=pending.hex())
    if "listen_ms" in step:
        t0 = cap.now()
        data, first = dev.collect(float(step["listen_ms"]) / 1000, 0, b"", 0)
        cap.write("bs_rx", step, cap.now(), data=data.hex(), listen_ms=step["listen_ms"], t0=round(t0, 4))
        return f"listened {step['listen_ms']} ms: {len(data)} bytes {show(data)}"
    tx = to_bytes(step.get("tx"), step.get("name", ""))
    t_send = cap.now()
    dev.write(tx)
    cap.write("bs_tx", step, t_send, data=tx.hex())
    data, first = dev.collect(timeout, quiet, terminator, length)
    fields = {"data": data.hex()}
    if first is not None:
        fields["latency_ms"] = round(first * 1000, 2)
    cap.write("bs_rx", step, cap.now(), **fields)
    lat = f" after {first * 1000:.1f} ms" if first is not None else ""
    return f"sent {show(tx)}\n        got  {show(data)}{lat}"


# ----------------------------------------------------------------------
# I2C (Linux i2c-dev, I2C_RDWR: a write and a read with a repeated start)
# ----------------------------------------------------------------------

class I2cMsg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16), ("len", ctypes.c_uint16),
                ("buf", ctypes.POINTER(ctypes.c_uint8))]


class I2cRdwr(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(I2cMsg)), ("nmsgs", ctypes.c_uint32)]


I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class I2c:
    def __init__(self, cfg: dict):
        bus = cfg.get("bus")
        path = cfg.get("device") or (f"/dev/i2c-{bus}" if bus is not None else None)
        if not path:
            raise PlanError("i2c.bus (or i2c.device) is required")
        self.fd = os.open(path, os.O_RDWR)

    def transact(self, addr: int, write: bytes, rlen: int) -> Tuple[int, bytes]:
        msgs = []
        wbuf = (ctypes.c_uint8 * max(1, len(write))).from_buffer_copy(write.ljust(1, b"\0"))
        rbuf = (ctypes.c_uint8 * max(1, rlen))()
        if write:
            msgs.append(I2cMsg(addr, 0, len(write), ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_uint8))))
        if rlen:
            msgs.append(I2cMsg(addr, I2C_M_RD, rlen, ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_uint8))))
        if not msgs:
            raise PlanError("an I2C step needs write bytes or a read length")
        array = (I2cMsg * len(msgs))(*msgs)
        data = I2cRdwr(ctypes.cast(array, ctypes.POINTER(I2cMsg)), len(msgs))
        try:
            fcntl.ioctl(self.fd, I2C_RDWR, data)
        except OSError as exc:
            # Drv.I2cStatus: 1 address (no acknowledge), 5 other
            status = 1 if exc.errno in (errno.ENXIO, errno.EREMOTEIO, errno.EIO) else 5
            return status, b""
        return 0, bytes(rbuf[:rlen])

    def close(self) -> None:
        os.close(self.fd)


def run_i2c_step(dev: I2c, step: dict, cap: Capture, defaults: dict) -> str:
    addr = int(str(step.get("addr", defaults.get("addr", ""))), 0)
    write = to_bytes(step.get("write"), step.get("name", ""))
    rlen = int(step.get("read", 0))
    t = cap.now()
    status, read = dev.transact(addr, write, rlen)
    cap.write("i2c", step, t, addr=addr, write=write.hex(), read=read.hex(), status=status)
    return f"@{addr:#04x} write {write.hex(' ') or '-'} read {show(read)} status {status}"


# ----------------------------------------------------------------------
# SPI (Linux spidev)
# ----------------------------------------------------------------------

class SpiTransfer(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64), ("len", ctypes.c_uint32),
                ("speed_hz", ctypes.c_uint32), ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8), ("rx_nbits", ctypes.c_uint8),
                ("word_delay_usecs", ctypes.c_uint8), ("pad", ctypes.c_uint8)]


SPI_IOC_MESSAGE_1 = 0x40206B00
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04


class Spi:
    def __init__(self, cfg: dict):
        path = cfg.get("device") or f"/dev/spidev{cfg.get('bus', 0)}.{cfg.get('cs', 0)}"
        self.fd = os.open(path, os.O_RDWR)
        self.speed = int(cfg.get("speed_hz", 500000))
        self.bits = int(cfg.get("bits", 8))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, ctypes.c_uint8(int(cfg.get("mode", 0))))
        fcntl.ioctl(self.fd, SPI_IOC_WR_BITS_PER_WORD, ctypes.c_uint8(self.bits))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, ctypes.c_uint32(self.speed))

    def transfer(self, write: bytes) -> Tuple[int, bytes]:
        tx = (ctypes.c_uint8 * len(write)).from_buffer_copy(write)
        rx = (ctypes.c_uint8 * len(write))()
        xfer = SpiTransfer(ctypes.addressof(tx), ctypes.addressof(rx), len(write), self.speed, 0, self.bits, 0, 0, 0, 0, 0)
        try:
            fcntl.ioctl(self.fd, SPI_IOC_MESSAGE_1, xfer)
        except OSError:
            return 1, b""
        return 0, bytes(rx)

    def close(self) -> None:
        os.close(self.fd)


def run_spi_step(dev: Spi, step: dict, cap: Capture, defaults: dict) -> str:
    write = to_bytes(step.get("write"), step.get("name", ""))
    rlen = int(step.get("read", len(write)))
    pad = int(str(step.get("pad", defaults.get("pad", 0))), 0)
    write = write.ljust(rlen, bytes([pad]))  # full duplex: clock as many bytes as are read
    t = cap.now()
    status, read = dev.transfer(write)
    cap.write("spi", step, t, write=write.hex(), read=read.hex(), status=status)
    return f"write {write.hex(' ')} read {show(read)} status {status}"


# ----------------------------------------------------------------------
# GPIO (libgpiod: Python bindings v2 or v1, else the command-line tools)
# ----------------------------------------------------------------------

class Gpio:
    def __init__(self, cfg: dict):
        self.chip = str(cfg.get("chip", "gpiochip0"))
        self.path = self.chip if self.chip.startswith("/dev/") else f"/dev/{self.chip}"
        self.endpoints = {int(k): v for k, v in (cfg.get("endpoints") or {}).items()}
        try:
            import gpiod  # type: ignore
            self.gpiod = gpiod
            self.v2 = hasattr(gpiod, "request_lines")
        except ImportError:
            self.gpiod = None
            if not shutil.which("gpioget"):
                raise PlanError("GPIO needs the gpiod Python package or the libgpiod tools (gpioget, gpioset, gpiomon)")

    def endpoint(self, line: int) -> str:
        return self.endpoints.get(line, f"gpio{line}")

    def get(self, line: int) -> bool:
        if self.gpiod and self.v2:
            from gpiod.line import Direction  # type: ignore
            with self.gpiod.request_lines(self.path, consumer="wsts-probe",
                                          config={line: self.gpiod.LineSettings(direction=Direction.INPUT)}) as req:
                return req.get_value(line).value == 1
        if self.gpiod:
            chip = self.gpiod.Chip(self.chip)
            ln = chip.get_line(line)
            ln.request(consumer="wsts-probe", type=self.gpiod.LINE_REQ_DIR_IN)
            try:
                return ln.get_value() == 1
            finally:
                ln.release()
        out = subprocess.run(["gpioget", self.chip, str(line)], capture_output=True, text=True)
        if out.returncode != 0:  # libgpiod v2 tools
            out = subprocess.run(["gpioget", "--numeric", "-c", self.chip, str(line)], capture_output=True, text=True, check=True)
        return out.stdout.strip().split()[-1] in ("1", "active")

    def set(self, line: int, level: bool, hold: float) -> None:
        if self.gpiod and self.v2:
            from gpiod.line import Direction, Value  # type: ignore
            settings = self.gpiod.LineSettings(direction=Direction.OUTPUT,
                                               output_value=Value.ACTIVE if level else Value.INACTIVE)
            with self.gpiod.request_lines(self.path, consumer="wsts-probe", config={line: settings}):
                time.sleep(hold)
            return
        if self.gpiod:
            chip = self.gpiod.Chip(self.chip)
            ln = chip.get_line(line)
            ln.request(consumer="wsts-probe", type=self.gpiod.LINE_REQ_DIR_OUT, default_vals=[int(level)])
            time.sleep(hold)
            ln.release()
            return
        raise PlanError("driving a pin needs the gpiod Python package (the CLI tools release the line at once)")

    def watch(self, line: int, seconds: float) -> List[Tuple[float, bool]]:
        if not self.gpiod:
            raise PlanError("watching edges needs the gpiod Python package")
        events: List[Tuple[float, bool]] = []
        start = time.monotonic()
        if self.v2:
            from gpiod.line import Direction, Edge  # type: ignore
            settings = self.gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH)
            with self.gpiod.request_lines(self.path, consumer="wsts-probe", config={line: settings}) as req:
                while (left := seconds - (time.monotonic() - start)) > 0:
                    if req.wait_edge_events(datetime.timedelta(seconds=left)):
                        for ev in req.read_edge_events():
                            events.append((time.monotonic() - start, ev.event_type == ev.Type.RISING_EDGE))
            return events
        chip = self.gpiod.Chip(self.chip)
        ln = chip.get_line(line)
        ln.request(consumer="wsts-probe", type=self.gpiod.LINE_REQ_EV_BOTH_EDGES)
        try:
            while (left := seconds - (time.monotonic() - start)) > 0:
                if ln.event_wait(sec=int(left), nsec=int((left % 1) * 1e9)):
                    ev = ln.event_read()
                    events.append((time.monotonic() - start, ev.type == self.gpiod.LineEvent.RISING_EDGE))
        finally:
            ln.release()
        return events

    def close(self) -> None:
        pass


def run_gpio_step(dev: Gpio, step: dict, cap: Capture, defaults: dict) -> str:
    line = int(step["line"])
    ep = step.get("endpoint") or dev.endpoint(line)
    if "set" in step:
        level = bool(int(step["set"]))
        t = cap.now()
        cap.write("gpio_out", step, t, endpoint=ep, level=level)
        dev.set(line, level, float(step.get("hold_ms", 100)) / 1000)
        return f"{ep} (line {line}) driven {int(level)}"
    if "watch_ms" in step:
        t0 = cap.now()
        events = dev.watch(line, float(step["watch_ms"]) / 1000)
        for dt, level in events:
            cap.write("gpio_in", step, t0 + dt, endpoint=ep, level=level)
        return f"{ep} (line {line}): {len(events)} edges in {step['watch_ms']} ms"
    t = cap.now()
    level = dev.get(line)
    cap.write("gpio_in", step, t, endpoint=ep, level=level)
    return f"{ep} (line {line}) reads {int(level)}"


# ----------------------------------------------------------------------

BACKENDS = {"uart": (Uart, run_uart_step), "i2c": (I2c, run_i2c_step), "spi": (Spi, run_spi_step),
            "gpio": (Gpio, run_gpio_step)}


def describe(step: dict, interface: str) -> str:
    name = step.get("name", "?")
    if interface == "uart":
        if "listen_ms" in step:
            return f"listen {step['listen_ms']} ms"
        return f"send {show(to_bytes(step.get('tx'), name))}"
    if interface == "i2c":
        return f"@{step.get('addr')} write {to_bytes(step.get('write'), name).hex(' ') or '-'} read {step.get('read', 0)}"
    if interface == "spi":
        return f"transfer {to_bytes(step.get('write'), name).hex(' ')}"
    if "set" in step:
        return f"drive line {step.get('line')} to {step['set']}"
    return f"read line {step.get('line')}" + (f" for {step['watch_ms']} ms" if "watch_ms" in step else "")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--out", type=Path, help="capture file to write (JSON lines)")
    parser.add_argument("--allow", action="append", default=[], help="run this write or hazardous step (repeatable)")
    parser.add_argument("--allow-class", action="append", default=[], choices=["write"], help="run every write step")
    parser.add_argument("--only", action="append", default=[], help="run only these steps (repeatable)")
    parser.add_argument("--port", help="override uart.port / i2c.device / spi.device / gpio.chip")
    parser.add_argument("--repeat", type=int, default=1, help="run the selected steps this many times")
    parser.add_argument("--dry-run", action="store_true", help="open nothing; show the steps and the gate")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
        interface = plan.get("interface")
        if interface not in BACKENDS:
            raise PlanError(f"interface must be one of {sorted(BACKENDS)}")
        cfg = dict(plan.get(interface) or {})
        if args.port:
            cfg["port" if interface == "uart" else "chip" if interface == "gpio" else "device"] = args.port
        steps = plan.get("steps") or []
        names = [s.get("name") for s in steps]
        if any(not n for n in names) or len(set(names)) != len(names):
            raise PlanError("every step needs a unique name")
        unknown = [n for n in args.only + args.allow if n not in names]
        if unknown:
            raise PlanError(f"no such steps: {unknown}")
        errors = [e for s in steps for e in check_step(s, interface)]
        if errors:
            raise PlanError("plan refused:\n  " + "\n  ".join(errors))
        selected = [s for s in steps if not args.only or s["name"] in args.only]
        decisions = [(s, *gate(s, args.allow, args.allow_class)) for s in selected]

        print(f"{plan.get('device', args.plan.name)}: {interface} {json.dumps(cfg)}")
        for s, ok, why in decisions:
            print(f"  {'run ' if ok else 'SKIP'} {s['name']:<20} {describe(s, interface):<50} [{why}]")
        if args.dry_run:
            return 0
        if not args.out:
            raise PlanError("--out is required unless --dry-run")

        backend, runner = BACKENDS[interface]
        defaults = plan.get("defaults") or {}
        dev = backend(cfg)
        cap = Capture(args.out, plan, args.plan)
        skipped = 0
        try:
            if interface == "uart" and plan.get("uart", {}).get("idle_listen_ms", 500):
                # Listening first is harmless and shows whether the device talks on its own
                ms = int(plan["uart"].get("idle_listen_ms", 500))
                print(f"  idle: {run_uart_step(dev, {'name': '_idle', 'listen_ms': ms, 'compare': 'ignore'}, cap, defaults)}")
            for rep in range(args.repeat):
                for s, ok, why in decisions:
                    if not ok:
                        skipped += 1
                        continue
                    if s.get("delay_ms"):
                        time.sleep(float(s["delay_ms"]) / 1000)
                    print(f"  {s['name']}: {runner(dev, s, cap, defaults)}")
        finally:
            dev.close()
            cap.close()
        print(f"{cap.count} records to {args.out}" + (f"; {skipped} steps not allowed" if skipped else ""))
        return 0
    except PlanError as exc:
        print(f"probe: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"probe: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
