"""`fprime-wsts new-model`: models that build and answer from the first run, named in the
manifest as text (its comments kept), and `init --models`."""

import json
import shutil
import subprocess

import pytest
import yaml

from fprime_wsts.cli import main
from fprime_wsts.fpp import WstsError
from fprime_wsts.manifest import I2C_ANY_ADDRESS, TOOLKIT_ROOT, load_manifest
from fprime_wsts.newmodel import KINDS, add_to_list, new_model

from mission import MISSION

MANIFEST = """\
# a manifest with comments
project: ../flight
deployment: Ref   # the flight deployment

bridge: tcp:50100
"""


@pytest.fixture
def project(tmp_path):
    sim = tmp_path / "sim"
    sim.mkdir()
    (sim / "wsts.yaml").write_text(MANIFEST)
    return sim


def test_a_cpp_model_is_written_and_named_in_the_manifest(project, capsys):
    assert main(["new-model", "Thermometer", "--kind", "i2c", "--endpoint", "i2cBus", "--address", "0x48",
                 "--project", str(project)]) == 0
    printed = capsys.readouterr().out
    assert "named the model in" in printed and "model_set model=thermometer key=value" in printed
    assert {p.name for p in (project / "Models").iterdir()} == {"Thermometer.hpp", "Thermometer.cpp", "CMakeLists.txt"}
    assert 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Models")' in (project / "library.cmake").read_text()
    text = (project / "wsts.yaml").read_text()
    assert text.startswith(MANIFEST.rstrip("\n")), "the manifest is added to, comments and all"
    manifest = load_manifest(project / "wsts.yaml")
    (model,) = manifest.models
    assert (model.name, model.cls, model.header, model.endpoint, model.kind, model.address) == (
        "thermometer", "Sim::Thermometer", "Models/Thermometer.hpp", "i2cBus", "i2c", 0x48)
    assert manifest.extra_depends == ["Models"]

    # A second model joins the first, in the manifest and in the module, in order
    assert main(["new-model", "Heater", "--kind", "gpio", "--endpoint", "heaterPin", "--project", str(project)]) == 0
    manifest = load_manifest(project / "wsts.yaml")
    assert [m.name for m in manifest.models] == ["thermometer", "heater"] and manifest.extra_depends == ["Models"]
    cmake = (project / "Models" / "CMakeLists.txt").read_text()
    assert cmake.index("Thermometer.cpp") < cmake.index("Heater.cpp") < cmake.index("HEADERS")
    assert cmake.index("Thermometer.hpp") < cmake.index("Heater.hpp") < cmake.index("DEPENDS")

    # Nothing is written over
    with pytest.raises(WstsError, match="exists already"):
        new_model(project, "Heater", "gpio", "heaterPin")


def test_a_python_model_is_named_in_run_models(project):
    written = new_model(project, "Probe", "i2c", "i2cBus", "0x48", python=True)
    path = project / "pymodels" / "probe.py"
    assert written.files == [str(path)]
    text = path.read_text()
    assert 'ENDPOINT = "i2cBus"' in text and "ADDRESS = 0x48" in text and "def serve(sim):" in text
    compile(text, str(path), "exec")
    assert load_manifest(project / "wsts.yaml").run_models == ["pymodels/probe.py"]


def test_without_an_endpoint_or_a_manifest_what_to_add_is_said(tmp_path):
    written = new_model(tmp_path, "Gauge", "spi")
    assert any("the project has not exactly one" in todo and "endpoint: TODO" in todo for todo in written.todo)
    (tmp_path / "wsts.yaml").write_text(MANIFEST)
    written = new_model(tmp_path, "Meter", "spi")
    assert any("once its endpoint is known" in todo for todo in written.todo)
    assert "models" not in yaml.safe_load((tmp_path / "wsts.yaml").read_text())


def test_manifest_lists_in_other_forms(tmp_path):
    flow = "deployment: D\nmodels: []\ncmake:\n  stub_variants: [A]   # kept\n  depends: [Other]\n"
    text = add_to_list(flow, ["models"], "  - {name: m}", "")
    text = add_to_list(text, ["cmake", "depends"], "", "Models")
    data = yaml.safe_load(text)
    assert data["models"] == [{"name": "m"}] and data["cmake"] == {"stub_variants": ["A"], "depends": ["Other", "Models"]}
    assert "# kept" in text
    block = "deployment: D\ncmake:\n    cache:\n        X: [1]\nrun:\n  models:\n    - a.py\n"
    data = yaml.safe_load(add_to_list(add_to_list(block, ["cmake", "depends"], "", "Models"), ["run", "models"], "", "b.py"))
    assert data["cmake"] == {"depends": ["Models"], "cache": {"X": [1]}} and data["run"]["models"] == ["b.py", "a.py"]
    assert add_to_list("models: {a: 1}\n", ["models"], "  - {}", "") is None, "a form it does not edit is left alone"
    assert add_to_list("cmake: {depends: [A]}\n", ["cmake", "depends"], "", "B") is None


