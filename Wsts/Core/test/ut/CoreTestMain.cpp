// ======================================================================
// \title  CoreTestMain.cpp
// \brief  Unit tests for the WSTS core: JSON, bus, faults, control, scenarios, recording, links,
//         registers
// ======================================================================
#include <gtest/gtest.h>

#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Clock.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Dma.hpp>
#include <Wsts/Core/Link.hpp>
#include <Wsts/Core/Models.hpp>
#include <Wsts/Core/RegisterSurrogate.hpp>

namespace {

using Wsts::Bus;
using Wsts::Bytes;
using Wsts::Control;
using Wsts::JsonObj;

Bytes text(const std::string& s) {
    return Bytes(s.begin(), s.end());
}

bool run(const std::string& line, std::string* error = nullptr) {
    std::string local;
    const bool ok = Control::get().execLine(line, local);
    if (error != nullptr) {
        *error = local;
    }
    return ok;
}

std::string tempFile(const char* name) {
    return std::string("/tmp/wsts-ut-") + std::to_string(::getpid()) + "-" + name;
}

bool eventually(const std::function<bool()>& condition) {
    for (int i = 0; i < 500; i++) {
        if (condition()) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return condition();
}

class CoreTest : public ::testing::Test {
  protected:
    void SetUp() override {
        Control::get().reset();
        Bus::get().bindByteStream("uart", [this](const Bytes& data) { received.push_back(data); });
        Bus::get().bindGpio("pin", [this](bool level) { edges.push_back(level); });
        Bus::get().attachI2c("i2c", 0x68, &imu);
    }
    void TearDown() override { Control::get().reset(); }

    int readRegister(std::uint8_t reg, std::uint8_t& value) {
        Bytes read(1, 0);
        const int status = Bus::get().i2cTransact("i2c", 0x68, Bytes{reg}, read);
        value = read[0];
        return status;
    }

    std::vector<Bytes> received;
    std::vector<bool> edges;
    Wsts::RegisterMapI2c imu;
};

// ----------------------------------------------------------------------
// JSON
// ----------------------------------------------------------------------

TEST(Json, ParsesFlatObjects) {
    JsonObj obj;
    std::string error;
    ASSERT_TRUE(obj.parse(R"({"op": "fault_set", "arg": 0x10, "count": 3, "level": true, "text": "a\nb\"c"})", error));
    EXPECT_EQ(obj.str("op"), "fault_set");
    EXPECT_EQ(obj.num("arg"), 16);
    EXPECT_EQ(obj.num("count"), 3);
    EXPECT_TRUE(obj.flag("level"));
    EXPECT_EQ(obj.str("text"), "a\nb\"c");
    EXPECT_EQ(obj.num("missing", 7), 7);
}

TEST(Json, RoundTripsThroughDump) {
    JsonObj obj;
    obj.set("text", "line\n\"quoted\"").setNum("n", -5).setBool("b", false);
    JsonObj again;
    std::string error;
    ASSERT_TRUE(again.parse(obj.dump(), error)) << error;
    EXPECT_EQ(again.str("text"), "line\n\"quoted\"");
    EXPECT_EQ(again.num("n"), -5);
    EXPECT_FALSE(again.flag("b", true));
}

TEST(Json, RejectsMalformedAndNestedInput) {
    JsonObj obj;
    std::string error;
    EXPECT_FALSE(obj.parse("not json", error));
    EXPECT_FALSE(obj.parse(R"({"a": {"b": 1}})", error));
    EXPECT_FALSE(obj.parse(R"({"a": "unterminated)", error));
}

TEST(Json, HexHelpers) {
    Bytes data;
    ASSERT_TRUE(Wsts::fromHex("0x48 65 6c6C6f", data));
    EXPECT_EQ(data, text("Hello"));
    EXPECT_EQ(Wsts::toHex(data), "48656c6c6f");
    EXPECT_FALSE(Wsts::fromHex("abc", data));
    EXPECT_FALSE(Wsts::fromHex("zz", data));
}

// ----------------------------------------------------------------------
// Bus
// ----------------------------------------------------------------------

TEST_F(CoreTest, ByteStreamReachesDeviceAndBack) {
    Wsts::LoopbackByteStream loopback;
    Bus::get().attachByteStream("uart", &loopback);
    EXPECT_EQ(Bus::get().byteStreamSend("uart", text("ping")), 0);
    ASSERT_EQ(received.size(), 1u);
    EXPECT_EQ(received[0], text("ping"));
}

TEST_F(CoreTest, ByteStreamDeviceSeesBoundFlightSideAndDetachesItself) {
    // What a device adapter (Wsts.BusByteStreamDevice) needs from the bus
    Wsts::LoopbackByteStream adapter;
    Wsts::LoopbackByteStream other;
    EXPECT_TRUE(Bus::get().byteStreamBound("uart"));  // the fixture binds the flight side
    EXPECT_FALSE(Bus::get().byteStreamBound("nobody"));
    Bus::get().attachByteStream("uart", &adapter);
    Bus::get().detachByteStream("uart", &other);  // not the device there: nothing happens
    EXPECT_EQ(Bus::get().byteStreamSend("uart", text("a")), 0);
    EXPECT_EQ(received.size(), 1u);
    Bus::get().detachByteStream("uart", &adapter);
    EXPECT_EQ(Bus::get().byteStreamSend("uart", text("b")), 0);
    EXPECT_EQ(received.size(), 1u);
    // A device's reply is recorded as model traffic and passes the fault table
    Wsts::Fault drop;
    drop.endpoint = "uart";
    drop.kind = Wsts::FaultKind::DROP;
    drop.dir = Wsts::FaultDir::TO_FLIGHT;
    (void)Bus::get().addFault(drop);
    Bus::get().byteStreamInject("uart", text("lost"), "model");
    EXPECT_EQ(received.size(), 1u);
}

TEST_F(CoreTest, I2cRegisterModel) {
    ASSERT_TRUE(imu.set("0x75", "0x68"));
    ASSERT_TRUE(imu.set("0x3B", "1, 2 3"));
    EXPECT_FALSE(imu.set("0x3B", "999"));
    std::uint8_t value = 0;
    EXPECT_EQ(readRegister(0x75, value), 0);
    EXPECT_EQ(value, 0x68);
    Bytes read(3, 0);
    EXPECT_EQ(Bus::get().i2cTransact("i2c", 0x68, Bytes{0x3B}, read), 0);
    EXPECT_EQ(read, (Bytes{1, 2, 3}));
    // Nothing answers at another address
    EXPECT_EQ(Bus::get().i2cTransact("i2c", 0x10, Bytes{0}, read), 1);
}

TEST_F(CoreTest, GpioLoopbackInputAndEdges) {
    bool level = true;
    EXPECT_EQ(Bus::get().gpioRead("pin", level), 0);
    EXPECT_FALSE(level);
    EXPECT_EQ(Bus::get().gpioWrite("pin", true), 0);
    EXPECT_EQ(Bus::get().gpioRead("pin", level), 0);
    EXPECT_TRUE(level) << "an undriven pin reads back what was written";
    EXPECT_TRUE(Bus::get().gpioOutput("pin"));

    Bus::get().gpioSetInput("pin", false);
    Bus::get().gpioSetInput("pin", false);
    Bus::get().gpioSetInput("pin", true);
    EXPECT_EQ(edges, (std::vector<bool>{false, true})) << "one callback per level change";
}

// ----------------------------------------------------------------------
// Faults
// ----------------------------------------------------------------------

TEST_F(CoreTest, ErrorFaultIsCountBounded) {
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "error", "arg": 3, "count": 2})"));
    std::uint8_t value = 0;
    EXPECT_EQ(readRegister(0, value), 3);
    EXPECT_EQ(readRegister(0, value), 3);
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(Bus::get().activeFaults(), 0u);
    EXPECT_EQ(Bus::get().counters().faultHits, 2u);
    EXPECT_EQ(Bus::get().counters().errors, 2u);
}

TEST_F(CoreTest, FaultExpiresAfterTicks) {
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "drop", "ticks": 2})"));
    std::uint8_t value = 0;
    EXPECT_NE(readRegister(0, value), 0);
    Control::get().onTick();
    EXPECT_NE(readRegister(0, value), 0);
    Control::get().onTick();
    EXPECT_EQ(readRegister(0, value), 0);
}

