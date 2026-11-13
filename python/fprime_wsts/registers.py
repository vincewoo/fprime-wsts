"""Register interface components and their surrogate, `Wsts.SimRegister`.

A register interface component is what flight device drivers read and write memory-mapped
registers through, and what delivers the hardware's interrupts. It is a component of a
project or of one of its libraries, with port types of its own, and FPP connects a port
only to a port of the same type. One component of the toolkit therefore cannot take the
place of every project's: where the port types differ, the generator writes
`Wsts.SimRegister` again with the flight component's port names and types, into a module
of the overlay. The ports are recognised by their signatures, never by a component's name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import fpp
from .fpp import WstsError

#: The toolkit's surrogate for register interface components
SURROGATE = "Wsts.SimRegister"
#: FPP module, C++ namespace and overlay directory of the surrogates written for flight components
ADAPTED_MODULE = "WstsAdapted"

UNSIGNED = ("U8", "U16", "U32", "U64")
FRAMEWORK_MODULES = ("Fw", "Svc", "Drv", "Os")
ROLES = ("read32", "write32", "read64", "write64", "interrupt")
#: What each role's port looks like, for messages
SHAPES = {
    "read32": "input port (offset) -> U32",
    "write32": "input port (offset, value: U32)",
    "read64": "input port (offset) -> U64",
    "write64": "input port (offset, value: U64)",
    "interrupt": "output port (number: U32), or with no argument",
}


def role_of(port: fpp.Port, definition: Optional[fpp.PortDef]) -> Optional[str]:
    """Which register access a port is, from its direction and its port type's signature"""
    if definition is None or port.kind == "Special" or any(by_ref for _, _, by_ref in definition.params):
        return None
    types = [type_name for _, type_name, _ in definition.params]
    if any(type_name not in UNSIGNED for type_name in types):
        return None
    if not port.is_input:
        # A framework port type is never an interrupt: Svc.Ping has the same signature
        framework = (port.type or "").split(".")[0] in FRAMEWORK_MODULES
        return "interrupt" if definition.returns is None and len(types) <= 1 and not framework else None
    if len(types) == 1 and definition.returns in ("U32", "U64"):
        return "read32" if definition.returns == "U32" else "read64"
    if len(types) == 2 and definition.returns is None and types[1] in ("U32", "U64"):
        return "write32" if types[1] == "U32" else "write64"
    return None


@dataclass
class AdaptedSurrogate:
    """`Wsts.SimRegister` written with the ports of one flight component"""
    name: str  # FPP name, WstsAdapted.<flight component, dots as underscores>
    original: fpp.Component
    component: fpp.Component  # the surrogate, as the FPP tools will see it
    roles: Dict[str, str] = field(default_factory=dict)  # port name -> role
    unmatched: List[str] = field(default_factory=list)  # general ports of the flight component with no role

    @property
    def base(self) -> str:
        return self.name.rpartition(".")[2]

    @property
    def directory(self) -> str:
        """The module's directory, relative to the overlay"""
        return f"{ADAPTED_MODULE}/{self.base}"

    @property
    def header(self) -> str:
        return f"{self.directory}/{self.base}.hpp"


def matches_stock(component: fpp.Component, stock: fpp.Component, used: Optional[set] = None) -> bool:
    """Whether a flight component's connected ports are those of Wsts.SimRegister itself, by
    name, type and direction: it then takes the stock component, and nothing is written"""
    general = [p for p in component.ports.values() if p.kind != "Special" and (used is None or p.name in used)]
    return bool(general) and all(
        p.name in stock.ports and stock.ports[p.name].type == p.type and stock.ports[p.name].is_input == p.is_input
        for p in general)


def register_roles(component: fpp.Component, port_defs: Dict[str, fpp.PortDef]) -> Dict[str, str]:
    """Ports of a component that are register accesses or interrupts: port name -> role"""
    roles = {}
    for port in component.ports.values():
        role = role_of(port, port_defs.get(port.type or ""))
        if role is not None:
            roles[port.name] = role
    return roles


def is_register_surrogate(component: fpp.Component, port_defs: Dict[str, fpp.PortDef]) -> bool:
    """A surrogate (the toolkit's, one written for a flight component, or a project's mirror)
    that serves register reads or writes"""
    return any(role != "interrupt" for role in register_roles(component, port_defs).values())


