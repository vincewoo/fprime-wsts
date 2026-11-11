"""Deployment transform: turn a flight deployment into a simulated one.

The output is an overlay F Prime project. It reuses the original project's components
and hand-written topology code by path, and carries only what differs: patched topology
FPP (hardware instances retyped to surrogates, SSE instances added), generated set-up
code, and build files.
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import fpp
from .fpp import SseError
from .manifest import DEFAULT_CATALOG, TOOLKIT_ROOT, Manifest, ModelSpec, Surrogate, load_catalog

SETUP_COMPONENT = "Sse.SimSetup"
CONTROL_COMPONENT = "Sse.SimControl"
SETUP_INSTANCE = "sseSetup"
CONTROL_INSTANCE = "sseControl"
ID_BLOCK = 0x1000
HOST_PLATFORMS = {"linux", "posix", "sockets"}
ATTRIBUTE_CLAUSE = re.compile(r"\b(queue\s+size|stack\s+size|priority|cpu)\s+[^\\\n{]+")


# ----------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------


@dataclass
class PlannedSubstitution:
    instance: fpp.Instance
    original: fpp.Component
    surrogate: fpp.Component
    catalog: Surrogate
    endpoint: str
    options: str
    reason: str  # manifest, catalog, structural


@dataclass
class Plan:
    topology: fpp.Topology
    substitutions: List[PlannedSubstitution] = field(default_factory=list)
    kept_hardware: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    setup_base_id: int = 0
    control_base_id: int = 0


def _restricted_from_host(component: fpp.Component, stop_at: List[Path]) -> Optional[str]:
    """If the component's module is platform-restricted away from Linux, say how"""
    directory = component.file.parent
    roots = {r.resolve() for r in stop_at}
    while True:
        for name in ("CMakeLists.txt", "library.cmake"):
            candidate = directory / name
            if not candidate.is_file():
                continue
            for match in re.finditer(r"restrict_platforms\(([^)]*)\)", candidate.read_text(errors="replace")):
                platforms = {p.strip().strip('"').lower() for p in match.group(1).split() if p.strip()}
                if platforms and not (platforms & HOST_PLATFORMS):
                    return f"restrict_platforms({match.group(1).strip()}) in {candidate}"
        if directory in roots or directory.parent == directory:
            return None
        directory = directory.parent


def _covers(surrogate: fpp.Component, original: fpp.Component, used_ports: Set[str]) -> List[str]:
    """Ports of `original` in use that `surrogate` cannot serve. Empty means it fits."""
    missing = []
    for name in sorted(used_ports):
        wanted = original.ports.get(name)
        offered = surrogate.ports.get(name)
        if wanted is None or wanted.kind == "Special":
            continue  # pattern-connected ports simply have nothing to connect to
        if offered is None:
            missing.append(f"{name} (not present)")
        elif offered.type != wanted.type:
            missing.append(f"{name} (type {offered.type}, flight uses {wanted.type})")
        elif offered.is_input != wanted.is_input:
            missing.append(f"{name} (direction differs)")
    return missing


