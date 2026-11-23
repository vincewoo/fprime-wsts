"""Couplings to external dynamics simulators (docs/dynamics.md).

A *plant* is a dynamics simulator stepped on the simulation's own time: a thermal model, a
spacecraft's attitude dynamics, a battery. The lockstep conductor (lockstep.Conductor) steps
it between two instants:

    instant k is finished: every element quiescent
      read the inputs        what flight software commanded (pins, registers, model values)
      step the plant         from t_k to t_k+1, the inputs held over the step
      write the outputs      what sensors see, as bridge actions, before instant k+1

The outputs are bridge actions, so they are recorded and journaled as a client's are, and a
stepped run with a plant in it records the same bytes every time.

A manifest (wsts.yaml or wsts-system.yaml) names its plants under `dynamics`:

    dynamics:
      - name: thermal
        python: plants/thermal.py        # defines create(params) -> plant; or fmu: plants/Thermal.fmu
        params: {C: 5.0}
        step: 0.05                       # seconds per exchange; default: every instant
        inputs:
          heater: {element: payload, gpio: heaterPin}
        outputs:
          T:
            - {element: payload, model: tempSensor, key: text, format: "$TEMP,{:.1f}\\n"}
            - {element: payload, model: fpga, key: reg.0x0C, scale: 10, round: true}
          switch: {element: payload, gpio: switchPin, above: 45, hysteresis: 5}

A plant is any object with `step(t, dt, inputs) -> outputs` (seconds of simulated time since
start-up, the step's length, and dicts by name), and optionally `start(t0)` and `stop()`.
A Python plant's file defines `create(params)`, which returns one.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .bridge import BridgeError
from .fpp import WstsError

KINDS = ("python", "fmu", "socket", "fortytwo")
#: What the mapping form of the `socket` and `fortytwo` kinds takes
SOCKET_KEYS = {"address", "command"}
FORTYTWO_KEYS = {"case", "executable"}
SPEC_KEYS = {"name", "params", "step", "inputs", "outputs", *KINDS}
#: Where an input is read from, and what each needs besides `element`
INPUT_TARGETS = {"gpio": (), "model": ("key",), "register": ("offset",)}
#: Where an output is written to
OUTPUT_TARGETS = {"gpio": (), "model": ("key",), "register": ("offset",), "i2c": ("addr", "reg"), "bytes": ()}
INPUT_TRANSFORMS = {"scale", "add"}
OUTPUT_TRANSFORMS = {"scale", "add", "round", "format", "above", "below", "hysteresis"}
#: An output is a whole number for these targets: `round` is implied
WHOLE = {"register", "i2c"}


class DynamicsError(WstsError):
    """A plant cannot be set up, or failed during an exchange"""


class PlantMismatch(DynamicsError):
    """A plant re-driven during a restore did not do what it did in the run that was
    snapshotted: `index` is the exchange (from 0), `expected` and `found` the two lines of its
    trace (None where one of them has none)"""

    def __init__(self, plant: str, index: int, expected: Optional[dict], found: Optional[dict], why: str = ""):
        self.plant, self.index, self.expected, self.found = plant, index, expected, found
        line = lambda value: json.dumps(value, separators=(",", ":")) if value is not None else None
        super().__init__(
            f"plant '{plant}': the restored run differs from the snapshotted one at exchange {index}"
            + (f" ({why})" if why else "") + ":\n"
            f"  snapshot: {line(expected) or '(none: the restore took more exchanges)'}\n"
            f"  restored: {line(found) or '(none: the restore stopped short)'}\n"
            "A plant must give the same values for the same inputs: no host clock, no unseeded randomness, "
            "the same plant file and parameters, and the same machine and build for compiled code")


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------

def _keys_error(where: str, unknown: Iterable, known: Iterable) -> DynamicsError:
    from .manifest import unknown_keys

    return DynamicsError(f"{where}: unknown key(s): {unknown_keys(set(unknown), set(known))}")


def _number(where: str, key: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DynamicsError(f"{where}: '{key}' is a number, not {value!r}")
    return float(value)


def _mapping(where: str, entry, targets: Dict[str, tuple], transforms: set, elements: Optional[List[str]]) -> dict:
    """One input or output mapping, checked: its element, one target, the target's own keys,
    and its transforms"""
    if not isinstance(entry, dict):
        raise DynamicsError(f"{where}: a mapping, e.g. {{gpio: heaterPin}}, not {entry!r}")
    found = [target for target in targets if target in entry]
    if len(found) != 1:
        raise DynamicsError(f"{where}: name one of {', '.join(targets)}" + (f", not {' and '.join(found)}" if found
                                                                             else ""))
    target = found[0]
    known = {"element", target, *targets[target], *transforms}
    unknown = set(entry) - known
    if unknown:
        raise _keys_error(where, unknown, known)
    for key in targets[target]:
        if key not in entry:
            raise DynamicsError(f"{where}: {target} needs '{key}'")
    mapping = dict(entry)
    if elements is None:
        if "element" in mapping:
            raise DynamicsError(f"{where}: 'element' belongs in a system manifest; a deployment's plant reaches "
                                "its own deployment")
    else:
        if "element" not in mapping and len(elements) == 1:
            mapping["element"] = elements[0]
        if mapping.get("element") not in elements:
            raise DynamicsError(f"{where}: say which element: \"element\": one of {', '.join(elements)}")
    for key in ("scale", "add", "above", "below", "hysteresis"):
        if key in mapping:
            mapping[key] = _number(where, key, mapping[key])
    if "above" in mapping and "below" in mapping:
        raise DynamicsError(f"{where}: one threshold: 'above' or 'below'")
    if "hysteresis" in mapping and not ("above" in mapping or "below" in mapping):
        raise DynamicsError(f"{where}: 'hysteresis' goes with a threshold ('above' or 'below')")
    if mapping.get("hysteresis", 0.0) < 0:
        raise DynamicsError(f"{where}: 'hysteresis' is not negative")
    if "format" in mapping and not isinstance(mapping["format"], str):
        raise DynamicsError(f"{where}: 'format' is a Python format string, e.g. \"$TEMP,{{:.1f}}\\n\"")
    if "round" in mapping and not isinstance(mapping["round"], bool):
        raise DynamicsError(f"{where}: 'round' is true or false")
    if target == "model" and not isinstance(mapping["model"], str):
        raise DynamicsError(f"{where}: 'model' is the name of a model of the manifest")
    for key in ("offset", "addr", "reg"):
        if key in mapping and (isinstance(mapping[key], bool) or not isinstance(mapping[key], (int, str))):
            raise DynamicsError(f"{where}: '{key}' is a number (decimal or \"0x...\")")
        if key in mapping and isinstance(mapping[key], str):
            try:
                mapping[key] = int(mapping[key], 0)
            except ValueError:
                raise DynamicsError(f"{where}: '{key}' is a number (decimal or \"0x...\"), not {mapping[key]!r}") \
                    from None
    if target == "bytes" and "format" not in mapping:
        raise DynamicsError(f"{where}: bytes needs 'format': the text sent, e.g. \"$TEMP,{{:.1f}}\\n\"")
    return mapping


def _kind(at: str, kind: str, value) -> dict:
    """What a plant's kind key says: `path` (a file, or 42's case directory) and the kind's own
    keys"""
    if kind in ("python", "fmu"):
        if not isinstance(value, str) or not value:
            raise DynamicsError(f"{at}: {kind} is the path of the plant's file, relative to the manifest")
        if kind == "python" and not value.endswith(".py"):
            raise DynamicsError(f"{at}: python names a Python file (it defines create(params))")
        if kind == "fmu" and not value.endswith(".fmu"):
            raise DynamicsError(f"{at}: fmu names an FMU (a .fmu file)")
        return {"path": value}
    known = SOCKET_KEYS if kind == "socket" else FORTYTWO_KEYS
    first = "address" if kind == "socket" else "case"
    if isinstance(value, str):
        value = {first: value}
    if not isinstance(value, dict):
        raise DynamicsError(f"{at}: {kind} is {'an address' if kind == 'socket' else 'a case directory'}, or a "
                            f"mapping with {', '.join(sorted(known))}")
    unknown = set(value) - known
    if unknown:
        raise _keys_error(f"{at}: {kind}", unknown, known)
    if not isinstance(value.get(first), str) or not value[first]:
        raise DynamicsError(f"{at}: {kind} needs '{first}'")
    if kind == "socket":
        from .plant_socket import parse_address

        try:
            parse_address(value["address"])
        except ValueError as error:
            raise DynamicsError(f"{at}: socket: {error}") from None
        command = value.get("command")
        if command is not None and (not isinstance(command, list) or not command
                                    or not all(isinstance(arg, (str, int, float)) for arg in command)):
            raise DynamicsError(f"{at}: socket.command is the plant server's command line, a list "
                                "(run in the manifest's directory; {port} and {address} are replaced)")
        if value["address"].endswith(":0") and command is None:
            raise DynamicsError(f"{at}: socket: port 0 (a free port, chosen at run time) needs a 'command' that "
                                "starts the server on it ({port})")
        return {"address": value["address"], "command": [str(arg) for arg in command] if command else None}
    executable = value.get("executable")
    if executable is not None and (not isinstance(executable, str) or not executable):
        raise DynamicsError(f"{at}: fortytwo.executable is the path of 42's executable")
    return {"path": value["case"], "executable": executable}


def parse_specs(data, where: str, elements: Optional[List[str]] = None) -> List[dict]:
    """The `dynamics` section of a manifest, checked. `elements` lists a system's elements;
    None for a deployment's manifest. Paths stay as written (relative to the manifest)."""
    if data is None:
        return []
    if not isinstance(data, list):
        raise DynamicsError(f"{where}: dynamics is a list of plants, each with a name")
    specs: List[dict] = []
    names: Set[str] = set()
    for index, entry in enumerate(data):
        at = f"{where}: dynamics[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise DynamicsError(f"{at}: a plant has a name")
        name = entry["name"]
        at = f"{where}: plant '{name}'"
        if name in names:
            raise DynamicsError(f"{at}: two plants of that name")
        names.add(name)
        unknown = set(entry) - SPEC_KEYS
        if unknown:
            raise _keys_error(at, unknown, SPEC_KEYS)
        kinds = [kind for kind in KINDS if kind in entry]
        if len(kinds) != 1:
            raise DynamicsError(f"{at}: say what the plant is with one of {', '.join(KINDS)}")
        kind = kinds[0]
        how = _kind(at, kind, entry[kind])
        path = how.pop("path", None)
        params = entry.get("params") or {}
        if not isinstance(params, dict) or not all(isinstance(value, (int, float, str, bool))
                                                   for value in params.values()):
            raise DynamicsError(f"{at}: params maps names to numbers, strings or booleans")
        step = entry.get("step")
        if step is not None:
            step = _number(at, "step", step)
            if step <= 0:
                raise DynamicsError(f"{at}: 'step' is a positive number of seconds")
        inputs = entry.get("inputs") or {}
        outputs = entry.get("outputs") or {}
        if not isinstance(inputs, dict) or not isinstance(outputs, dict):
            raise DynamicsError(f"{at}: inputs and outputs map the plant's names to mappings")
        if not outputs:
            raise DynamicsError(f"{at}: no outputs: a plant gives flight software what its sensors see")
        spec = {
            "name": name, "kind": kind, "path": path, **how, "params": {str(k): v for k, v in params.items()},
            "step": step,
            "inputs": {str(key): _mapping(f"{at}: input '{key}'", value, INPUT_TARGETS, INPUT_TRANSFORMS, elements)
                       for key, value in inputs.items()},
            "outputs": {str(key): [_mapping(f"{at}: output '{key}'", one, OUTPUT_TARGETS, OUTPUT_TRANSFORMS, elements)
                                   for one in (value if isinstance(value, list) else [value])]
                        for key, value in outputs.items()},
        }
        written: Dict[tuple, str] = {}
        for key, mappings in spec["outputs"].items():
            for mapping in mappings:
                slot = slot_of(mapping)
                if slot in written:
                    raise DynamicsError(f"{at}: outputs '{written[slot]}' and '{key}' write the same place")
                written[slot] = key
        specs.append(spec)
    return specs