def test_a_model_name_is_used_once(project):
    new_model(project, "Gauge", "spi", "spiBus")
    (project / "Models" / "Gauge.cpp").unlink()
    (project / "Models" / "Gauge.hpp").unlink()
    with pytest.raises(WstsError, match="already has a model named 'gauge'"):
        new_model(project, "Gauge", "spi", "spiBus")


def test_bad_arguments(project):
    with pytest.raises(WstsError, match="not a C\\+\\+ class name"):
        new_model(project, "2fast", "spi")
    with pytest.raises(WstsError, match="for I2C and packet models only"):
        new_model(project, "Pin", "gpio", address="0x10")
    with pytest.raises(WstsError, match="7-bit"):
        new_model(project, "Far", "i2c", address="0x99")


HARNESS = r"""
#include <cstdio>
#include <cstdlib>
#include <Models/DevI2c.hpp>
#include <Models/DevSpi.hpp>
#include <Models/DevStream.hpp>
#include <Models/DevPin.hpp>
#include <Models/DevBlock.hpp>
#include <Models/DevNode.hpp>

#define CHECK(c) do { if (!(c)) { std::printf("FAILED line %d: %s\n", __LINE__, #c); std::exit(1); } } while (0)

struct Sink : Wsts::ByteSink {
    Wsts::Bytes got;
    void send(const Wsts::Bytes& data) override { got.insert(got.end(), data.begin(), data.end()); }
};

struct PacketSink : Wsts::PacketSink {
    Wsts::Bytes got;
    void send(const Wsts::Bytes& packet) override { got = packet; }
};

int main() {
    Sim::DevI2c i2c;
    CHECK(i2c.set("value", "12.34"));
    CHECK(!i2c.set("value", "foo(1)"));
    Wsts::Bytes read(2, 0);
    CHECK(i2c.transact(0x48, Wsts::Bytes{0x00}, read) == 0);
    CHECK(read == (Wsts::Bytes{0x04, 0xD2}));  // 1234
    CHECK(i2c.set("value", "step(before=0, after=-1, at=1)"));
    i2c.onTick(0);  // the time of a tick that has not come: still 0
    CHECK(i2c.transact(0x48, Wsts::Bytes{0x00}, read) == 0 && read == (Wsts::Bytes{0x00, 0x00}));

    Sim::DevSpi spi;
    CHECK(spi.set("value", "5"));
    Wsts::Bytes miso(3, 0);
    CHECK(spi.transfer(Wsts::Bytes{0x80, 0, 0}, miso) == 0);
    CHECK(miso == (Wsts::Bytes{0, 0x01, 0xF4}));  // 500
    CHECK(spi.transfer(Wsts::Bytes{0x10, 0xAB}, miso) == 0);
    CHECK(spi.transfer(Wsts::Bytes{0x90, 0}, miso) == 0 && miso[1] == 0xAB);

    Sim::DevStream stream;
    CHECK(stream.set("value", "5"));
    Sink sink;
    const std::string question = "READ?\r\n*IDN?\nwhat\n";
    stream.onBytes(Wsts::Bytes(question.begin(), question.end()), sink);
    CHECK(std::string(sink.got.begin(), sink.got.end()) == "5.000\r\nDevStream\r\nERR\r\n");

    Sim::DevPin pin;
    CHECK(pin.set("ready", "readyPin") && pin.set("delay", "2"));
    bool level = true;
    pin.onWrite(true);
    pin.onTick(1);
    CHECK(Wsts::Bus::get().gpioRead("readyPin", level) == 0 && !level);
    pin.onTick(2);
    CHECK(Wsts::Bus::get().gpioRead("readyPin", level) == 0 && level);
    pin.onWrite(false);
    CHECK(Wsts::Bus::get().gpioRead("readyPin", level) == 0 && !level);

    Sim::DevBlock block;
    CHECK(block.set("value", "1.5") && block.set("busy", "2") && block.set("strict", "1"));
    std::uint64_t value = 0;
    CHECK(block.read(Sim::DevBlock::REG_ID, 4, value) == 0);
    CHECK(block.write(Sim::DevBlock::REG_CONTROL, 4, Sim::DevBlock::START) == 0);
    CHECK(block.peek(Sim::DevBlock::REG_STATUS) == Sim::DevBlock::BUSY);
    block.onTick(1);
    block.onTick(2);
    CHECK(block.peek(Sim::DevBlock::REG_DATA) == 1500);
    CHECK(block.peek(Sim::DevBlock::REG_STATUS) == Sim::DevBlock::DONE);
    CHECK(block.write(Sim::DevBlock::REG_STATUS, 4, Sim::DevBlock::DONE) == 0 && block.peek(Sim::DevBlock::REG_STATUS) == 0);
    // An RMAP read of the measurement: 4 bytes at ADDR_VALUE, no reply address
    Sim::DevNode node;
    CHECK(node.set("value", "12.5"));
    node.onTick(1);
    Wsts::Bytes command{0xFE, 0x01, 0x4C, 0x00, 0x67, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00,
                        static_cast<std::uint8_t>(Sim::DevNode::ADDR_VALUE), 0x00, 0x00, 0x04};
    command.push_back(Wsts::rmapCrc(command));
    PacketSink packets;
    node.onPacket(command, packets);
    CHECK(packets.got.size() == 17u && packets.got[3] == 0);
    CHECK((Wsts::Bytes(packets.got.begin() + 12, packets.got.begin() + 16)) == (Wsts::Bytes{0x00, 0x00, 0x30, 0xD4}));  // 12500
    std::printf("models ok\n");
    return 0;
}
"""


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_every_cpp_model_builds_and_answers(tmp_path):
    names = {"i2c": "DevI2c", "spi": "DevSpi", "bytestream": "DevStream", "gpio": "DevPin", "register": "DevBlock",
             "packet": "DevNode"}
    assert set(names) == set(KINDS)
    for kind, name in names.items():
        new_model(tmp_path, name, kind, f"{kind}Endpoint", edit=False)
    (tmp_path / "harness.cpp").write_text(HARNESS)
    core = sorted(str(p) for p in (TOOLKIT_ROOT / "Wsts" / "Core").glob("*.cpp"))
    models = sorted(str(p) for p in (tmp_path / "Models").glob("*.cpp"))
    binary = tmp_path / "harness"
    build = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(TOOLKIT_ROOT), "-I",
                            str(tmp_path), str(tmp_path / "harness.cpp"), *models, *core, "-lpthread", "-o", str(binary)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-4000:]
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "models ok" in run.stdout, run.stdout + run.stderr