TEST_F(CoreTest, CorruptStuckAndDirection) {
    ASSERT_TRUE(imu.set("0", "0x0F"));
    std::uint8_t value = 0;
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "corrupt", "arg": 0xFF, "dir": "to_flight", "count": 1})"));
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0xF0);
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "stuck", "arg": 0x7F, "dir": "to_flight"})"));
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0x7F);
    ASSERT_TRUE(run(R"({"op": "fault_clear", "endpoint": "i2c"})"));
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0x0F);
}

TEST_F(CoreTest, ByteStreamDropDuplicateAndInject) {
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "uart", "kind": "drop", "dir": "to_flight", "count": 1})"));
    ASSERT_TRUE(run(R"({"op": "inject_bytes", "endpoint": "uart", "text": "lost"})"));
    EXPECT_TRUE(received.empty());
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "uart", "kind": "duplicate", "dir": "to_flight", "count": 1})"));
    ASSERT_TRUE(run(R"({"op": "inject_bytes", "endpoint": "uart", "data": "4142"})"));
    ASSERT_EQ(received.size(), 2u);
    EXPECT_EQ(received[0], text("AB"));
    EXPECT_EQ(received[1], text("AB"));
}

TEST_F(CoreTest, GpioStuckFault) {
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "pin", "kind": "stuck", "arg": 1})"));
    bool level = false;
    EXPECT_EQ(Bus::get().gpioWrite("pin", false), 0);
    EXPECT_EQ(Bus::get().gpioRead("pin", level), 0);
    EXPECT_TRUE(level);
}

TEST_F(CoreTest, FaultEventsAreReportedOncePerFault) {
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "error", "count": 3})"));
    std::uint8_t value = 0;
    for (int i = 0; i < 3; i++) {
        (void)readRegister(0, value);
    }
    std::vector<Wsts::SimEvent::Code> codes;
    Wsts::SimEvent event;
    while (Control::get().popEvent(event)) {
        codes.push_back(event.code);
    }
    EXPECT_EQ(codes, (std::vector<Wsts::SimEvent::Code>{Wsts::SimEvent::FAULT_ARMED, Wsts::SimEvent::FAULT_TRIGGERED,
                                                        Wsts::SimEvent::FAULT_EXPIRED}));
}

// ----------------------------------------------------------------------
// Control
// ----------------------------------------------------------------------

TEST_F(CoreTest, RejectsBadActions) {
    std::string error;
    EXPECT_FALSE(run(R"({"op": "nope"})", &error));
    EXPECT_NE(error.find("unknown op"), std::string::npos);
    EXPECT_FALSE(run(R"({"op": "fault_set", "endpoint": "missing", "kind": "drop"})", &error));
    EXPECT_NE(error.find("unknown endpoint"), std::string::npos);
    EXPECT_FALSE(run(R"({"op": "fault_set", "endpoint": "i2c", "kind": "melt"})", &error));
    EXPECT_FALSE(run(R"({"op": "set_rate", "rate": 0})", &error));
    EXPECT_FALSE(run(R"({"op": "inject_bytes", "endpoint": "uart", "data": "xyz"})", &error));
    EXPECT_FALSE(run(R"({"op": "model_set", "model": "ghost", "key": "a", "value": "b"})", &error));
    EXPECT_FALSE(run("{broken", &error));
}

TEST_F(CoreTest, PauseRateAndModels) {
    Wsts::ScriptedByteStream sensor;
    ASSERT_TRUE(sensor.set("endpoint", "uart"));
    Control::get().addModel("sensor", &sensor);
    ASSERT_TRUE(run(R"({"op": "model_set", "model": "sensor", "key": "text", "value": "hi"})"));
    ASSERT_TRUE(run(R"({"op": "model_set", "model": "sensor", "key": "period", "value": "2"})"));
    for (int i = 0; i < 4; i++) {
        Control::get().onTick();
    }
    EXPECT_EQ(received.size(), 2u);

    ASSERT_TRUE(run(R"({"op": "pause"})"));
    EXPECT_TRUE(Control::get().paused());
    ASSERT_TRUE(run(R"({"op": "resume"})"));
    ASSERT_TRUE(run(R"({"op": "set_rate", "rate": 2.5})"));
    EXPECT_DOUBLE_EQ(Control::get().rate(), 2.5);
    ASSERT_TRUE(run(R"({"op": "i2c_reg_set", "endpoint": "i2c", "addr": 0x68, "reg": 5, "value": 9})"));
    EXPECT_EQ(imu.peekRegister(5), 9);
}

// ----------------------------------------------------------------------
// Scenarios and recording
// ----------------------------------------------------------------------

TEST_F(CoreTest, ScenarioRunsStepsAtTheirTicks) {
    const std::string path = tempFile("scenario.jsonl");
    {
        std::ofstream file(path);
        file << "# comment\n\n"
             << R"({"tick": 1, "op": "inject_bytes", "endpoint": "uart", "text": "one"})" << "\n"
             << R"({"tick": 3, "op": "gpio_force", "endpoint": "pin", "level": true})" << "\n";
    }
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"("})"));
    EXPECT_EQ(Control::get().status().scenarioSteps, 2u);
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    Control::get().onTick();
    EXPECT_EQ(received.size(), 1u);
    Control::get().onTick();
    EXPECT_TRUE(edges.empty());
    Control::get().onTick();
    EXPECT_EQ(edges, (std::vector<bool>{true}));
    EXPECT_EQ(Control::get().status().scenarioState, Wsts::ScenarioState::COMPLETED);
    std::remove(path.c_str());
}

TEST_F(CoreTest, ScenarioLoadReportsBadFiles) {
    std::string error;
    EXPECT_FALSE(run(R"({"op": "scenario_load", "path": "/no/such/file"})", &error));
    EXPECT_FALSE(run(R"({"op": "scenario_start"})", &error));
    const std::string path = tempFile("bad.jsonl");
    {
        std::ofstream file(path);
        file << "{\"tick\": 1}\n";
    }
    EXPECT_FALSE(run(R"({"op": "scenario_load", "path": ")" + path + R"("})", &error));
    EXPECT_NE(error.find(":1:"), std::string::npos) << error;
    std::remove(path.c_str());
}

TEST_F(CoreTest, RecordingReplaysAsStimulusAndAsResponses) {
    const std::string path = tempFile("recording.jsonl");
    ASSERT_TRUE(imu.set("0", "0x11"));
    ASSERT_TRUE(run(R"({"op": "record_start", "path": ")" + path + R"("})"));
    std::uint8_t value = 0;
    Control::get().onTick();
    EXPECT_EQ(readRegister(0, value), 0);
    Control::get().onTick();
    ASSERT_TRUE(run(R"({"op": "inject_bytes", "endpoint": "uart", "text": "stim"})"));
    Control::get().onTick();
    EXPECT_EQ(readRegister(0, value), 0);
    ASSERT_TRUE(run(R"({"op": "record_stop"})"));
    received.clear();

    // Stimulus replay: the injection happens again, at the same relative tick
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"(", "mode": "stimulus"})"));
    EXPECT_EQ(Control::get().status().scenarioSteps, 1u);
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    Control::get().onTick();
    EXPECT_TRUE(received.empty());
    Control::get().onTick();
    ASSERT_EQ(received.size(), 1u);
    EXPECT_EQ(received[0], text("stim"));

    // Response replay: flight software sees the recorded register value, not the live model
    ASSERT_TRUE(imu.set("0", "0x99"));
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"(", "mode": "responses"})"));
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    EXPECT_TRUE(Bus::get().replay());
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0x11);
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0x11);
    EXPECT_NE(readRegister(0, value), 0) << "the recording holds two responses";
    ASSERT_TRUE(run(R"({"op": "scenario_stop"})"));
    EXPECT_FALSE(Bus::get().replay());
    EXPECT_EQ(readRegister(0, value), 0);
    EXPECT_EQ(value, 0x99);
    std::remove(path.c_str());
}

