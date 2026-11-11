"""Systems of several elements: the system manifest, link planning, ID windows and the
system client. The generator tests need the FPP tools (fprime-fpp) on PATH."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from fprime_wsts.bridge import BridgeError, WstsSystem
from fprime_wsts.fpp import WstsError
from fprime_wsts.system import generate_system, is_system_manifest, load_system, parse_system
from test_bridge import FakeSim

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "examples" / "hub-demo"
LINUX = REPO / "examples" / "linux-demo"
VX = REPO / "examples" / "vx-demo"

needs_fpp = pytest.mark.skipif(
    shutil.which("fpp-to-json") is None or not (REPO / "lib" / "fprime" / "cmake").is_dir(),
    reason="needs the FPP tools on PATH and the lib/fprime submodule",
)

TWO = {
    "system": "Demo",
    "elements": {"cdh": {"manifest": "Cdh/wsts.yaml", "ids": [0, "0x1FFFFFFF"]}, "payload": "Payload/wsts.yaml"},
    "links": [{"name": "hub", "a": "cdh.hubDriver", "b": "payload.Payload.hubDriver"}],
}


def changed(**changes):
    return {**TWO, **changes}


# ----------------------------------------------------------------------
# System manifest
# ----------------------------------------------------------------------


def test_system_manifest(tmp_path):
    system = parse_system(TWO, tmp_path / "wsts-system.yaml")
    assert system.name == "Demo"
    assert system.ground == "cdh", "the first element unless 'ground' says otherwise"
    assert [(e.name, e.manifest, e.ids) for e in system.elements] == [
        ("cdh", tmp_path / "Cdh" / "wsts.yaml", (0, 0x1FFFFFFF)),
        ("payload", tmp_path / "Payload" / "wsts.yaml", None),
    ]
    link = system.links[0]
    assert (link.name, link.kind, link.a, link.b) == ("hub", "bytestream", ("cdh", "hubDriver"), ("payload", "Payload.hubDriver"))
    assert parse_system(changed(ground="payload")).ground == "payload"


def test_system_manifests_are_told_from_deployment_manifests():
    assert is_system_manifest(HUB / "wsts-system.yaml")
    assert not is_system_manifest(HUB / "Cdh" / "wsts.yaml")
    assert load_system(HUB / "wsts-system.yaml").name == "HubDemo"


@pytest.mark.parametrize("data, message", [
    ({"elements": {"a": "a.yaml"}}, "'system' is required"),
    ({"system": "S"}, "'elements' must map"),
    (changed(extra=1), "unknown key"),
    (changed(ground="nowhere"), "not an element"),
    (changed(elements={"a b": "x.yaml"}), "must be an identifier"),
    (changed(elements={"cdh": {"bridge": "tcp:1"}}), "needs 'manifest'"),
    (changed(elements={"cdh": {"manifest": "m", "ids": [5]}, "payload": "p"}), r"\[first, last\]"),
    (changed(elements={"cdh": {"manifest": "m", "ids": [0, 100]}, "payload": {"manifest": "p", "ids": [100, 200]}}),
     "windows of 'cdh' and 'payload' overlap"),
    (changed(links=[{"name": "hub", "a": "cdh.hubDriver"}]), "needs 'name', 'a' and 'b'"),
    (changed(links=[{"name": "hub", "a": "cdh.hubDriver", "b": "elsewhere.x"}]), "<element>.<instance>"),
    (changed(links=[{"name": "hub", "a": "cdh.x", "b": "cdh.y"}]), "same element"),
    (changed(links=[{"name": "hub", "kind": "gpio", "a": "cdh.x", "b": "payload.y"}]), "not supported"),
    (changed(links=[{"name": "cdh", "a": "cdh.x", "b": "payload.y"}]), "already used"),
    (changed(links=[{"name": "one", "a": "cdh.x", "b": "payload.y"}, {"name": "two", "a": "cdh.x", "b": "payload.z"}]),
     "an end of both link 'one' and link 'two'"),
])
def test_system_manifest_errors(data, message):
    with pytest.raises(WstsError, match=message):
        parse_system(data)


# ----------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------


def hub_system(tmp_path, **changes):
    """The HubDemo system manifest, changed, in a place of its own"""
    data = yaml.safe_load((HUB / "wsts-system.yaml").read_text())
    for name, element in data["elements"].items():
        element["manifest"] = str(HUB / element["manifest"])
    for key, value in changes.items():
        if key in data["elements"]:
            data["elements"][key].update(value)
        else:
            data[key] = value
    path = tmp_path / "wsts-system.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_system(path)


@needs_fpp
def test_hub_demo_system(tmp_path):
    result = generate_system(hub_system(tmp_path), tmp_path / "out")
    assert set(result.elements) == {"cdh", "payload"}
    chosen = {
        sub["instance"]: (sub["flight_component"], sub["surrogate"], sub["chosen_by"])
        for element in result.elements.values() for sub in element.report["substitutions"]
    }
    # No catalog entry replaces the socket drivers: the link is what substitutes them
    assert chosen["Cdh.hubDriver"] == ("Drv.TcpServer", "Wsts.SimByteStream", "link")
    assert chosen["Payload.hubDriver"] == ("Drv.TcpClient", "Wsts.SimByteStream", "link")
    assert "Cdh.comDriver" not in chosen, "the ground link is the same component as Payload's hub driver, and is kept"

    cdh = (tmp_path / "out" / "cdh" / "Cdh" / "Top" / "WstsSetup.cpp").read_text()
    payload = (tmp_path / "out" / "payload" / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    assert 'Wsts::configureModel(&link_hub, "role", "listen")' in cdh
    assert 'Wsts::configureModel(&link_hub, "role", "connect")' in payload
    for setup in (cdh, payload):
        assert 'Wsts::Bus::get().attachByteStream("hubDriver", &link_hub);' in setup
        assert setup.index("Wsts::Runtime::start(config);") < setup.index("link_hub.start();")
        assert setup.index("link_hub.stop();") < setup.index("Wsts::Runtime::stop();")
    compat = (tmp_path / "out" / "cdh" / "Cdh" / "Top" / "WstsCompat.hpp").read_text()
    assert "using TcpServer = ::Wsts::SimByteStream;" in compat
    assert "TcpClient" not in compat, "comDriver still uses the flight component"

    description = json.loads((tmp_path / "out" / "wsts-system.json").read_text())
    assert description == result.description
    assert description["ground"] == "cdh"
    elements = {element["name"]: element for element in description["elements"]}
    # Each element has its own bridge, and its wstsControl inside its own ID window
    assert elements["cdh"]["bridge"] != elements["payload"]["bridge"]
    assert 0x10022000 < elements["cdh"]["control_base_id"] < 0x20000000
    assert 0x20021000 < elements["payload"]["control_base_id"] <= 0x2FFFFFFF
    assert description["links"] == [{
        "name": "hub", "kind": "bytestream",
        "a": {"element": "cdh", "instance": "Cdh.hubDriver", "endpoint": "hubDriver"},
        "b": {"element": "payload", "instance": "Payload.hubDriver", "endpoint": "hubDriver"},
    }]
    report = result.elements["payload"].report
    assert report["system"] == {"name": "HubDemo", "element": "payload", "ids": [0x20000000, 0x2FFFFFFF]}
    assert report["links"] == [{"link": "hub", "instance": "Payload.hubDriver", "endpoint": "hubDriver",
                                "role": "connect", "peer": "cdh.hubDriver"}]
    assert "## Links" in (tmp_path / "out" / "payload" / "wsts-report.md").read_text()

    # Regenerating over the output is allowed, and --check finds it current
    assert generate_system(hub_system(tmp_path), tmp_path / "out", check=True).changed == []
    (tmp_path / "out" / "wsts-system.json").write_text("{}\n")
    assert generate_system(hub_system(tmp_path), tmp_path / "out", check=True).changed == ["wsts-system.json"]


@needs_fpp
def test_checked_in_hub_demo_is_current():
    result = generate_system(load_system(HUB / "wsts-system.yaml"), check=True)
    assert result.changed == [], f"regenerate: fprime-wsts generate {HUB / 'wsts-system.yaml'}"


@needs_fpp
@pytest.mark.parametrize("changes, message", [
    ({"links": [{"name": "hub", "a": "cdh.noSuchInstance", "b": "payload.hubDriver"}]},
     "element 'cdh': link 'hub' ends at instance 'noSuchInstance', which is not in topology"),
    ({"links": [{"name": "hub", "a": "cdh.comDriver", "b": "payload.hubDriver"}]}, "lists under 'keep'"),
    ({"links": [{"name": "hub", "a": "cdh.linuxTimer", "b": "payload.hubDriver"}]}, "not a byte stream driver"),
    ({"links": [{"name": "hub", "a": "cdh.hub", "b": "payload.hubDriver"}]}, "cannot replace"),
    # The top of this window is where Payload's flight instances already are
    ({"payload": {"ids": [0x20000000, 0x20021FFF]}}, "no room for the WSTS instances"),
    ({"payload": {"ids": [0x20000000, 0x20000FFF]}}, "outside the element's 'ids' window"),
    ({"cdh": {"bridge": "tcp:50101"}}, "both use bridge tcp:50101"),
])
def test_system_generation_errors(tmp_path, changes, message):
    with pytest.raises(WstsError, match=message):
        generate_system(hub_system(tmp_path, **changes), tmp_path / "out")


def standalone(tmp_path, name, project, deployment, **changes):
    """A deployment manifest kept outside its flight project, with its models removed"""
    data = yaml.safe_load((project / deployment / "wsts.yaml").read_text())
    data.pop("models", None)
    data.update(project=str(project), **changes)
    directory = tmp_path / name
    directory.mkdir()
    (directory / "wsts.yaml").write_text(yaml.safe_dump(data))
    return f"{name}/wsts.yaml"


@needs_fpp
def test_uart_link_between_projects_and_platforms(tmp_path):
    """A serial link between a Linux deployment and a VxWorks one, from different projects.
    Their UART drivers are replaced from the catalog; the link takes the model's place."""
    data = {
        "system": "Serial",
        "elements": {
            "linux": standalone(tmp_path, "linux", LINUX, "LinuxDemo"),
            "vx": standalone(tmp_path, "vx", VX, "VxDemo"),
        },
        "links": [{"name": "crosslink", "a": "vx.sensorUart", "b": "linux.sensorUart"}],
    }
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    result = generate_system(load_system(tmp_path / "wsts-system.yaml"))
    assert result.output == tmp_path / "wsts" / "Serial"
    for name, role in (("linux", "connect"), ("vx", "listen")):
        report = result.elements[name].report
        uart = next(sub for sub in report["substitutions"] if sub["endpoint"] == "sensorUart")
        assert uart["chosen_by"] == "catalog"
        assert report["links"][0]["role"] == role
    # Without 'ids' windows each wstsControl sits above its own element's flight IDs
    bases = [element["control_base_id"] for element in result.description["elements"]]
    assert len(set(bases)) == 2
    bridges = [element["bridge"] for element in result.description["elements"]]
    assert len(set(bridges)) == 2 and "none" not in bridges


