// ======================================================================
// \title  CoreTestMain.cpp
// \brief  Unit tests for the WSTS core: JSON, bus, faults, control, scenarios, recording, links
// ======================================================================
#include <gtest/gtest.h>

#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Core/Control.hpp>
#include <Wsts/Core/Link.hpp>
#include <Wsts/Core/Models.hpp>

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

TEST_F(CoreTest, LinkWithoutAnAddressStaysDown) {
    ::unsetenv("WSTS_LINK_DIR");
    Wsts::LinkByteStream link;
    ASSERT_TRUE(link.set("link", "alone"));
    link.start();
    EXPECT_FALSE(link.up());
    EXPECT_EQ(Wsts::LinkByteStream::started(), 0u);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