// Two devices on one I2C bus are polled at different rates, so a replay that starts at
// another point of the schedule interleaves them differently: each address is served its
// own responses
TEST_F(CoreTest, ReplayedResponsesAreKeptApartByI2cAddress) {
    Wsts::RegisterMapI2c power;
    Bus::get().attachI2c("i2c", 0x40, &power);
    ASSERT_TRUE(imu.set("0", "0x11"));
    ASSERT_TRUE(power.set("0", "0x22"));
    auto read = [](std::uint32_t addr) {
        Bytes data(1, 0);
        EXPECT_EQ(Bus::get().i2cTransact("i2c", addr, Bytes{0}, data), 0);
        return data[0];
    };
    const std::string path = tempFile("two-devices.jsonl");
    ASSERT_TRUE(run(R"({"op": "record_start", "path": ")" + path + R"("})"));
    read(0x68);
    read(0x68);
    read(0x40);
    ASSERT_TRUE(run(R"({"op": "record_stop"})"));

    ASSERT_TRUE(imu.set("0", "0x99"));
    ASSERT_TRUE(power.set("0", "0x88"));
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"(", "mode": "responses"})"));
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    EXPECT_EQ(read(0x40), 0x22);
    EXPECT_EQ(read(0x68), 0x11);
    EXPECT_EQ(read(0x68), 0x11);
    ASSERT_TRUE(run(R"({"op": "scenario_stop"})"));
    EXPECT_EQ(read(0x40), 0x88);
    std::remove(path.c_str());
}


// ----------------------------------------------------------------------
// Registers
// ----------------------------------------------------------------------

TEST(Json, UnsignedSixtyFourBitValues) {
    JsonObj obj;
    obj.setHex("offset", 0xFFFFFFFF00000010ull);
    EXPECT_EQ(obj.dump(), R"({"offset":"0xffffffff00000010"})");
    JsonObj parsed;
    std::string error;
    ASSERT_TRUE(parsed.parse(R"({"offset": "0xffffffff00000010", "small": 16, "neg": -1})", error)) << error;
    EXPECT_EQ(parsed.unum("offset"), 0xFFFFFFFF00000010ull);
    EXPECT_EQ(parsed.unum("small"), 16u);
    EXPECT_EQ(parsed.unum("neg", 7), 7u);
    EXPECT_EQ(parsed.unum("missing", 3), 3u);
}

class RegisterTest : public ::testing::Test {
  protected:
    void SetUp() override {
        Control::get().reset();
        Wsts::Dma::reset();
        ASSERT_TRUE(block.set("endpoint", "fpga"));
        Bus::get().attachRegister("fpga", &block);
        Bus::get().bindRegister("fpga", [this](std::uint32_t number) {
            std::lock_guard<std::mutex> lock(mutex);
            interrupts.push_back(number);
            interruptThread = std::this_thread::get_id();
        });
    }
    void TearDown() override { Control::get().reset(); }

    std::uint32_t read32(std::uint64_t offset, int* status = nullptr) {
        std::uint64_t value = 0;
        const int result = Bus::get().registerRead("fpga", offset, 4, value);
        if (status != nullptr) {
            *status = result;
        }
        return static_cast<std::uint32_t>(value);
    }
    int write32(std::uint64_t offset, std::uint32_t value) { return Bus::get().registerWrite("fpga", offset, 4, value); }
    std::size_t interruptCount() {
        std::lock_guard<std::mutex> lock(mutex);
        return interrupts.size();
    }

    Wsts::RegisterFile block;
    std::mutex mutex;
    std::vector<std::uint32_t> interrupts;
    std::thread::id interruptThread;
};

TEST_F(RegisterTest, ReadAndWriteThroughTheBus) {
    ASSERT_TRUE(block.set("reg.0x00", "0xC0DE0001"));
    ASSERT_TRUE(block.set("reg.0x10", "5"));
    int status = -1;
    EXPECT_EQ(read32(0x00, &status), 0xC0DE0001u);
    EXPECT_EQ(status, 0);
    EXPECT_EQ(write32(0x10, 0x12345678), 0);
    EXPECT_EQ(read32(0x10), 0x12345678u);
    EXPECT_EQ(block.peek(0x10), 0x12345678u);

    // An undefined offset fails, and flight reads all ones
    EXPECT_EQ(read32(0x40, &status), 0xFFFFFFFFu);
    EXPECT_EQ(status, Wsts::RegisterStatus::NO_DEVICE);
    EXPECT_EQ(write32(0x40, 1), Wsts::RegisterStatus::NO_DEVICE);
    // ... unless the block is plain memory
    ASSERT_TRUE(block.set("strict", "0"));
    EXPECT_EQ(write32(0x40, 7), 0);
    EXPECT_EQ(read32(0x40, &status), 7u);
    EXPECT_EQ(status, 0);

    const Wsts::BusCounters counters = Bus::get().counters();
    EXPECT_EQ(counters.transactions, 7u);
    EXPECT_EQ(counters.bytes, 28u);
    EXPECT_EQ(counters.errors, 2u);
}

TEST_F(RegisterTest, RegisterSemantics) {
    // Write mask: only the low byte is writable
    block.define(0x04, 0xAB00, 0x000000FF);
    EXPECT_EQ(write32(0x04, 0xFFFFFF12), 0);
    EXPECT_EQ(read32(0x04), 0xAB12u);
    // Write 1 to clear
    block.define(0x08, 0x0F, 0xFFFFFFFF, Wsts::RegisterFile::WRITE_1_TO_CLEAR);
    EXPECT_EQ(write32(0x08, 0x05), 0);
    EXPECT_EQ(read32(0x08), 0x0Au);
    // Read clears
    ASSERT_TRUE(block.set("reg.0x0C", "0x30"));
    ASSERT_TRUE(block.set("rc.0x0C", "1"));
    EXPECT_EQ(read32(0x0C), 0x30u);
    EXPECT_EQ(read32(0x0C), 0u);
    // Reset values
    ASSERT_TRUE(block.set("reset", "1"));
    EXPECT_EQ(block.peek(0x04), 0xAB00u);
    EXPECT_EQ(block.peek(0x08), 0x0Fu);
    EXPECT_EQ(block.peek(0x0C), 0x30u);
    // Hooks run after the access, and may use the model
    std::vector<std::pair<std::uint64_t, std::uint32_t>> seen;
    block.onWrite(0x04, [&](std::uint64_t offset, std::uint32_t value) {
        seen.emplace_back(offset, value);
        block.setBits(0x08, 0x100);
    });
    block.onRead(0x08, [&](std::uint64_t offset, std::uint32_t value) { seen.emplace_back(offset, value); });
    EXPECT_EQ(write32(0x04, 0x34), 0);
    EXPECT_EQ(read32(0x08), 0x10Fu);
    ASSERT_EQ(seen.size(), 2u);
    EXPECT_EQ(seen[0], (std::pair<std::uint64_t, std::uint32_t>(0x04, 0xAB34)));
    EXPECT_EQ(seen[1], (std::pair<std::uint64_t, std::uint32_t>(0x08, 0x10F)));
    // Bad parameters
    EXPECT_FALSE(block.set("reg.0x02", "1")) << "offsets are multiples of 4";
    EXPECT_FALSE(block.set("reg.0x10", "0x100000000"));
    EXPECT_FALSE(block.set("bogus.0x10", "1"));
    EXPECT_FALSE(block.set("reg.0x10", "x"));
}