def slot_of(mapping: dict) -> tuple:
    """Where an output writes, to tell one place from another"""
    for target in OUTPUT_TARGETS:
        if target in mapping:
            extra = tuple(mapping.get(key) for key in OUTPUT_TARGETS[target])
            return (mapping.get("element"), target, mapping[target], *extra)
    raise ValueError(mapping)


def check_targets(specs: List[dict], where: str, reports: Dict[Optional[str], dict]) -> None:
    """Every model and endpoint a plant reads or writes exists in its element's generated
    report (`reports` by element; None for a deployment's own)"""
    for spec in specs:
        for direction in ("inputs", "outputs"):
            for name, value in spec[direction].items():
                for mapping in (value if isinstance(value, list) else [value]):
                    report = reports.get(mapping.get("element"))
                    if report is None:
                        continue
                    at = f"{where}: plant '{spec['name']}': {direction[:-1]} '{name}'"
                    models = {model["name"] for model in report.get("models", [])}
                    endpoints = {s["endpoint"] for s in report.get("substitutions", []) if s.get("endpoint")}
                    endpoints |= {model["endpoint"] for model in report.get("models", []) if model.get("endpoint")}
                    if "model" in mapping and mapping["model"] not in models:
                        raise DynamicsError(f"{at}: no model '{mapping['model']}'"
                                            + (f" on element {mapping['element']}" if mapping.get("element") else "")
                                            + f" (models: {', '.join(sorted(models)) or 'none'})")
                    for target in ("gpio", "register", "i2c", "bytes"):
                        if target in mapping and mapping[target] not in endpoints:
                            raise DynamicsError(f"{at}: no endpoint '{mapping[target]}'"
                                                + (f" on element {mapping['element']}" if mapping.get("element")
                                                   else "")
                                                + f" (endpoints: {', '.join(sorted(endpoints)) or 'none'})")


