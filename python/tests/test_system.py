"""Systems of several elements: the system manifest, link planning, ID windows and the
system client. The generator tests need the FPP tools (fprime-fpp) on PATH."""

import json

import pytest
import yaml

from fprime_wsts.bridge import BridgeError, WstsSystem
from fprime_wsts.fpp import WstsError
from fprime_wsts.system import generate_system, is_system_manifest, load_system, parse_system
from mission import CDH_MAIN, MISSION, SIM, SYSTEM, copy_flight, needs_fpp
from test_bridge import FakeSim

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


def test_datagram_link_kind_parses_and_keeps_its_ends(tmp_path):
    """A datagram link has the same a/b ends as a bytestream one: only the transport differs"""
    data = changed(links=[{"name": "telem", "kind": "datagram", "a": "cdh.tlmDriver",
                           "b": "payload.tlmDriver"}])
    system = parse_system(data, tmp_path / "wsts-system.yaml")
    (link,) = system.links
    assert (link.name, link.kind) == ("telem", "datagram")
    assert (link.a, link.b) == (("cdh", "tlmDriver"), ("payload", "tlmDriver"))
    # The kind is case-insensitive, like the others
    assert parse_system(changed(links=[{"name": "telem", "kind": "DataGram", "a": "cdh.x",
                                        "b": "payload.y"}])).links[0].kind == "datagram"


def test_gpio_link_kind_is_accepted():
    system = parse_system(changed(links=[{"name": "sep", "kind": "gpio", "a": "cdh.x", "b": "payload.y"}]))
    assert system.links[0].kind == "gpio"


def test_an_element_can_be_left_out_of_the_gds_dictionary():
    system = parse_system(changed(elements={"cdh": "c.yaml", "payload": {"manifest": "p.yaml", "gds": False}}))
    assert [(e.name, e.gds) for e in system.elements] == [("cdh", True), ("payload", False)]
    assert all(e.gds for e in parse_system(TWO).elements), "every element is in it by default"


def test_system_manifests_are_told_from_deployment_manifests():
    assert is_system_manifest(SYSTEM)
    assert not is_system_manifest(SIM / "cdh.yaml")
    assert load_system(SYSTEM).name == "Mission"


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
    (changed(elements={"cdh": "c.yaml", "payload": {"manifest": "p.yaml", "gds": "no"}}), "'gds' must be true or false"),
    (changed(elements={"cdh": {"manifest": "c.yaml", "gds": False}, "payload": "p.yaml"}), "ground element"),
    (changed(links=[{"name": "hub", "a": "cdh.hubDriver"}]), "needs 'name', 'a' and 'b'"),
    (changed(links=[{"name": "hub", "a": "cdh.hubDriver", "b": "elsewhere.x"}]), "<element>.<instance>"),
    (changed(links=[{"name": "hub", "a": "cdh.x", "b": "cdh.y"}]), "same element"),
    (changed(links=[{"name": "hub", "kind": "carrier", "a": "cdh.x", "b": "payload.y"}]), "not supported"),
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


def mission_system(tmp_path, **changes):
    """The example's system manifest, changed, in a place of its own"""
    data = yaml.safe_load(SYSTEM.read_text())
    for name, element in data["elements"].items():
        element["manifest"] = str(SIM / element["manifest"])
    for plant in data.get("dynamics", []):
        for kind in ("python", "fmu"):
            if kind in plant:
                plant[kind] = str(SIM / plant[kind])
    for key, value in changes.items():
        if key in data["elements"]:
            data["elements"][key].update(value)
        else:
            data[key] = value
    path = tmp_path / "wsts-system.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_system(path)


