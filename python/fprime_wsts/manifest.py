"""Deployment manifest (wsts.yaml) and surrogate catalogs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .fpp import WstsError

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = TOOLKIT_ROOT / "catalog" / "wsts-catalog.yaml"


def _int(value, what: str) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 0)
    except ValueError:
        raise WstsError(f"{what}: '{value}' is not a number") from None


# ----------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------


@dataclass
class Surrogate:
    name: str  # FPP name, e.g. Wsts.SimI2c
    replaces: List[str] = field(default_factory=list)
    options: str = ""
    options_for: Dict[str, str] = field(default_factory=dict)
    compat: str = ""
    source: str = ""

    @property
    def cpp_name(self) -> str:
        return "::" + self.name.replace(".", "::")

    def options_when_replacing(self, component: str) -> str:
        return self.options_for.get(component, self.options)


def load_catalog(path: Path) -> List[Surrogate]:
    if not path.is_file():
        raise WstsError(f"surrogate catalog not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    surrogates = []
    for entry in data.get("surrogates", []):
        if "surrogate" not in entry:
            raise WstsError(f"{path}: catalog entry without 'surrogate'")
        surrogates.append(
            Surrogate(
                name=entry["surrogate"],
                replaces=list(entry.get("replaces", [])),
                options=str(entry.get("options", "")),
                options_for={k: str(v) for k, v in (entry.get("options_for") or {}).items()},
                compat=entry.get("compat", "") or "",
                source=str(path),
            )
        )
    return surrogates


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


@dataclass
class Substitution:
    surrogate: str
    instance: Optional[str] = None
    component: Optional[str] = None
    endpoint: Optional[str] = None
    options: Optional[str] = None


@dataclass
class ModelSpec:
    name: str
    cls: str  # C++ class, e.g. Wsts::RegisterMapI2c
    header: str
    endpoint: str
    kind: str  # bytestream, i2c, spi, gpio, none
    address: int = 0
    params: Dict[str, str] = field(default_factory=dict)


@dataclass
class MainSpec:
    generate: bool = False
    declarations: str = ""
    start: str = ""
    stop: str = ""
    sources: Optional[List[str]] = None


@dataclass
class Manifest:
    path: Optional[Path]
    deployment: str
    topology: Optional[str] = None
    output: Optional[str] = None
    substitutions: List[Substitution] = field(default_factory=list)
    keep: List[str] = field(default_factory=list)
    models: List[ModelSpec] = field(default_factory=list)
    control_enabled: bool = True
    control_base_id: Optional[int] = None
    bridge: str = "none"
    tick_hz: int = 10
    catalogs: List[str] = field(default_factory=list)
    topology_fpp: Optional[List[str]] = None
    topology_sources: Optional[List[str]] = None
    main: MainSpec = field(default_factory=MainSpec)
    extra_subdirectories: List[str] = field(default_factory=list)
    exclude_subdirectories: List[str] = field(default_factory=list)
    extra_depends: List[str] = field(default_factory=list)
    keep_libraries: List[str] = field(default_factory=list)
    drop_libraries: List[str] = field(default_factory=list)
    fpp_exclude: List[str] = field(default_factory=list)
    defines: List[str] = field(default_factory=list)

    def resolve(self, value: str, project_root: Path) -> Path:
        """Paths in a manifest are relative to the project root"""
        return (project_root / value).resolve()


KNOWN_KEYS = {
    "deployment", "topology", "output", "substitute", "keep", "models", "control", "bridge", "tick_hz",
    "catalogs", "sources", "main", "cmake", "libraries", "fpp_exclude", "defines",
}
MODEL_KINDS = {"bytestream", "i2c", "spi", "gpio", "none"}


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise WstsError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return parse_manifest(data, path)


def parse_manifest(data: dict, path: Optional[Path] = None) -> Manifest:
    where = str(path) if path else "manifest"
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise WstsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    if "deployment" not in data:
        raise WstsError(f"{where}: 'deployment' is required (the deployment directory, relative to the project root)")
    manifest = Manifest(path=path, deployment=str(data["deployment"]).strip("/"))
    manifest.topology = data.get("topology")
    manifest.output = data.get("output")
    manifest.keep = [str(k) for k in data.get("keep", [])]
    manifest.bridge = str(data.get("bridge", "none"))
    manifest.tick_hz = _int(data.get("tick_hz", 10), f"{where}: tick_hz")
    manifest.catalogs = [str(c) for c in data.get("catalogs", [])]
    manifest.fpp_exclude = [str(g) for g in data.get("fpp_exclude", [])]
    manifest.defines = [str(d) for d in data.get("defines", [])]

    for entry in data.get("substitute", []):
        if "with" not in entry or ("instance" not in entry and "component" not in entry):
            raise WstsError(f"{where}: each 'substitute' entry needs 'with' and one of 'instance' or 'component'")
        manifest.substitutions.append(
            Substitution(
                surrogate=entry["with"],
                instance=entry.get("instance"),
                component=entry.get("component"),
                endpoint=entry.get("endpoint"),
                options=entry.get("options"),
            )
        )

    for entry in data.get("models", []):
        for key in ("name", "class", "endpoint", "kind"):
            if key not in entry:
                raise WstsError(f"{where}: model entry is missing '{key}'")
        kind = str(entry["kind"]).lower()
        if kind not in MODEL_KINDS:
            raise WstsError(f"{where}: model '{entry['name']}': kind must be one of {', '.join(sorted(MODEL_KINDS))}")
        manifest.models.append(
            ModelSpec(
                name=str(entry["name"]),
                cls=str(entry["class"]),
                header=str(entry.get("header", "Wsts/Core/Models.hpp")),
                endpoint=str(entry["endpoint"]),
                kind=kind,
                address=_int(entry.get("address", 0), f"{where}: model '{entry['name']}' address"),
                params={str(k): str(v) for k, v in (entry.get("params") or {}).items()},
            )
        )

    control = data.get("control", {})
    if isinstance(control, str):
        control = {"enabled": control.lower() not in ("none", "off", "false")}
    elif isinstance(control, bool):
        control = {"enabled": control}
    manifest.control_enabled = bool(control.get("enabled", True))
    if "base_id" in control:
        manifest.control_base_id = _int(control["base_id"], f"{where}: control.base_id")

    sources = data.get("sources") or {}
    manifest.topology_fpp = sources.get("fpp")
    manifest.topology_sources = sources.get("topology")
    main = data.get("main") or {}
    manifest.main = MainSpec(
        generate=bool(main.get("generate", False)),
        declarations=str(main.get("declarations", "")),
        start=str(main.get("start", "")),
        stop=str(main.get("stop", "")),
        sources=main.get("sources", sources.get("main")),
    )
    if manifest.main.generate and not manifest.main.start:
        raise WstsError(f"{where}: main.generate needs main.start (the C++ expression that starts the deployment)")

    cmake = data.get("cmake") or {}
    manifest.extra_subdirectories = [str(d) for d in cmake.get("extra_subdirectories", [])]
    manifest.exclude_subdirectories = [str(d) for d in cmake.get("exclude_subdirectories", [])]
    manifest.extra_depends = [str(d) for d in cmake.get("depends", [])]
    libraries = data.get("libraries") or {}
    manifest.keep_libraries = [str(d) for d in libraries.get("keep", [])]
    manifest.drop_libraries = [str(d) for d in libraries.get("drop", [])]
    return manifest
