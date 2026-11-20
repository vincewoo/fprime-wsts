"""Telemetry of the registers of device models: a channel per register a stock register model
defines in the manifest, with the register's current value.

The registers are those a manifest gives a value (`params`):

* `Wsts::RegisterMapI2c`: each `"<register>"`. A value of one to four bytes, or a signal stored
  in as many bytes, is one channel (big-endian in the order given, or the signal's format);
  a longer list of bytes is a channel per register.
* `Wsts::RegisterFile`: each `reg.<offset>`, a 32-bit word (or the signal's format).

The channels belong to a component written for the deployment, `WstsAdapted.ModelTelemetry`
(its dictionary differs per deployment, which a toolkit component cannot do). It is passive:
`Wsts.SimControl` publishes it with its own telemetry, on its own thread, once a second (a
simulated second in stepped time), through `Wsts::Control::addTelemetrySource`. Without
`Wsts.SimControl` (`control: false`) there is no register telemetry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .manifest import ModelSpec
from .registers import ADAPTED_MODULE
from .signals import Format, SignalError, is_plain_number, signal

COMPONENT_BASE = "ModelTelemetry"
COMPONENT = f"{ADAPTED_MODULE}.{COMPONENT_BASE}"
INSTANCE = "wstsModelTlm"
DIRECTORY = f"{ADAPTED_MODULE}/{COMPONENT_BASE}"
HEADER = f"{DIRECTORY}/{COMPONENT_BASE}.hpp"
#: Offset of the instance's base ID in the block of the set-up anchor, which has no IDs of its own
BASE_OFFSET = 0x800
MAX_CHANNELS = 0x800

STOCK = ("Wsts::RegisterMapI2c", "Wsts::RegisterFile")


@dataclass
class Channel:
    name: str  # FPP name
    type: str  # U8, U16, U32, I8, I16, I32
    model: str  # model name in the manifest
    register: int  # register address or offset
    description: str
    read: str  # C++ expression of the raw value (std::uint64_t), given the model's variable as {var}
    bits: int = 32  # bits of the stored value (a signed value is sign-extended from them)


def _identifier(text: str) -> str:
    name = re.sub(r"\W", "_", text)
    return name if re.match(r"[A-Za-z_]", name) else f"m_{name}"


def _type(fmt: Format) -> str:
    bits = 8 * fmt.bytes
    bits = 32 if bits == 24 else bits
    return f"{'I' if fmt.signed else 'U'}{bits}"


def _i2c_read(register: int, count: int, big_endian: bool) -> str:
    parts = []
    for i in range(count):
        shift = 8 * (count - 1 - i if big_endian else i)
        byte = f"std::uint64_t{{{{{{var}}.peekRegister({(register + i) & 0xFF:#04x})}}}}"
        parts.append(f"({byte} << {shift})" if shift else byte)
    return " | ".join(parts)


def _model_channels(spec: ModelSpec) -> List[Channel]:
    """The channels of one model, by register (a later parameter for a register wins)"""
    found: Dict[int, Channel] = {}
    where = f"{spec.cls} on {spec.endpoint}"
    for key, value in spec.params.items():
        if spec.cls == "Wsts::RegisterMapI2c":
            try:
                register = int(key, 0)
            except ValueError:
                continue
            if not 0 <= register <= 255:
                continue
            if is_plain_number(value):
                count = len(str(value).replace(",", " ").split())
                if count == 0:
                    continue
                groups = [(register, count)] if count <= 4 else [(register + i, 1) for i in range(count)]
                for start, width in groups:
                    fmt = Format(width)
                    found[start & 0xFF] = Channel(
                        "", _type(fmt), spec.name, start & 0xFF,
                        f"Register {start & 0xFF:#04x} of model {spec.name} ({where})"
                        + (f", {width} bytes big-endian" if width > 1 else ""),
                        _i2c_read(start, width, True), 8 * width)
            else:
                try:
                    parsed = signal(value)
                except SignalError:
                    continue  # the manifest check has said so
                fmt = parsed.format or Format(1)
                found[register] = Channel(
                    "", _type(fmt), spec.name, register,
                    f"Register {register:#04x} of model {spec.name} ({where}): {parsed.text}",
                    _i2c_read(register, fmt.bytes, fmt.big_endian), 8 * fmt.bytes)
        elif spec.cls == "Wsts::RegisterFile":
            if not key.startswith("reg."):
                continue
            try:
                offset = int(key[4:], 0)
            except ValueError:
                continue
            fmt = Format(4)
            text = ""
            if not is_plain_number(value):
                try:
                    parsed = signal(value)
                except SignalError:
                    continue
                fmt = parsed.format or Format(4)
                text = f": {parsed.text}"
            found[offset] = Channel(
                "", _type(fmt), spec.name, offset,
                f"Register at offset {offset:#x} of model {spec.name} ({where}){text}",
                f"std::uint64_t{{{{{{var}}.peek({offset:#x})}}}}", 8 * fmt.bytes)
    channels = []
    for register in sorted(found):
        channel = found[register]
        width = 2 if spec.cls == "Wsts::RegisterMapI2c" else 4
        channel.name = f"{_identifier(spec.name)}_0x{register:0{width}x}"
        channels.append(channel)
    return channels


def channels(models: List[ModelSpec]) -> List[Channel]:
    """The register channels of the manifest's models"""
    out: List[Channel] = []
    for spec in models:
        if spec.telemetry and spec.cls in STOCK:
            out += _model_channels(spec)
    return out