def relocate(specs: List[dict], resolve: Callable[[str], Path], rel: Callable[[Path], str]) -> List[dict]:
    """The specs with each plant's path made relative to the generated directory. `resolve`
    turns a path of the manifest into a file, `rel` a file into a path of the output."""
    found = []
    for spec in specs:
        if spec["kind"] == "socket":
            # The server's command runs in the manifest's directory
            found.append({**spec, "cwd": rel(resolve("."))} if spec.get("command") else dict(spec))
            continue
        path = resolve(spec["path"])
        if spec["kind"] == "fortytwo":
            missing = [name for name in ("Inp_Sim.txt", "Inp_IPC.txt") if not (path / name).is_file()]
            if missing:
                raise DynamicsError(f"plant '{spec['name']}': {spec['path']} is not a case of 42 ({path} has no "
                                    f"{' or '.join(missing)})")
            executable = spec.get("executable")
            if executable and executable.startswith(("./", "../")):
                executable = rel(resolve(executable))
                if not executable.startswith("../"):
                    executable = "./" + executable  # a path of the generated directory, not a name on the PATH
            found.append({**spec, "path": rel(path), "executable": executable})
            continue
        if not path.is_file():
            raise DynamicsError(f"plant '{spec['name']}': {spec['path']} not found ({path})")
        found.append({**spec, "path": rel(path)})
    return found


