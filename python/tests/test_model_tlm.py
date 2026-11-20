"""Device models in the manifest: `address: any`, signals checked when the deployment is
generated, and a telemetry channel per register of a stock register model (model_tlm.py)."""

import pytest

from fprime_wsts import model_tlm
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import I2C_ANY_ADDRESS, ModelSpec, parse_manifest

from mission import MISSION, element_data, element_manifest, needs_fpp


def model(**entry):
    return parse_manifest({"deployment": "D", "models": [{"name": "m", "endpoint": "e", **entry}]}).models[0]


def test_address_any_and_the_telemetry_switch():
    spec = model(**{"class": "Wsts::RegisterMapI2c", "kind": "i2c", "address": "any"})
    assert spec.address == I2C_ANY_ADDRESS and spec.telemetry
    assert not model(**{"class": "C", "kind": "spi", "telemetry": False}).telemetry
    with pytest.raises(WstsError, match="address 'any' is for I2C models only"):
        model(**{"class": "C", "kind": "spi", "address": "any"})
    with pytest.raises(WstsError, match="telemetry must be true or false"):
        model(**{"class": "C", "kind": "spi", "telemetry": "yes"})
    with pytest.raises(WstsError, match="unknown key"):
        model(**{"class": "C", "kind": "spi", "adress": 3})


def test_signals_of_stock_models_are_checked_when_the_manifest_is_read():
    i2c = {"class": "Wsts::RegisterMapI2c", "kind": "i2c", "address": 0x40}
    assert model(**i2c, params={"0x02": "sine(1400, 50, 20) as u16be", "0x04": "0x01 0xF4"})
    with pytest.raises(WstsError, match=r"parameter '0x02': unknown function 'sin'"):
        model(**i2c, params={"0x02": "sin(1)"})
    with pytest.raises(WstsError, match="needs its byte order"):
        model(**i2c, params={"0x02": "t as u16"})
    block = {"class": "Wsts::RegisterFile", "kind": "register"}
    assert model(**block, params={"reg.0x10": "sawtooth(0, 1000, 10) as s16", "mask.0x10": "0", "strict": "0"})
    with pytest.raises(WstsError, match="has no byte order"):
        model(**block, params={"reg.0x10": "t as u16le"})
    # A project's own model reads its parameters itself
    assert model(**{"class": "Sim::Thing", "kind": "spi"}, params={"value": "anything(at all"})


def test_a_channel_per_register_with_its_width_and_sign():
    found = model_tlm.channels([
        ModelSpec("power", "Wsts::RegisterMapI2c", "h", "i2cBus", "i2c", 0x40,
                  {"0x02": "0x6D 0x60", "0x10": "1 2 3 4 5", "0x20": "sine(0, 100, 5) as s24le", "not": "1"}),
        ModelSpec("fpga", "Wsts::RegisterFile", "h", "fpgaRegs", "register", 0,
                  {"reg.0x10": "5", "reg.0x8": "t as s16", "mask.0x10": "1", "irq.period": "20"}),
        ModelSpec("quiet", "Wsts::RegisterFile", "h", "r", "register", 0, {"reg.0x0": "1"}, telemetry=False),
        ModelSpec("own", "Sim::Imu", "h", "i2cBus", "i2c", 0x68, {"0x75": "0x68"}),
    ])
    assert [(c.name, c.type, c.bits) for c in found] == [
        ("power_0x02", "U16", 16),
        *[(f"power_0x{r:02x}", "U8", 8) for r in range(0x10, 0x15)],  # longer than a word: a register each
        ("power_0x20", "I32", 24),
        ("fpga_0x0008", "I16", 16), ("fpga_0x0010", "U32", 32)]
    reads = {c.name: c.read.format(var="model_x") for c in found}
    assert reads["power_0x02"] == ("(std::uint64_t{model_x.peekRegister(0x02)} << 8) | "
                                   "std::uint64_t{model_x.peekRegister(0x03)}")
    assert reads["power_0x20"].startswith("std::uint64_t{model_x.peekRegister(0x20)} | (")
    assert reads["fpga_0x0010"] == "std::uint64_t{model_x.peek(0x10)}"
    header = model_tlm.render_header(found)
    assert "this->tlmWrite_power_0x20(static_cast<I32>(static_cast<std::int64_t>(m_read[6]() << 40) >> 40));" in header
    assert "telemetry fpga_0x0008: I16" in model_tlm.render_fpp(found)
    assert model_tlm.check(found) is None
    twice = model_tlm.channels([ModelSpec("a-b", "Wsts::RegisterFile", "h", "r", "register", 0, {"reg.0": "1"}),
                                ModelSpec("a_b", "Wsts::RegisterFile", "h", "s", "register", 0, {"reg.0": "1"})])
    assert "the same name (a_b_0x0000)" in model_tlm.check(twice)


@needs_fpp
def test_generated_deployment_publishes_the_registers(tmp_path):
    models = element_data("payload")["models"] + [
        {"name": "power", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c", "address": "any",
         "params": {"0x02": "sine(offset=1400, amplitude=50, period=20) as u16be"}}]
    result = generate(MISSION, element_manifest("payload", models=models), tmp_path / "out")
    top = tmp_path / "out" / "Payload" / "Top"
    instances = (top / "wsts_instances.fpp").read_text()
    assert f"instance {model_tlm.INSTANCE}: WstsAdapted.ModelTelemetry base id" in instances
    assert f"instance {model_tlm.INSTANCE}\n" in (top / "topology.fpp").read_text()
    setup = (top / "WstsSetup.cpp").read_text()
    assert 'Wsts::Bus::get().attachI2c("i2cBus", Wsts::I2C_ANY_ADDRESS, &model_power);' in setup
    assert "::Payload::wstsModelTlm.wstsSource(6, [] { return (std::uint64_t{model_power.peekRegister(0x02)} << 8)" in setup
    assert "Wsts::Control::get().addTelemetrySource([] { ::Payload::wstsModelTlm.wstsPublish(); });" in setup
    assert 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/WstsAdapted/ModelTelemetry")' in (
        tmp_path / "out" / "CMakeLists.txt").read_text()
    power = next(m for m in result.report["models"] if m["name"] == "power")
    assert power["address"] == "any" and power["channels"] == [{"channel": "power_0x02", "type": "U16", "register": 2}]
    added = result.report["dictionary"]["added"]["channels"]
    assert "Payload.wstsModelTlm.power_0x02" in added and "Payload.wstsModelTlm.fpga_0x0000" in added

    # Without the control component, no register telemetry, and a warning that says so
    result = generate(MISSION, element_manifest("payload", control=False), tmp_path / "bare")
    assert any("have no telemetry" in w for w in result.report["warnings"])
    assert not (tmp_path / "bare" / "WstsAdapted" / "ModelTelemetry").exists()
