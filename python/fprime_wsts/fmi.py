"""FMI co-simulation FMUs as plants (docs/dynamics.md), through ctypes alone.

An FMU is a zip of `modelDescription.xml` and a shared library per platform. FMI 2.0 and
3.0 co-simulation are supported: the importer (here, the conductor's coupling) owns time and
asks the FMU to integrate from `t` to `t + dt`, with its own solver inside. Model-exchange
FMUs, which need a solver from the importer, are not.

    plant = FmuPlant(Path("Thermal.fmu"), {"C": 5.0}, inputs=["heater"], outputs=["T"])
    plant.start(0.0)
    plant.step(0.0, 0.05, {"heater": 1.0})       # {"T": 21.6}
    state = plant.save_state()                    # bytes, when the FMU can serialise its state

Parameters are set by name before initialisation, inputs before each step, outputs read after.

`build_fmu()` compiles a small FMU from C sources and its model description: for the
example's plant and for tests.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .dynamics import DynamicsError


class FmuError(DynamicsError):
    """An FMU cannot be loaded, or one of its functions failed"""


# Types of FMI 3.0 variables and their ctypes; FMI 2.0's Real, Integer and Boolean map to
# Float64, Int32 and a C int
FMI3_TYPES = {
    "Float64": ctypes.c_double, "Float32": ctypes.c_float,
    "Int8": ctypes.c_int8, "UInt8": ctypes.c_uint8, "Int16": ctypes.c_int16, "UInt16": ctypes.c_uint16,
    "Int32": ctypes.c_int32, "UInt32": ctypes.c_uint32, "Int64": ctypes.c_int64, "UInt64": ctypes.c_uint64,
    "Boolean": ctypes.c_bool,
}
FMI2_TYPES = {"Real": ctypes.c_double, "Integer": ctypes.c_int, "Boolean": ctypes.c_int}
STATUS = ["OK", "Warning", "Discard", "Error", "Fatal", "Pending"]


@dataclass
class Variable:
    name: str
    reference: int
    type: str  # Real, Integer, Boolean, String (2.0); Float64, Int32, Boolean, String, ... (3.0)
    causality: str
    variability: str
    start: Optional[str] = None


@dataclass
class ModelDescription:
    version: str  # "2.0" or "3.0"
    name: str
    identifier: str  # the co-simulation model identifier: the library's name
    token: str  # guid (2.0) or instantiationToken (3.0)
    variables: Dict[str, Variable] = field(default_factory=dict)
    can_save_state: bool = False
    can_serialize_state: bool = False

    def of(self, causality: str) -> List[str]:
        return [name for name, variable in self.variables.items() if variable.causality == causality]


def _flag(value: Optional[str]) -> bool:
    return (value or "false").strip().lower() == "true"


def read_model_description(text: str, where: str = "modelDescription.xml") -> ModelDescription:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise FmuError(f"{where}: {error}") from None
    version = root.get("fmiVersion", "")
    major = version.split(".")[0]
    if major not in ("2", "3"):
        raise FmuError(f"{where}: FMI {version or '(no version)'}: FMI 2.0 and 3.0 are supported")
    cosim = root.find("CoSimulation")
    if cosim is None:
        kinds = [child.tag for child in root if child.tag in ("ModelExchange", "ScheduledExecution")]
        raise FmuError(f"{where}: not a co-simulation FMU ({', '.join(kinds) or 'no interface'}): the importer here "
                       "steps a plant, and a model-exchange FMU needs a solver from it")
    if major == "2":
        description = ModelDescription("2.0", root.get("modelName", ""), cosim.get("modelIdentifier", ""),
                                       root.get("guid", ""), can_save_state=_flag(cosim.get("canGetAndSetFMUstate")),
                                       can_serialize_state=_flag(cosim.get("canSerializeFMUstate")))
        for scalar in root.iter("ScalarVariable"):
            typed = next((child for child in scalar if child.tag in (*FMI2_TYPES, "String", "Enumeration")), None)
            kind = "Integer" if typed is not None and typed.tag == "Enumeration" else (typed.tag if typed is not None
                                                                                      else "Real")
            description.variables[scalar.get("name")] = Variable(
                scalar.get("name"), int(scalar.get("valueReference")), kind, scalar.get("causality", "local"),
                scalar.get("variability", "continuous"), typed.get("start") if typed is not None else None)
    else:
        description = ModelDescription("3.0", root.get("modelName", ""), cosim.get("modelIdentifier", ""),
                                       root.get("instantiationToken", ""),
                                       can_save_state=_flag(cosim.get("canGetAndSetFMUState")),
                                       can_serialize_state=_flag(cosim.get("canSerializeFMUState")))
        variables = root.find("ModelVariables")
        for variable in (variables if variables is not None else []):
            if variable.tag not in (*FMI3_TYPES, "String", "Enumeration"):
                continue
            kind = "Int64" if variable.tag == "Enumeration" else variable.tag
            description.variables[variable.get("name")] = Variable(
                variable.get("name"), int(variable.get("valueReference")), kind, variable.get("causality", "local"),
                variable.get("variability", "continuous"), variable.get("start"))
    if not description.identifier:
        raise FmuError(f"{where}: CoSimulation has no modelIdentifier")
    return description


def platform_directories() -> List[str]:
    """Where an FMU keeps the library for this machine: FMI 3.0's name, then FMI 2.0's"""
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(platform.system(), platform.system())
    bits = "64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "32"
    two = {"linux": "linux", "darwin": "darwin", "windows": "win"}[system] + bits if system in (
        "linux", "darwin", "windows") else system + bits
    return [f"{machine}-{system}", two]


def library_suffix() -> str:
    return {"Darwin": ".dylib", "Windows": ".dll"}.get(platform.system(), ".so")


# Callbacks: FMI 2.0's logger is variadic in C; the message is passed as written, unformatted
_LOGGER2 = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)
_ALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t)
_FREE = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_STEP_FINISHED = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)
_LOG3 = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)


class _Callbacks2(ctypes.Structure):
    _fields_ = [("logger", _LOGGER2), ("allocateMemory", _ALLOC), ("freeMemory", _FREE),
                ("stepFinished", _STEP_FINISHED), ("componentEnvironment", ctypes.c_void_p)]


_LIBC = ctypes.CDLL(None)


class FmuPlant:
    """A co-simulation FMU, stepped as a plant. `inputs` and `outputs` are the names the
    manifest maps (checked against the model description); `params` are set by name."""

    def __init__(self, path: Path, params: Optional[Dict[str, Any]] = None, name: str = "plant",
                 inputs: Optional[Sequence[str]] = None, outputs: Optional[Sequence[str]] = None):
        self.path = Path(path)
        self.name = name
        self.params = dict(params or {})
        self.log: List[str] = []
        self._instance = None
        self._directory = tempfile.mkdtemp(prefix="wsts-fmu-")
        try:
            with zipfile.ZipFile(self.path) as archive:
                archive.extractall(self._directory)
        except (OSError, zipfile.BadZipFile) as error:
            self._cleanup()
            raise FmuError(f"{self.path}: not an FMU (a zip file): {error}") from None
        try:
            text = (Path(self._directory) / "modelDescription.xml").read_text()
            self.description = read_model_description(text, f"{self.path}: modelDescription.xml")
            self._check(inputs, outputs)
            self._load()
        except OSError as error:
            self._cleanup()
            raise FmuError(f"{self.path}: {error}") from None
        except BaseException:
            self._cleanup()
            raise
        self.inputs = list(inputs) if inputs is not None else self.description.of("input")
        self.outputs = list(outputs) if outputs is not None else self.description.of("output")

    # ---- Loading ----

    def _check(self, inputs, outputs) -> None:
        description = self.description
        for kind, names in (("input", inputs or []), ("output", outputs or [])):
            for name in names:
                variable = description.variables.get(name)
                if variable is None or variable.causality != kind:
                    have = ", ".join(description.of(kind)) or "none"
                    raise FmuError(f"plant '{self.name}': {self.path.name} has no {kind} '{name}' ({kind}s: {have})")
                if variable.type == "String":
                    raise FmuError(f"plant '{self.name}': {kind} '{name}' of {self.path.name} is a string, which a "
                                   "plant exchanges as numbers only")
        for name in self.params:
            variable = description.variables.get(name)
            if variable is None or variable.causality not in ("parameter", "structuralParameter", "input"):
                have = ", ".join(description.of("parameter")) or "none"
                raise FmuError(f"plant '{self.name}': {self.path.name} has no parameter '{name}' (parameters: {have})")

    def _load(self) -> None:
        identifier = self.description.identifier
        tried = []
        for directory in platform_directories():
            candidate = Path(self._directory) / "binaries" / directory / f"{identifier}{library_suffix()}"
            tried.append(str(candidate.relative_to(self._directory)))
            if candidate.is_file():
                break
        else:
            have = sorted(str(p.relative_to(self._directory)) for p in (Path(self._directory) / "binaries").glob("*/*"))
            raise FmuError(f"{self.path}: no library for this machine (looked for {', '.join(tried)}; it has "
                           f"{', '.join(have) or 'none'})")
        try:
            self._lib = ctypes.CDLL(str(candidate))
        except OSError as error:
            raise FmuError(f"{self.path}: cannot load {candidate.name}: {error}") from None
        prefix = "fmi2" if self.description.version == "2.0" else "fmi3"
        self._prefix = prefix

    def _function(self, name: str, restype=ctypes.c_int, argtypes=None):
        try:
            function = getattr(self._lib, name)
        except AttributeError:
            raise FmuError(f"{self.path}: its library has no {name}") from None
        function.restype = restype
        if argtypes is not None:
            function.argtypes = argtypes
        return function

    def _call(self, name: str, *args, restype=ctypes.c_int, argtypes=None) -> int:
        status = self._function(name, restype, argtypes)(*args)
        if status >= 2:
            said = "; ".join(self.log[-3:])
            raise FmuError(f"plant '{self.name}': {name} returned {STATUS[status] if status < len(STATUS) else status}"
                           + (f" ({said})" if said else ""))
        return status

    def _logged(self, category, message) -> None:
        text = f"{(category or b'').decode(errors='replace')}: {(message or b'').decode(errors='replace')}"
        self.log.append(text)
        del self.log[:-50]

    # ---- Values ----

    def _set(self, values: Dict[str, Any]) -> None:
        by_type: Dict[str, List] = {}
        for name, value in values.items():
            variable = self.description.variables[name]
            by_type.setdefault(variable.type, []).append((variable, value))
        for kind, pairs in by_type.items():
            references = (ctypes.c_uint * len(pairs))(*[variable.reference for variable, _ in pairs])
            count = ctypes.c_size_t(len(pairs))
            if kind == "String":
                array = (ctypes.c_char_p * len(pairs))(*[str(value).encode() for _, value in pairs])
                name = f"{self._prefix}SetString"
            else:
                ctype = (FMI2_TYPES if self._prefix == "fmi2" else FMI3_TYPES)[kind]
                convert = float if kind in ("Real", "Float64", "Float32") else (bool if kind == "Boolean" and
                                                                                 self._prefix == "fmi3" else int)
                array = (ctype * len(pairs))(*[convert(value) for _, value in pairs])
                name = f"{self._prefix}Set{self._kind_name(kind)}"
            if self._prefix == "fmi2":
                self._call(name, self._instance, references, count, array)
            else:
                self._call(name, self._instance, references, count, array, count)

    def _kind_name(self, kind: str) -> str:
        return kind  # 2.0: Real, Integer, Boolean; 3.0: Float64, Int32, Boolean, ...

    def _get(self, names: Sequence[str]) -> Dict[str, Any]:
        found: Dict[str, Any] = {}
        by_type: Dict[str, List[Variable]] = {}
        for name in names:
            variable = self.description.variables[name]
            by_type.setdefault(variable.type, []).append(variable)
        for kind, variables in by_type.items():
            ctype = (FMI2_TYPES if self._prefix == "fmi2" else FMI3_TYPES)[kind]
            references = (ctypes.c_uint * len(variables))(*[variable.reference for variable in variables])
            count = ctypes.c_size_t(len(variables))
            array = (ctype * len(variables))()
            name = f"{self._prefix}Get{self._kind_name(kind)}"
            if self._prefix == "fmi2":
                self._call(name, self._instance, references, count, array)
            else:
                self._call(name, self._instance, references, count, array, count)
            for variable, value in zip(variables, array):
                found[variable.name] = bool(value) if kind == "Boolean" else value
        return found

    # ---- The plant ----

    def start(self, t0: float) -> None:
        description = self.description
        resources = Path(self._directory) / "resources"
        if self._prefix == "fmi2":
            self._callbacks = _Callbacks2(_LOGGER2(lambda env, name, status, category, message:
                                                   self._logged(category, message)),
                                          ctypes.cast(_LIBC.calloc, _ALLOC), ctypes.cast(_LIBC.free, _FREE),
                                          _STEP_FINISHED(), None)
            instantiate = self._function("fmi2Instantiate", ctypes.c_void_p,
                                         [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                                          ctypes.POINTER(_Callbacks2), ctypes.c_int, ctypes.c_int])
            self._instance = instantiate(self.name.encode(), 1, description.token.encode(),
                                         resources.as_uri().encode(), ctypes.byref(self._callbacks), 0, 0)
            if not self._instance:
                raise FmuError(f"plant '{self.name}': fmi2Instantiate of {self.path.name} failed"
                               + (f" ({'; '.join(self.log[-3:])})" if self.log else ""))
            self._instance = ctypes.c_void_p(self._instance)
            self._call("fmi2SetupExperiment", self._instance, 0, ctypes.c_double(0.0), ctypes.c_double(t0), 0,
                       ctypes.c_double(0.0),
                       argtypes=[ctypes.c_void_p, ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_int,
                                 ctypes.c_double])
            self._set(self.params)
            self._call("fmi2EnterInitializationMode", self._instance)
            self._call("fmi2ExitInitializationMode", self._instance)
        else:
            self._log3 = _LOG3(lambda env, status, category, message: self._logged(category, message))
            instantiate = self._function("fmi3InstantiateCoSimulation", ctypes.c_void_p,
                                         [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_bool,
                                          ctypes.c_bool, ctypes.c_bool, ctypes.c_bool, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.c_void_p, _LOG3, ctypes.c_void_p])
            self._instance = instantiate(self.name.encode(), description.token.encode(),
                                         (str(resources) + os.sep).encode(), False, False, False, False, None, 0,
                                         None, self._log3, None)
            if not self._instance:
                raise FmuError(f"plant '{self.name}': fmi3InstantiateCoSimulation of {self.path.name} failed"
                               + (f" ({'; '.join(self.log[-3:])})" if self.log else ""))
            self._instance = ctypes.c_void_p(self._instance)
            self._set(self.params)
            self._call("fmi3EnterInitializationMode", self._instance, False, ctypes.c_double(0.0),
                       ctypes.c_double(t0), False, ctypes.c_double(0.0),
                       argtypes=[ctypes.c_void_p, ctypes.c_bool, ctypes.c_double, ctypes.c_double, ctypes.c_bool,
                                 ctypes.c_double])
            self._call("fmi3ExitInitializationMode", self._instance)

    def step(self, t: float, dt: float, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self._instance is None:
            raise FmuError(f"plant '{self.name}': stepped before it was started")
        given = {name: value for name, value in inputs.items() if name in self.inputs}
        if given:
            self._set(given)
        if self._prefix == "fmi2":
            self._call("fmi2DoStep", self._instance, ctypes.c_double(t), ctypes.c_double(dt), 1,
                       argtypes=[ctypes.c_void_p, ctypes.c_double, ctypes.c_double, ctypes.c_int])
        else:
            event, terminate, early = ctypes.c_bool(), ctypes.c_bool(), ctypes.c_bool()
            reached = ctypes.c_double()
            self._call("fmi3DoStep", self._instance, ctypes.c_double(t), ctypes.c_double(dt), True,
                       ctypes.byref(event), ctypes.byref(terminate), ctypes.byref(early), ctypes.byref(reached),
                       argtypes=[ctypes.c_void_p, ctypes.c_double, ctypes.c_double, ctypes.c_bool,
                                 ctypes.POINTER(ctypes.c_bool), ctypes.POINTER(ctypes.c_bool),
                                 ctypes.POINTER(ctypes.c_bool), ctypes.POINTER(ctypes.c_double)])
            if terminate.value:
                raise FmuError(f"plant '{self.name}': the FMU asked to end the simulation at {reached.value:g} s")
            if early.value:
                raise FmuError(f"plant '{self.name}': the FMU returned early, at {reached.value:g} s of a step to "
                               f"{t + dt:g} s")
        return self._get(self.outputs)

    def stop(self) -> None:
        try:
            if self._instance is not None:
                try:
                    self._function(f"{self._prefix}Terminate")(self._instance)
                finally:
                    self._function(f"{self._prefix}FreeInstance", None, [ctypes.c_void_p])(self._instance)
                    self._instance = None
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._directory:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = ""

    # ---- State (docs/dynamics.md, "Replay and snapshots") ----

    def save_state(self) -> bytes:
        """The FMU's whole state, serialised"""
        if not self.description.can_serialize_state:
            raise FmuError(f"plant '{self.name}': {self.path.name} cannot serialise its state")
        p = self._prefix
        names = ("fmi2GetFMUstate", "fmi2SerializedFMUstateSize", "fmi2SerializeFMUstate", "fmi2FreeFMUstate") \
            if p == "fmi2" else ("fmi3GetFMUState", "fmi3SerializedFMUStateSize", "fmi3SerializeFMUState",
                                 "fmi3FreeFMUState")
        state = ctypes.c_void_p()
        self._call(names[0], self._instance, ctypes.byref(state))
        try:
            size = ctypes.c_size_t()
            self._call(names[1], self._instance, state, ctypes.byref(size))
            buffer = (ctypes.c_char * size.value)()
            self._call(names[2], self._instance, state, buffer, size)
            return bytes(buffer)
        finally:
            self._call(names[3], self._instance, ctypes.byref(state))

    def load_state(self, data: bytes) -> None:
        """Restore a state that save_state() gave (of a started FMU)"""
        if not self.description.can_serialize_state:
            raise FmuError(f"plant '{self.name}': {self.path.name} cannot serialise its state")
        p = self._prefix
        names = ("fmi2DeSerializeFMUstate", "fmi2SetFMUstate", "fmi2FreeFMUstate") if p == "fmi2" else \
            ("fmi3DeserializeFMUState", "fmi3SetFMUState", "fmi3FreeFMUState")
        state = ctypes.c_void_p()
        self._call(names[0], self._instance, data, ctypes.c_size_t(len(data)), ctypes.byref(state))
        try:
            self._call(names[1], self._instance, state)
        finally:
            self._call(names[2], self._instance, ctypes.byref(state))