def adapt(original: fpp.Component, port_defs: Dict[str, fpp.PortDef], overlay: Path,
          dropped: Optional[set] = None) -> AdaptedSurrogate:
    """Describe Wsts.SimRegister with the ports of `original`, without those in `dropped`"""
    base = original.name.replace(".", "_")
    name = f"{ADAPTED_MODULE}.{base}"
    component = fpp.Component(name, "passive", overlay / ADAPTED_MODULE / base / f"{base}.fpp")
    adapted = AdaptedSurrogate(name, original, component)
    for port in original.ports.values():
        if port.kind == "Special":
            continue  # command, event, telemetry, time: the surrogate has none
        role = None if port.name in (dropped or ()) else role_of(port, port_defs.get(port.type or ""))
        if role is None:
            adapted.unmatched.append(port.name)
            continue
        adapted.roles[port.name] = role
        component.ports[port.name] = fpp.Port(port.name, "SyncInput" if port.is_input else "Output", port.type, port.size)
    if not adapted.roles:
        raise WstsError(
            f"{SURROGATE} cannot stand in for {original.name}: none of its ports has the shape of a register access "
            "or an interrupt. " + shapes_text() + f" Ports of {original.name}: "
            + (", ".join(describe(p, port_defs) for p in original.ports.values() if p.kind != "Special") or "none") + "."
        )
    return adapted


def shapes_text() -> str:
    return ("The shapes are: " + "; ".join(f"{role}: {SHAPES[role]}" for role in ROLES)
            + " (offsets and numbers are unsigned integers).")


def describe(port: fpp.Port, port_defs: Dict[str, fpp.PortDef]) -> str:
    definition = port_defs.get(port.type or "")
    signature = ""
    if definition is not None:
        signature = "(" + ", ".join(f"{name}: {type_name}" for name, type_name, _ in definition.params) + ")"
        signature += f" -> {definition.returns}" if definition.returns else ""
    return f"{port.name} ({'input' if port.is_input else 'output'} {port.type}{signature})"


def render_fpp(adapted: AdaptedSurrogate) -> str:
    lines = [
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "",
        f"module {ADAPTED_MODULE} {{",
        "",
        f"  @ {SURROGATE} with the port names and port types of {adapted.original.name}: accesses are",
        "  @ answered by the register block on the instance's WSTS bus endpoint",
        f"  passive component {adapted.base} {{",
        "",
    ]
    for port in adapted.component.ports.values():
        size = f"[{port.size}] " if port.size != 1 else ""
        kind = "sync input port" if port.is_input else "output port"
        lines += [f"    @ {adapted.roles[port.name]}", f"    {kind} {_escape(port.name)}: {size}{port.type}", ""]
    lines += ["  }", "", "}"]
    return "\n".join(lines) + "\n"


FPP_KEYWORDS = {
    "action", "active", "activity", "always", "array", "assert", "async", "at", "base", "block", "bool", "change",
    "choice", "command", "component", "connections", "constant", "container", "cpu", "default", "diagnostic", "do",
    "drop", "else", "enter", "entry", "enum", "event", "exit", "external", "fatal", "format", "get", "group",
    "guard", "guarded", "health", "high", "hook", "id", "if", "import", "include", "initial", "input", "instance",
    "interface", "internal", "locate", "low", "machine", "match", "module", "omit", "on", "opcode", "orange",
    "output", "packet", "packets", "param", "passive", "phase", "port", "priority", "product", "queue", "queued",
    "raw", "record", "recv", "red", "ref", "reg", "request", "resp", "save", "send", "serial", "set", "severity",
    "signal", "size", "stack", "state", "string", "struct", "sync", "telemetry", "text", "throttle", "time",
    "topology", "type", "update", "warning", "with", "yellow",
}


def _escape(name: str) -> str:
    return "$" + name if name in FPP_KEYWORDS else name


