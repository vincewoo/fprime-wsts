"""The signal language of model parameters (fprime_wsts.signals), which is Wsts/Core/Signal.hpp
again: the values here are those the C++ unit tests hold (Wsts/Core/test/ut, SignalTest)."""

import pytest

from fprime_wsts.signals import Format, SignalError, is_plain_number, signal


def at(text, seconds):
    return signal(text).at(seconds)


def test_functions_of_simulated_time():
    assert at("sine(offset=25, amplitude=5, period=60)", 15) == pytest.approx(30)
    assert at("sine(25, 5, 60)", 45) == pytest.approx(20)
    assert at("square(low=0, high=1, period=2, duty=0.25)", 0.25) == 1
    assert at("square(low=0, high=1, period=2, duty=0.25)", 1) == 0
    assert at("triangle(0, 10, 4)", 1) == pytest.approx(5)
    assert at("triangle(0, 10, 4)", 3) == pytest.approx(5)
    assert at("sawtooth(0, 10, 4)", 3) == pytest.approx(7.5)
    assert at("ramp(from=12, to=10, start=10, duration=20)", 5) == 12
    assert at("ramp(from=12, to=10, start=10, duration=20)", 20) == pytest.approx(11)
    assert at("ramp(from=12, to=10, start=10, duration=20)", 99) == 10
    assert at("step(before=1, after=2, at=3)", 2.5) == 1
    assert at("step(before=1, after=2, at=3)", 3) == 2
    assert at("(t + 1) * 2 - 0x10 / 4", 3) == 4
    assert at("-step(at=0)", 1) == -1
    assert at("1 / 0", 0) == 0, "a division by zero is 0, as in C++"
    assert signal("0x10 + 2").constant and not signal("t").constant


def test_random_samples_are_those_of_the_cpp_models():
    assert at("uniform(1, 2, seed=7) * 1000", 1.5) == pytest.approx(1291.3598704631711, abs=1e-6)
    assert at("noise(sd=0.1, seed=3)", 1.5) == at("noise(sd=0.1, seed=3)", 1.5)
    assert at("noise(seed=3)", 1.5) != at("noise(seed=4)", 1.5)
    samples = [at("noise(sd=1, mean=5)", i * 0.01) for i in range(2000)]
    assert sum(samples) / len(samples) == pytest.approx(5, abs=0.1)
    assert all(-1 <= at("uniform(low=-1, high=1)", i * 0.01) < 1 for i in range(500))


def test_formats():
    stored = signal("step(before=-2, after=70000, at=1) as s16be")
    assert stored.to_bytes(0) == b"\xff\xfe" and stored.to_bytes(1) == b"\x7f\xff"
    assert signal("0x1234 + 0.4 as u16le").to_bytes(0) == b"\x34\x12"
    assert Format.parse("s8").encode(-1) == 0xFF and Format.parse("s8").encode(-500) == 0x80
    assert Format.parse("u24be").encode(2.5) == 3, "half away from zero"
    assert signal("t").encode(5) == 5, "a register without a format is a u32"
    assert Format.parse("u32le") == Format(4, False, False, True)


@pytest.mark.parametrize("text, message", [
    ("sine(1, period=0)", "period must be greater than 0"),
    ("foo(1)", "unknown function 'foo'"),
    ("sine(amp=1)", "has no argument 'amp'"),
    ("sine(phase=1, 2)", "positional argument"),
    ("sine(period=t)", "must not depend on t"),
    ("1 as u12", "unknown format"),
    ("(1 + 2", "expected '\\)'"),
    ("square(duty=2)", "duty must be between 0 and 1"),
    ("sine(1, 2, 3, 4, 5)", "at most 4 arguments"),
])
def test_errors_say_what_is_wrong(text, message):
    with pytest.raises(SignalError, match=message):
        signal(text)


def test_plain_numbers_are_not_signals():
    assert is_plain_number("0x6D 0x60") and is_plain_number("1, 2 3") and is_plain_number("-5")
    assert not is_plain_number("sine(1)") and not is_plain_number("1400 as u16be") and not is_plain_number("t")