def make_plan(model: fpp.Model, manifest: Manifest, catalog: List[Surrogate], topology: fpp.Topology, roots: List[Path]) -> Plan:
    plan = Plan(topology)
    by_surrogate = {s.name: s for s in catalog}
    replaces: Dict[str, Surrogate] = {}
    for surrogate in catalog:
        for component in surrogate.replaces:
            replaces.setdefault(component, surrogate)

    used_ports: Dict[str, Set[str]] = {}
    for connection in topology.connections:
        used_ports.setdefault(connection.from_instance, set()).add(connection.from_port)
        used_ports.setdefault(connection.to_instance, set()).add(connection.to_port)

    def matches(spec: str, instance: fpp.Instance) -> bool:
        return spec == instance.name or spec == instance.base_name

    for unknown in [s.instance for s in manifest.substitutions if s.instance] + manifest.keep:
        if not any(matches(unknown, model.instances[name]) for name in topology.instances):
            raise SseError(f"manifest names instance '{unknown}', which is not in topology {topology.name}")

    for name in topology.instances:
        instance = model.instances[name]
        original = model.components.get(instance.component)
        if original is None:
            continue
        chosen: Optional[Surrogate] = None
        endpoint = instance.base_name
        options: Optional[str] = None
        reason = ""
        kept = any(matches(k, instance) for k in manifest.keep)

        for entry in manifest.substitutions:
            if (entry.instance and matches(entry.instance, instance)) or (entry.component == original.name and not kept):
                if entry.surrogate not in model.components:
                    raise SseError(f"manifest substitutes {name} with unknown component '{entry.surrogate}'")
                chosen = by_surrogate.get(entry.surrogate) or Surrogate(name=entry.surrogate, source="manifest")
                endpoint = entry.endpoint or endpoint
                options = entry.options
                reason = "manifest"
                break
        if chosen is None and not kept and original.name in replaces:
            chosen = replaces[original.name]
            reason = "catalog"
        restriction = None if chosen else _restricted_from_host(original, roots)
        if chosen is None and restriction and not kept:
            # Nothing was named for a component that cannot build on the host: accept a
            # surrogate only if exactly one fits the ports the topology actually uses.
            fits = [s for s in catalog if s.name in model.components
                    and not _covers(model.components[s.name], original, used_ports.get(name, set()))
                    and used_ports.get(name)]
            if len(fits) == 1:
                chosen = fits[0]
                reason = "structural"
            else:
                candidates = ", ".join(s.name for s in fits) or "none"
                raise SseError(
                    f"instance {name} ({original.name}) cannot build on Linux [{restriction}] and no surrogate "
                    f"is mapped to it. Add a 'substitute' entry to the manifest. Surrogates whose ports fit: {candidates}."
                )
        if chosen is None:
            if restriction:
                plan.kept_hardware.append(name)
                plan.warnings.append(f"{name} ({original.name}) is kept by the manifest although it is restricted: {restriction}")
            continue

        if chosen.name not in model.components:
            raise SseError(f"surrogate component {chosen.name} (for {name}) was not found in the FPP model")
        surrogate = model.components[chosen.name]
        missing = _covers(surrogate, original, used_ports.get(name, set()))
        if missing:
            raise SseError(
                f"{chosen.name} cannot replace {name} ({original.name}): connected ports it does not serve: "
                + "; ".join(missing)
            )
        if instance.file.parent != topology.file.parent:
            raise SseError(
                f"instance {name} is defined in {instance.file}, outside the deployment's Top directory. "
                "Substituting instances defined elsewhere (for example inside a subtopology) is not supported yet."
            )
        if options is None:
            options = chosen.options_when_replacing(original.name)
        plan.substitutions.append(PlannedSubstitution(instance, original, surrogate, chosen, endpoint, options, reason))

    # Base IDs for the SSE instances: above everything the flight topology uses
    highest = 0
    for name in topology.instances:
        instance = model.instances[name]
        highest = max(highest, instance.base_id, instance.max_id)
    base = manifest.control_base_id
    if base is None:
        base = ((highest // ID_BLOCK) + 1) * ID_BLOCK
    elif base <= highest:
        for name in topology.instances:
            instance = model.instances[name]
            top = max(instance.base_id, instance.max_id)
            if not (base + 2 * ID_BLOCK <= instance.base_id or base > top):
                raise SseError(f"control.base_id {base:#x} overlaps the ID range of {name}")
    plan.setup_base_id = base
    plan.control_base_id = base + ID_BLOCK

    if manifest.control_enabled and topology.explicit_pattern_targets:
        plan.warnings.append(
            "topology lists explicit targets for pattern connections ("
            + ", ".join(sorted(set(topology.explicit_pattern_targets)))
            + f"): add {CONTROL_INSTANCE} to them or its commands/events/telemetry will not be connected"
        )
    endpoints: Dict[str, str] = {}
    for sub in plan.substitutions:
        if sub.endpoint in endpoints:
            raise SseError(f"endpoint '{sub.endpoint}' is used by both {endpoints[sub.endpoint]} and {sub.instance.name}")
        endpoints[sub.endpoint] = sub.instance.name
    for spec in manifest.models:
        if spec.kind != "none" and spec.endpoint not in endpoints:
            raise SseError(
                f"model '{spec.name}' attaches to endpoint '{spec.endpoint}', which no surrogate is bound to. "
                f"Endpoints: {', '.join(sorted(endpoints)) or 'none'}"
            )
    return plan


# ----------------------------------------------------------------------
# FPP patching
# ----------------------------------------------------------------------


def _mask(text: str) -> str:
    """Copy of `text` with comments and string contents blanked, same length"""
    out = list(text)
    i = 0
    while i < len(text):
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            end = len(text) if end < 0 else end + 3
        elif text[i] == '"':
            end = i + 1
            while end < len(text) and text[end] != '"' and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            end = min(len(text), end + 1)
        elif text[i] in "#@":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
        else:
            i += 1
            continue
        for j in range(i, end):
            if out[j] != "\n":
                out[j] = " "
        i = end
    return "".join(out)


def _offset(text: str, line: int, col: int) -> int:
    lines = text.split("\n")
    return sum(len(l) + 1 for l in lines[: line - 1]) + col - 1


def _matching_brace(masked: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise SseError("unbalanced braces while patching topology FPP")


def patch_instance(text: str, sub: PlannedSubstitution) -> str:
    """Retype one instance definition to its surrogate"""
    instance = sub.instance
    at = _offset(text, instance.type_line, instance.type_col)
    if not text.startswith(instance.type_text, at):
        raise SseError(f"{instance.file}:{instance.type_line}: expected '{instance.type_text}' in the definition of {instance.name}")
    text = text[:at] + sub.surrogate.name + text[at + len(instance.type_text):]
    if instance.has_queue and sub.surrogate.kind == "passive":
        # A passive surrogate takes no queue or task attributes. The definition runs from
        # its first line through every line continued with a backslash.
        start = _offset(text, instance.line, 1)
        end = start
        while True:
            newline = text.find("\n", end)
            if newline < 0:
                end = len(text)
                break
            if not text[end:newline].rstrip().endswith("\\"):
                end = newline
                break
            end = newline + 1
        text = text[:start] + ATTRIBUTE_CLAUSE.sub("", text[start:end]) + text[end:]
    return text


def patch_topology(text: str, topology: fpp.Topology, instance_names: List[str]) -> str:
    """Add instance specifiers at the top of the topology body"""
    masked = _mask(text)
    opening = masked.find("{", _offset(text, topology.line, topology.col))
    if opening < 0:
        raise SseError(f"{topology.file}: cannot find the body of topology {topology.name}")
    addition = "\n\n    # SSE instances (generated)\n" + "".join(f"    instance {name}\n" for name in instance_names)
    return text[: opening + 1] + addition + text[opening + 1:]


def patch_packets(text: str, line: int, col: int, channels: List[str]) -> str:
    """Omit the SSE channels from a telemetry packet set"""
    masked = _mask(text)
    opening = masked.find("{", _offset(text, line, col))
    closing = _matching_brace(masked, opening)
    listing = "".join(f"    {channel}\n" for channel in channels)
    tail = re.match(r"\s*omit\s*\{", masked[closing + 1:])
    if tail:
        insert_at = closing + 1 + tail.end()
        return text[:insert_at] + "\n    # SSE channels (generated)\n" + listing + text[insert_at:]
    return text[: closing + 1] + " omit {\n    # SSE channels (generated)\n" + listing + "  }" + text[closing + 1:]


# ----------------------------------------------------------------------
# CMake reading
# ----------------------------------------------------------------------

DIRECTIVES = {
    "SOURCES", "AUTOCODER_INPUTS", "DEPENDS", "HEADERS", "CHOOSES_IMPLEMENTATIONS",
    "REQUIRES_IMPLEMENTATIONS", "IMPLEMENTS", "EXCLUDE_FROM_ALL", "INTERFACE", "UT_AUTO_HELPERS",
}


def read_registration(cmake_file: Path, function: str) -> Dict[str, List[str]]:
    """Arguments of the first register_fprime_<function>(...) call, grouped by directive"""
    if not cmake_file.is_file():
        return {}
    text = re.sub(r"#[^\n]*", "", cmake_file.read_text())
    match = re.search(r"\b" + re.escape(function) + r"\s*\(([^)]*)\)", text, re.S)
    if not match:
        return {}
    groups: Dict[str, List[str]] = {}
    current = "_"
    for token in re.findall(r'"[^"]*"|\S+', match.group(1)):
        token = token.strip('"')
        if token in DIRECTIVES:
            current = token
            groups.setdefault(current, [])
        else:
            token = token.replace("${CMAKE_CURRENT_LIST_DIR}", str(cmake_file.parent))
            token = token.replace("${CMAKE_CURRENT_SOURCE_DIR}", str(cmake_file.parent))
            groups.setdefault(current, []).append(token)
    return groups


def module_dir(fpp_file: Path, root: Path) -> Optional[Path]:
    """Nearest directory at or above `fpp_file` that registers an F Prime module"""
    directory = fpp_file.parent
    while root == directory or root in directory.parents:
        cmake = directory / "CMakeLists.txt"
        if cmake.is_file() and "register_fprime_" in cmake.read_text(errors="replace"):
            return directory
        if directory == root:
            break
        directory = directory.parent
    return None


def module_name(directory: Path, roots: List[Path]) -> str:
    """F Prime's build target name for a module directory"""
    best = None
    for root in roots:
        if root == directory or root in directory.parents:
            relative = directory.relative_to(root).as_posix()
            if best is None or len(relative) < len(best):
                best = relative
    return (best or directory.name).replace("/", "_")


# ----------------------------------------------------------------------
# Emission
# ----------------------------------------------------------------------


def _cpp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _cpp_instance(name: str) -> str:
    return "::" + name.replace(".", "::")


def _rel(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start)).as_posix()


def render_instances(plan: Plan, manifest: Manifest) -> str:
    module = plan.topology.module
    indent = "  " if module else ""
    lines = ["# Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.", ""]
    if module:
        lines.append(f"module {module} {{")
        lines.append("")
    lines += [
        f"{indent}@ Anchor for generated simulation set-up code",
        f"{indent}instance {SETUP_INSTANCE}: {SETUP_COMPONENT} base id {plan.setup_base_id:#010x} {{",
        f'{indent}  phase Fpp.ToCpp.Phases.configComponents """',
        f"{indent}  SseGenerated::setup();",
        f'{indent}  """',
        f'{indent}  phase Fpp.ToCpp.Phases.tearDownComponents """',
        f"{indent}  SseGenerated::teardown();",
        f'{indent}  """',
        f"{indent}}}",
        "",
    ]
    if manifest.control_enabled:
        lines += [
            f"{indent}@ SSE commands, events and telemetry",
            f"{indent}instance {CONTROL_INSTANCE}: {CONTROL_COMPONENT} base id {plan.control_base_id:#010x} \\",
            f"{indent}  queue size 30 \\",
            f"{indent}  stack size 64 * 1024 \\",
            f"{indent}  priority 20",
            "",
        ]
    if module:
        lines.append("}")
    return "\n".join(lines) + "\n"


def render_compat(plan: Plan, model: fpp.Model, manifest: Manifest, header_of) -> str:
    # An instance that is not substituted keeps its real class, so no alias may shadow it
    kept_components = {
        model.instances[name].component
        for name in plan.topology.instances
        if name not in {s.instance.name for s in plan.substitutions}
    }
    lines = [
        "// Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        "//",
        "// Force-included into the topology module. It lets topology code written against the",
        "// flight drivers compile against their surrogates, and declares the generated set-up",
        "// entry points used by the topology phase code.",
        "#ifndef SSE_GENERATED_COMPAT_HPP",
        "#define SSE_GENERATED_COMPAT_HPP",
        "#ifdef __cplusplus",
        "",
    ]
    headers = sorted({header_of(sub.surrogate) for sub in plan.substitutions})
    lines += [f"#include <{header}>" for header in headers]
    lines.append("")
    aliased: Set[str] = set()
    for sub in plan.substitutions:
        original = sub.original.name
        if original in aliased:
            continue
        aliased.add(original)
        if original in kept_components:
            lines.append(f"// {original}: no alias, another instance still uses the flight component")
            continue
        namespace, _, cls = original.rpartition(".")
        alias = f"using {cls} = {sub.catalog.cpp_name};"
        lines.append(f"namespace {namespace.replace('.', '::')} {{ {alias} }}" if namespace else alias)
    lines.append("")
    seen_compat: Set[str] = set()
    for sub in plan.substitutions:
        if sub.catalog.compat and sub.catalog.name not in seen_compat:
            seen_compat.add(sub.catalog.name)
            lines.append(sub.catalog.compat.rstrip())
            lines.append("")
    lines += [
        "namespace SseGenerated {",
        "//! Bind surrogates to bus endpoints, attach models and start the SSE runtime",
        "void setup();",
        "void teardown();",
        "}  // namespace SseGenerated",
        "",
        "#endif  // __cplusplus",
        "#endif",
    ]
    return "\n".join(lines) + "\n"


ATTACH = {
    "bytestream": "Sse::Bus::get().attachByteStream({endpoint}, &{var});",
    "i2c": "Sse::Bus::get().attachI2c({endpoint}, {address:#x}, &{var});",
    "spi": "Sse::Bus::get().attachSpi({endpoint}, &{var});",
    "gpio": "Sse::Bus::get().attachGpio({endpoint}, &{var});",
}


def render_setup(plan: Plan, manifest: Manifest, topology_header: str) -> str:
    lines = [
        "// Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        "#include <cstdio>",
        "",
        "#include <Sse/Core/Bus.hpp>",
        "#include <Sse/Core/Control.hpp>",
        "#include <Sse/Core/Runtime.hpp>",
    ]
    for header in sorted({m.header for m in manifest.models}):
        lines.append(f"#include <{header}>")
    lines += [f"#include <{topology_header}>", "", "namespace {", ""]
    for spec in manifest.models:
        lines.append(f"{spec.cls} model_{_identifier(spec.name)};")
    lines += [
        "",
        "void configure(const char* model, bool accepted, const char* key) {",
        "    if (!accepted) {",
        '        std::fprintf(stderr, "[SSE] model %s rejected parameter %s\\n", model, key);',
        "    }",
        "}",
        "",
        "}  // namespace",
        "",
        "namespace SseGenerated {",
        "",
        "void setup() {",
        "    // Surrogates: one bus endpoint each",
    ]
    for sub in plan.substitutions:
        lines.append(
            f"    {_cpp_instance(sub.instance.name)}.sseBind({_cpp_string(sub.endpoint)}, {_cpp_string(sub.options)});"
        )
    if manifest.models:
        lines += ["", "    // Device models"]
    for spec in manifest.models:
        var = f"model_{_identifier(spec.name)}"
        name = _cpp_string(spec.name)
        # Every model is told where it lives; models that do not care ignore it
        lines.append(f"    (void)Sse::configureModel(&{var}, \"endpoint\", {_cpp_string(spec.endpoint)});")
        for key, value in spec.params.items():
            lines.append(
                f"    configure({name}, Sse::configureModel(&{var}, {_cpp_string(key)}, {_cpp_string(value)}), {_cpp_string(key)});"
            )
        if spec.kind in ATTACH:
            lines.append("    " + ATTACH[spec.kind].format(endpoint=_cpp_string(spec.endpoint), address=spec.address, var=var))
        lines.append(f"    Sse::registerModel({name}, &{var});")
    lines.append("")
    if manifest.control_enabled:
        control = _cpp_instance((plan.topology.module + "." if plan.topology.module else "") + CONTROL_INSTANCE)
        lines.append(f'    {control}.sseBind("", "");')
    lines += [
        "    Sse::RuntimeConfig config;",
        f"    config.bridge = {_cpp_string(manifest.bridge)};",
        f"    config.internalTickHz = {manifest.tick_hz};",
        "    Sse::Runtime::start(config);",
        "}",
        "",
        "void teardown() {",
        "    Sse::Runtime::stop();",
        "}",
        "",
        "}  // namespace SseGenerated",
    ]
    return "\n".join(lines) + "\n"


def _identifier(name: str) -> str:
    return re.sub(r"\W", "_", name)


MAIN_TEMPLATE = """// Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.
//
// Linux entry point for a deployment whose flight entry point is called by an RTOS
// start-up script rather than by main().
#include <getopt.h>
#include <signal.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>

#include <Os/Os.hpp>

{declarations}

namespace {{
volatile sig_atomic_t s_stop = 0;
void onSignal(int) {{
    s_stop = 1;
}}
}}  // namespace

int main(int argc, char* argv[]) {{
    const char* hostname = nullptr;
    int port = 0;
    int option = 0;
    while ((option = getopt(argc, argv, "ha:p:")) != -1) {{
        switch (option) {{
            case 'a':
                hostname = optarg;
                break;
            case 'p':
                port = std::atoi(optarg);
                break;
            default:
                std::printf("Usage: %s [-a hostname] [-p port]\\n", argv[0]);
                return (option == 'h') ? 0 : 1;
        }}
    }}
    (void)hostname;
    (void)port;
    Os::init();
    signal(SIGINT, onSignal);
    signal(SIGTERM, onSignal);

    {start};
    while (!s_stop) {{
        (void)usleep(100000);
    }}
{stop}
    return 0;
}}
"""


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


def build_report(plan: Plan, model: fpp.Model, manifest: Manifest, libraries: Dict[str, List[str]],
                 modules: List[str], settings: fpp.ProjectSettings, reused: Dict[str, List[str]]) -> dict:
    substitutions = []
    removed = {"commands": [], "events": [], "channels": [], "params": []}
    for sub in plan.substitutions:
        lost = {
            kind: sorted(set(getattr(sub.original, kind)) - set(getattr(sub.surrogate, kind)))
            for kind in ("commands", "events", "channels", "params")
        }
        for kind, names in lost.items():
            removed[kind] += [f"{sub.instance.name}.{n}" for n in names]
        substitutions.append({
            "instance": sub.instance.name,
            "flight_component": sub.original.name,
            "surrogate": sub.surrogate.name,
            "seam": "S1 topology",
            "chosen_by": sub.reason,
            "endpoint": sub.endpoint,
            "options": sub.options,
            "base_id": sub.instance.base_id,
            "dictionary_items_removed": lost,
        })
    added = {"commands": [], "events": [], "channels": []}
    if manifest.control_enabled and CONTROL_COMPONENT in model.components:
        control = model.components[CONTROL_COMPONENT]
        prefix = (plan.topology.module + "." if plan.topology.module else "") + CONTROL_INSTANCE
        for kind in added:
            added[kind] = [f"{prefix}.{n}" for n in getattr(control, kind)]
    identical = not any(removed.values()) and not any(added.values())
    return {
        "generator": "fprime-sse",
        "topology": plan.topology.name,
        "flight_toolchain": settings.default_toolchain,
        "sim_toolchain": "native (Linux)",
        "time": {"mode": "real-time", "tick_source": next(
            (s.instance.name for s in plan.substitutions if s.surrogate.name == "Sse.SimTick"), "internal ticker")},
        "substitutions": substitutions,
        "kept_hardware_instances": plan.kept_hardware,
        "models": [
            {"name": m.name, "class": m.cls, "endpoint": m.endpoint, "kind": m.kind, "address": m.address}
            for m in manifest.models
        ],
        "control": {
            "enabled": manifest.control_enabled,
            "setup_base_id": plan.setup_base_id,
            "control_base_id": plan.control_base_id if manifest.control_enabled else None,
        },
        "bridge": manifest.bridge,
        "dictionary": {
            "identical_to_flight": identical,
            "flight_ids_unchanged": True,
            "removed": removed,
            "added": added,
        },
        "libraries": libraries,
        "project_modules": modules,
        "reused_sources": reused,
        "fidelity_notes": [
            "Operating system: flight OSAL implementations are replaced by the Linux/POSIX ones. Task priorities "
            "are advisory and scheduling is not real-time, so timing and preemption order differ from the target.",
            "Word size and byte order are those of the host; code that depends on the target's differs here.",
            "Surrogates replace drivers at their port interface: driver internals (register access, interrupt "
            "handling, device files) are not exercised.",
            "Time follows the host wall clock, scaled by the simulation rate. Runs are repeatable to tick "
            "granularity, not cycle-exact.",
        ],
        "warnings": plan.warnings,
    }


def render_report(report: dict) -> str:
    lines = [
        f"# SSE report: {report['topology']}",
        "",
        "Generated by `fprime-sse generate`. This records what the simulated deployment changes "
        "relative to flight, and what that costs in fidelity.",
        "",
        f"- Flight toolchain: `{report['flight_toolchain']}`; simulation toolchain: `{report['sim_toolchain']}`",
        f"- Time: {report['time']['mode']}, tick source `{report['time']['tick_source']}`",
        f"- Bridge: `{report['bridge']}`",
        *([f"- Flight build options not carried over: `{' '.join(report['dropped_cmake_options'])}`"]
          if report.get("dropped_cmake_options") else []),
        *([f"- Libraries not built for simulation: {', '.join('`' + l + '`' for l in report['libraries']['dropped'])}"]
          if report["libraries"]["dropped"] else []),
        f"- Dictionary identical to flight: **{'yes' if report['dictionary']['identical_to_flight'] else 'no'}**; "
        "flight IDs unchanged: **yes**",
        "",
        "## Substituted instances",
        "",
        "| Instance | Flight component | Surrogate | Chosen by | Endpoint |",
        "|---|---|---|---|---|",
    ]
    for sub in report["substitutions"]:
        lines.append(
            f"| `{sub['instance']}` | `{sub['flight_component']}` | `{sub['surrogate']}` | {sub['chosen_by']} | `{sub['endpoint']}` |"
        )
    if not report["substitutions"]:
        lines.append("| (none) | | | | |")
    lines += ["", "## Device models", ""]
    for model in report["models"]:
        address = f" address {model['address']:#x}" if model["kind"] == "i2c" else ""
        lines.append(f"- `{model['name']}`: `{model['class']}` on `{model['endpoint']}` ({model['kind']}{address})")
    if not report["models"]:
        lines.append("- none in-process (attach external models through the bridge)")
    lines += ["", "## Dictionary changes", ""]
    for title, key in (("Removed (flight items of replaced drivers)", "removed"), ("Added (SSE)", "added")):
        items = [i for names in report["dictionary"][key].values() for i in names]
        lines.append(f"**{title}:** {len(items)}")
        lines += [f"- `{item}`" for item in items]
        lines.append("")
    lines += ["## Fidelity notes", ""]
    lines += [f"- {note}" for note in report["fidelity_notes"]]
    if report["warnings"]:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in report["warnings"]]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------


@dataclass
class Result:
    output: Path
    report: dict
    changed: List[str] = field(default_factory=list)  # --check: files that differ


def generate(project_root: Path, manifest: Manifest, output: Optional[Path] = None, check: bool = False) -> Result:
    project_root = project_root.resolve()
    settings = fpp.read_settings(project_root)
    project_root = settings.project_root
    if output is None:
        output = manifest.resolve(manifest.output, project_root) if manifest.output else project_root / "sse" / Path(manifest.deployment).name
    output = output.resolve()
    if output.exists() and any(output.iterdir()) and not (output / fpp.GENERATED_MARKER).exists():
        raise SseError(f"{output} exists and was not generated by fprime-sse; refusing to overwrite it")

    if check:
        with tempfile.TemporaryDirectory(prefix="sse-check-") as scratch:
            target = Path(scratch) / "out"
            result = _generate(project_root, settings, manifest, target, final_output=output)
            result.changed = _diff(target, output)
            result.output = output
            return result
    return _generate(project_root, settings, manifest, output, final_output=output)


def _diff(new: Path, old: Path) -> List[str]:
    if not old.is_dir():
        return ["(output directory missing)"]
    changed = []
    names = {p.relative_to(new).as_posix() for p in new.rglob("*") if p.is_file()}
    names |= {p.relative_to(old).as_posix() for p in old.rglob("*") if p.is_file()}
    for name in sorted(names):
        if name.startswith(("build-", "sse-logs/")) or "/build-" in name:
            continue
        a, b = new / name, old / name
        if not a.is_file() or not b.is_file() or not filecmp.cmp(a, b, shallow=False):
            changed.append(name)
    return changed


def _generate(project_root: Path, settings: fpp.ProjectSettings, manifest: Manifest, output: Path, final_output: Path) -> Result:
    deployment_dir = (project_root / manifest.deployment).resolve()
    top_dir = deployment_dir / "Top"
    if not top_dir.is_dir():
        raise SseError(f"{deployment_dir} has no Top directory: is '{manifest.deployment}' a deployment?")
    deployment = deployment_dir.name

    # ---- Catalogs ----
    catalog: List[Surrogate] = []
    for entry in manifest.catalogs:
        catalog += load_catalog(manifest.resolve(entry, project_root))
    catalog += load_catalog(DEFAULT_CATALOG)

    # ---- Analyse the flight topology ----
    with tempfile.TemporaryDirectory(prefix="sse-work-") as scratch:
        work = Path(scratch)
        toolkit_fpp_root = TOOLKIT_ROOT / "Sse"
        roots = [settings.framework_path, *settings.library_locations, project_root, toolkit_fpp_root]
        top_registration = read_registration(top_dir / "CMakeLists.txt", "register_fprime_module")
        if manifest.topology_fpp:
            top_fpp = [manifest.resolve(f, project_root) for f in manifest.topology_fpp]
        else:
            top_fpp = [Path(f).resolve() for f in top_registration.get("AUTOCODER_INPUTS", []) if f.endswith(".fpp")]
            top_fpp = top_fpp or sorted(top_dir.glob("*.fpp"))
        if not top_fpp:
            raise SseError(f"no topology FPP files found in {top_dir}")
        candidates = fpp.select_candidates(fpp.find_fpp_files(roots, manifest.fpp_exclude), settings.framework_path)
        # Other deployments in the project may define the same names; only this one is read
        candidates = [c for c in candidates if c not in set(top_fpp)]
        located = fpp.locate_defs(work, candidates)
        surrogate_files = sorted({
            located[("component", name)]
            for name in {s.name for s in catalog} | {SETUP_COMPONENT, CONTROL_COMPONENT} | {s.surrogate for s in manifest.substitutions}
            if ("component", name) in located
        })
        sources = top_fpp + [f for f in surrogate_files if f not in top_fpp]
        model = fpp.analyze(work, fpp.depend(work, sources) + sources)

        local = [t for t in model.topologies.values() if t.file in top_fpp or t.file.parent == top_dir]
        if manifest.topology:
            local = [t for t in local if t.name == manifest.topology or t.base_name == manifest.topology]
        if len(local) != 1:
            names = ", ".join(t.name for t in local) or "none"
            raise SseError(f"expected exactly one topology in {top_dir}, found: {names}. Set 'topology' in the manifest.")
        topology = local[0]

        plan = make_plan(model, manifest, catalog, topology, roots)

        # ---- Write the overlay ----
        if output.exists():
            for child in output.iterdir():
                if child.name.startswith("build-") or child.name == "sse-logs":
                    continue  # keep the build cache (regeneration is incremental) and run logs
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        out_top = output / deployment / "Top"
        out_top.mkdir(parents=True, exist_ok=True)
        (output / fpp.GENERATED_MARKER).write_text("Generated by fprime-sse. Safe to delete and regenerate.\n")

        texts: Dict[Path, str] = {}

        def text_of(path: Path) -> str:
            if path not in texts:
                if path.parent != top_dir:
                    raise SseError(f"{path} needs patching but is outside {top_dir}")
                texts[path] = path.read_text()
            return texts[path]

        for f in top_fpp:
            text_of(f)
        # Patch bottom-up within each file so earlier positions stay valid
        for sub in sorted(plan.substitutions, key=lambda s: (s.instance.type_line, s.instance.type_col), reverse=True):
            texts[sub.instance.file] = patch_instance(text_of(sub.instance.file), sub)
        prefix = topology.module + "." if topology.module else ""
        added_instances = [SETUP_INSTANCE] + ([CONTROL_INSTANCE] if manifest.control_enabled else [])
        if manifest.control_enabled and topology.packet_sets:
            # Named relative to the topology's module, as the topology's own packets are: a
            # topology that shares its module's name would capture a fully qualified name
            channels = [f"{CONTROL_INSTANCE}.{c}" for c in model.components[CONTROL_COMPONENT].channels]
            for file, line, col in sorted(topology.packet_sets, key=lambda p: (p[1], p[2]), reverse=True):
                texts[file] = patch_packets(text_of(file), line, col, channels)
        texts[topology.file] = patch_topology(text_of(topology.file), topology, added_instances)

        patched_fpp: List[Path] = []
        for path in sorted(top_dir.iterdir()):
            if path.suffix not in (".fpp", ".fppi"):
                continue
            target = out_top / path.name
            target.write_text(texts.get(path, path.read_text()))
            if path in top_fpp:
                patched_fpp.append(target)
        instances_file = out_top / "sse_instances.fpp"
        instances_file.write_text(render_instances(plan, manifest))
        patched_fpp.append(instances_file)

        # ---- Validate the patched model and find what it needs ----
        post_candidates = [c for c in candidates if c not in set(top_fpp)]
        fpp.locate_defs(work, post_candidates)
        closure = fpp.depend(work, patched_fpp)
        try:
            fpp.check(work, closure + patched_fpp)
        except SseError as error:
            raise SseError(f"the generated topology does not pass fpp-check:\n{error}") from None

    # ---- Libraries and project modules the simulated deployment builds ----
    # A file belongs to the deepest root that contains it: the framework is often checked
    # out inside the project, and a project may itself live inside a library's repository.
    all_roots = [settings.framework_path, *settings.library_locations, TOOLKIT_ROOT / "Sse", project_root]

    def owner(path: Path) -> Optional[Path]:
        containing = [root for root in all_roots if root in path.parents]
        return max(containing, key=lambda root: len(root.parts)) if containing else None

    libraries = {"kept": [], "dropped": []}
    library_paths: List[Path] = [TOOLKIT_ROOT]
    for library in settings.library_locations:
        used = any(owner(f) == library for f in closure)
        named_keep = any(manifest.resolve(k, project_root) == library for k in manifest.keep_libraries)
        named_drop = any(manifest.resolve(k, project_root) == library for k in manifest.drop_libraries)
        if (used or named_keep) and not named_drop:
            libraries["kept"].append(str(library))
            if library not in library_paths:
                library_paths.append(library)
        else:
            libraries["dropped"].append(str(library))
    library_paths.append(project_root)

    excluded = {manifest.resolve(d, project_root) for d in manifest.exclude_subdirectories}
    module_dirs: Dict[Path, None] = {}
    for f in closure:
        if owner(f) != project_root or output in f.parents or final_output in f.parents:
            continue
        directory = module_dir(f, project_root)
        if directory is None:
            plan.warnings.append(f"{f} is not inside a module directory; it will not be built")
        elif directory != top_dir and directory not in excluded:
            module_dirs[directory] = None
    for extra in manifest.extra_subdirectories:
        module_dirs[manifest.resolve(extra, project_root)] = None

    # ---- Hand-written sources ----
    if manifest.topology_sources is not None:
        topology_sources = [manifest.resolve(s, project_root) for s in manifest.topology_sources]
    else:
        topology_sources = [Path(s).resolve() for s in top_registration.get("SOURCES", [])]
    substituted_modules = set()
    for sub in plan.substitutions:
        substituted_modules.add(module_name(sub.original.file.parent, [settings.framework_path, *settings.library_locations, project_root]))
    top_depends = [d for d in top_registration.get("DEPENDS", []) if d not in substituted_modules and "${" not in d]
    top_depends += [d for d in manifest.extra_depends if d not in top_depends]
    if "Sse_Core" not in top_depends:
        top_depends.append("Sse_Core")

    deployment_registration = read_registration(deployment_dir / "CMakeLists.txt", "register_fprime_deployment")
    main_spec = manifest.main
    if main_spec.sources is not None:
        main_sources = [manifest.resolve(s, project_root) for s in main_spec.sources]
    else:
        main_sources = [Path(s).resolve() for s in deployment_registration.get("SOURCES", [])]
    generated_main = main_spec.generate
    if not generated_main and not any(re.search(r"\bmain\s*\(", s.read_text(errors="replace")) for s in main_sources if s.is_file()):
        raise SseError(
            f"deployment {deployment} has no main(): its entry point is started by the target OS. "
            "Add to the manifest:\n  main:\n    generate: true\n    declarations: <C++ declarations>\n"
            "    start: <C++ expression that starts the deployment>\n    stop: <C++ expression that stops it>"
        )
    for source in topology_sources + main_sources:
        if not source.is_file():
            raise SseError(f"source file not found: {source}")

    def header_of(component: fpp.Component) -> str:
        base = component.file.with_suffix(".hpp")
        for root in sorted([TOOLKIT_ROOT, *settings.library_locations, project_root, settings.framework_path], key=lambda r: -len(str(r))):
            if root in base.parents:
                return base.relative_to(root).as_posix()
        return base.name

    # ---- Emit build files and generated code ----
    (out_top / "SseCompat.hpp").write_text(render_compat(plan, model, manifest, header_of))
    (out_top / "SseSetup.cpp").write_text(
        render_setup(plan, manifest, f"{deployment}/Top/{topology.base_name}TopologyAc.hpp")
    )
    out_deployment = output / deployment

    def cmake_path(path: Path) -> str:
        return '"${CMAKE_CURRENT_LIST_DIR}/' + _rel(path, cmake_path.base) + '"'

    # Relative to where the files will finally live, which differs from `output` under --check
    cmake_path.base = final_output / deployment / "Top"
    top_cmake = [
        "# Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        "register_fprime_module(",
        "    AUTOCODER_INPUTS",
        *[f'        "${{CMAKE_CURRENT_LIST_DIR}}/{f.name}"' for f in patched_fpp],
        "    SOURCES",
        *[f"        {cmake_path(s)}" for s in topology_sources],
        '        "${CMAKE_CURRENT_LIST_DIR}/SseSetup.cpp"',
        "    DEPENDS",
        *[f"        {d}" for d in top_depends],
        ")",
        "# Flight topology code is compiled against the surrogates through this header",
        'target_compile_options(${FPRIME_CURRENT_MODULE} PRIVATE -include "${CMAKE_CURRENT_LIST_DIR}/SseCompat.hpp")',
        "",
    ]
    (out_top / "CMakeLists.txt").write_text("\n".join(top_cmake))

    cmake_path.base = final_output / deployment
    if generated_main:
        stop = f"    {main_spec.stop};" if main_spec.stop else ""
        (out_deployment / "SseMain.cpp").write_text(
            MAIN_TEMPLATE.format(declarations=main_spec.declarations.strip(), start=main_spec.start, stop=stop)
        )
    deployment_sources = [f"        {cmake_path(s)}" for s in main_sources]
    if generated_main:
        deployment_sources.append('        "${CMAKE_CURRENT_LIST_DIR}/SseMain.cpp"')
    deployment_depends = [
        d for d in deployment_registration.get("DEPENDS", [])
        if not d.endswith("_Top") and d not in substituted_modules and "${" not in d
    ]
    deployment_cmake = [
        "# Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Top/")',
        "",
        "register_fprime_deployment(",
        "    SOURCES",
        *deployment_sources,
        "    DEPENDS",
        "        ${FPRIME_CURRENT_MODULE}_Top",
        *[f"        {d}" for d in deployment_depends],
        ")",
        "",
    ]
    (out_deployment / "CMakeLists.txt").write_text("\n".join(deployment_cmake))
    for config in ("fprime-gds.yml", "gds.ini"):
        if (deployment_dir / config).is_file():
            shutil.copy(deployment_dir / config, out_deployment / config)

    # Paths are written relative to where the overlay will live so it can be checked in
    def rel(path: Path) -> str:
        return _rel(path, final_output)

    defines = ["SSE_SIM=1", *manifest.defines]
    root_cmake = [
        "# Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        "#",
        f"# Simulated build of {topology.name}. Flight components are built from the original",
        "# project; hardware-facing instances are replaced by SSE surrogates.",
        "cmake_minimum_required(VERSION 3.26)",
        f"project({deployment}Sim VERSION 1.0.0 LANGUAGES C CXX)",
        "",
        f"add_compile_definitions({' '.join(defines)})",
        f'find_package(FPrime REQUIRED PATHS "${{CMAKE_CURRENT_LIST_DIR}}/{rel(settings.framework_path)}/cmake")',
        "",
        f'set(SSE_FLIGHT_PROJECT "${{CMAKE_CURRENT_LIST_DIR}}/{rel(project_root)}")',
        *[f'add_fprime_subdirectory("${{SSE_FLIGHT_PROJECT}}/{_rel(d, project_root)}")' for d in module_dirs],
        f'add_fprime_subdirectory("${{CMAKE_CURRENT_LIST_DIR}}/{deployment}")',
        "",
    ]
    (output / "CMakeLists.txt").write_text("\n".join(root_cmake))

    original_ini = (project_root / "settings.ini").read_text()
    options = re.search(r"^default_cmake_options\s*[:=].*(?:\n[ \t]+\S.*)*", original_ini, re.M)
    settings_ini = [
        "; Generated by fprime-sse. Do not edit: regenerate with `fprime-sse generate`.",
        "[fprime]",
        f"framework_path: {rel(settings.framework_path)}",
        "project_root: .",
        "library_locations: " + ":".join(rel(p) for p in library_paths),
        "default_toolchain: native",
    ]
    dropped_options: List[str] = []
    if options:
        # Options that select the flight toolchain or platform do not apply to a host build
        kept_options = []
        for option in options.group(0).split(":", 1)[-1].replace("=", "=", 1).split():
            name = option.split("=", 1)[0]
            if (name.startswith("FPRIME_") and name != "FPRIME_PLATFORM") or name == "CMAKE_BUILD_TYPE":
                kept_options.append(option)
            else:
                dropped_options.append(option)
        if kept_options:
            settings_ini.append("default_cmake_options: " + "\n    ".join(kept_options))
    (output / "settings.ini").write_text("\n".join(settings_ini) + "\n")

    reused = {
        "topology": [str(s) for s in topology_sources],
        "main": [str(s) for s in main_sources] + (["SseMain.cpp (generated)"] if generated_main else []),
    }
    report = build_report(plan, model, manifest, libraries, [_rel(d, project_root) for d in module_dirs], settings, reused)
    # The report is part of the checked-in output: keep machine-specific paths out of it
    report["libraries"] = {k: [rel(Path(p)) for p in v] for k, v in libraries.items()}
    report["dropped_cmake_options"] = dropped_options
    report["reused_sources"] = {
        k: [rel(Path(p)) if os.path.isabs(p) else p for p in v] for k, v in reused.items()
    }
    (output / "sse-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "sse-report.md").write_text(render_report(report))
    return Result(output, report)
