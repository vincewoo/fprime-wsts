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
from typing import Callable, Dict, List, Optional, Tuple


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
                elif kind is not None:
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
            with self._events_changed:
                self._events_changed.notify_all()

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

    # ---- Requests ----

    def request(self, op: str, **fields) -> dict:
        """Send one action and return the simulation's response. Raises BridgeError if refused."""
        with self._responses_lock:
            request_id = self._next_id
            self._next_id += 1
            waiter: "queue.Queue[dict]" = queue.Queue()
            self._responses[request_id] = waiter
        try:
            self._send({"id": request_id, "op": op, **fields})
            try:
                response = waiter.get(timeout=self.request_timeout)
            except queue.Empty:
                raise BridgeError(f"no response to '{op}' within {self.request_timeout} s") from None
        finally:
            with self._responses_lock:
                self._responses.pop(request_id, None)
        if not response.get("ok", False):
            raise BridgeError(response.get("error", f"'{op}' failed"))
        return response

    def status(self) -> dict:
        """Tick, run state, fault and scenario state, bus counters and endpoint names"""
        response = self.request("status")
        response["endpoints"] = [e for e in response.get("endpoints", "").split(",") if e]
        return response

    # ---- Run control ----

    def pause(self) -> None:
        self.request("pause")

    def resume(self) -> None:
        self.request("resume")

    def set_rate(self, rate: float) -> None:
        self.request("set_rate", rate=rate)

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

    def gpio_force(self, endpoint: str, level: bool) -> None:
        """Drive a GPIO input pin"""
        self.request("gpio_force", endpoint=endpoint, level=bool(level))

    def gpio_get(self, endpoint: str) -> bool:
        """Level flight software last wrote to a GPIO pin"""
        return bool(self.request("gpio_get", endpoint=endpoint)["level"])

    def i2c_reg_set(self, endpoint: str, addr: int, reg: int, value: int) -> None:
        self.request("i2c_reg_set", endpoint=endpoint, addr=addr, reg=reg, value=value)

    def model_set(self, model: str, key: str, value) -> None:
        self.request("model_set", model=model, key=key, value=str(value))

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


class WstsSystem:
    """Connections to every element of a running system (one simulated deployment each).

        with WstsSystem("examples/mission-sim/wsts/Mission") as system:
            system["payload"].inject_text("sensorUart", "$TEMP,48.0\\n")   # one element's bridge
            system.fault_set("hub", "drop")                                  # cut the link named hub
            system.pause()                                                   # every element

    `system` is the generated system directory, its wsts-system.json, or that file's content.
    Elements whose bridge is off are left out.
    """

    def __init__(self, system, connect_timeout: float = 10.0, request_timeout: float = 5.0):
        if not isinstance(system, dict):
            path = os.fspath(system)
            if os.path.isdir(path):
                path = os.path.join(path, "wsts-system.json")
            with open(path) as file:
                system = json.load(file)
        self.description = system
        self.name: str = system["system"]
        self.ground: str = system["ground"]
        self.links: Dict[str, dict] = {link["name"]: link for link in system.get("links", [])}
        self.elements: Dict[str, WstsBridge] = {}
        try:
            for element in system["elements"]:
                if element["bridge"] != "none":
                    self.elements[element["name"]] = WstsBridge(element["bridge"], connect_timeout, request_timeout)
        except BridgeError:
            self.close()
            raise

    def close(self) -> None:
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
        """Every link end of every element is connected to its peer"""
        return all(status.get("links_up", 0) >= status.get("links", 0) for status in self.status().values())

    def subscribe(self) -> None:
        for bridge in self.elements.values():
            bridge.subscribe()

    def pause(self) -> None:
        """Pause every element. The elements stop one after another, not at the same instant."""
        for bridge in self.elements.values():
            bridge.pause()

    def resume(self) -> None:
        for bridge in self.elements.values():
            bridge.resume()

    def set_rate(self, rate: float) -> None:
        for bridge in self.elements.values():
            bridge.set_rate(rate)

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