def test_init_with_models_answers_every_endpoint(tmp_path, capsys):
    sim = tmp_path / "mission-sim"
    assert main(["init", str(MISSION), "--output", str(sim), "--no-trial", "--no-submodule", "--models", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert str(sim / "library.cmake") in report["starters_written"]
    assert sorted(p.name for p in (sim / "Models").glob("*.hpp")) == [
        "FpgaRegsModel.hpp", "HeaterPinModel.hpp", "I2cBusModel.hpp", "SensorUartModel.hpp", "SpiBusModel.hpp",
        "SwitchPinModel.hpp", "UartModel.hpp"]
    payload = load_manifest(sim / "payload.yaml")
    assert {(m.endpoint, m.kind) for m in payload.models} == {
        ("i2cBus", "i2c"), ("sensorUart", "bytestream"), ("heaterPin", "gpio"), ("switchPin", "gpio"),
        ("spiBus", "spi"), ("fpgaRegs", "register")}
    assert next(m for m in payload.models if m.kind == "i2c").address == I2C_ANY_ADDRESS
    assert payload.extra_depends == ["Models"]
    cdh = load_manifest(sim / "cdh.yaml")
    assert [(m.name, m.endpoint) for m in cdh.models] == [("uartModel", "uart")], "a hub link end has no model"
    # Run again, the manifests are the user's: no model is written that they would not name
    shutil.rmtree(sim / "Models")
    assert main(["init", str(MISSION), "--output", str(sim), "--no-trial", "--models"]) == 0
    assert not (sim / "Models").exists()
    # Without --models, the endpoints are a comment to decide on
    plain = tmp_path / "plain"
    assert main(["init", str(MISSION), "--output", str(plain), "--no-trial", "--no-submodule"]) == 0
    assert not load_manifest(plain / "payload.yaml").models and not (plain / "Models").exists()
