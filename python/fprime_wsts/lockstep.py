"""Lockstep: the elements of a system stepped together in simulated time.

Phase 4 of stepped time (docs/stepped-time.md). Every element runs in stepped time with
`lockstep` on: it takes a tick only when it is granted one. The conductor advances the
system's simulated time instant by instant. An instant is the next time at which any
element has a tick (Cdh at 10 Hz and Payload at 20 Hz give one every 50 ms, with Cdh in
every other one). The conductor grants one tick to each element due at that instant, all
at once, and waits until every one of them has finished it.

What a link brings is tagged with the simulated time it was sent at and held by the
receiving element until its first tick later than that. Before granting a tick, the
conductor tells the receiving end how many messages the other end had sent by the end of
the last instant: the tick waits for them, so a slow host cannot make one arrive a tick
late. What elements send to each other in the same instant goes to the next one.

Actions are applied between instants. A system scenario places them on the system's
time base, in simulated seconds:

    {"time": 2.0, "element": "payload", "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\\n"}
    {"time": 3.0, "link": "hub", "op": "fault_set", "kind": "drop", "direction": "a_to_b"}

The conductor has bridge connections of its own: the requests that grant ticks block their
connection until the ticks are finished.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .bridge import BridgeError, WstsBridge, link_traffic

#: Wall-clock seconds an element may take over one instant before the conductor gives up.
#: An element reports a tick that does not finish itself, after WSTS_STEP_LIMIT_MS (10 s).
INSTANT_LIMIT = 60.0


class LockstepError(BridgeError):
    """The system cannot be stepped, or an element stopped in the middle of an instant"""


class Stuck(LockstepError):
    """An element did not finish a tick. `stuck` maps each such element to where its work is."""

    def __init__(self, stuck: Dict[str, str], time_ns: int):
        self.stuck = stuck
        self.time_ns = time_ns
        super().__init__("; ".join(f"{name}: {why}" for name, why in stuck.items())
                         + f" (at {time_ns / 1e9:g} s of simulated time)")


def _seconds_to_ns(seconds: float) -> int:
    return int(round(float(seconds) * 1e9))


class SystemScenario:
    """Actions on the time base of a whole system: one JSON object per line, with `time` in
    simulated seconds from the start of the scenario, and `element` (or, for `fault_set` and
    `fault_clear`, `link`) saying where. The rest of the line is the action, as in an
    element's scenario. Blank lines and lines starting with # are skipped."""

    def __init__(self, steps: List[Tuple[int, dict]], path: str = "<scenario>"):
        self.path = path
        self.steps = sorted(steps, key=lambda step: step[0])  # stable: lines at one time keep their order
        self.next = 0
        self.start_ns: Optional[int] = None

    @classmethod
    def load(cls, path, elements: List[str], links: List[str]) -> "SystemScenario":
        steps: List[Tuple[int, dict]] = []
        for number, line in enumerate(Path(path).read_text().splitlines(), 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            where = f"{path}:{number}"
            try:
                step = json.loads(text)
            except ValueError as error:
                raise LockstepError(f"{where}: {error}") from None
            if not isinstance(step, dict) or "op" not in step:
                raise LockstepError(f"{where}: a line of a system scenario is an action with an 'op'")
            if "time" not in step:
                raise LockstepError(f"{where}: no 'time' (simulated seconds from the start of the scenario); "
                                    "a system has no one tick count")
            if "link" in step:
                if step["op"] not in ("fault_set", "fault_clear"):
                    raise LockstepError(f"{where}: a link takes fault_set and fault_clear only, not {step['op']}")
                if step["link"] not in links:
                    raise LockstepError(f"{where}: no link '{step['link']}' in this system "
                                        f"({', '.join(links) or 'none'})")
            elif step.get("element") not in elements:
                raise LockstepError(f"{where}: say which element the action is for: \"element\": one of "
                                    + ", ".join(elements))
            try:
                at = _seconds_to_ns(step.pop("time"))
            except (TypeError, ValueError):
                raise LockstepError(f"{where}: 'time' is a number of seconds") from None
            if at < 0:
                raise LockstepError(f"{where}: 'time' is not negative")
            steps.append((at, step))
        return cls(steps, str(path))

    @property
    def done(self) -> bool:
        return self.next >= len(self.steps)

    @property
    def end_ns(self) -> int:
        """Time of the last action, from the start of the scenario"""
        return self.steps[-1][0] if self.steps else 0

    def due(self, system_ns: int) -> List[dict]:
        """The actions whose time the system has reached"""
        if self.start_ns is None:
            self.start_ns = system_ns
        found = []
        while self.next < len(self.steps) and self.start_ns + self.steps[self.next][0] <= system_ns:
            found.append(self.steps[self.next][1])
            self.next += 1
        return found


class Conductor:
    """Steps the elements of a system together (lockstep). `system` is what `WstsSystem`
    takes: the generated system directory, its wsts-system.json, or that file's content."""

    def __init__(self, system, connect_timeout: float = 10.0, request_timeout: float = 5.0):
        from .bridge import WstsSystem

        # Connections of its own: a grant blocks its connection until the tick is finished
        self.system = WstsSystem(system, connect_timeout=connect_timeout, request_timeout=request_timeout)
        missing = [element["name"] for element in self.system.description["elements"]
                   if element["name"] not in self.system.elements]
        if missing:
            self.system.close()
            raise LockstepError(f"element(s) {', '.join(missing)} have no bridge: a system is stepped through the "
                                "bridges of all its elements")
        self.bridges: Dict[str, WstsBridge] = self.system.elements
        # (link, element at one end, element at the other)
        self.ends: List[Tuple[str, str, str]] = []
        for name, link in self.system.links.items():
            self.ends.append((name, link["a"]["element"], link["b"]["element"]))
            self.ends.append((name, link["b"]["element"], link["a"]["element"]))
        self.state: Dict[str, dict] = {}
        self.instants = 0  # instants taken by this conductor

    def close(self) -> None:
        self.system.close()

    def __enter__(self) -> "Conductor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- State ----

    def refresh(self, names: Optional[List[str]] = None) -> Dict[str, dict]:
        """Read the status of the elements (all of them by default)"""
        for name in names or list(self.bridges):
            self.state[name] = self.bridges[name].status()
        return self.state

    def check(self) -> None:
        """Raise LockstepError unless every element is in lockstep and knows its tick period"""
        problems = []
        for name, status in self.state.items():
            if not status.get("lockstep"):
                problems.append(f"{name} is not in lockstep" + ("" if "lockstep" in status else
                                " (its deployment was built by an older fprime-wsts: build it again)"))
            elif not status.get("tick_period_ns"):
                problems.append(f"{name}'s tick source has not said how long a tick is yet")
        if problems:
            raise LockstepError("; ".join(problems))

    def wait_ready(self, timeout: float = 20.0, alive: Callable[[], bool] = lambda: True) -> None:
        """Wait until every element is in lockstep, has settled after start-up and knows its
        tick period: the state the first instant must meet in every run"""
        deadline = time.monotonic() + timeout
        while True:
            self.refresh()
            waiting = [name for name, status in self.state.items()
                       if not (status.get("tick_period_ns") and status.get("settled", True))]
            if not waiting:
                break
            if time.monotonic() >= deadline or not alive():
                raise LockstepError(f"element(s) {', '.join(waiting)} did not settle after start-up and say how long "
                                    f"a tick is within {timeout:g} s")
            time.sleep(0.02)
        self.check()

    def element_ns(self, name: str) -> int:
        status = self.state[name]
        return int(status["tick"]) * int(status["tick_period_ns"])

    @property
    def time_ns(self) -> int:
        """The system's simulated time: that of the last instant taken. Each element is at its
        own last tick, at or before it."""
        return max(self.element_ns(name) for name in self.state)

    def next_instant(self) -> Tuple[int, List[str]]:
        """The time of the next instant, and the elements that have a tick at it"""
        upcoming = {name: (int(status["tick"]) + 1) * int(status["tick_period_ns"])
                    for name, status in self.state.items()}
        at = min(upcoming.values())
        return at, [name for name in self.bridges if upcoming[name] == at]

    def expectations(self, name: str) -> Dict[str, int]:
        """What each link of an element will have received before its next tick: what the
        other end had sent by the end of the last instant"""
        found = {}
        for link, here, there in self.ends:
            if here == name:
                sent = self.state[there].get("link_traffic", {}).get(link, {}).get("sent", 0)
                found[f"expect.{link}"] = int(sent)
        return found

    def paused(self) -> List[str]:
        return [name for name, status in self.state.items() if status.get("paused")]

    # ---- Time ----

    def instant(self) -> int:
        """Take the next instant: grant a tick to each element due at it, at once, and wait
        until all have finished. Returns its time."""
        at, due = self.next_instant()
        pending = {name: self.bridges[name].submit("advance", ticks=1, timeout_ms=int(INSTANT_LIMIT * 1000),
                                                   **self.expectations(name))
                   for name in due}
        stuck: Dict[str, str] = {}
        for name, request in pending.items():
            try:
                answer = link_traffic(request.result(INSTANT_LIMIT + 5))
            except BridgeError as error:
                raise LockstepError(f"element {name} at {at / 1e9:g} s of simulated time: {error}") from None
            self.state[name] = answer
            if answer.get("stuck"):
                stuck[name] = answer["stuck"]
            elif not answer.get("done", False):
                stuck[name] = f"its tick did not finish within {INSTANT_LIMIT:g} s"
        if stuck:
            raise Stuck(stuck, at)
        self.instants += 1
        return at

    def apply(self, steps: List[dict]) -> None:
        """Apply actions between two instants, each on its element (or the `a` end of its
        link), then read again what those elements sent"""
        touched = []
        for step in steps:
            step = dict(step)
            if "link" in step:
                element, endpoint, direction = self.system.resolve(step.pop("link"), step.pop("direction", "both"))
                step["endpoint"] = endpoint
                if step["op"] == "fault_set":
                    step["dir"] = direction
            else:
                element = step.pop("element")
            op = step.pop("op")
            try:
                self.bridges[element].request(op, **step)
            except BridgeError as error:
                raise LockstepError(f"{op} on element {element}: {error}") from None
            touched.append(element)
        if touched:
            self.refresh(sorted(set(touched)))

    def run(self, until_ns: Optional[int] = None, instants: Optional[int] = None,
            scenario: Optional[SystemScenario] = None, hold_while_paused: bool = False,
            keep_going: Callable[[], bool] = lambda: True) -> int:
        """Take instants until the system's time would pass `until_ns`, or `instants` have been
        taken, or `keep_going()` says no. With `hold_while_paused`, wait while any element is
        paused (someone paused the system: a pause from the GDS, or `WstsSystem.pause()`).
        Returns the system's time."""
        self.refresh()
        self.check()
        taken = 0
        while keep_going():
            if scenario is not None:
                self.apply(scenario.due(self.time_ns))
            if instants is not None and taken >= instants:
                break
            if hold_while_paused and self.paused():
                time.sleep(0.02)
                self.refresh()
                continue
            at, _ = self.next_instant()
            if until_ns is not None and at > until_ns and (scenario is None or scenario.done):
                break
            self.instant()
            taken += 1
        return self.time_ns