@needs_fpp
def test_mission_system(tmp_path):
    result = generate_system(mission_system(tmp_path), tmp_path / "out")
    assert set(result.elements) == {"cdh", "payload"}
    chosen = {
        sub["instance"]: (sub["flight_component"], sub["surrogate"], sub["chosen_by"])
        for element in result.elements.values() for sub in element.report["substitutions"]
    }
    # No catalog entry replaces the socket drivers: the link is what substitutes them
    assert chosen["Cdh.hubDriver"] == ("Drv.TcpServer", "Wsts.SimByteStream", "link")
    assert chosen["Payload.hubDriver"] == ("Drv.TcpClient", "Wsts.SimByteStream", "link")
    assert "Cdh.comDriver" not in chosen, "the ground link is the same component as Payload's hub driver, and is kept"
    # The plant (docs/dynamics.md) goes to the description, its file named from there
    [plant] = result.description["dynamics"]
    assert plant["name"] == "thermal" and plant["kind"] == "python"
    assert (tmp_path / "out" / plant["path"]).resolve() == (SIM / "plants" / "thermal.py").resolve()

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
    # (above the simulators the Cdh element adds, at 0x18000000)
    assert 0x18006000 < elements["cdh"]["control_base_id"] < 0x20000000
    assert 0x20036000 < elements["payload"]["control_base_id"] <= 0x2FFFFFFF
    assert description["links"] == [{
        "name": "hub", "kind": "bytestream",
        "a": {"element": "cdh", "instance": "Cdh.hubDriver", "endpoint": "hubDriver"},
        "b": {"element": "payload", "instance": "Payload.hubDriver", "endpoint": "hubDriver"},
    }]
    report = result.elements["payload"].report
    assert report["system"] == {"name": "Mission", "element": "payload", "ids": [0x20000000, 0x2FFFFFFF]}
    assert report["links"] == [{"link": "hub", "instance": "Payload.hubDriver", "endpoint": "hubDriver",
                                "role": "connect", "peer": "cdh.hubDriver"}]
    assert "## Links" in (tmp_path / "out" / "payload" / "wsts-report.md").read_text()

    # Regenerating over the output is allowed, and --check finds it current
    assert generate_system(mission_system(tmp_path), tmp_path / "out", check=True).changed == []
    (tmp_path / "out" / "wsts-system.json").write_text("{}\n")
    assert generate_system(mission_system(tmp_path), tmp_path / "out", check=True).changed == ["wsts-system.json"]


