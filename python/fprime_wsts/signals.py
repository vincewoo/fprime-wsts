"""Values that change with simulated time: the signal language of device model parameters.

The same language as `Wsts/Core/Signal.hpp`, evaluated the same way, so that

* the manifest's parameters of stock models are checked when the deployment is generated,
  not when it starts, and
* a Python model (`run.models`, or a test serving a device through the bridge) can use the
  signals the C++ models use:

      from fprime_wsts.signals import signal

      temperature = signal("sine(offset=25, amplitude=5, period=60) + noise(sd=0.1)")
      temperature.at(12.5)                       # value at 12.5 s of simulated time
      stored = signal("ramp(from=12.6, to=11.1, duration=600) * 1000 as u16be")
      stored.to_bytes(30.0)                      # b'\\x30\\x...'

Functions (arguments by position, in this order, or by name; all are optional):

    sine(offset=0, amplitude=1, period=1, phase=0)
    square(low=0, high=1, period=1, duty=0.5, phase=0)
    triangle(low=0, high=1, period=1, phase=0)
    sawtooth(low=0, high=1, period=1, phase=0)
    ramp(from=0, to=1, start=0, duration=1)
    step(before=0, after=1, at=0)
    noise(sd=1, mean=0, seed=0)
    uniform(low=0, high=1, seed=0)

`t` is the simulated time in seconds; `+ - * /` and parentheses combine terms. A stored value
ends in `as <format>`: u8, s8, u16, s16, u24, s24, u32 or s32, with `be` or `le` for byte order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

TWO_PI = 2 * math.pi
MASK64 = (1 << 64) - 1

#: Function name -> parameters in positional order, with defaults
FUNCTIONS = {
    "sine": [("offset", 0.0), ("amplitude", 1.0), ("period", 1.0), ("phase", 0.0)],
    "square": [("low", 0.0), ("high", 1.0), ("period", 1.0), ("duty", 0.5), ("phase", 0.0)],
    "triangle": [("low", 0.0), ("high", 1.0), ("period", 1.0), ("phase", 0.0)],
    "sawtooth": [("low", 0.0), ("high", 1.0), ("period", 1.0), ("phase", 0.0)],
    "ramp": [("from", 0.0), ("to", 1.0), ("start", 0.0), ("duration", 1.0)],
    "step": [("before", 0.0), ("after", 1.0), ("at", 0.0)],
    "noise": [("sd", 1.0), ("mean", 0.0), ("seed", 0.0)],
    "uniform": [("low", 0.0), ("high", 1.0), ("seed", 0.0)],
}


class SignalError(ValueError):
    """A signal that cannot be parsed"""


def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
    return x ^ (x >> 31)


def _unit(h: int) -> float:
    """Uniform in (0, 1], from 53 bits of a hash"""
    return ((h >> 11) + 1.0) * (1.0 / 9007199254740992.0)


def _fraction(x: float) -> float:
    return x - math.floor(x)


def _seed(value: float) -> int:
    # llround, then the two's complement bits, as the C++ casts do
    rounded = math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)
    return int(rounded) & MASK64


def _function(name: str, a: List[float], t: float, ns: int) -> float:
    if name == "sine":
        return a[0] + a[1] * math.sin(TWO_PI * (t / a[2] + a[3]))
    if name == "square":
        return a[1] if _fraction(t / a[2] + a[4]) < a[3] else a[0]
    if name == "triangle":
        x = _fraction(t / a[2] + a[3])
        return a[0] + (a[1] - a[0]) * (2 * x if x < 0.5 else 2 * (1 - x))
    if name == "sawtooth":
        return a[0] + (a[1] - a[0]) * _fraction(t / a[2] + a[3])
    if name == "ramp":
        if t <= a[2]:
            return a[0]
        if a[3] <= 0 or t >= a[2] + a[3]:
            return a[1]
        return a[0] + (a[1] - a[0]) * (t - a[2]) / a[3]
    if name == "step":
        return a[0] if t < a[2] else a[1]
    if name == "noise":
        h1 = _splitmix64(_seed(a[2]) ^ _splitmix64(ns))
        h2 = _splitmix64(h1)
        return a[1] + a[0] * math.sqrt(-2.0 * math.log(_unit(h1))) * math.cos(TWO_PI * _unit(h2))
    if name == "uniform":
        h = _splitmix64(_seed(a[2]) ^ _splitmix64(ns))
        return a[0] + (a[1] - a[0]) * (_unit(h) - 1.0 / 9007199254740992.0)
    raise AssertionError(name)


# A node is (evaluate(t, ns) -> float, constant)
Node = Tuple[Callable[[float, int], float], bool]

_TOKEN = re.compile(r"\s*(?:(0[xX][0-9a-fA-F]+)|((?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(.))")


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens: List[Tuple[str, str, int]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if m is None or m.end() == pos:
                break
            start = m.start(m.lastindex) if m.lastindex else pos
            if m.group(1):
                self.tokens.append(("num", str(int(m.group(1), 16)), start))
            elif m.group(2):
                self.tokens.append(("num", m.group(2), start))
            elif m.group(3):
                self.tokens.append(("id", m.group(3), start))
            elif m.group(4):
                if not m.group(4).isspace():
                    self.tokens.append(("op", m.group(4), start))
            pos = m.end()
        self.i = 0

    def fail(self, message: str):
        pos = self.tokens[self.i][2] if self.i < len(self.tokens) else len(self.text)
        raise SignalError(f"{message} at position {pos + 1} of '{self.text}'")

    def peek(self) -> Optional[Tuple[str, str, int]]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def accept(self, op: str) -> bool:
        token = self.peek()
        if token and token[0] == "op" and token[1] == op:
            self.i += 1
            return True
        return False

    def parse(self) -> Node:
        node = self.expression()
        if self.peek() is not None:
            self.fail(f"unexpected '{self.peek()[1]}'")
        return node

    def expression(self) -> Node:
        left = self.term()
        while True:
            if self.accept("+"):
                left = _binary(lambda x, y: x + y, left, self.term())
            elif self.accept("-"):
                left = _binary(lambda x, y: x - y, left, self.term())
            else:
                return left

    def term(self) -> Node:
        left = self.factor()
        while True:
            if self.accept("*"):
                left = _binary(lambda x, y: x * y, left, self.factor())
            elif self.accept("/"):
                left = _binary(lambda x, y: 0.0 if y == 0 else x / y, left, self.factor())
            else:
                return left

    def factor(self) -> Node:
        token = self.peek()
        if token is None:
            self.fail("expected a number, t or a function")
        if self.accept("-"):
            inner, constant = self.factor()
            return (lambda t, ns: -inner(t, ns)), constant
        if self.accept("+"):
            return self.factor()
        if self.accept("("):
            node = self.expression()
            if not self.accept(")"):
                self.fail("expected ')'")
            return node
        kind, text, _ = token
        if kind == "num":
            self.i += 1
            value = float(text)
            return (lambda t, ns: value), True
        if kind == "id":
            self.i += 1
            if text == "t":
                return (lambda t, ns: t), False
            return self.call(text)
        self.fail(f"unexpected '{text}'")

    def call(self, name: str) -> Node:
        if name not in FUNCTIONS:
            self.i -= 1
            self.fail(f"unknown function '{name}' (known: {', '.join(FUNCTIONS)}, and t)")
        params = FUNCTIONS[name]
        args = [default for _, default in params]
        given = [False] * len(params)
        if not self.accept("("):
            self.fail(f"expected '(' after {name}")
        position = 0
        named = False
        if not self.accept(")"):
            while True:
                index = position
                token = self.peek()
                nxt = self.tokens[self.i + 1] if self.i + 1 < len(self.tokens) else None
                if token and token[0] == "id" and nxt and nxt[:2] == ("op", "="):
                    keys = [key for key, _ in params]
                    if token[1] not in keys:
                        self.fail(f"{name}() has no argument '{token[1]}' (it takes {', '.join(keys)})")
                    index = keys.index(token[1])
                    named = True
                    self.i += 2
                else:
                    if named:
                        self.fail(f"a positional argument of {name}() after a named one")
                    if position >= len(params):
                        self.fail(f"{name}() takes at most {len(params)} arguments")
                    position += 1
                if given[index]:
                    self.fail(f"{name}() is given '{params[index][0]}' twice")
                evaluate, constant = self.expression()
                if not constant:
                    self.fail(f"the arguments of {name}() must not depend on t")
                args[index] = evaluate(0.0, 0)
                given[index] = True
                if not self.accept(","):
                    break
            if not self.accept(")"):
                self.fail("expected ')' or ','")
        if name in ("sine", "square", "triangle", "sawtooth") and args[2] <= 0:
            self.fail(f"{name}(): period must be greater than 0")
        if name == "square" and not 0 <= args[3] <= 1:
            self.fail(f"{name}(): duty must be between 0 and 1")
        if name == "ramp" and args[3] < 0:
            self.fail(f"{name}(): duration must not be negative")
        return (lambda t, ns: _function(name, args, t, ns)), False


def _binary(op, left: Node, right: Node) -> Node:
    (lf, lc), (rf, rc) = left, right
    return (lambda t, ns: op(lf(t, ns), rf(t, ns))), lc and rc


_FORMAT = re.compile(r"^([us])(8|16|24|32)(be|le)?$")


@dataclass(frozen=True)
class Format:
    """How a value is stored: u16be, s8, ..."""
    bytes: int = 1
    signed: bool = False
    big_endian: bool = True
    explicit_order: bool = False

    @staticmethod
    def parse(text: str) -> "Format":
        m = _FORMAT.match(text.strip())
        if not m:
            raise SignalError(f"unknown format '{text.strip()}' (u8, s8, u16be, s16le, u24be, u32le, ...)")
        return Format(int(m.group(2)) // 8, m.group(1) == "s", m.group(3) != "le", m.group(3) is not None)

    def encode(self, value: float) -> int:
        bits = 8 * self.bytes
        low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if self.signed else (0, (1 << bits) - 1)
        rounded = 0 if math.isnan(value) else math.floor(abs(value) + 0.5) * (1 if value >= 0 else -1)
        return int(min(max(rounded, low), high)) & ((1 << bits) - 1)

    def to_bytes(self, value: float) -> bytes:
        return self.encode(value).to_bytes(self.bytes, "big" if self.big_endian else "little")


@dataclass
class Signal:
    """A parsed signal, and how it is stored when `as <format>` was given"""
    text: str
    format: Optional[Format] = None
    _node: Node = field(default=((lambda t, ns: 0.0), True), repr=False)

    @property
    def constant(self) -> bool:
        return self._node[1]

    def at(self, seconds: float) -> float:
        """The value at `seconds` of simulated time"""
        return self.at_ns(int(round(seconds * 1e9)))

    def at_ns(self, ns: int) -> float:
        return float(self._node[0](ns * 1e-9, ns))

    def encode(self, seconds: float) -> int:
        """The stored integer at `seconds` (u32 when no format was given)"""
        return (self.format or Format(4)).encode(self.at(seconds))

    def to_bytes(self, seconds: float) -> bytes:
        """The stored bytes at `seconds` (big-endian u8 when no format was given)"""
        return (self.format or Format(1)).to_bytes(self.at(seconds))


def signal(text: str) -> Signal:
    """Parse `<expression> [as <format>]`; raises SignalError"""
    text = str(text)
    expression, fmt = text, None
    if " as " in text:
        expression, _, format_text = text.rpartition(" as ")
        fmt = Format.parse(format_text)
    return Signal(text.strip(), fmt, _Parser(expression).parse())


def is_plain_number(text: str) -> bool:
    """A value the stock models read as a number or a list of bytes, not as a signal"""
    return all(re.fullmatch(r"[+-]?(0[xX][0-9a-fA-F]+|\d+)", token) for token in str(text).replace(",", " ").split())