TEST_F(RegisterTest, SixtyFourBitAccessIsTwoRegisters) {
    block.define(0x20, 0);
    block.define(0x24, 0);
    EXPECT_EQ(Bus::get().registerWrite("fpga", 0x20, 8, 0x1122334455667788ull), 0);
    EXPECT_EQ(block.peek(0x20), 0x55667788u);
    EXPECT_EQ(block.peek(0x24), 0x11223344u);
    std::uint64_t value = 0;
    EXPECT_EQ(Bus::get().registerRead("fpga", 0x20, 8, value), 0);
    EXPECT_EQ(value, 0x1122334455667788ull);
    // The high half is not defined here: the access fails as a whole
    block.define(0x30, 1);
    EXPECT_EQ(Bus::get().registerRead("fpga", 0x30, 8, value), Wsts::RegisterStatus::NO_DEVICE);
    EXPECT_EQ(value, ~0ull);
    // Only 4 and 8 bytes exist
    EXPECT_EQ(Bus::get().registerRead("fpga", 0x20, 2, value), Wsts::RegisterStatus::WIDTH_ERR);
    EXPECT_EQ(Bus::get().registerWrite("fpga", 0x20, 1, 0), Wsts::RegisterStatus::WIDTH_ERR);
    EXPECT_EQ(block.peek(0x20), 0x55667788u);
}

//! A device with 32-bit accesses only
class NarrowBlock : public Wsts::RegisterDevice {
  public:
    int read(std::uint64_t, unsigned width, std::uint64_t& value) override {
        value = 0x77;
        return (width == 4) ? 0 : static_cast<int>(Wsts::RegisterStatus::WIDTH_ERR);
    }
    int write(std::uint64_t, unsigned width, std::uint64_t) override {
        return (width == 4) ? 0 : static_cast<int>(Wsts::RegisterStatus::WIDTH_ERR);
    }
};

TEST_F(RegisterTest, AttachingTwiceReplacesAndAnUnattachedEndpointReadsAllOnes) {
    ASSERT_TRUE(block.set("reg.0", "1"));
    NarrowBlock second;
    EXPECT_EQ(Bus::get().attachRegister("fpga", &second), &block);
    EXPECT_EQ(read32(0), 0x77u) << "one block per endpoint: the last one attached answers";
    // The second block leaves and gives the endpoint back; that only works while it is the one there
    Bus::get().replaceRegister("fpga", &second, &block);
    EXPECT_EQ(read32(0), 1u);
    Bus::get().replaceRegister("fpga", &second, nullptr);
    EXPECT_EQ(read32(0), 1u);
    EXPECT_EQ(Bus::get().attachRegister("fpga", &second), &block);
    std::uint64_t wide = 0;
    EXPECT_EQ(Bus::get().registerRead("fpga", 0, 8, wide), Wsts::RegisterStatus::WIDTH_ERR);
    EXPECT_EQ(wide, ~0ull);
    EXPECT_FALSE(Bus::get().registerPoke("fpga", 0, 1)) << "this device has no poke";

    std::uint64_t value = 0;
    EXPECT_EQ(Bus::get().registerRead("nothing", 0x10, 4, value), Wsts::RegisterStatus::NO_DEVICE);
    EXPECT_EQ(value, 0xFFFFFFFFull);
    EXPECT_EQ(Bus::get().registerWrite("nothing", 0x10, 4, 1), Wsts::RegisterStatus::NO_DEVICE);
    Bus::get().detach("fpga");
    EXPECT_EQ(read32(0), 0xFFFFFFFFu);
    // An interrupt nobody is bound to is recorded and goes nowhere
    Bus::get().registerInterrupt("nothing", 1);
    EXPECT_EQ(Bus::get().counters().errors, 4u);
    EXPECT_EQ(Bus::get().counters().transactions, 8u);
}

TEST_F(RegisterTest, FaultsOnReadsAndWrites) {
    ASSERT_TRUE(block.set("reg.0x10", "0x000000F0"));
    int status = 0;

    // ERROR: status to the surrogate, the register is not reached, flight reads all ones
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "error", "arg": 9, "count": 2})"));
    EXPECT_EQ(read32(0x10, &status), 0xFFFFFFFFu);
    EXPECT_EQ(status, 9);
    EXPECT_EQ(write32(0x10, 1), 9);
    EXPECT_EQ(block.peek(0x10), 0xF0u);
    EXPECT_EQ(read32(0x10, &status), 0xF0u) << "the fault was bounded to two accesses";

    // DROP: a read fails; a write is lost and flight cannot tell
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "drop"})"));
    EXPECT_EQ(read32(0x10, &status), 0xFFFFFFFFu);
    EXPECT_EQ(status, Wsts::RegisterStatus::READ_ERR);
    EXPECT_EQ(write32(0x10, 0x55), 0);
    EXPECT_EQ(block.peek(0x10), 0xF0u);
    ASSERT_TRUE(run(R"({"op": "fault_clear", "endpoint": "fpga"})"));

    // STUCK: reads return arg, writes are ignored
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "stuck", "arg": "0xDEADBEEF"})"));
    EXPECT_EQ(read32(0x10, &status), 0xDEADBEEFu);
    EXPECT_EQ(status, 0);
    EXPECT_EQ(write32(0x10, 0x55), 0);
    EXPECT_EQ(block.peek(0x10), 0xF0u);
    ASSERT_TRUE(run(R"({"op": "fault_clear", "endpoint": "fpga"})"));

    // CORRUPT: all 32 bits of arg, in the direction of the fault
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "corrupt", "arg": "0x80000001", "dir": "to_flight"})"));
    EXPECT_EQ(read32(0x10), 0x800000F1u);
    EXPECT_EQ(write32(0x10, 0x0F), 0);
    EXPECT_EQ(block.peek(0x10), 0x0Fu) << "a to_flight fault leaves writes alone";
    ASSERT_TRUE(run(R"({"op": "fault_clear"})"));
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "corrupt", "arg": "0xFF00", "dir": "to_device"})"));
    EXPECT_EQ(write32(0x10, 0x0F), 0);
    EXPECT_EQ(block.peek(0x10), 0xFF0Fu);
    EXPECT_EQ(read32(0x10), 0xFF0Fu) << "a to_device fault leaves reads alone";
    ASSERT_TRUE(run(R"({"op": "fault_clear"})"));

    // DELAY holds the access
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "delay", "arg": 40, "count": 1})"));
    const auto before = std::chrono::steady_clock::now();
    EXPECT_EQ(read32(0x10), 0xFF0Fu);
    EXPECT_GE(std::chrono::steady_clock::now() - before, std::chrono::milliseconds(40));

    const Wsts::BusCounters counters = Bus::get().counters();
    EXPECT_EQ(counters.errors, 3u) << "the two ERROR accesses and the dropped read";
    EXPECT_EQ(counters.faultHits, 9u);
}

TEST_F(RegisterTest, InterruptReachesTheHandlerOnTheThreadThatRaisedIt) {
    ASSERT_TRUE(block.set("irq.number", "3"));
    ASSERT_TRUE(block.set("irq.period", "2"));
    ASSERT_TRUE(block.set("irq.status", "0x08"));
    ASSERT_TRUE(block.set("irq.bits", "0x4"));
    ASSERT_TRUE(block.set("w1c.0x08", "1"));
    Control::get().addModel("fpga", &block);

    std::thread::id tickThread;
    std::thread ticker([&]() {
        tickThread = std::this_thread::get_id();
        for (int i = 0; i < 4; i++) {
            Control::get().onTick();
        }
    });
    ticker.join();
    ASSERT_EQ(interruptCount(), 2u) << "every second tick";
    EXPECT_EQ(interrupts[0], 3u);
    EXPECT_EQ(interruptThread, tickThread);
    EXPECT_NE(interruptThread, std::this_thread::get_id()) << "not the thread that reads the registers";
    // What a flight handler does next, on its own thread: read the status and clear it
    EXPECT_EQ(read32(0x08), 0x4u);
    EXPECT_EQ(write32(0x08, 0x4), 0);
    EXPECT_EQ(read32(0x08), 0u);

    // The action, as a bridge client, a scenario or the IRQ_RAISE command would send it
    ASSERT_TRUE(run(R"({"op": "irq_raise", "endpoint": "fpga", "number": 7})"));
    ASSERT_EQ(interruptCount(), 3u);
    EXPECT_EQ(interrupts[2], 7u);
    std::string error;
    EXPECT_FALSE(run(R"({"op": "irq_raise", "endpoint": "nowhere", "number": 7})", &error));
    EXPECT_NE(error.find("unknown endpoint"), std::string::npos);
}

