"""`fprime-sse scaffold`: start a surrogate that mirrors a flight component.

A mirror surrogate has exactly the ports, commands, events, telemetry and parameters of
the component it replaces, with the same identifiers. Substituting it leaves the
deployment's dictionary identical to flight, which the general-purpose surrogates cannot
do for drivers that have dictionary entries of their own.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import fpp
from .fpp import SseError
from .generate import _mask, _matching_brace, _offset
from .manifest import TOOLKIT_ROOT

README = """# {new_base}: simulation surrogate for {original}

Created by `fprime-sse scaffold`. The model in `{new_base}.fpp` is a copy of
`{original}`: same ports, same dictionary entries, same identifiers. Keep it that way
and a deployment that substitutes it keeps a flight-identical dictionary.

To finish it:

1. Generate the implementation templates and rename them:

       fprime-util impl        # run in this directory, inside a project that builds it

   That writes `{new_base}.template.hpp/.cpp`; rename them to `{new_base}.hpp/.cpp`.
2. Add the binding hook the generator calls, and talk to the simulation bus in the
   port handlers (see `Sse/Drv/SimByteStream` in the toolkit for a complete example):

       void sseBind(const char* endpoint, const char* options = "");

3. Give the class the public methods flight topology code calls on `{original_cpp}`
   (for example `open(...)`), so that code compiles unchanged.
4. Add this directory to the project's build and name `sse-catalog.yaml` under
   `catalogs:` in the deployment manifest. From then on `{original}` instances are
   replaced by `{new_name}` automatically.
"""


def scaffold_cli(args) -> int:
    project_root = Path(args.project).resolve()
    settings = fpp.read_settings(project_root)
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise SseError(f"{output} exists and is not empty")

    with tempfile.TemporaryDirectory(prefix="sse-scaffold-") as scratch:
        work = Path(scratch)
        roots = [settings.framework_path, *settings.library_locations, settings.project_root, TOOLKIT_ROOT / "Sse"]
        candidates = fpp.select_candidates(fpp.find_fpp_files(roots), settings.framework_path)
        located = fpp.locate_defs(work, candidates)
        source = located.get(("component", args.component))
        if source is None:
            raise SseError(f"component {args.component} was not found under the project, its libraries or the framework")
        model = fpp.analyze(work, fpp.depend(work, [source]) + [source])
        component = model.components[args.component]

        module, _, base = args.component.rpartition(".")
        new_name = args.name or ((module + "." if module else "") + "Sim" + base)
        new_module, _, new_base = new_name.rpartition(".")

        text = component.file.read_text()
        masked = _mask(text)
        start = _offset(text, component.line, component.col)
        opening = masked.find("{", start)
        closing = _matching_brace(masked, opening)
        header = text[start:opening]
        if not re.search(r"\bcomponent\s+" + re.escape(base) + r"\b", header):
            raise SseError(f"{component.file}:{component.line}: could not locate the definition of {args.component}")
        header = re.sub(r"\bcomponent\s+" + re.escape(base) + r"\b", f"component {new_base}", header)
        body = text[opening:closing + 1]

        output.mkdir(parents=True, exist_ok=True)
        # Included model fragments (events, telemetry) are copied so the mirror stays self-contained
        for include in re.findall(r'^\s*include\s+"([^"]+)"', body, re.M):
            target = output / include
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((component.file.parent / include).read_text())

        indent = "  " if new_module else ""
        lines = [f"# Simulation surrogate mirroring {args.component}. Created by `fprime-sse scaffold`.", ""]
        if new_module:
            lines += [f"module {new_module} {{", ""]
        lines.append(f"{indent}@ Simulation surrogate for {args.component}: same ports and dictionary entries")
        lines.append(indent + (header + body).replace("\n", "\n" + indent if new_module else "\n"))
        if new_module:
            lines += ["", "}"]
        model_file = output / f"{new_base}.fpp"
        model_file.write_text("\n".join(lines) + "\n")

        (output / "CMakeLists.txt").write_text(
            "register_fprime_library(\n"
            "    AUTOCODER_INPUTS\n"
            f'        "${{CMAKE_CURRENT_LIST_DIR}}/{new_base}.fpp"\n'
            "    SOURCES\n"
            f'        "${{CMAKE_CURRENT_LIST_DIR}}/{new_base}.cpp"\n'
            "    DEPENDS\n"
            "        Sse_Core\n"
            ")\n"
        )
        (output / "sse-catalog.yaml").write_text(
            "surrogates:\n"
            f"  - surrogate: {new_name}\n"
            "    replaces:\n"
            f"      - {args.component}\n"
        )
        (output / "README.md").write_text(README.format(
            new_base=new_base, new_name=new_name, original=args.component,
            original_cpp=args.component.replace(".", "::"),
        ))
        try:
            fpp.check(work, fpp.depend(work, [model_file]) + [model_file])
        except SseError as error:
            raise SseError(f"the scaffolded model {model_file} does not pass fpp-check:\n{error}") from None

    counts = (f"{len(component.ports)} ports, {len(component.commands)} commands, {len(component.events)} events, "
              f"{len(component.channels)} channels")
    print(f"Created {new_name} in {output}, mirroring {args.component} ({counts}).")
    print(f"Next steps are in {output / 'README.md'}.")
    return 0