@needs_fpp
def test_link_and_model_cannot_share_an_endpoint(tmp_path):
    data = {
        "system": "Serial",
        # LinuxDemo's own manifest attaches the temperature sensor model to sensorUart
        "elements": {"linux": str(LINUX / "LinuxDemo" / "wsts.yaml"), "vx": standalone(tmp_path, "vx", VX, "VxDemo")},
        "links": [{"name": "crosslink", "a": "vx.sensorUart", "b": "linux.sensorUart"}],
    }
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(WstsError, match="has both the model 'tempSensor' and the link 'crosslink'"):
        generate_system(load_system(tmp_path / "wsts-system.yaml"))


@needs_fpp
def test_elements_must_not_share_wsts_ids(tmp_path):
    """Two elements built from deployments with the same IDs: the merged dictionary could
    not tell their wstsControl instances apart"""
    data = {
        "system": "Twins",
        "elements": {
            "one": standalone(tmp_path, "one", LINUX, "LinuxDemo"),
            "two": standalone(tmp_path, "two", LINUX, "LinuxDemo", bridge="tcp:50107"),
        },
    }
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(WstsError, match="wstsControl instances of elements 'one' and 'two' share IDs"):
        generate_system(load_system(tmp_path / "wsts-system.yaml"))


# ----------------------------------------------------------------------
# System client
# ----------------------------------------------------------------------