def build_fmu(sources: Sequence[Path], model_description: Path, output: Path, cc: Optional[str] = None,
              flags: Sequence[str] = ()) -> Path:
    """Compile C sources into an FMU for this machine: the library under binaries/ (both the
    FMI 3.0 and the FMI 2.0 name of the platform), and the model description. Floating-point
    contraction is off, so that the FMU computes as written."""
    text = Path(model_description).read_text()
    description = read_model_description(text, str(model_description))
    compiler = cc or os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise FmuError("no C compiler (cc) to build an FMU with")
    output = Path(output)
    with tempfile.TemporaryDirectory(prefix="wsts-fmu-build-") as work:
        library = Path(work) / f"{description.identifier}{library_suffix()}"
        command = [compiler, "-shared", "-fPIC", "-O2", "-ffp-contract=off", "-fvisibility=hidden", *flags,
                   *map(str, sources), "-o", str(library), "-lm"]
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode != 0:
            raise FmuError(f"building {output.name} failed:\n{done.stderr.strip()}")
        output.parent.mkdir(parents=True, exist_ok=True)
        directories = platform_directories() if description.version == "3.0" else platform_directories()[1:]
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("modelDescription.xml", text)
            for directory in directories:
                archive.write(library, f"binaries/{directory}/{library.name}")
            for source in sources:
                archive.write(source, f"sources/{Path(source).name}")
    return output