def describe(spec: dict) -> str:
    """One line for a report"""
    outputs = ", ".join(sorted(spec["outputs"]))
    inputs = ", ".join(sorted(spec["inputs"])) or "none"
    every = f"every {spec['step']:g} s" if spec.get("step") else "every instant"
    what = spec["address"] if spec["kind"] == "socket" else spec["path"]
    return f"`{spec['name']}` ({spec['kind']}: `{what}`), {every}; inputs {inputs}; outputs {outputs}"


# ---------------------------------------------------------------------------
# Plants
# ---------------------------------------------------------------------------

def load_python(path: Path, params: dict):
    """The plant a Python file's create(params) returns"""
    spec = importlib.util.spec_from_file_location(f"wsts_plant_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise DynamicsError(f"{path}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))  # a plant may import what lies beside it
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    create = getattr(module, "create", None)
    if not callable(create):
        raise DynamicsError(f"{path} does not define create(params): the function that returns the plant "
                            "(an object with step(t, dt, inputs) -> outputs)")
    plant = create(dict(params))
    if not callable(getattr(plant, "step", None)):
        raise DynamicsError(f"{path}: create(params) returned {type(plant).__name__}, which has no "
                            "step(t, dt, inputs) method")
    return plant


def load_plant(spec: dict, base: Path, log_dir: Optional[Path] = None):
    """The plant a spec names. `base` is what its paths are relative to (the generated
    directory); `log_dir` where a plant process logs"""
    log = Path(log_dir) / f"dynamics-{spec['name']}.log" if log_dir else None
    if spec["kind"] == "socket":
        from .plant_socket import SocketPlant

        cwd = base / spec["cwd"] if spec.get("cwd") else base
        return SocketPlant(spec["address"], spec["params"], name=spec["name"], inputs=list(spec["inputs"]),
                           outputs=list(spec["outputs"]), command=spec.get("command"), cwd=cwd, log=log)
    if spec["kind"] == "fortytwo":
        from .fortytwo import FortyTwoPlant

        case = Path(spec["path"]) if Path(spec["path"]).is_absolute() else base / spec["path"]
        executable = spec.get("executable")
        if executable and executable.startswith(("./", "../")):
            executable = str((base / executable).resolve())
        return FortyTwoPlant(case, executable, name=spec["name"], inputs=list(spec["inputs"]),
                             outputs=list(spec["outputs"]), params=spec["params"], log=log)
    path = Path(spec["path"])
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise DynamicsError(f"plant '{spec['name']}': {path} not found")
    if spec["kind"] == "python":
        return load_python(path, spec["params"])
    from .fmi import FmuPlant

    return FmuPlant(path, spec["params"], name=spec["name"], inputs=list(spec["inputs"]),
                    outputs=list(spec["outputs"]))


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------