@needs_fpp
def test_datagram_link_generates_both_ends_with_their_own_socket(tmp_path):
    """A datagram link ends at the same surrogate as a bytestream one, but the generated
    set-up carries a Wsts::LinkDatagram and each end names its own socket file."""
    result = generate_system(mission_system(tmp_path, links=[
        {"name": "hub", "kind": "datagram", "a": "cdh.hubDriver", "b": "payload.Payload.hubDriver"},
    ]), tmp_path / "out")
    cdh = (tmp_path / "out" / "cdh" / "Cdh" / "Top" / "WstsSetup.cpp").read_text()
    payload = (tmp_path / "out" / "payload" / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    for setup in (cdh, payload):
        assert "#include <Wsts/Core/LinkDatagram.hpp>" in setup
        assert "Wsts::LinkDatagram link_hub;" in setup
        assert "Wsts::LinkByteStream" not in setup, "the transport is the only thing that differs"
    # The role still says which socket file each end binds: .a.sock at 'a', .b.sock at 'b'
    assert 'Wsts::configureModel(&link_hub, "role", "listen")' in cdh
    assert 'Wsts::configureModel(&link_hub, "role", "connect")' in payload
    for setup in (cdh, payload):
        assert 'Wsts::Bus::get().attachByteStream("hubDriver", &link_hub);' in setup
        assert setup.index("Wsts::Runtime::start(config);") < setup.index("link_hub.start();")
        assert setup.index("link_hub.stop();") < setup.index("Wsts::Runtime::stop();")

    description = json.loads((tmp_path / "out" / "wsts-system.json").read_text())
    assert description["links"] == [{
        "name": "hub", "kind": "datagram",
        "a": {"element": "cdh", "instance": "Cdh.hubDriver", "endpoint": "hubDriver"},
        "b": {"element": "payload", "instance": "Payload.hubDriver", "endpoint": "hubDriver"},
    }]
    # Both elements still substitute the socket drivers the same way
    chosen = {
        sub["instance"]: (sub["surrogate"], sub["chosen_by"])
        for element in result.elements.values() for sub in element.report["substitutions"]
    }
    assert chosen["Cdh.hubDriver"] == ("Wsts.SimByteStream", "link")
    assert chosen["Payload.hubDriver"] == ("Wsts.SimByteStream", "link")


@needs_fpp
def test_checked_in_example_is_current():
    result = generate_system(load_system(SYSTEM), check=True)
    assert result.changed == [], f"regenerate: fprime-wsts generate {SYSTEM}"


@needs_fpp
@pytest.mark.parametrize("changes, message", [
    ({"links": [{"name": "hub", "a": "cdh.noSuchInstance", "b": "payload.hubDriver"}]},
     "element 'cdh': link 'hub' ends at instance 'noSuchInstance', which is not in topology"),
    ({"links": [{"name": "hub", "a": "cdh.comDriver", "b": "payload.hubDriver"}]}, "lists under 'keep'"),
    ({"links": [{"name": "hub", "a": "cdh.vxTimer", "b": "payload.hubDriver"}]}, "not a byte stream driver"),
    ({"links": [{"name": "hub", "a": "cdh.hub", "b": "payload.hubDriver"}]}, "cannot replace"),
    # The top of this window is where Payload's flight instances already are
    ({"payload": {"ids": [0x20000000, 0x20036FFF]}}, "no room for the WSTS instances"),
    ({"payload": {"ids": [0x20000000, 0x20000FFF]}}, "outside the element's 'ids' window"),
    ({"cdh": {"bridge": "tcp:50101"}}, "both use bridge tcp:50101"),
])
def test_system_generation_errors(tmp_path, changes, message):
    with pytest.raises(WstsError, match=message):
        generate_system(mission_system(tmp_path, **changes), tmp_path / "out")


def standalone(tmp_path, name, deployment, project=MISSION, **data):
    """A manifest for a deployment, in a simulation project of its own with no models"""
    data = {"project": str(project), "deployment": deployment, **data}
    if deployment == "Cdh":
        data = {"keep": ["comDriver"], "main": CDH_MAIN, **data}
    directory = tmp_path / name
    directory.mkdir()
    (directory / "wsts.yaml").write_text(yaml.safe_dump(data))
    return f"{name}/wsts.yaml"


@needs_fpp
def test_uart_link_between_projects_and_platforms(tmp_path):
    """A serial link between a Linux deployment and a VxWorks one, from different projects
    (the VxWorks one from a copy of the flight project). Their UART drivers are replaced
    from the catalog, the VxWorks one inside its subtopology; the link takes the place of
    a model."""
    data = {
        "system": "Serial",
        "elements": {
            "linux": standalone(tmp_path, "linux", "Payload"),
            "vx": standalone(tmp_path, "vx", "Cdh", project=copy_flight(tmp_path)),
        },
        "links": [{"name": "crosslink", "a": "vx.uart", "b": "linux.sensorUart"}],
    }
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    result = generate_system(load_system(tmp_path / "wsts-system.yaml"))
    assert result.output == tmp_path / "wsts" / "Serial"
    for name, endpoint, role in (("linux", "sensorUart", "connect"), ("vx", "uart", "listen")):
        report = result.elements[name].report
        uart = next(sub for sub in report["substitutions"] if sub["endpoint"] == endpoint)
        assert uart["chosen_by"] == "catalog"
        assert report["links"][0]["role"] == role
    assert result.elements["vx"].report["links"][0]["instance"] == "SensorLink.uart"
    # Without 'ids' windows each wstsControl sits above its own element's flight IDs
    bases = [element["control_base_id"] for element in result.description["elements"]]
    assert len(set(bases)) == 2
    bridges = [element["bridge"] for element in result.description["elements"]]
    assert len(set(bridges)) == 2 and "none" not in bridges


def gpio_system(tmp_path, **changes):
    """Two elements of the Payload deployment, joined by a gpio link between two pins"""
    elements = {
        "one": {"manifest": standalone(tmp_path, "one", "Payload"), "ids": [0x20000000, 0x2FFFFFFF]},
        "two": {"manifest": standalone(tmp_path, "two", "Payload", bridge="tcp:50107"),
                "ids": [0x30000000, 0x3FFFFFFF]},
    }
    data = {
        "system": "Sep",
        "elements": elements,
        "links": [{"name": "sep", "kind": "gpio", "a": "one.Payload.heaterPin", "b": "two.Payload.switchPin"}],
    }
    data.update(changes)
    path = tmp_path / "wsts-system.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_system(path)


@needs_fpp
def test_gpio_link_generates_both_ends(tmp_path):
    result = generate_system(gpio_system(tmp_path), tmp_path / "out")
    for element, endpoint, role in (("one", "heaterPin", "listen"), ("two", "switchPin", "connect")):
        gpio = next(sub for sub in result.elements[element].report["substitutions"] if sub["endpoint"] == endpoint)
        assert gpio["surrogate"] == "Wsts.SimGpio"
        assert gpio["flight_component"] == "Drv.LinuxGpioDriver"
        setup = (tmp_path / "out" / element / "Payload" / "Top" / "WstsSetup.cpp").read_text()
        assert "Wsts::LinkGpio link_sep;" in setup
        assert f'Wsts::Bus::get().attachGpio("{endpoint}", &link_sep);' in setup
        assert f'Wsts::configureModel(&link_sep, "role", "{role}")' in setup
        assert setup.index("Wsts::Runtime::start(config);") < setup.index("link_sep.start();")
        assert setup.index("link_sep.stop();") < setup.index("Wsts::Runtime::stop();")


@needs_fpp
def test_gpio_link_description(tmp_path):
    generate_system(gpio_system(tmp_path), tmp_path / "out")
    description = json.loads((tmp_path / "out" / "wsts-system.json").read_text())
    assert description["links"] == [{
        "name": "sep", "kind": "gpio",
        "a": {"element": "one", "instance": "Payload.heaterPin", "endpoint": "heaterPin"},
        "b": {"element": "two", "instance": "Payload.switchPin", "endpoint": "switchPin"},
    }]


def pulse_system(tmp_path, endpoints=("pulseOut",), a="one.pulseOut", **changes):
    """Element `one` declares a gpio endpoint no flight instance has; its pulse goes to a pin of element `two`"""
    elements = {
        "one": {"manifest": standalone(tmp_path, "one", "Payload", gpio_endpoints=list(endpoints)),
                "ids": [0x20000000, 0x2FFFFFFF]},
        "two": {"manifest": standalone(tmp_path, "two", "Payload", bridge="tcp:50107"),
                "ids": [0x30000000, 0x3FFFFFFF]},
    }
    data = {"system": "Pulse", "elements": elements,
            "links": [{"name": "pulse", "kind": "gpio", "a": a, "b": "two.Payload.switchPin"}]}
    data.update(changes)
    path = tmp_path / "wsts-system.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_system(path)


@needs_fpp
def test_gpio_link_may_end_at_a_declared_endpoint_without_an_instance(tmp_path):
    result = generate_system(pulse_system(tmp_path), tmp_path / "out")
    one = result.elements["one"].report
    # Nothing is substituted for the declared end: no surrogate has the endpoint
    assert all(sub["endpoint"] != "pulseOut" for sub in one["substitutions"])
    assert one["links"] == [{"link": "pulse", "instance": None, "endpoint": "pulseOut", "role": "listen",
                             "peer": "two.Payload.switchPin"}]
    setup = (tmp_path / "out" / "one" / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    assert "Wsts::LinkGpio link_pulse;" in setup
    assert 'Wsts::Bus::get().attachGpio("pulseOut", &link_pulse);' in setup
    assert 'Wsts::configureModel(&link_pulse, "endpoint", "pulseOut")' in setup
    assert setup.index("Wsts::Runtime::start(config);") < setup.index("link_pulse.start();")
    # The other end is the pin of a flight driver, as for any gpio link
    two = next(sub for sub in result.elements["two"].report["substitutions"] if sub["endpoint"] == "switchPin")
    assert two["surrogate"] == "Wsts.SimGpio"
    description = json.loads((tmp_path / "out" / "wsts-system.json").read_text())
    assert description["links"][0]["a"] == {"element": "one", "instance": "pulseOut", "endpoint": "pulseOut"}
    assert "endpoint `pulseOut` (declared in `gpio_endpoints`, listens) to `two.Payload.switchPin`" in (
        tmp_path / "out" / "one" / "wsts-report.md").read_text()


@needs_fpp
def test_an_undeclared_endpoint_is_not_an_instance_and_the_error_says_what_is_declared(tmp_path):
    system = pulse_system(tmp_path, a="one.noSuchPulse")
    with pytest.raises(WstsError, match=r"ends at instance 'noSuchPulse', which is not in topology .*gpio_endpoints.*pulseOut"):
        generate_system(system, tmp_path / "out")


@needs_fpp
def test_a_declared_endpoint_cannot_also_name_an_instance(tmp_path):
    system = pulse_system(tmp_path, endpoints=("heaterPin",), a="one.heaterPin")
    with pytest.raises(WstsError, match="both a gpio endpoint of the manifest and an instance of the topology"):
        generate_system(system, tmp_path / "out")


@needs_fpp
def test_a_declared_gpio_endpoint_is_for_gpio_links_only(tmp_path):
    system = pulse_system(tmp_path, links=[{"name": "pulse", "kind": "bytestream", "a": "one.pulseOut",
                                            "b": "two.Payload.sensorUart"}])
    with pytest.raises(WstsError, match="ends at instance 'pulseOut', which is not in topology"):
        generate_system(system, tmp_path / "out")


def test_a_declared_gpio_endpoint_is_the_end_of_one_link_only(tmp_path):
    # The system manifest refuses it before anything is generated
    with pytest.raises(WstsError, match=r"one\.pulseOut is an end of both link 'pulse' and link 'again'"):
        pulse_system(tmp_path, links=[
            {"name": "pulse", "kind": "gpio", "a": "one.pulseOut", "b": "two.Payload.switchPin"},
            {"name": "again", "kind": "gpio", "a": "two.Payload.heaterPin", "b": "one.pulseOut"}])


@needs_fpp
def test_a_surrogate_with_ports_of_its_own_shape_is_a_gpio_link_end_when_it_says_so(tmp_path):
    """Wsts.SimByteStream has send and recv, not gpioWrite and gpioRead: `gpio_endpoint` is what lets a gpio link end at it"""
    def element(flag):
        return standalone(tmp_path, f"flag{int(flag)}", "Payload",
                          substitute=[{"instance": "Payload.sensorUart", "with": "Wsts.SimByteStream",
                                       **({"gpio_endpoint": True} if flag else {})}])

    for flag, ok in ((False, False), (True, True)):
        elements = {"one": {"manifest": element(flag), "ids": [0x20000000, 0x2FFFFFFF]},
                    "two": {"manifest": standalone(tmp_path, f"two{int(flag)}", "Payload", bridge="tcp:50107"),
                            "ids": [0x30000000, 0x3FFFFFFF]}}
        path = tmp_path / f"wsts-system{int(flag)}.yaml"
        path.write_text(yaml.safe_dump({"system": "Shape", "elements": elements, "links": [
            {"name": "pulse", "kind": "gpio", "a": "one.Payload.sensorUart", "b": "two.Payload.switchPin"}]}))
        if ok:
            result = generate_system(load_system(path), tmp_path / f"out{int(flag)}")
            assert result.elements["one"].report["links"][0]["instance"] == "Payload.sensorUart"
        else:
            with pytest.raises(WstsError, match=r"is not a GPIO driver.*'gpio_endpoint: true'"):
                generate_system(load_system(path), tmp_path / f"out{int(flag)}")


@needs_fpp
def test_gpio_link_end_without_gpio_ports_is_an_error(tmp_path):
    system = gpio_system(tmp_path, links=[{"name": "sep", "kind": "gpio",
                                           "a": "one.Payload.heaterPin", "b": "two.Payload.sensorUart"}])
    with pytest.raises(WstsError, match="not a GPIO driver"):
        generate_system(system, tmp_path / "out")


@needs_fpp
def test_link_and_model_cannot_share_an_endpoint(tmp_path):
    data = {
        "system": "Serial",
        # Payload's own manifest attaches the temperature sensor model to sensorUart
        "elements": {"linux": str(SIM / "payload.yaml"), "vx": standalone(tmp_path, "vx", "Cdh", bridge="tcp:50107")},
        "links": [{"name": "crosslink", "a": "vx.uart", "b": "linux.sensorUart"}],
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
            "one": standalone(tmp_path, "one", "Payload"),
            "two": standalone(tmp_path, "two", "Payload", bridge="tcp:50107"),
        },
    }
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(WstsError, match="wstsControl instances of elements 'one' and 'two' share IDs"):
        generate_system(load_system(tmp_path / "wsts-system.yaml"))

    # Unless one of them is left out of the merged dictionary
    data["elements"]["two"] = {"manifest": data["elements"]["two"], "gds": False}
    (tmp_path / "wsts-system.yaml").write_text(yaml.safe_dump(data))
    elements = generate_system(load_system(tmp_path / "wsts-system.yaml"), tmp_path / "out").description["elements"]
    assert [element.get("gds", True) for element in elements] == [True, False]
    assert "gds" not in elements[0], "written only when left out, so other descriptions stay as they were"


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