def check(found: List[Channel]) -> Optional[str]:
    """Why these channels cannot be written, or None"""
    if len(found) > MAX_CHANNELS:
        return (f"{len(found)} register channels, more than the {MAX_CHANNELS} the telemetry of models has room "
                "for: set 'telemetry: false' on the models whose registers need no channel")
    names = [c.name for c in found]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        return (f"two models give register channels the same name ({', '.join(twice)}): model names that differ "
                "only in characters other than letters, digits and _ are not told apart")
    return None


def render_fpp(found: List[Channel]) -> str:
    lines = [
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "",
        f"module {ADAPTED_MODULE} {{",
        "",
        "  @ The registers of the deployment's device models, as telemetry: a channel per register",
        "  @ a model is given in the manifest, with its current value. Published with the",
        "  @ telemetry of the WSTS control component.",
        f"  passive component {COMPONENT_BASE} {{",
        "",
        "    telemetry port tlmOut",
        "",
        "    time get port timeCaller",
        "",
    ]
    for channel in found:
        lines += [f"    @ {channel.description}", f"    telemetry {channel.name}: {channel.type}", ""]
    lines += ["  }", "", "}"]
    return "\n".join(lines) + "\n"


def render_header(found: List[Channel]) -> str:
    guard = f"WSTS_ADAPTED_{COMPONENT_BASE.upper()}_HPP"
    publish = []
    for index, channel in enumerate(found):
        shift = 64 - channel.bits
        cast = (f"static_cast<{channel.type}>(static_cast<std::int64_t>(m_read[{index}]() << {shift}) >> {shift})"
                if channel.type.startswith("I") else f"static_cast<{channel.type}>(m_read[{index}]())")
        publish += [f"        if (m_read[{index}]) {{", f"            this->tlmWrite_{channel.name}({cast});", "        }"]
    lines = [
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "//",
        "// The registers of the deployment's device models, as telemetry (python/fprime_wsts/model_tlm.py).",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <cstdint>",
        "#include <functional>",
        "",
        f"#include <{DIRECTORY}/{COMPONENT_BASE}ComponentAc.hpp>",
        "",
        f"namespace {ADAPTED_MODULE} {{",
        "",
        f"class {COMPONENT_BASE} final : public {COMPONENT_BASE}ComponentBase {{",
        "  public:",
        f"    static constexpr FwIndexType CHANNELS = {max(len(found), 1)};",
        "",
        f"    explicit {COMPONENT_BASE}(const char* compName) : {COMPONENT_BASE}ComponentBase(compName) {{}}",
        "",
        "    //! Where channel `index` reads its value (the generated set-up)",
        "    void wstsSource(FwIndexType index, std::function<std::uint64_t()> read) {",
        "        if (index >= 0 && index < CHANNELS) {",
        "            m_read[index] = std::move(read);",
        "        }",
        "    }",
        "",
        "    //! Write every channel (Wsts.SimControl, with its own telemetry)",
        "    void wstsPublish() {",
        *publish,
        "    }",
        "",
        "  private:",
        "    std::function<std::uint64_t()> m_read[CHANNELS];",
        "};",
        "",
        f"}}  // namespace {ADAPTED_MODULE}",
        "",
        "#endif",
    ]
    return "\n".join(lines) + "\n"


def render_cmake() -> str:
    return "\n".join([
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "register_fprime_library(",
        "    AUTOCODER_INPUTS",
        f'        "${{CMAKE_CURRENT_LIST_DIR}}/{COMPONENT_BASE}.fpp"',
        "    HEADERS",
        f'        "${{CMAKE_CURRENT_LIST_DIR}}/{COMPONENT_BASE}.hpp"',
        ")",
        "",
    ])


def write(found: List[Channel], overlay: Path) -> Path:
    """Write the module into the overlay. Returns its FPP file."""
    directory = overlay / DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    model = directory / f"{COMPONENT_BASE}.fpp"
    model.write_text(render_fpp(found))
    (directory / f"{COMPONENT_BASE}.hpp").write_text(render_header(found))
    (directory / "CMakeLists.txt").write_text(render_cmake())
    return model


def setup_lines(found: List[Channel], instance: str, variable_of) -> List[str]:
    """C++ of the generated set-up that connects each channel to its model. `instance` is the
    instance as C++ names it; `variable_of(model name)` the model's variable."""
    if not found:
        return []
    lines = ["", "    // Telemetry of the models' registers: a channel each, published with the WSTS telemetry"]
    for index, channel in enumerate(found):
        read = channel.read.format(var=variable_of(channel.model))
        lines.append(f"    {instance}.wstsSource({index}, [] {{ return {read}; }});")
    lines.append(f"    Wsts::Control::get().addTelemetrySource([] {{ {instance}.wstsPublish(); }});")
    return lines


def report(found: List[Channel]) -> List[Dict[str, object]]:
    return [{"channel": c.name, "type": c.type, "model": c.model, "register": c.register} for c in found]