def _as_number(text: str):
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def input_value(mapping: dict, raw) -> Any:
    """What the plant is given for what was read"""
    if mapping.get("gpio") is not None:
        value: Any = 1.0 if raw else 0.0
    elif mapping.get("register") is not None:
        value = int(raw)
    else:
        value = _as_number(str(raw))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "scale" in mapping or "add" in mapping:
            value = value * mapping.get("scale", 1.0) + mapping.get("add", 0.0)
    return value


class Output:
    """One place an output of the plant is written to, with what was last written there"""

    def __init__(self, name: str, mapping: dict, plant: str):
        self.name = name
        self.mapping = mapping
        self.plant = plant
        self.element = mapping.get("element")
        self.target = next(target for target in OUTPUT_TARGETS if target in mapping)
        self.last: Optional[dict] = None
        self.level: Optional[bool] = None  # a threshold's state, for hysteresis

    def convert(self, value) -> Any:
        mapping = self.mapping
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        if not isinstance(value, (int, float)):
            if "format" in mapping or self.target in ("model", "bytes"):
                return value  # text the plant made itself
            raise DynamicsError(f"plant '{self.plant}': output '{self.name}' is {value!r}, not a number")
        if not math.isfinite(value):
            raise DynamicsError(f"plant '{self.plant}': output '{self.name}' is {value}")
        value = value * mapping.get("scale", 1.0) + mapping.get("add", 0.0)
        if "above" in mapping or "below" in mapping:
            band = mapping.get("hysteresis", 0.0)
            if "above" in mapping:
                limit = mapping["above"] - (band if self.level else 0.0)
                self.level = value > limit
            else:
                limit = mapping["below"] + (band if self.level else 0.0)
                self.level = value < limit
            value = self.level
        if self.target == "gpio":
            return bool(value)
        if isinstance(value, bool):
            value = 1 if value else 0
        if mapping.get("round") or self.target in WHOLE:
            value = int(math.floor(value + 0.5))
        if "format" in mapping:
            return mapping["format"].format(value)
        return value

    def action(self, value) -> dict:
        """The bridge action that gives flight software this value"""
        mapping = self.mapping
        converted = self.convert(value)
        if self.target == "gpio":
            return {"op": "gpio_force", "endpoint": mapping["gpio"], "level": converted}
        if self.target == "model":
            return {"op": "model_set", "model": mapping["model"], "key": mapping["key"], "value": str(converted)}
        if self.target == "register":
            return {"op": "reg_set", "endpoint": mapping["register"], "offset": hex(mapping["offset"]),
                    "value": hex(int(converted) & 0xFFFFFFFFFFFFFFFF)}
        if self.target == "i2c":
            return {"op": "i2c_reg_set", "endpoint": mapping["i2c"], "addr": int(mapping["addr"]),
                    "reg": int(mapping["reg"]), "value": int(converted) & 0xFF}
        return {"op": "inject_bytes", "endpoint": mapping["bytes"], "text": str(converted)}


