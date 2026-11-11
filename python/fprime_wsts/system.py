"""Systems of several compute elements.

A flight system with more than one computer is simulated as one process per element: each
element is an ordinary deployment with its own manifest, converted by the same transform.
A system manifest names the elements and the links between them:

    system: HubDemo
    ground: cdh
    elements:
      cdh:     {manifest: Cdh/wsts.yaml,     ids: [0x00000000, 0x1FFFFFFF]}
      payload: {manifest: Payload/wsts.yaml, ids: [0x20000000, 0x2FFFFFFF]}
    links:
      - {name: hub, a: cdh.hubDriver, b: payload.hubDriver}

A link joins a byte stream driver instance of one element to one of another. The instance
at each end is replaced by a surrogate (a link is itself a reason to substitute), and a
`Wsts::LinkByteStream` model carries the bytes between the two processes.

`ids` is the range of IDs that reaches the element, as the flight software routes commands
(for example the opcode ranges either side of a `Svc.CmdSplitter`). Each element's WSTS
instances are placed inside its window, so that they stay reachable through the flight
routing and distinct in the merged dictionary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import fpp
from .fpp import WstsError
from .generate import ID_BLOCK, Result, generate
from .manifest import LinkEnd, Manifest, _int, load_manifest

SYSTEM_FILE = "wsts-system.json"
SYSTEM_KEYS = {"system", "ground", "elements", "links", "output"}
ELEMENT_KEYS = {"manifest", "bridge", "ids"}
LINK_KEYS = {"name", "kind", "a", "b"}
LINK_KINDS = {"bytestream"}
FIRST_BRIDGE_PORT = 50100
ELEMENT_NAME = re.compile(r"[A-Za-z_]\w*")
LINK_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")


@dataclass
class ElementSpec:
    name: str
    manifest: Path
    bridge: Optional[str] = None
    ids: Optional[Tuple[int, int]] = None


@dataclass
class LinkSpec:
    name: str
    kind: str
    a: Tuple[str, str]  # (element, instance): this end listens
    b: Tuple[str, str]  # this end connects


@dataclass
class SystemManifest:
    path: Optional[Path]
    name: str
    ground: str
    elements: List[ElementSpec] = field(default_factory=list)
    links: List[LinkSpec] = field(default_factory=list)
    output: Optional[str] = None

    @property
    def root(self) -> Path:
        return (self.path.parent if self.path else Path.cwd()).resolve()

    def element(self, name: str) -> ElementSpec:
        return next(e for e in self.elements if e.name == name)


@dataclass
class SystemResult:
    output: Path
    description: dict
    elements: Dict[str, Result] = field(default_factory=dict)
    changed: List[str] = field(default_factory=list)  # --check: files that differ


def find_project(start: Path) -> Path:
    """The F Prime project (the directory with settings.ini) at or above `start`"""
    for directory in [start, *start.parents]:
        if (directory / "settings.ini").is_file():
            return directory
    raise WstsError(f"no F Prime project (settings.ini) found at or above {start}")


def is_system_manifest(path: Path) -> bool:
    """Whether a manifest file describes a system rather than a single deployment"""
    if not path.is_file():
        raise WstsError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return isinstance(data, dict) and ("system" in data or "elements" in data)


def load_system(path: Path) -> SystemManifest:
    if not path.is_file():
        raise WstsError(f"system manifest not found: {path}")
    return parse_system(yaml.safe_load(path.read_text()) or {}, path)


def parse_system(data: dict, path: Optional[Path] = None) -> SystemManifest:
    where = str(path) if path else "system manifest"
    unknown = set(data) - SYSTEM_KEYS
    if unknown:
        raise WstsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    if "system" not in data:
        raise WstsError(f"{where}: 'system' is required (a name for the system)")
    elements = data.get("elements") or {}
    if not isinstance(elements, dict) or not elements:
        raise WstsError(f"{where}: 'elements' must map element names to their deployment manifests")
    system = SystemManifest(path=path, name=str(data["system"]), ground=str(data.get("ground", next(iter(elements)))))
    if not ELEMENT_NAME.fullmatch(system.name):
        raise WstsError(f"{where}: system name '{system.name}' must be an identifier")
    system.output = data.get("output")

    for name, entry in elements.items():
        name = str(name)
        if not ELEMENT_NAME.fullmatch(name):
            raise WstsError(f"{where}: element name '{name}' must be an identifier")
        if isinstance(entry, str):
            entry = {"manifest": entry}
        if not isinstance(entry, dict) or "manifest" not in entry:
            raise WstsError(f"{where}: element '{name}' needs 'manifest' (its deployment's wsts.yaml)")
        unknown = set(entry) - ELEMENT_KEYS
        if unknown:
            raise WstsError(f"{where}: element '{name}': unknown key(s): {', '.join(sorted(unknown))}")
        spec = ElementSpec(name=name, manifest=(system.root / str(entry["manifest"])).resolve())
        if entry.get("bridge") is not None:
            spec.bridge = str(entry["bridge"])
        if entry.get("ids") is not None:
            ids = entry["ids"]
            if not isinstance(ids, (list, tuple)) or len(ids) != 2:
                raise WstsError(f"{where}: element '{name}': 'ids' must be [first, last]")
            low, high = (_int(value, f"{where}: element '{name}' ids") for value in ids)
            if low > high:
                raise WstsError(f"{where}: element '{name}': 'ids' must be [first, last] with first <= last")
            spec.ids = (low, high)
        system.elements.append(spec)
    names = [e.name for e in system.elements]
    if system.ground not in names:
        raise WstsError(f"{where}: 'ground' names '{system.ground}', which is not an element ({', '.join(names)})")
    windows = sorted((e.ids, e.name) for e in system.elements if e.ids)
    for (first, first_name), (second, second_name) in zip(windows, windows[1:]):
        if second[0] <= first[1]:
            raise WstsError(f"{where}: the 'ids' windows of '{first_name}' and '{second_name}' overlap")

    def end(link: str, key: str, value) -> Tuple[str, str]:
        element, _, instance = str(value or "").partition(".")
        if element not in names or not instance:
            raise WstsError(f"{where}: link '{link}': '{key}' must be <element>.<instance>, with one of the elements "
                            f"{', '.join(names)}")
        return element, instance

    used: Dict[Tuple[str, str], str] = {}
    for entry in data.get("links") or []:
        if not isinstance(entry, dict) or not {"name", "a", "b"} <= set(entry):
            raise WstsError(f"{where}: each link needs 'name', 'a' and 'b'")
        unknown = set(entry) - LINK_KEYS
        if unknown:
            raise WstsError(f"{where}: link '{entry['name']}': unknown key(s): {', '.join(sorted(unknown))}")
        link = LinkSpec(name=str(entry["name"]), kind=str(entry.get("kind", "bytestream")).lower(),
                        a=end(str(entry["name"]), "a", entry["a"]), b=end(str(entry["name"]), "b", entry["b"]))
        if not LINK_NAME.fullmatch(link.name):
            raise WstsError(f"{where}: link name '{link.name}' may only contain letters, digits, '_' and '-'")
        if link.name in names or any(link.name == other.name for other in system.links):
            raise WstsError(f"{where}: link name '{link.name}' is already used by an element or another link")
        if link.kind not in LINK_KINDS:
            raise WstsError(f"{where}: link '{link.name}': kind '{link.kind}' is not supported "
                            f"(supported: {', '.join(sorted(LINK_KINDS))})")
        if link.a[0] == link.b[0]:
            raise WstsError(f"{where}: link '{link.name}' joins two instances of the same element '{link.a[0]}'")
        for side in (link.a, link.b):
            if side in used:
                raise WstsError(f"{where}: {side[0]}.{side[1]} is an end of both link '{used[side]}' and link '{link.name}'")
            used[side] = link.name
        system.links.append(link)
    return system


def _element_manifests(system: SystemManifest) -> Dict[str, Manifest]:
    """Each element's deployment manifest, with what the system adds to it filled in"""
    manifests: Dict[str, Manifest] = {}
    bridges: Dict[str, str] = {}
    for index, element in enumerate(system.elements):
        manifest = load_manifest(element.manifest)
        manifest.system = system.name
        manifest.element = element.name
        manifest.id_window = element.ids
        # Every element has its own process, and so its own bridge
        bridge = element.bridge or manifest.bridge
        if bridge == "none" and element.bridge is None:
            bridge = f"tcp:{FIRST_BRIDGE_PORT + index}"
        if bridge != "none":
            if bridge in bridges:
                raise WstsError(
                    f"elements '{bridges[bridge]}' and '{element.name}' both use bridge {bridge}: give one of them "
                    "a different 'bridge' in the system manifest"
                )
            bridges[bridge] = element.name
        manifest.bridge = bridge
        for link in system.links:
            for (name, instance), role, peer in ((link.a, "listen", link.b), (link.b, "connect", link.a)):
                if name == element.name:
                    manifest.links.append(LinkEnd(link=link.name, instance=instance, role=role, peer=".".join(peer)))
        manifests[element.name] = manifest
    return manifests