def _handler(port: fpp.Port, role: str, definition: fpp.PortDef) -> List[str]:
    types = [type_name for _, type_name, _ in definition.params]
    name = f"{port.name}_handler"
    if role in ("read32", "read64"):
        returns = "U32" if role == "read32" else "U64"
        call = "registerRead32" if role == "read32" else "registerRead64"
        return [
            f"    {returns} {name}(FwIndexType portNum, {types[0]} offset) override {{",
            "        (void)portNum;",
            f"        return this->{call}(offset);",
            "    }",
        ]
    call = "registerWrite32" if role == "write32" else "registerWrite64"
    return [
        f"    void {name}(FwIndexType portNum, {types[0]} offset, {types[1]} value) override {{",
        "        (void)portNum;",
        f"        this->{call}(offset, value);",
        "    }",
    ]


def render_header(adapted: AdaptedSurrogate, port_defs: Dict[str, fpp.PortDef]) -> str:
    base = adapted.base
    guard = f"WSTS_ADAPTED_{base.upper()}_HPP"
    deliver: List[str] = []
    for port in adapted.component.ports.values():
        if adapted.roles[port.name] != "interrupt":
            continue
        definition = port_defs[port.type]
        argument = f", static_cast<{definition.params[0][1]}>(number)" if definition.params else ""
        deliver += [
            f"            for (FwIndexType port = 0; port < this->getNum_{port.name}_OutputPorts(); port++) {{",
            f"                if (this->isConnected_{port.name}_OutputPort(port)) {{",
            f"                    this->{port.name}_out(port{argument});",
            "                }",
            "            }",
        ]
    handlers: List[str] = []
    for port in adapted.component.ports.values():
        role = adapted.roles[port.name]
        if role != "interrupt":
            handlers += _handler(port, role, port_defs[port.type])
    lines = [
        "// Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "//",
        f"// {SURROGATE} with the port names and port types of {adapted.original.name}.",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <Wsts/Core/RegisterSurrogate.hpp>",
        f"#include <{adapted.directory}/{base}ComponentAc.hpp>",
        "",
        f"namespace {ADAPTED_MODULE} {{",
        "",
        f"class {base} final : public {base}ComponentBase, public Wsts::RegisterSurrogate {{",
        "  public:",
        f"    explicit {base}(const char* compName) : {base}ComponentBase(compName) {{}}",
        "    // Before the ports go away: no interrupt is delivered into a component being destroyed",
        f"    ~{base}() {{ this->unbindRegister(); }}",
        "",
        "    //! Bind this instance to a WSTS bus endpoint. Called from generated topology code.",
        '    //! Options: "irq=serial" (the default), "irq=direct", "irq=off".',
        '    void wstsBind(const char* endpoint, const char* options = "") {',
        "        this->bindRegister(endpoint, options, [this](std::uint32_t number) {",
        "            (void)number;",
        *deliver,
        "        });",
        "    }",
        "",
        "    //! Stand-ins for what flight topology code calls to open the device: the arguments",
        "    //! are accepted and ignored",
        "    template <typename... Args>",
        "    void configure(Args&&...) {}",
        "    template <typename... Args>",
        "    bool open(Args&&...) {",
        "        return true;",
        "    }",
        "",
        "  private:",
        *handlers,
        "};",
        "",
        f"}}  // namespace {ADAPTED_MODULE}",
        "",
        "#endif",
    ]
    return "\n".join(lines) + "\n"


def render_cmake(adapted: AdaptedSurrogate) -> str:
    base = adapted.base
    return "\n".join([
        "# Generated by fprime-wsts. Do not edit: regenerate with `fprime-wsts generate`.",
        "register_fprime_library(",
        "    AUTOCODER_INPUTS",
        f'        "${{CMAKE_CURRENT_LIST_DIR}}/{base}.fpp"',
        "    HEADERS",
        f'        "${{CMAKE_CURRENT_LIST_DIR}}/{base}.hpp"',
        "    DEPENDS",
        "        Wsts_Core",
        ")",
        "",
    ])


def write(adapted: AdaptedSurrogate, overlay: Path, port_defs: Dict[str, fpp.PortDef]) -> Path:
    """Write the module into the overlay. Returns its FPP file."""
    directory = overlay / adapted.directory
    directory.mkdir(parents=True, exist_ok=True)
    model = directory / f"{adapted.base}.fpp"
    model.write_text(render_fpp(adapted))
    (directory / f"{adapted.base}.hpp").write_text(render_header(adapted, port_defs))
    (directory / "CMakeLists.txt").write_text(render_cmake(adapted))
    return model