@pytest.fixture
def system():
    sims = {"cdh": FakeSim(), "payload": FakeSim()}
    description = {
        "system": "Demo",
        "ground": "cdh",
        "elements": [{"name": name, "bridge": f"tcp:{sim.port}"} for name, sim in sims.items()]
        + [{"name": "dark", "bridge": "none"}],
        "links": [{"name": "hub", "a": {"element": "cdh", "endpoint": "hubDriver"},
                   "b": {"element": "payload", "endpoint": "payloadSide"}}],
    }
    with WstsSystem(description, request_timeout=2) as client:
        yield client, sims


def last(sim):
    return {key: value for key, value in sim.requests[-1].items() if key != "id"}


def test_system_client_reaches_every_element(system):
    client, sims = system
    assert list(client) == ["cdh", "payload"], "an element without a bridge is left out"
    assert set(client.status()) == {"cdh", "payload"}
    client.pause()
    assert last(sims["cdh"]) == {"op": "pause"} and last(sims["payload"]) == {"op": "pause"}
    client.set_rate(2.0)
    assert last(sims["payload"]) == {"op": "set_rate", "rate": 2.0}
    client["payload"].inject_text("uart", "hi")
    assert last(sims["payload"])["op"] == "inject_bytes" and last(sims["cdh"])["op"] == "set_rate"
    with pytest.raises(BridgeError, match="no element 'dark'"):
        client["dark"]


def test_system_client_addresses_faults_by_link_or_element(system):
    client, sims = system
    # A link is faulted at its 'a' end
    client.fault_set("hub", "drop", direction="b_to_a", count=2)
    assert last(sims["cdh"]) == {"op": "fault_set", "endpoint": "hubDriver", "kind": "drop", "arg": 0, "count": 2,
                                 "ticks": 0, "dir": "to_flight"}
    client.fault_set("hub", "corrupt", arg=1, direction="a_to_b")
    assert last(sims["cdh"])["dir"] == "to_device"
    client.fault_set("payload.payloadSide", "delay", arg=5, direction="to_device")
    assert last(sims["payload"]) == {"op": "fault_set", "endpoint": "payloadSide", "kind": "delay", "arg": 5,
                                     "count": 0, "ticks": 0, "dir": "to_device"}
    client.fault_clear("hub")
    assert last(sims["cdh"]) == {"op": "fault_clear", "endpoint": "hubDriver"}
    client.fault_clear()
    assert last(sims["cdh"]) == last(sims["payload"]) == {"op": "fault_clear", "endpoint": "*"}
    with pytest.raises(BridgeError, match="direction for a link"):
        client.fault_set("hub", "drop", direction="to_flight")
    with pytest.raises(BridgeError, match="neither a link"):
        client.fault_set("nowhere", "drop")