def generate_system(system: SystemManifest, output: Optional[Path] = None, check: bool = False,
                    project: Optional[Path] = None) -> SystemResult:
    """Generate the simulated deployment of every element, and the description of the
    system that `fprime-wsts run` starts from"""
    if output is None:
        output = system.root / (system.output or f"wsts/{system.name}")
    output = output.resolve()
    if output.exists() and any(output.iterdir()) and not (output / fpp.GENERATED_MARKER).exists():
        raise WstsError(f"{output} exists and was not generated by fprime-wsts; refusing to overwrite it")
    manifests = _element_manifests(system)

    result = SystemResult(output=output, description={})
    for element in system.elements:
        manifest = manifests[element.name]
        root = project or manifest.project_root() or find_project(element.manifest.parent)
        try:
            generated = generate(root, manifest, output / element.name, check=check)
        except WstsError as error:
            raise WstsError(f"element '{element.name}': {error}") from None
        result.elements[element.name] = generated
        result.changed += [f"{element.name}/{name}" for name in generated.changed]

    # The merged dictionary identifies items by ID: two wstsControl instances with the same
    # base ID would become one
    blocks = [
        (generated.report["control"]["control_base_id"], name)
        for name, generated in result.elements.items() if generated.report["control"]["enabled"]
    ]
    blocks.sort()
    for (first, first_name), (second, second_name) in zip(blocks, blocks[1:]):
        if second < first + ID_BLOCK:
            raise WstsError(
                f"the wstsControl instances of elements '{first_name}' and '{second_name}' share IDs "
                f"({first:#x} and {second:#x}): the merged dictionary could not tell them apart. Give each "
                "element an 'ids' window in the system manifest, or set control.base_id in its manifest."
            )

    endpoints = {
        name: {link["link"]: link for link in generated.report.get("links", [])}
        for name, generated in result.elements.items()
    }
    result.description = {
        "generator": "fprime-wsts",
        "system": system.name,
        "ground": system.ground,
        "elements": [
            {
                "name": element.name,
                "directory": element.name,
                "topology": result.elements[element.name].report["topology"],
                "bridge": manifests[element.name].bridge,
                "ids": list(element.ids) if element.ids else None,
                "control_base_id": result.elements[element.name].report["control"]["control_base_id"],
            }
            for element in system.elements
        ],
        "links": [
            {
                "name": link.name,
                "kind": link.kind,
                **{
                    side: {"element": element, "instance": endpoints[element][link.name]["instance"],
                           "endpoint": endpoints[element][link.name]["endpoint"]}
                    for side, (element, _) in (("a", link.a), ("b", link.b))
                },
            }
            for link in system.links
        ],
    }
    text = json.dumps(result.description, indent=2) + "\n"
    target = output / SYSTEM_FILE
    if check:
        if not target.is_file() or target.read_text() != text:
            result.changed.append(SYSTEM_FILE)
    else:
        output.mkdir(parents=True, exist_ok=True)
        (output / fpp.GENERATED_MARKER).write_text("Generated by fprime-wsts. Safe to delete and regenerate.\n")
        target.write_text(text)
    return result


def load_description(directory: Path) -> dict:
    """The description of a generated system (wsts-system.json)"""
    path = Path(directory) / SYSTEM_FILE
    if not path.is_file():
        raise WstsError(f"{path} not found: is this a generated system? (fprime-wsts generate <system manifest>)")
    return json.loads(path.read_text())