TEST_F(RegisterTest, InterruptFaultsHaveATargetOfTheirOwn) {
    ASSERT_TRUE(block.set("reg.0x10", "1"));
    // Faults on the endpoint act on register traffic, not on interrupts
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga", "kind": "drop"})"));
    block.raise(1);
    EXPECT_EQ(interruptCount(), 1u);
    ASSERT_TRUE(run(R"({"op": "fault_clear"})"));

    // A lost interrupt, once
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga:irq", "kind": "drop", "count": 1})"));
    block.raise(2);
    EXPECT_EQ(interruptCount(), 1u);
    EXPECT_EQ(read32(0x10), 1u) << "reads are not affected";
    block.raise(2);
    EXPECT_EQ(interruptCount(), 2u);

    // A duplicated one
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "fpga:irq", "kind": "duplicate", "count": 1})"));
    block.raise(5);
    ASSERT_EQ(interruptCount(), 4u);
    EXPECT_EQ(interrupts[2], 5u);
    EXPECT_EQ(interrupts[3], 5u);

    std::string error;
    EXPECT_FALSE(run(R"({"op": "fault_set", "endpoint": "nowhere:irq", "kind": "drop"})", &error));
    EXPECT_NE(error.find("unknown endpoint"), std::string::npos);
}

TEST_F(RegisterTest, RegSetActionPokesTheModel) {
    block.define(0x10, 0, 0x0000000F);
    ASSERT_TRUE(run(R"({"op": "reg_set", "endpoint": "fpga", "offset": "0x10", "value": "0xCAFE"})"));
    EXPECT_EQ(read32(0x10), 0xCAFEu) << "a poke is not subject to the write mask";
    ASSERT_TRUE(run(R"({"op": "reg_set", "endpoint": "fpga", "offset": 20, "value": 3})"));
    EXPECT_EQ(block.peek(20), 3u);
    std::string error;
    EXPECT_FALSE(run(R"({"op": "reg_set", "endpoint": "uart", "offset": 0, "value": 1})", &error));
    EXPECT_NE(error.find("no register block"), std::string::npos);
    EXPECT_FALSE(run(R"({"op": "reg_set", "endpoint": "fpga", "offset": 2, "value": 1})", &error));
    EXPECT_FALSE(run(R"({"op": "reg_set", "endpoint": "fpga", "value": 1})", &error));
    EXPECT_NE(error.find("offset"), std::string::npos);
}

TEST_F(RegisterTest, RecordingHoldsRegisterTrafficAndReplaysIt) {
    const std::string path = tempFile("registers.jsonl");
    ASSERT_TRUE(block.set("reg.0x10", "0x11"));
    ASSERT_TRUE(block.set("reg.0x14", "0x22"));
    ASSERT_TRUE(run(R"({"op": "record_start", "path": ")" + path + R"("})"));
    Control::get().onTick();
    EXPECT_EQ(read32(0x10), 0x11u);
    EXPECT_EQ(read32(0x10), 0x11u);
    EXPECT_EQ(read32(0x14), 0x22u);
    EXPECT_EQ(write32(0x14, 0x33), 0);
    Control::get().onTick();
    block.raise(4);
    Control::get().onTick();
    ASSERT_TRUE(run(R"({"op": "irq_raise", "endpoint": "fpga", "number": 9})"));
    ASSERT_TRUE(run(R"({"op": "record_stop"})"));

    // The lines, as the recorder writes them
    std::ifstream file(path);
    std::vector<JsonObj> lines;
    std::string line;
    while (std::getline(file, line)) {
        JsonObj obj;
        std::string error;
        ASSERT_TRUE(obj.parse(line, error)) << line;
        lines.push_back(obj);
    }
    ASSERT_EQ(lines.size(), 7u);
    EXPECT_EQ(lines[0].dump(),
              R"({"endpoint":"fpga","offset":"0x10","read":"11000000","rec":"reg_rd","src":"flight","status":0,"tick":1,"width":4})");
    EXPECT_EQ(lines[3].dump(),
              R"({"endpoint":"fpga","offset":"0x14","rec":"reg_wr","src":"flight","status":0,"tick":1,"width":4,"write":"33000000"})");
    EXPECT_EQ(lines[4].dump(), R"({"endpoint":"fpga","number":4,"rec":"reg_irq","src":"model","tick":2})");
    EXPECT_EQ(lines[5].str("rec"), "reg_irq");
    EXPECT_EQ(lines[5].str("src"), "inject");
    EXPECT_EQ(lines[6].str("op"), "irq_raise");

    // Stimulus replay: the injected interrupt happens again, the model's own does not
    interrupts.clear();
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"(", "mode": "stimulus"})"));
    EXPECT_EQ(Control::get().status().scenarioSteps, 1u);
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    for (int i = 0; i < 3; i++) {
        Control::get().onTick();
    }
    ASSERT_EQ(interruptCount(), 1u);
    EXPECT_EQ(interrupts[0], 9u);

    // Response replay: recorded reads per offset, whatever the order; the model is bypassed
    interrupts.clear();
    ASSERT_TRUE(block.set("reg.0x10", "0x99"));
    ASSERT_TRUE(block.set("reg.0x14", "0x88"));
    ASSERT_TRUE(run(R"({"op": "scenario_load", "path": ")" + path + R"(", "mode": "responses"})"));
    ASSERT_TRUE(run(R"({"op": "scenario_start"})"));
    int status = 0;
    EXPECT_EQ(read32(0x14, &status), 0x22u);
    EXPECT_EQ(read32(0x10), 0x11u);
    EXPECT_EQ(write32(0x14, 0x44), 0);
    EXPECT_EQ(block.peek(0x14), 0x88u) << "writes do not reach the model during a replay";
    block.raise(1);
    EXPECT_EQ(interruptCount(), 0u) << "the model's interrupts are replaced by the recording";
    EXPECT_EQ(read32(0x10), 0x11u);
    // The recording has two reads of 0x10 and one of 0x14: after that, reads fail
    EXPECT_EQ(read32(0x10, &status), 0xFFFFFFFFu);
    EXPECT_EQ(status, Wsts::RegisterStatus::OTHER_ERR);
    EXPECT_EQ(read32(0x14, &status), 0xFFFFFFFFu);
    EXPECT_EQ(status, Wsts::RegisterStatus::OTHER_ERR);
    for (int i = 0; i < 3; i++) {
        Control::get().onTick();
    }
    ASSERT_EQ(interruptCount(), 2u) << "the model's interrupt from the recording, and the injected one";
    EXPECT_EQ(interrupts[0], 4u);
    EXPECT_EQ(interrupts[1], 9u);
    EXPECT_FALSE(Bus::get().replay()) << "the scenario has completed";
    EXPECT_EQ(read32(0x10), 0x99u);
    std::remove(path.c_str());
}