# ---------------------------------------------------------------------------
# The coupling
# ---------------------------------------------------------------------------

class Coupling:
    """One plant, coupled to the elements it reads and writes. The conductor calls
    `exchange(now, at)` between two instants: `now` is the system's time (that of the last
    instant taken) and `at` that of the next."""

    def __init__(self, spec: dict, bridges: Dict[str, Any], base: Path, trace_dir: Optional[Path] = None,
                 default_element: Optional[str] = None):
        self.spec = spec
        self.name: str = spec["name"]
        self.bridges = bridges
        self.base = Path(base)
        self.default_element = default_element
        self.step_ns = int(round(spec["step"] * 1e9)) if spec.get("step") else None
        self.inputs = {name: dict(mapping) for name, mapping in spec["inputs"].items()}
        self.outputs = [Output(name, dict(mapping), self.name)
                        for name, mappings in spec["outputs"].items() for mapping in mappings]
        for mapping in [*self.inputs.values(), *(output.mapping for output in self.outputs)]:
            mapping.setdefault("element", default_element)
        for output in self.outputs:
            output.element = output.mapping["element"]
        missing = sorted({m["element"] for m in [*self.inputs.values(), *(o.mapping for o in self.outputs)]
                          if m["element"] not in bridges})
        if missing:
            raise DynamicsError(f"plant '{self.name}': element(s) {', '.join(map(str, missing))} have no bridge to "
                                "read or write through")
        self.plant = None
        self.time_ns: Optional[int] = None  # the plant's time: where its last step ended
        self.exchanges = 0
        self.actions = 0
        self.state: dict = {"inputs": {}, "outputs": {}, "time": None}
        # A restore: the trace of the run that was snapshotted, which the plant is re-driven
        # along (shadow mode: compared, not written, as the journal writes it)
        self.reference: Optional[List[dict]] = None
        self.shadowed = 0
        self.trace_path = Path(trace_dir) / f"dynamics-{self.name}.jsonl" if trace_dir else None
        self._trace = None

    # ---- Life ----

    def start(self, now_ns: int) -> None:
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)  # where a plant's process logs, too
        self.plant = load_plant(self.spec, self.base, self.trace_path.parent if self.trace_path else None)
        self.time_ns = now_ns
        start = getattr(self.plant, "start", None)
        if callable(start):
            start(now_ns / 1e9)
        if self.trace_path is not None:
            self._trace = self.trace_path.open("w")

    def stop(self) -> None:
        if self.plant is not None:
            stop = getattr(self.plant, "stop", None)
            if callable(stop):
                try:
                    stop()
                finally:
                    self.plant = None
            self.plant = None
        if self._trace is not None:
            self._trace.close()
            self._trace = None

    @property
    def started(self) -> bool:
        return self.plant is not None

    # ---- Restores (docs/dynamics.md, phase 3) ----

    def shadow(self, reference: List[dict]) -> None:
        """Re-drive the plant along the trace of a snapshotted run: until the trace's exchanges
        are used up, each exchange reads its inputs and steps the plant as usual, compares the
        line it would trace with the reference, and writes nothing (the restored run's journal
        gives flight software the plant's values)"""
        self.reference = list(reference)
        self.shadowed = 0

    @property
    def shadowing(self) -> bool:
        return self.reference is not None

    def end_shadow(self) -> None:
        """The restore has reached the snapshot: every exchange of the reference must have been
        taken again. Coupling goes on from here, from the re-driven plant."""
        if self.reference is None:
            return
        left = self.reference[self.shadowed:]
        self.reference = None
        if left:
            raise PlantMismatch(self.name, self.shadowed, left[0], None,
                                f"{len(left)} exchange(s) of the snapshot not taken again")

    def due(self, now_ns: int, at_ns: int) -> bool:
        """Whether the instant at `at_ns` ends a step. A plant without a step of its own
        exchanges before every instant; one with a step, at its start plus whole steps."""
        if self.step_ns is None:
            return True
        if not self.started:
            self.start(now_ns)
        return at_ns - self.time_ns >= self.step_ns

    # ---- An exchange ----

    def read_inputs(self) -> Dict[str, Any]:
        pending = {}
        for name, mapping in self.inputs.items():
            bridge = self.bridges[mapping["element"]]
            if "gpio" in mapping:
                pending[name] = (mapping, "level", bridge.submit("gpio_get", endpoint=mapping["gpio"]))
            elif "model" in mapping:
                pending[name] = (mapping, "value", bridge.submit("model_get", model=mapping["model"],
                                                                 key=mapping["key"]))
            else:
                pending[name] = (mapping, "value", bridge.submit("reg_get", endpoint=mapping["register"],
                                                                 offset=hex(mapping["offset"])))
        values = {}
        for name, (mapping, field, request) in pending.items():
            try:
                raw = request.result()[field]
            except (BridgeError, WstsError) as error:
                raise DynamicsError(f"plant '{self.name}': input '{name}' on element {mapping['element']}: "
                                    f"{error}") from None
            if "register" in mapping:
                raw = int(raw, 0) if isinstance(raw, str) else int(raw)
            values[name] = input_value(mapping, raw)
        return values

    def exchange(self, now_ns: int, at_ns: int) -> Set[str]:
        """Read the inputs, step the plant to `at_ns`, write what changed. Returns the elements
        written to (their link counts may have moved: the conductor reads them again)."""
        if not self.started:
            self.start(now_ns)
        inputs = self.read_inputs()
        t = self.time_ns / 1e9
        dt = (at_ns - self.time_ns) / 1e9
        try:
            outputs = self.plant.step(t, dt, dict(inputs))
        except DynamicsError:
            raise
        except Exception as error:
            raise DynamicsError(f"plant '{self.name}' failed to step from {t:g} s by {dt:g} s: "
                                f"{type(error).__name__}: {error}") from error
        if not isinstance(outputs, dict):
            raise DynamicsError(f"plant '{self.name}': step() returned {type(outputs).__name__}, not a dict of outputs")
        missing = sorted({output.name for output in self.outputs} - set(outputs))
        if missing:
            raise DynamicsError(f"plant '{self.name}': step() gave no value for output(s) {', '.join(missing)}")
        batches: Dict[str, List[dict]] = {}
        for output in self.outputs:
            action = output.action(outputs[output.name])
            if action != output.last:  # only what changed: every action is a journal line
                output.last = action
                batches.setdefault(output.element, []).append({**action, "via": self.name})
        plain = {name: _plain(value) for name, value in outputs.items()}
        line = {"t": at_ns / 1e9, "dt": dt, "in": {k: _plain(v) for k, v in inputs.items()}, "out": plain}
        if self.reference is not None:
            # Shadow mode: the same exchange as in the snapshotted run, to the bit (a trace line
            # read back from JSON is the same float), and nothing written
            found = json.loads(json.dumps(line))
            expected = self.reference[self.shadowed] if self.shadowed < len(self.reference) else None
            if found != expected:
                raise PlantMismatch(self.name, self.shadowed, expected, found)
            self.shadowed += 1
            batches = {}
        pending = {element: self.bridges[element].submit("batch", actions="\n".join(
            json.dumps(action, separators=(",", ":")) for action in actions)) for element, actions in batches.items()}
        for element, request in pending.items():
            try:
                request.result()
            except (BridgeError, WstsError) as error:
                raise DynamicsError(f"plant '{self.name}': writing to element {element}: {error}") from None
            self.actions += len(batches[element])
        self.time_ns = at_ns
        self.exchanges += 1
        self.state = {"time": line["t"], "inputs": line["in"], "outputs": plain}
        if self._trace is not None:
            self._trace.write(json.dumps(line, separators=(",", ":")) + "\n")
            self._trace.flush()
        return set(batches)


def _plain(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def couplings(specs: List[dict], bridges: Dict[str, Any], base: Path, trace_dir: Optional[Path] = None,
              default_element: Optional[str] = None) -> List[Coupling]:
    return [Coupling(spec, bridges, base, trace_dir, default_element) for spec in specs]
