"""Client for the WSTS socket bridge.

Gives tests, scripts and agents the same control over a running simulation that the
Wsts.SimControl commands give an operator, and lets Python code act as a device model.

    with WstsBridge("tcp:50100") as sim:
        sim.fault_set("imuBus", "error", arg=3, count=5)
        sim.inject_text("sensorUart", "$TEMP,48.0\\n")
        sim.wait_for_tick(sim.status()["tick"] + 20)
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, Union


class BridgeError(Exception):
    """The simulation rejected a request, or the bridge is not reachable"""


def _connect(address: str, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    last: Optional[Exception] = None
    while True:
        try:
            if address.startswith("unix:"):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(address[5:])
                return sock
            if address.startswith("tcp:"):
                host, _, port = address[4:].rpartition(":")
                return socket.create_connection((host or "127.0.0.1", int(port)), timeout=5)
            raise BridgeError(f"unrecognized bridge address '{address}' (use tcp:<port> or unix:<path>)")
        except OSError as error:
            last = error
            if time.monotonic() >= deadline:
                raise BridgeError(f"cannot connect to the WSTS bridge at {address}: {last}") from None
            time.sleep(0.1)


def _number(value) -> int:
    """A number of the protocol: JSON number, or a "0x..." string for 64-bit values"""
    return int(value, 0) if isinstance(value, str) else int(value)


def _running(directory) -> Optional[dict]:
    from .runner import read_run_file

    return read_run_file(directory)


def bridge_address(directory) -> str:
    """The bridge address of the simulated deployment in a generated directory: the one its
    run is using when one is up (`wsts-logs/wsts-run.json`), else the manifest's"""
    running = _running(directory)
    if running and running.get("bridge"):
        return running["bridge"]
    with open(os.path.join(os.fspath(directory), "wsts-report.json")) as file:
        return json.load(file).get("bridge", "none")


def link_traffic(response: dict) -> dict:
    """Gather the flat `link.<name>.sent` and `link.<name>.received` of a status into
    response["link_traffic"] = {name: {"sent": n, "received": n}}"""
    traffic: Dict[str, Dict[str, int]] = {}
    for key in [key for key in response if key.startswith("link.")]:
        name, _, count = key[len("link."):].rpartition(".")
        if name and count in ("sent", "received"):
            traffic.setdefault(name, {"sent": 0, "received": 0})[count] = int(response.pop(key))
    response["link_traffic"] = traffic
    return response


#: Counters of an endpoint in bus_stats, and of an address on it
TRAFFIC_COUNTERS = ("transactions", "to_device", "to_flight", "bytes_to_device", "bytes_to_flight", "errors",
                    "unanswered", "fault_hits", "last_tick")
ADDRESS_COUNTERS = ("transactions", "errors", "unanswered")


def bus_traffic(response: dict) -> Dict[str, dict]:
    """Nest the flat answer of `bus_stats` (`bus.<endpoint>.<counter>`,
    `bus.<endpoint>.at.<address>.<counter>`) as {endpoint: {counter: n, ..., "addresses":
    {"0x48": {"transactions", "errors", "unanswered"}}}}"""
    traffic: Dict[str, dict] = {}
    for key, value in response.items():
        if not key.startswith("bus."):
            continue
        rest = key[len("bus."):]
        if ".at." in rest:
            endpoint, _, tail = rest.partition(".at.")
            address, _, counter = tail.rpartition(".")
            entry = traffic.setdefault(endpoint, {"addresses": {}})
            entry["addresses"].setdefault(address, {})[counter] = int(value)
        else:
            endpoint, _, counter = rest.rpartition(".")
            traffic.setdefault(endpoint, {"addresses": {}})[counter] = int(value)
    return traffic


def tick_times(response: dict) -> dict:
    """Nest the flat answer of `tick_times` (`slowest.<i>.tick`, `slowest.<i>.us`) as
    {"ticks", "mean_ms", "max_ms", "over_period", "slowest": [{"tick", "ms"}, ...]}, the
    slowest tick first"""
    slowest = []
    i = 0
    while f"slowest.{i}.tick" in response:
        slowest.append({"tick": int(response[f"slowest.{i}.tick"]),
                        "ms": round(float(response[f"slowest.{i}.us"]) / 1e3, 3)})
        i += 1
    return {
        "ticks": int(response.get("ticks", 0)),
        "mean_ms": round(float(response.get("mean_us", 0.0)) / 1e3, 3),
        "max_ms": round(float(response.get("max_us", 0.0)) / 1e3, 3),
        "over_period": int(response.get("over_period", 0)),
        "slowest": slowest,
    }


