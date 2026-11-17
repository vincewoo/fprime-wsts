"""The JSON Schemas of the manifests (schemas/): what editors complete and check against.
They are written by hand, so they are held to the keys the parser accepts."""

import json

import pytest
import yaml

from fprime_wsts import manifest, system
from fprime_wsts.manifest import TOOLKIT_ROOT

SCHEMAS = TOOLKIT_ROOT / "schemas"
SIM = TOOLKIT_ROOT / "examples" / "mission-sim"


def schema(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def keys(node):
    return set(node["properties"])


def test_the_deployment_schema_has_the_keys_the_parser_accepts():
    top = schema("wsts")
    properties = top["properties"]
    assert keys(top) == manifest.KNOWN_KEYS and top["required"] == ["deployment"]
    assert not top["additionalProperties"], "a misspelt key is flagged while typing"
    assert keys(properties["substitute"]["items"]) == manifest.SUBSTITUTE_KEYS
    assert keys(properties["simulators"]) == manifest.SIMULATOR_KEYS
    assert keys(properties["devices"]["items"]) == manifest.DEVICE_KEYS
    assert keys(properties["clocks"]["items"]) == manifest.CLOCK_KEYS
    assert keys(properties["cmake"]) == manifest.CMAKE_KEYS
    assert keys(properties["libraries"]) == manifest.LIBRARY_KEYS
    assert keys(properties["run"]) == manifest.RUN_KEYS
    assert keys(properties["files"]) == manifest.FILES_KEYS
    assert keys(properties["host"]) == manifest.HOST_KEYS
    assert keys(properties["time"]) == manifest.TIME_KEYS
    assert set(properties["time"]["properties"]["mode"]["enum"]) <= set(manifest.TIME_MODES)
    assert set(properties["shims"]["items"]["enum"]) == set(manifest.SHIMS)
    assert keys(properties["setup"]["oneOf"][1]) == manifest.SETUP_KEYS
    assert set(properties["models"]["items"]["properties"]["kind"]["enum"]) == set(manifest.MODEL_KINDS)


def test_the_system_schema_has_the_keys_the_parser_accepts():
    top = schema("wsts-system")
    assert keys(top) == system.SYSTEM_KEYS
    assert keys(top["properties"]["elements"]["additionalProperties"]["oneOf"][1]) == system.ELEMENT_KEYS
    assert keys(top["properties"]["links"]["items"]) == system.LINK_KEYS
    assert set(top["properties"]["links"]["items"]["properties"]["kind"]["enum"]) == system.LINK_KINDS


@pytest.mark.parametrize("file, name", [("cdh.yaml", "wsts"), ("payload.yaml", "wsts"), ("wsts-system.yaml", "wsts-system")])
def test_the_example_manifests_fit_the_schemas(file, name):
    jsonschema = pytest.importorskip("jsonschema")
    text = (SIM / file).read_text()
    assert text.startswith(f"# yaml-language-server: $schema=../../schemas/{name}.schema.json")
    jsonschema.validate(yaml.safe_load(text), schema(name))


def test_the_cmake_bits_property_is_32_or_64():
    cmake = schema("wsts")["properties"]["cmake"]["properties"]
    assert [branch["enum"] for branch in cmake["bits"]["oneOf"]] == [[32, 64], ["32", "64"]]
    jsonschema = pytest.importorskip("jsonschema")
    for value in (32, 64, "32", "64"):
        jsonschema.validate({"deployment": "D", "cmake": {"bits": value}}, schema("wsts"))
    for value in (16, 8, "wide"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"deployment": "D", "cmake": {"bits": value}}, schema("wsts"))


def test_the_schema_flags_what_the_parser_refuses():
    jsonschema = pytest.importorskip("jsonschema")
    for data in ({"deployment": "D", "substitutes": []}, {"keep": []}, {"deployment": "D", "bridge": "udp:1"},
                 {"deployment": "D", "models": [{"name": "m", "class": "C", "endpoint": "e", "kind": "can"}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, schema("wsts"))