TEST_F(RegisterTest, SurrogateHelperForwardsAndSerialisesInterrupts) {
    block.define(0x10, 0x10);
    block.define(0x14, 0);
    std::atomic<int> inside{0};
    std::atomic<int> overlaps{0};
    std::atomic<int> delivered{0};
    {
        Wsts::RegisterSurrogate surrogate;
        surrogate.bindRegister("fpga", "", [&](std::uint32_t) {
            if (inside.fetch_add(1) != 0) {
                overlaps++;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            inside--;
            delivered++;
        });
        EXPECT_EQ(surrogate.registerRead32(0x10), 0x10u);
        surrogate.registerWrite64(0x10, 0x0000000200000001ull);
        EXPECT_EQ(surrogate.registerRead64(0x10), 0x0000000200000001ull);
        EXPECT_EQ(surrogate.registerRead32(0x80), 0xFFFFFFFFu) << "a failed read is all ones";
        std::uint64_t value = 0;
        EXPECT_EQ(surrogate.registerRead(0x80, 4, value), Wsts::RegisterStatus::NO_DEVICE);
        EXPECT_EQ(surrogate.registerFailures(), 2u);

        // Two sources raise at once: the flight handler is entered one at a time
        std::thread a([&]() { for (int i = 0; i < 10; i++) { block.raise(1); } });
        std::thread b([&]() { for (int i = 0; i < 10; i++) { Bus::get().registerInterrupt("fpga", 2, "inject"); } });
        a.join();
        b.join();
        EXPECT_EQ(delivered.load(), 20);
        EXPECT_EQ(overlaps.load(), 0);
        EXPECT_EQ(surrogate.registerInterrupts(), 20u);
    }
    // The surrogate is gone: an interrupt is not delivered into it
    block.raise(1);
    EXPECT_EQ(delivered.load(), 20);

    Wsts::RegisterSurrogate quiet;
    quiet.bindRegister("fpga", "irq=off", [&](std::uint32_t) { delivered++; });
    block.raise(1);
    EXPECT_EQ(delivered.load(), 20);
}

TEST(Dma, RegionsTranslateBothWays) {
    Wsts::Dma::reset();
    std::vector<std::uint8_t> receive(256), transmit(64);
    ASSERT_TRUE(Wsts::Dma::addRegion(receive.data(), 0x80000000ull, receive.size()));
    ASSERT_TRUE(Wsts::Dma::addRegion(transmit.data(), 0x80001000ull, transmit.size()));
    EXPECT_FALSE(Wsts::Dma::addRegion(receive.data() + 8, 0x90000000ull, 16)) << "overlaps in host memory";
    EXPECT_FALSE(Wsts::Dma::addRegion(&receive, 0x800000F0ull, 32)) << "overlaps in physical memory";

    std::uint64_t physical = 0;
    ASSERT_TRUE(Wsts::Dma::toPhysical(receive.data() + 16, physical));
    EXPECT_EQ(physical, 0x80000010ull);
    ASSERT_TRUE(Wsts::Dma::toPhysical(transmit.data(), physical));
    EXPECT_EQ(physical, 0x80001000ull) << "two buffers have two addresses";
    EXPECT_FALSE(Wsts::Dma::toPhysical(&physical, physical));

    EXPECT_EQ(Wsts::Dma::toVirtual(0x80000010ull, 8), receive.data() + 16);
    EXPECT_EQ(Wsts::Dma::toVirtual(0x800000F8ull, 8), receive.data() + 248);
    EXPECT_EQ(Wsts::Dma::toVirtual(0x800000F8ull, 9), nullptr) << "runs past the end of the region";
    EXPECT_EQ(Wsts::Dma::toVirtual(0x70000000ull), nullptr);
    EXPECT_EQ(Wsts::Dma::toVirtual(0), nullptr);

    // A project's own functions take over
    Wsts::Dma::setTranslator(
        [](const void*, std::uint64_t& out) { out = 0x1234; return true; },
        [](std::uint64_t address, std::size_t) -> void* { return reinterpret_cast<void*>(address + 1); });
    ASSERT_TRUE(Wsts::Dma::toPhysical(nullptr, physical));
    EXPECT_EQ(physical, 0x1234u);
    EXPECT_EQ(Wsts::Dma::toVirtual(0x10), reinterpret_cast<void*>(0x11));
    Wsts::Dma::setTranslator(nullptr, nullptr);
    Wsts::Dma::removeRegion(receive.data());
    EXPECT_EQ(Wsts::Dma::toVirtual(0x80000010ull, 8), nullptr);
    Wsts::Dma::reset();
}

// The model of docs/surrogate-guide.md, "A register block": a time unit that is both the
// source of the flight cycles (through its interrupt) and the source of time. The flight
// driver on top of it is kept as it is.
class TimeUnitModel : public Wsts::RegisterFile {
  public:
    enum : std::uint64_t { REVISION = 0x00, IRQ_STATUS = 0x08, SECONDS = 0x10, SUBSECONDS = 0x14 };
    enum : std::uint32_t { IRQ_CYCLE = 0x1, IRQ_PPS = 0x2, IRQ_NUMBER = 0 };

    explicit TimeUnitModel(std::uint64_t periodNs) : m_periodNs(periodNs) {
        this->define(REVISION, 0x00010002, 0);                                // read only
        this->define(IRQ_STATUS, 0, 0xFFFFFFFF, WRITE_1_TO_CLEAR);
        this->define(SECONDS, 0, 0);
        this->define(SUBSECONDS, 0, 0);
        // This model is the simulation's tick source: no internal ticker is started
        Control::get().registerTickSource();
        Control::get().setTickPeriod(periodNs);
    }
    ~TimeUnitModel() { this->stop(); }

    //! One flight cycle: simulation time first, then the registers, then the interrupt
    void cycle() {
        Control& control = Control::get();
        control.onTick();
        const std::uint64_t now = control.tick() * m_periodNs;
        this->poke(SECONDS, now / 1000000000ull);
        this->poke(SUBSECONDS, now % 1000000000ull);
        const bool pps = (now / 1000000000ull) != ((now - m_periodNs) / 1000000000ull);
        this->setBits(IRQ_STATUS, IRQ_CYCLE | (pps ? static_cast<std::uint32_t>(IRQ_PPS) : 0u));
        this->raise(IRQ_NUMBER);
    }

    void start() {
        m_quit.store(false);
        m_thread = std::thread([this]() {
            Control& control = Control::get();
            auto next = std::chrono::steady_clock::now();
            while (!m_quit.load()) {
                const double rate = std::max(0.001, control.rate());
                next += std::chrono::nanoseconds(static_cast<std::int64_t>(static_cast<double>(m_periodNs) / rate));
                const auto now = std::chrono::steady_clock::now();
                if (next < now) {
                    next = now;  // fell behind: do not burst to catch up
                }
                std::this_thread::sleep_until(next);
                if (control.paused()) {
                    control.onIdle();
                } else if (!m_quit.load()) {
                    this->cycle();
                }
            }
        });
    }
    void stop() {
        m_quit.store(true);
        if (m_thread.joinable()) {
            m_thread.join();
        }
    }

  private:
    std::uint64_t m_periodNs;
    std::atomic<bool> m_quit{false};
    std::thread m_thread;
};

TEST(RegisterTimeUnit, InterruptCyclesFlightAndRegistersFollowSimulatedTime) {
    Control::get().reset();
    TimeUnitModel unit(250000000);  // 4 Hz
    ASSERT_TRUE(unit.set("endpoint", "timeUnit"));
    Bus::get().attachRegister("timeUnit", &unit);
    EXPECT_TRUE(Control::get().hasTickSource());
    EXPECT_EQ(Control::get().tickPeriodNs(), 250000000u);

    // The flight driver's side: the interrupt handler only notes the interrupt; its own
    // thread then reads the status register, clears it, and reads the time
    Wsts::RegisterSurrogate regif;
    std::atomic<int> pending{0};
    regif.bindRegister("timeUnit", "", [&](std::uint32_t) { pending++; });
    EXPECT_EQ(regif.registerRead32(TimeUnitModel::REVISION), 0x00010002u);

    int cycles = 0;
    int pps = 0;
    for (int i = 0; i < 5; i++) {
        unit.cycle();
        ASSERT_EQ(pending.exchange(0), 1);
        const std::uint32_t status = regif.registerRead32(TimeUnitModel::IRQ_STATUS);
        regif.registerWrite32(TimeUnitModel::IRQ_STATUS, status);
        cycles += (status & TimeUnitModel::IRQ_CYCLE) ? 1 : 0;
        pps += (status & TimeUnitModel::IRQ_PPS) ? 1 : 0;
    }
    EXPECT_EQ(cycles, 5);
    EXPECT_EQ(pps, 1) << "one second boundary in five cycles of 250 ms";
    EXPECT_EQ(Control::get().tick(), 5u);
    EXPECT_EQ(regif.registerRead32(TimeUnitModel::SECONDS), 1u);
    EXPECT_EQ(regif.registerRead32(TimeUnitModel::SUBSECONDS), 250000000u);
    EXPECT_EQ(regif.registerRead32(TimeUnitModel::IRQ_STATUS), 0u);
    regif.registerWrite32(TimeUnitModel::SECONDS, 99);
    EXPECT_EQ(regif.registerRead32(TimeUnitModel::SECONDS), 1u) << "read only";

    // On its own thread, pause stops the interrupts and so the flight cycles
    ASSERT_TRUE(run(R"({"op": "set_rate", "rate": 50})"));
    unit.start();
    ASSERT_TRUE(eventually([&]() { return pending.load() >= 3; }));
    ASSERT_TRUE(run(R"({"op": "pause"})"));
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    const int held = pending.load();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_EQ(pending.load(), held);
    ASSERT_TRUE(run(R"({"op": "resume"})"));
    ASSERT_TRUE(eventually([&]() { return pending.load() > held; }));
    unit.stop();
    regif.unbindRegister();
    Control::get().reset();
}

}  // namespace