def links_expected_up(description: dict) -> Dict[str, int]:
    """The link ends of each element that have a connection and so can come up. A datagram
    link has none: it is started, and counted in `links`, but has nothing to wait for."""
    expected: Dict[str, int] = {}
    for link in description.get("links", []):
        if link.get("kind", "bytestream") == "datagram":
            continue
        for side in ("a", "b"):
            element = link[side]["element"]
            expected[element] = expected.get(element, 0) + 1
    return expected


def links_expected_started(description: dict) -> Dict[str, int]:
    """The link ends of each element, of every kind: what `links` counts once they have all
    been started (a datagram link has bound its socket then, and can receive at once)"""
    expected: Dict[str, int] = {}
    for link in description.get("links", []):
        for side in ("a", "b"):
            element = link[side]["element"]
            expected[element] = expected.get(element, 0) + 1
    return expected


class PendingRequest:
    """A request sent to a simulation whose answer has not been waited for yet"""

    def __init__(self, bridge: "WstsBridge", op: str, request_id: int, waiter: "queue.Queue[dict]"):
        self.bridge = bridge
        self.op = op
        self._id = request_id
        self._waiter = waiter

    def result(self, timeout: Optional[float] = None) -> dict:
        """The answer. Raises BridgeError if the request was refused or not answered in time."""
        timeout = self.bridge.request_timeout if timeout is None else timeout
        try:
            response = self._waiter.get(timeout=timeout)
        except queue.Empty:
            raise BridgeError(f"no response to '{self.op}' within {timeout} s") from None
        finally:
            with self.bridge._responses_lock:
                self.bridge._responses.pop(self._id, None)
        if not response.get("ok", False):
            raise BridgeError(response.get("error", f"'{self.op}' failed"))
        return response