// ----------------------------------------------------------------------
// Links between elements
// ----------------------------------------------------------------------

// Both ends live in this one process here, on two endpoints of the same bus. In a system
// each end is in its own deployment.
TEST_F(CoreTest, LinkCarriesBytesBetweenEndpointsBothWays) {
    std::mutex mutex;
    std::vector<Bytes> atA;
    std::vector<Bytes> atB;
    Bus::get().bindByteStream("linkA", [&](const Bytes& data) {
        std::lock_guard<std::mutex> lock(mutex);
        atA.push_back(data);
    });
    Bus::get().bindByteStream("linkB", [&](const Bytes& data) {
        std::lock_guard<std::mutex> lock(mutex);
        atB.push_back(data);
    });
    const std::string path = tempFile("link.sock");
    Wsts::LinkByteStream a;
    Wsts::LinkByteStream b;
    ASSERT_TRUE(a.set("endpoint", "linkA") && a.set("link", "x") && a.set("role", "listen") && a.set("address", path));
    ASSERT_TRUE(b.set("endpoint", "linkB") && b.set("link", "x") && b.set("role", "connect") && b.set("address", path));
    EXPECT_FALSE(a.set("role", "sideways"));
    Bus::get().attachByteStream("linkA", &a);
    Bus::get().attachByteStream("linkB", &b);

    // Nothing at the far end yet: the bytes are lost, as on an unplugged cable
    EXPECT_EQ(Bus::get().byteStreamSend("linkA", text("lost")), 0);
    EXPECT_EQ(a.dropped(), 1u);

    b.start();  // the connecting side may come up first
    a.start();
    ASSERT_TRUE(eventually([&]() { return a.up() && b.up(); }));
    EXPECT_EQ(Wsts::LinkByteStream::started(), 2u);
    EXPECT_EQ(Wsts::LinkByteStream::connected(), 2u);

    EXPECT_EQ(Bus::get().byteStreamSend("linkA", text("to b")), 0);
    EXPECT_EQ(Bus::get().byteStreamSend("linkA", text("again")), 0);
    EXPECT_EQ(Bus::get().byteStreamSend("linkB", text("to a")), 0);
    ASSERT_TRUE(eventually([&]() {
        std::lock_guard<std::mutex> lock(mutex);
        return atB.size() == 2 && atA.size() == 1;
    }));
    {
        // Each send arrives whole and in order
        std::lock_guard<std::mutex> lock(mutex);
        EXPECT_EQ(atB[0], text("to b"));
        EXPECT_EQ(atB[1], text("again"));
        EXPECT_EQ(atA[0], text("to a"));
    }

    // A fault on one endpoint cuts that direction of the link
    ASSERT_TRUE(run(R"({"op": "fault_set", "endpoint": "linkA", "kind": "drop", "dir": "to_device"})"));
    EXPECT_EQ(Bus::get().byteStreamSend("linkA", text("dropped")), 0);
    EXPECT_EQ(Bus::get().byteStreamSend("linkB", text("passes")), 0);
    ASSERT_TRUE(eventually([&]() {
        std::lock_guard<std::mutex> lock(mutex);
        return atA.size() == 2;
    }));
    {
        std::lock_guard<std::mutex> lock(mutex);
        EXPECT_EQ(atB.size(), 2u);
    }

    // One end going away takes the link down; it comes back when that end restarts
    b.stop();
    ASSERT_TRUE(eventually([&]() { return !a.up(); }));
    b.start();
    ASSERT_TRUE(eventually([&]() { return a.up() && b.up(); }));
    a.stop();
    b.stop();
    EXPECT_EQ(Wsts::LinkByteStream::started(), 0u);
    Bus::get().detach("linkA");
    Bus::get().detach("linkB");
}

// While recorded responses are replayed, what the linked element sent comes from the
// recording, like the output of any other model
TEST_F(CoreTest, LinkTrafficIsReplacedByTheRecordingDuringReplay) {
    Bus::get().setReplay(true);
    Bus::get().byteStreamInject("uart", text("live"), "link");
    EXPECT_TRUE(received.empty());
    Bus::get().byteStreamInject("uart", text("recorded"), "replay");
    ASSERT_EQ(received.size(), 1u);
    EXPECT_EQ(received[0], text("recorded"));
    Bus::get().setReplay(false);
    Bus::get().byteStreamInject("uart", text("live"), "link");
    EXPECT_EQ(received.size(), 2u);
}

// The other end of a link is a live element, not a model: while recorded responses are
// replayed, what flight software sends still reaches it
TEST_F(CoreTest, FlightOutputReachesALinkPeerDuringReplay) {
    class Counter : public Wsts::ByteStreamDevice {
      public:
        explicit Counter(bool peer) : m_peer(peer) {}
        void onBytes(const Bytes&, Wsts::ByteSink&) override { seen++; }
        bool peer() const override { return m_peer; }
        int seen = 0;

      private:
        bool m_peer;
    };
    Counter model(false);
    Counter link(true);
    Bus::get().attachByteStream("modelPort", &model);
    Bus::get().attachByteStream("linkPort", &link);
    Bus::get().setReplay(true);
    EXPECT_EQ(Bus::get().byteStreamSend("modelPort", text("x")), 0);
    EXPECT_EQ(Bus::get().byteStreamSend("linkPort", text("x")), 0);
    Bus::get().setReplay(false);
    EXPECT_EQ(model.seen, 0);
    EXPECT_EQ(link.seen, 1);
    Bus::get().detach("modelPort");
    Bus::get().detach("linkPort");
}

TEST_F(CoreTest, LinkWithoutAnAddressStaysDown) {
    ::unsetenv("WSTS_LINK_DIR");
    Wsts::LinkByteStream link;
    ASSERT_TRUE(link.set("link", "alone"));
    link.start();
    EXPECT_FALSE(link.up());
    EXPECT_EQ(Wsts::LinkByteStream::started(), 0u);
}

// ----------------------------------------------------------------------
// Cycle clocks
// ----------------------------------------------------------------------

TEST(CycleSchedule, CountIsExactAtANonIntegerRatio) {
    // 1000 Hz against a 64 Hz tick: 15.625 cycles per tick
    Wsts::CycleSchedule schedule(1000 * 1000);
    const std::uint64_t tickNs = 15625000;
    std::uint64_t last = 0;
    bool first = true;
    for (std::uint64_t tick = 0; tick < 64000; tick++) {
        std::vector<std::uint64_t> offsets;
        schedule.advance(tickNs, offsets);
        ASSERT_TRUE(offsets.size() == 15 || offsets.size() == 16) << "tick " << tick;
        for (const std::uint64_t offset : offsets) {
            ASSERT_LT(offset, tickNs);
            const std::uint64_t at = tick * tickNs + offset;
            if (!first) {
                ASSERT_EQ(at - last, 1000000u) << "cycles are one millisecond apart in simulated time";
            }
            first = false;
            last = at;
        }
    }
    EXPECT_EQ(schedule.issued(), 1000000u) << "1000 s of simulated time at 1000 Hz";
}

TEST(CycleSchedule, SlowAndFractionalRates) {
    // 2 Hz against 64 Hz: a cycle every 32nd tick, the first on the first tick
    Wsts::CycleSchedule slow(2 * 1000);
    std::vector<int> ticksWithACycle;
    for (int tick = 0; tick < 128; tick++) {
        std::vector<std::uint64_t> offsets;
        slow.advance(15625000, offsets);
        if (!offsets.empty()) {
            EXPECT_EQ(offsets.size(), 1u);
            EXPECT_EQ(offsets[0], 0u) << "in phase with the tick";
            ticksWithACycle.push_back(tick);
        }
    }
    EXPECT_EQ(ticksWithACycle, (std::vector<int>{0, 32, 64, 96}));
    // 0.5 Hz, and a tick period that is not a whole number of cycles: 3 Hz against 10 Hz
    Wsts::CycleSchedule half(500);
    Wsts::CycleSchedule three(3000);
    std::vector<std::uint64_t> offsets;
    for (int tick = 0; tick < 100; tick++) {
        half.advance(100000000, offsets);
        three.advance(100000000, offsets);
    }
    EXPECT_EQ(half.issued(), 5u);
    EXPECT_EQ(three.issued(), 30u);
}

TEST_F(CoreTest, ClockRejectsBadRates) {
    Wsts::CycleClock clock;
    EXPECT_FALSE(clock.configure(0.0, nullptr));
    EXPECT_FALSE(clock.configure(-5.0, nullptr));
    EXPECT_FALSE(clock.configure(Wsts::CycleClock::MAX_HZ + 1.0, nullptr));
    EXPECT_TRUE(clock.configure(1000.0, nullptr));
    EXPECT_DOUBLE_EQ(clock.hz(), 1000.0);
}

//! Tick the control core as a tick source would: `count` ticks of `periodNs` simulated
//! time, paced by the wall clock at the current time scale
void tickPaced(int count, std::uint64_t periodNs) {
    auto next = std::chrono::steady_clock::now();
    for (int i = 0; i < count; i++) {
        Control::get().onTick();
        next += std::chrono::nanoseconds(static_cast<std::int64_t>(static_cast<double>(periodNs) / Control::get().rate()));
        std::this_thread::sleep_until(next);
    }
}

TEST_F(CoreTest, ClockFollowsTicksPauseAndRate) {
    const std::uint64_t tickNs = 15625000;  // 64 Hz
    std::atomic<std::uint64_t> count{0};
    Wsts::CycleClock clock;
    ASSERT_TRUE(clock.configure(1000.0, [&count]() { count++; }));
    Control::get().setTickPeriod(tickNs);
    clock.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    EXPECT_EQ(count.load(), 0u) << "no ticks, no cycles: the clock does not run by itself";

    // Half a second of simulated time at twice real time: exactly 500 cycles
    ASSERT_TRUE(run(R"({"op": "set_rate", "rate": 2})"));
    const auto started = std::chrono::steady_clock::now();
    tickPaced(32, tickNs);
    ASSERT_TRUE(eventually([&]() { return clock.pending() == 0; }));
    const auto elapsed = std::chrono::steady_clock::now() - started;
    EXPECT_EQ(count.load(), 500u);
    EXPECT_EQ(clock.cycles(), 500u);
    EXPECT_EQ(clock.overruns(), 0u);
    EXPECT_LT(elapsed, std::chrono::milliseconds(450)) << "0.5 s of simulated time took 0.25 s at rate 2";
    ASSERT_TRUE(run(R"({"op": "set_rate", "rate": 1})"));

    // Paused: what the last tick granted is held, and idle calls grant nothing
    Control::get().onTick();
    ASSERT_TRUE(run(R"({"op": "pause"})"));
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    const std::uint64_t atPause = count.load();
    EXPECT_LT(atPause, 516u);
    for (int i = 0; i < 20; i++) {
        Control::get().onIdle();
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    EXPECT_EQ(count.load(), atPause) << "no cycles while paused";

    // Resumed: the held cycles come out spaced as they were, not in a burst, and one more
    // tick grants one tick's worth
    ASSERT_TRUE(run(R"({"op": "resume"})"));
    std::this_thread::sleep_for(std::chrono::milliseconds(3));
    EXPECT_LE(count.load() - atPause, 6u) << "about 1 per millisecond, not 16 at once";
    ASSERT_TRUE(eventually([&]() { return clock.pending() == 0; }));
    EXPECT_EQ(count.load(), 516u);
    Control::get().onTick();
    ASSERT_TRUE(eventually([&]() { return clock.pending() == 0; }));
    const std::uint64_t afterOneTick = count.load() - 516u;
    EXPECT_TRUE(afterOneTick == 15 || afterOneTick == 16);
    EXPECT_EQ(clock.overruns(), 0u);
    clock.stop();
}

TEST_F(CoreTest, ClockCyclesAreEvenlySpacedWithinATick) {
    // 125 Hz against a 10 Hz tick: 12.5 cycles per tick, 8 ms apart
    std::mutex mutex;
    std::vector<std::chrono::steady_clock::time_point> times;
    Wsts::CycleClock clock;
    ASSERT_TRUE(clock.configure(125.0, [&]() {
        std::lock_guard<std::mutex> lock(mutex);
        times.push_back(std::chrono::steady_clock::now());
    }));
    Control::get().setTickPeriod(100000000);
    clock.start();
    const auto firstTick = std::chrono::steady_clock::now();
    tickPaced(20, 100000000);
    ASSERT_TRUE(eventually([&]() { return clock.pending() == 0; }));
    clock.stop();
    ASSERT_EQ(times.size(), 250u);
    std::vector<double> gaps;
    for (std::size_t i = 1; i < times.size(); i++) {
        gaps.push_back(std::chrono::duration<double, std::milli>(times[i] - times[i - 1]).count());
    }
    std::sort(gaps.begin(), gaps.end());
    const double median = gaps[gaps.size() / 2];
    const double phase = std::chrono::duration<double, std::milli>(times[0] - firstTick).count();
    std::printf("[ clock    ] 125 Hz on a 10 Hz tick: gaps min %.2f ms, median %.2f ms, 95%% %.2f ms, max %.2f ms; "
                "first cycle %.2f ms after the first tick\n",
                gaps.front(), median, gaps[gaps.size() * 95 / 100], gaps.back(), phase);
    EXPECT_NEAR(median, 8.0, 1.0);
    EXPECT_LT(gaps[gaps.size() * 95 / 100], 16.0) << "no bursts at the tick boundaries";
    EXPECT_LT(phase, 20.0) << "cycle 0 is issued at the first tick";
}

TEST_F(CoreTest, ClockMeasuresTheTickWhenNoSourceDeclaresIt) {
    // A project tick surrogate that only calls onTick(): the period is taken from the ticks
    std::atomic<std::uint64_t> count{0};
    Wsts::CycleClock clock;
    ASSERT_TRUE(clock.configure(200.0, [&count]() { count++; }));
    clock.start();
    EXPECT_EQ(Control::get().tickPeriodNs(), 0u);
    tickPaced(26, 20000000);  // 50 Hz for about half a second
    ASSERT_TRUE(eventually([&]() { return clock.pending() == 0; }));
    EXPECT_NEAR(static_cast<double>(Control::get().tickPeriodNs()), 20000000.0, 2000000.0);
    EXPECT_NEAR(static_cast<double>(count.load()), 100.0, 12.0) << "4 cycles per tick from the second tick on";
    clock.stop();
}

TEST_F(CoreTest, SlowClockCallbackDropsCyclesInsteadOfPilingUp) {
    std::atomic<std::uint64_t> count{0};
    Wsts::CycleClock clock;
    ASSERT_TRUE(clock.configure(1000.0, [&count]() {
        count++;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }));
    Control::get().setTickPeriod(15625000);
    clock.start();
    tickPaced(16, 15625000);
    EXPECT_GT(clock.overruns(), 100u);
    EXPECT_LE(clock.pending(), 64u);
    clock.stop();
    EXPECT_LT(count.load(), 40u);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