class WstsBridge:
    """Connection to a running simulated deployment"""

    def __init__(self, address: str = "tcp:50100", connect_timeout: float = 10.0, request_timeout: float = 5.0):
        self.address = address
        self.request_timeout = request_timeout
        self._sock = _connect(address, connect_timeout)
        self._sock.settimeout(None)
        self._file = self._sock.makefile("r", encoding="utf-8", newline="\n")
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._responses: Dict[int, "queue.Queue[dict]"] = {}
        self._responses_lock = threading.Lock()
        self._events: List[dict] = []
        self._events_changed = threading.Condition()
        self._i2c: Dict[Tuple[str, int], Callable[[bytes, int], Tuple[int, bytes]]] = {}
        self._spi: Dict[str, Callable[[bytes, int], Tuple[int, bytes]]] = {}
        self._registers: Dict[str, Tuple[Callable, Optional[Callable]]] = {}
        self._packets: Dict[Tuple[str, Union[int, str]], Callable[[bytes], Optional[bytes]]] = {}
        # Handlers of what flight software sends on byte stream and GPIO endpoints. They run
        # on a thread of their own, so that they may call the bridge (inject a reply).
        self._watchers: Dict[Tuple[str, str], Callable] = {}
        self._watched: "queue.Queue[Optional[Tuple[Callable, dict]]]" = queue.Queue()
        self._watcher: Optional[threading.Thread] = None
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name="wsts-bridge", daemon=True)
        self._reader.start()

    # ---- Connection ----

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def __enter__(self) -> "WstsBridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _send(self, message: dict) -> None:
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._send_lock:
            self._sock.sendall(data)

    def _read_loop(self) -> None:
        try:
            for line in self._file:
                line = line.strip()
                if not line:
                    continue
                message = json.loads(line)
                kind = message.get("ev")
                if kind in ("i2c_req", "spi_req"):
                    self._serve(kind, message)
                elif kind == "reg_req":
                    self._serve_register(message)
                elif kind == "pkt_req":
                    self._serve_packet(message)
                elif kind is not None:
                    watcher = self._watchers.get((message.get("rec"), message.get("endpoint"))) \
                        if message.get("src") == "flight" else None
                    if watcher is not None:
                        self._watched.put((watcher, message))
                    with self._events_changed:
                        self._events.append(message)
                        self._events_changed.notify_all()
                else:
                    with self._responses_lock:
                        waiter = self._responses.get(message.get("id"))
                    if waiter is not None:
                        waiter.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._closed = True
            self._watched.put(None)
            with self._events_changed:
                self._events_changed.notify_all()

    def _watch_loop(self) -> None:
        while True:
            item = self._watched.get()
            if item is None:
                return
            handler, message = item
            try:
                handler(message)
            except BridgeError:
                if self._closed:
                    return
            except Exception:  # a failing model is reported and does not end the others
                import traceback

                traceback.print_exc()

    def _watch(self, rec: str, endpoint: str, handler: Callable[[dict], None]) -> None:
        self._watchers[(rec, endpoint)] = handler
        if self._watcher is None:
            self._watcher = threading.Thread(target=self._watch_loop, name="wsts-models", daemon=True)
            self._watcher.start()
        self.subscribe()

    def wait_closed(self, timeout: Optional[float] = None) -> bool:
        """Block until the simulation has closed the connection (it stopped). True if it has."""
        self._reader.join(timeout)
        return not self._reader.is_alive()

    def _serve(self, kind: str, message: dict) -> None:
        """Answer a transaction for a device this client is modelling"""
        write = bytes.fromhex(message.get("write", ""))
        length = int(message.get("rlen", 0))
        if kind == "i2c_req":
            handler = self._i2c.get((message["endpoint"], int(message.get("addr", 0))))
        else:
            handler = self._spi.get(message["endpoint"])
        status, data = 5, b""
        if handler is not None:
            try:
                status, data = handler(write, length)
            except Exception:  # a failing model must not hang flight software
                status, data = 5, b""
        self._send({"op": "reply", "rid": message["rid"], "status": int(status), "data": bytes(data).hex()})

    def _serve_packet(self, message: dict) -> None:
        """Answer a packet for a node this client is modelling: its reply packet, or nothing"""
        endpoint = message["endpoint"]
        handler = self._packets.get((endpoint, int(message.get("addr", 0)))) or self._packets.get((endpoint, "any"))
        answer = b""
        if handler is not None:
            try:
                answer = handler(bytes.fromhex(message.get("data", ""))) or b""
            except Exception:  # a failing model must not hang flight software
                answer = b""
        self._send({"op": "reply", "rid": message["rid"], "status": 0, "data": bytes(answer).hex()})

    def _serve_register(self, message: dict) -> None:
        """Answer a register access for a block this client is modelling"""
        read_handler, write_handler = self._registers.get(message["endpoint"], (None, None))
        offset = _number(message.get("offset", 0))
        width = int(message.get("width", 4))
        status, data = 5, b""
        try:
            if message.get("access") == "write":
                if write_handler is not None:
                    status = int(write_handler(offset, width, _number(message.get("value", 0))) or 0)
                elif read_handler is not None:
                    status = 0  # a block with only a read handler ignores writes
            elif read_handler is not None:
                answer = read_handler(offset, width)
                status, value = answer if isinstance(answer, tuple) else (0, answer)
                data = (int(value) & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
        except Exception:  # a failing model must not hang flight software
            status, data = 5, b""
        self._send({"op": "reply", "rid": message["rid"], "status": int(status), "data": bytes(data).hex()})

    # ---- Requests ----

    def request(self, op: str, timeout: Optional[float] = None, **fields) -> dict:
        """Send one action and return the simulation's response. Raises BridgeError if refused.
        `timeout` overrides `request_timeout` for this request."""
        return self.submit(op, **fields).result(timeout)

    def submit(self, op: str, **fields) -> "PendingRequest":
        """Send one request without waiting for its answer: `.result()` waits for it. Lets a
        client wait on several simulations at once (the conductor of a system)."""
        with self._responses_lock:
            request_id = self._next_id
            self._next_id += 1
            waiter: "queue.Queue[dict]" = queue.Queue()
            self._responses[request_id] = waiter
        try:
            self._send({"id": request_id, "op": op, **fields})
        except OSError as error:
            with self._responses_lock:
                self._responses.pop(request_id, None)
            raise BridgeError(f"cannot send '{op}' to the WSTS bridge at {self.address}: {error}") from None
        return PendingRequest(self, op, request_id, waiter)

    def status(self) -> dict:
        """Tick, run state, fault and scenario state, bus counters and endpoint names. What
        each link sent and received is under "link_traffic": {link: {"sent", "received"}}."""
        response = self.request("status")
        response["endpoints"] = [e for e in response.get("endpoints", "").split(",") if e]
        return link_traffic(response)

    def bus_stats(self) -> Dict[str, dict]:
        """The traffic of every bus endpoint since start-up: {endpoint: {"transactions",
        "to_device", "to_flight", "bytes_to_device", "bytes_to_flight", "errors", "unanswered",
        "fault_hits", "last_tick", "addresses": {"0x48": {"transactions", "errors",
        "unanswered"}}}}. "unanswered" counts what nothing answered (no device, none at the I2C
        address, nothing at the register offset); "addresses" are I2C addresses or register
        offsets."""
        return bus_traffic(self.request("bus_stats"))

    def tick_times(self) -> dict:
        """Stepped time: how long the ticks took in host time, from the start of a tick until
        its work was finished: {"ticks" (ticks timed), "mean_ms", "max_ms", "over_period"
        (ticks that took longer than the tick period: on the target they would have overrun),
        "slowest": [{"tick", "ms"}, ...]} with the five slowest, slowest first. Ticks taken in
        real time, and one that did not finish, are not timed. A hint, never a failure."""
        return tick_times(self.request("tick_times"))

    # ---- Run control ----

    def pause(self) -> None:
        self.request("pause")

    def resume(self) -> None:
        self.request("resume")

    def set_rate(self, rate: float) -> None:
        """Simulated time per wall-clock time. In stepped time it is a limit, and 0 takes
        the limit away: as fast as the host computes."""
        self.request("set_rate", rate=rate)

    def set_stepped(self, stepped: bool = True, lockstep: Optional[bool] = None) -> None:
        """Stepped time: a tick is taken when flight software has finished the last one,
        not when the wall clock says so, and what this client sends is applied between two
        ticks. False returns to real time. `lockstep` (an element of a system stepped as a
        whole, by `WstsSystem.set_stepped`): a tick is taken only when the system's conductor
        grants one."""
        fields = {"stepped": bool(stepped)}
        if lockstep is not None:
            fields["lockstep"] = bool(lockstep)
        self.request("set_stepped", **fields)

    def step(self, ticks: int = 1, wait: bool = True, timeout: float = 30.0) -> int:
        """Run the simulation for `ticks` ticks, then pause it. From a paused simulation the
        count is exact: flight software runs for that many ticks and stops. With `wait`,
        block until it has paused again and return the tick it stopped at."""
        target = self.status()["tick"] + ticks
        self.request("step", ticks=int(ticks))
        if not wait:
            return target
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if status["paused"] and not status.get("stepping"):
                if status.get("counting"):
                    # The ticks are taken; what they caused is finished when this returns
                    self.wait_quiescent(max(0.1, deadline - time.monotonic()))
                return status["tick"]
            if time.monotonic() >= deadline:
                raise BridgeError(f"the simulation did not finish a step of {ticks} tick(s) within {timeout} s "
                                  f"(at tick {status['tick']}, {status.get('stepping')} to go)")
            time.sleep(0.01)

    def wait_quiescent(self, timeout: float = 5.0) -> None:
        """Block until flight software has finished everything it was given: no message is
        waiting on a task's queue and no handler is running. Meaningful while the
        simulation is paused (a running one is given more work every tick). Raises
        BridgeError, naming the queues that hold work, if that does not happen in time.
        Needs a deployment generated by this version of the toolkit (`status()["counting"]`)."""
        deadline = time.monotonic() + timeout
        while True:
            # Short waits on the simulation's side, so that a request never outlives its timeout
            slice_ms = int(max(1, min(deadline - time.monotonic(), self.request_timeout / 2, 1.0) * 1000))
            answer = self.request("wait_quiescent", timeout_ms=slice_ms)
            if answer.get("quiescent", True):
                return
            if time.monotonic() >= deadline:
                raise BridgeError(f"flight software did not finish its work within {timeout} s: "
                                  f"{answer.get('activity')} piece(s) outstanding"
                                  + (f" ({answer['holding']})" if answer.get("holding") else ""))

    def wait_for_tick(self, tick: int, timeout: float = 30.0) -> int:
        """Block until the simulation reaches `tick`. Returns the tick reached."""
        deadline = time.monotonic() + timeout
        while True:
            now = self.status()["tick"]
            if now >= tick:
                return now
            if time.monotonic() >= deadline:
                raise BridgeError(f"simulation did not reach tick {tick} within {timeout} s (at {now})")
            time.sleep(0.02)

    def advance(self, ticks: int, timeout: float = 30.0) -> int:
        """Block until `ticks` more ticks have elapsed"""
        return self.wait_for_tick(self.status()["tick"] + ticks, timeout)

    # ---- Faults and injection ----

    def fault_set(self, endpoint: str, kind: str, arg: int = 0, count: int = 0, ticks: int = 0, direction: str = "both") -> None:
        """Arm a fault. kind: drop, corrupt, delay, duplicate, error, stuck."""
        self.request("fault_set", endpoint=endpoint, kind=kind, arg=arg, count=count, ticks=ticks, dir=direction)

    def fault_clear(self, endpoint: str = "*") -> None:
        self.request("fault_clear", endpoint=endpoint)

    def inject_bytes(self, endpoint: str, data: bytes) -> None:
        """Deliver bytes to flight software as if the device had sent them"""
        self.request("inject_bytes", endpoint=endpoint, data=bytes(data).hex())

    def inject_text(self, endpoint: str, text: str) -> None:
        self.inject_bytes(endpoint, text.encode("utf-8"))

    def inject_packet(self, endpoint: str, packet: bytes) -> None:
        """Deliver one packet to flight software on a packet (SpaceWire) endpoint, as if a
        node had sent it"""
        self.request("inject_packet", endpoint=endpoint, data=bytes(packet).hex())

    def gpio_force(self, endpoint: str, level: bool) -> None:
        """Drive a GPIO input pin"""
        self.request("gpio_force", endpoint=endpoint, level=bool(level))

    def gpio_get(self, endpoint: str) -> bool:
        """Level flight software last wrote to a GPIO pin"""
        return bool(self.request("gpio_get", endpoint=endpoint)["level"])

    def i2c_reg_set(self, endpoint: str, addr: int, reg: int, value: int) -> None:
        self.request("i2c_reg_set", endpoint=endpoint, addr=addr, reg=reg, value=value)

    def reg_set(self, endpoint: str, offset: int, value: int) -> None:
        """Set a register of the register block on an endpoint, without a flight write"""
        self.request("reg_set", endpoint=endpoint, offset=hex(offset), value=hex(value))

    def raise_irq(self, endpoint: str, number: int = 0) -> None:
        """Raise an interrupt of the register block on an endpoint. Flight software's handler
        runs on the simulation's bridge thread, as it would in an interrupt context."""
        self.request("irq_raise", endpoint=endpoint, number=number)

    def model_set(self, model: str, key: str, value) -> None:
        self.request("model_set", model=model, key=key, value=str(value))

    def model_get(self, model: str, key: str) -> str:
        """A model's value, as text (Model::get: `text` of a scripted byte stream, a register
        of a register map, `reg.<offset>` of a register file, `mem.<address>` of an RMAP
        target). Reading changes nothing and is not recorded."""
        return str(self.request("model_get", model=model, key=key)["value"])

    def reg_get(self, endpoint: str, offset: int) -> int:
        """A register of the block on an endpoint, read without a flight read: no read hook,
        nothing cleared, not on the bus"""
        return _number(self.request("reg_get", endpoint=endpoint, offset=hex(offset))["value"])

    def batch(self, actions: List[dict]) -> int:
        """Apply several actions in order between the same two ticks, in one round trip. Each
        is executed and recorded as if sent by itself. Returns how many were applied; raises
        BridgeError at the first that fails (the ones before it stay applied)."""
        if not actions:
            return 0
        lines = "\n".join(json.dumps(action, separators=(",", ":")) for action in actions)
        return int(self.request("batch", actions=lines)["applied"])

    # ---- Scenarios and recording ----

    def record_start(self, path: str) -> None:
        self.request("record_start", path=str(path))

    def record_stop(self) -> None:
        self.request("record_stop")

    def scenario_load(self, path: str, mode: str = "stimulus") -> None:
        self.request("scenario_load", path=str(path), mode=mode)

    def scenario_start(self) -> None:
        self.request("scenario_start")

    def scenario_stop(self) -> None:
        self.request("scenario_stop")

    def run_scenario(self, path: str, mode: str = "stimulus", timeout: float = 60.0) -> None:
        """Load and start a scenario, then block until it completes"""
        self.scenario_load(path, mode)
        self.scenario_start()
        deadline = time.monotonic() + timeout
        while self.status()["scenario_state"] == "running":
            if time.monotonic() >= deadline:
                raise BridgeError(f"scenario {path} did not complete within {timeout} s")
            time.sleep(0.05)

    # ---- Observation ----

    def subscribe(self) -> None:
        """Start receiving bus records, executed actions and simulation events"""
        self.request("subscribe")

    def events(self, clear: bool = False) -> List[dict]:
        """Everything received since subscribe(): bus records ("rec"), actions and sim events"""
        with self._events_changed:
            copy = list(self._events)
            if clear:
                self._events.clear()
            return copy

    def trim_events(self, keep: int) -> None:
        """Keep only the latest `keep` messages received since subscribe(), so that a long-lived
        client (the MCP server) holds a bounded amount"""
        with self._events_changed:
            if len(self._events) > keep:
                del self._events[:len(self._events) - keep]

    def wait_for(self, predicate: Callable[[dict], bool], timeout: float = 10.0) -> dict:
        """Block until a received message satisfies `predicate`; requires subscribe()"""
        deadline = time.monotonic() + timeout
        seen = 0
        with self._events_changed:
            while True:
                for message in self._events[seen:]:
                    if predicate(message):
                        return message
                seen = len(self._events)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    raise BridgeError("no matching bus record or event arrived in time")
                self._events_changed.wait(remaining)

    def bytes_sent(self, endpoint: str) -> bytes:
        """Bytes flight software has transmitted on a byte stream endpoint since subscribe()"""
        return b"".join(
            bytes.fromhex(m.get("data", "")) for m in self.events() if m.get("rec") == "bs_tx" and m.get("endpoint") == endpoint
        )

    # ---- Acting as a device model ----

    def serve_i2c(self, endpoint: str, addr: int, handler: Callable[[bytes, int], Tuple[int, bytes]]) -> None:
        """Become the I2C device at endpoint/addr.

        `handler(write, read_length)` returns `(status, data)`; status 0 is success.
        """
        self._i2c[(endpoint, addr)] = handler
        self.request("attach", endpoint=endpoint, kind="i2c", addr=addr)

    def serve_spi(self, endpoint: str, handler: Callable[[bytes, int], Tuple[int, bytes]]) -> None:
        """Become the SPI device on an endpoint"""
        self._spi[endpoint] = handler
        self.request("attach", endpoint=endpoint, kind="spi")

    def serve_register(self, endpoint: str, read_handler: Callable, write_handler: Optional[Callable] = None) -> None:
        """Become the register block on an endpoint, in place of any model there.

        `read_handler(offset, width)` returns the value, or `(status, value)`; width is 4 or
        8 bytes and status 0 is success. `write_handler(offset, width, value)` returns None
        or a status; without one, writes are accepted and ignored. A handler that fails, or
        does not answer within two seconds, gives flight software all ones on a read.
        Raise the block's interrupts with `raise_irq`. The handlers run on this client's
        reader thread: do not call the bridge from inside one.
        """
        self._registers[endpoint] = (read_handler, write_handler)
        self.request("attach", endpoint=endpoint, kind="register")

    def serve_bytestream(self, endpoint: str, handler: Callable[[bytes], Optional[bytes]]) -> None:
        """Become the device on a byte stream endpoint: `handler(data)` is called with what
        flight software sends, and what it returns (bytes, or None for nothing) is delivered
        to flight software as the device's answer. Send at other times with `inject_bytes`.
        The handler runs on a thread of this client, one call at a time, and may call the
        bridge. Subscribes, if this client has not yet."""
        def on_record(message: dict) -> None:
            answer = handler(bytes.fromhex(message.get("data", "")))
            if answer:
                self.inject_bytes(endpoint, bytes(answer))
        self._watch("bs_tx", endpoint, on_record)

    def serve_packet(self, endpoint: str, addr: Union[int, str], handler: Callable[[bytes], Optional[bytes]]) -> None:
        """Become the node at a logical address (32 to 255, or "any" for the addresses that have
        no node) of a packet (SpaceWire) endpoint. `handler(packet)` is called with each packet
        routed there, from its logical address on, and returns the reply packet (bytes,
        addressed as the node would address it) or None. The reply reaches flight software
        during the send, as a model's would. Send at other times with `inject_packet`. The
        handler runs on this client's reader thread: do not call the bridge from inside one."""
        key = "any" if str(addr).lower() == "any" else int(addr)
        self._packets[(endpoint, key)] = handler
        self.request("attach", endpoint=endpoint, kind="packet", addr=key)

    def packets_sent(self, endpoint: str) -> List[bytes]:
        """Packets flight software has sent on a packet endpoint since subscribe(), whole"""
        return [bytes.fromhex(m.get("data", "")) for m in self.events()
                if m.get("rec") == "pkt_tx" and m.get("endpoint") == endpoint]

    def serve_gpio(self, endpoint: str, handler: Callable[[bool], None]) -> None:
        """Be told each level flight software writes to a GPIO output pin: `handler(level)`.
        The handler may call the bridge, for example to drive an input with `gpio_force`."""
        self._watch("gpio_out", endpoint, lambda message: handler(bool(message.get("level"))))

    def register_accesses(self, endpoint: str) -> List[dict]:
        """Register reads, writes and interrupts seen on an endpoint since subscribe(), as
        `{"rec": "reg_rd"|"reg_wr"|"reg_irq", "offset", "value", "status"}` (an interrupt
        has "number" instead)"""
        found = []
        for message in self.events():
            if message.get("endpoint") != endpoint or message.get("rec") not in ("reg_rd", "reg_wr", "reg_irq"):
                continue
            entry = {"rec": message["rec"], "tick": message.get("tick"), "src": message.get("src")}
            if message["rec"] == "reg_irq":
                entry["number"] = _number(message.get("number", 0))
            else:
                data = message.get("read" if message["rec"] == "reg_rd" else "write", "")
                entry.update(offset=_number(message.get("offset", 0)), status=int(message.get("status", 0)),
                             value=int.from_bytes(bytes.fromhex(data), "little"))
            found.append(entry)
        return found


class WstsSystem:
    """Connections to every element of a running system (one simulated deployment each).

        with WstsSystem("examples/mission-sim/wsts/Mission") as system:
            system["payload"].inject_text("sensorUart", "$TEMP,48.0\\n")   # one element's bridge
            system.fault_set("hub", "drop")                                  # cut the link named hub
            system.pause()                                                   # every element

    `system` is the generated system directory, its wsts-system.json, or that file's content.
    Elements whose bridge is off are left out.

    In lockstep (`set_stepped`, or `fprime-wsts run <system> --stepped`) the system is
    stepped as a whole, in simulated time: `step(seconds=0.5)`, `time()`, `run_scenario()`.
    """

    def __init__(self, system, connect_timeout: float = 10.0, request_timeout: float = 5.0):
        if not isinstance(system, dict):
            path = os.fspath(system)
            running = None
            if os.path.isdir(path):
                running = _running(path)
                path = os.path.join(path, "wsts-system.json")
            with open(path) as file:
                system = json.load(file)
            # A run started with free ports (`fprime-wsts run --auto-ports`) says where it is
            for element in system["elements"]:
                element["bridge"] = (running or {}).get("elements", {}).get(element["name"], element["bridge"])
        self.description = system
        self.name: str = system["system"]
        self.ground: str = system["ground"]
        self.links: Dict[str, dict] = {link["name"]: link for link in system.get("links", [])}
        self.elements: Dict[str, WstsBridge] = {}
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._conductor = None
        try:
            for element in system["elements"]:
                if element["bridge"] != "none":
                    self.elements[element["name"]] = WstsBridge(element["bridge"], connect_timeout, request_timeout)
        except BridgeError:
            self.close()
            raise

    def close(self) -> None:
        if self._conductor is not None:
            self._conductor.close()
            self._conductor = None
        for bridge in self.elements.values():
            bridge.close()

    def __enter__(self) -> "WstsSystem":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __getitem__(self, element: str) -> WstsBridge:
        try:
            return self.elements[element]
        except KeyError:
            raise BridgeError(f"no element '{element}' with a bridge in system {self.name} "
                              f"(elements: {', '.join(self.elements)})") from None

    def __iter__(self):
        return iter(self.elements)

    # ---- The whole system ----

    def status(self) -> Dict[str, dict]:
        return {name: bridge.status() for name, bridge in self.elements.items()}

    def links_up(self) -> bool:
        """Every link end that has a connection is connected to its peer. A datagram link
        has none, and is left out."""
        expected = links_expected_up(self.description)
        return all(status.get("links_up", 0) >= expected.get(name, 0) for name, status in self.status().items())

    def subscribe(self) -> None:
        for bridge in self.elements.values():
            bridge.subscribe()

    def pause(self) -> None:
        """Pause every element. In real time the elements stop one after another, not at the
        same instant. In lockstep a conductor that runs freely (`fprime-wsts run --stepped`)
        stops at the end of the instant it is in."""
        for bridge in self.elements.values():
            bridge.pause()

    def resume(self) -> None:
        """Resume every element. In lockstep time runs only while a conductor runs it: one that
        runs freely goes on; otherwise `step` it."""
        for bridge in self.elements.values():
            bridge.resume()

    def set_rate(self, rate: float) -> None:
        for bridge in self.elements.values():
            bridge.set_rate(rate)

    # ---- Lockstep: the system stepped as a whole ----

    def set_stepped(self, stepped: bool = True) -> None:
        """Lockstep: every element in stepped time, taking a tick only when this system's
        conductor grants one, and what links bring held until the tick after it was sent.
        Time then runs with `step`. False returns every element to real time; `pause()`
        before and `resume()` after, as for one element."""
        for bridge in self.elements.values():
            bridge.set_stepped(stepped, lockstep=bool(stepped))

    def conductor(self):
        """The conductor of this client (lockstep.Conductor), with connections of its own"""
        from .lockstep import Conductor

        if self._conductor is None:
            self._conductor = Conductor(self.description, self._connect_timeout, self._request_timeout)
        return self._conductor

    def time(self) -> float:
        """The system's simulated time in seconds: that of the last instant taken"""
        conductor = self.conductor()
        conductor.refresh()
        conductor.check()
        return conductor.time_ns / 1e9

    def step(self, seconds: Optional[float] = None, instants: Optional[int] = None) -> float:
        """Lockstep: advance the system's simulated time by `seconds`, or by a number of
        `instants` (the times at which any element has a tick; one by default). Each tick is
        finished, on every element, when this returns. Returns the system's time in seconds.
        Raises lockstep.Stuck if an element's tick did not finish."""
        conductor = self.conductor()
        if seconds is None:
            conductor.run(instants=1 if instants is None else int(instants))
        else:
            conductor.refresh()
            conductor.check()
            conductor.run(until_ns=conductor.time_ns + int(round(float(seconds) * 1e9)), instants=instants)
        return conductor.time_ns / 1e9

    def run_scenario(self, path) -> float:
        """Lockstep: play a system scenario (lockstep.SystemScenario: actions with `time` in
        simulated seconds and the `element` or `link` they are for) from now to its last
        action. Returns the system's time in seconds."""
        from .lockstep import SystemScenario

        conductor = self.conductor()
        scenario = SystemScenario.load(path, list(self.elements), list(self.links))
        conductor.refresh()
        conductor.check()
        conductor.run(until_ns=conductor.time_ns + scenario.end_ns, scenario=scenario)
        return conductor.time_ns / 1e9

    # ---- Faults, addressed by link or by element ----

    def resolve(self, target: str, direction: str = "both") -> Tuple[str, str, str]:
        """(element, endpoint, fault direction) for a link name or "<element>.<endpoint>".

        A link is faulted at its `a` end, where "a_to_b" is what that element sends and
        "b_to_a" is what it receives; "both" cuts the link in both directions."""
        if target in self.links:
            directions = {"both": "both", "a_to_b": "to_device", "b_to_a": "to_flight"}
            if direction not in directions:
                raise BridgeError(f"direction for a link is one of {', '.join(directions)}, not '{direction}'")
            end = self.links[target]["a"]
            return end["element"], end["endpoint"], directions[direction]
        element, _, endpoint = target.partition(".")
        if element not in self.elements or not endpoint:
            raise BridgeError(f"'{target}' is neither a link ({', '.join(self.links) or 'none'}) nor "
                              f"<element>.<endpoint> with one of the elements {', '.join(self.elements)}")
        return element, endpoint, direction

    def fault_set(self, target: str, kind: str, arg: int = 0, count: int = 0, ticks: int = 0, direction: str = "both") -> None:
        """Arm a fault on a link (by name) or on "<element>.<endpoint>" """
        element, endpoint, direction = self.resolve(target, direction)
        self[element].fault_set(endpoint, kind, arg=arg, count=count, ticks=ticks, direction=direction)

    def fault_clear(self, target: str = "*") -> None:
        """Clear the faults on a link or "<element>.<endpoint>"; by default, all faults everywhere"""
        if target == "*":
            for bridge in self.elements.values():
                bridge.fault_clear("*")
            return
        element, endpoint, _ = self.resolve(target)
        self[element].fault_clear(endpoint)
