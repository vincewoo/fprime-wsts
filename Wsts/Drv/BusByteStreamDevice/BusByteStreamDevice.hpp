// ======================================================================
// \title  BusByteStreamDevice.hpp
// \brief  Adapter from a WSTS byte stream endpoint to an F Prime device simulator
// ======================================================================
#ifndef WSTS_BUS_BYTE_STREAM_DEVICE_HPP
#define WSTS_BUS_BYTE_STREAM_DEVICE_HPP

#include <atomic>
#include <string>

#include <Wsts/Core/Bus.hpp>
#include <Wsts/Drv/BusByteStreamDevice/BusByteStreamDeviceComponentAc.hpp>

namespace Wsts {

class BusByteStreamDevice final : public BusByteStreamDeviceComponentBase, public ByteStreamDevice {
  public:
    explicit BusByteStreamDevice(const char* compName);
    ~BusByteStreamDevice();

    //! Become the device on a bus endpoint. Called by generated set-up code, after the
    //! surrogates are bound. Options: "chunk=<bytes>" maximum size of one delivery (default 1024).
    void wstsServe(const char* endpoint, const char* options = "");

    //! Bus side: flight software transmitted `tx`. Runs on the thread that sent.
    void onBytes(const Bytes& tx, ByteSink& reply) override;

  private:
    Drv::ByteStreamStatus send_handler(FwIndexType portNum, Fw::Buffer& sendBuffer) override;
    void recvReturnIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;

    //! Tell the simulator the link is up, once the flight side of the endpoint is bound
    void signalReady();

    std::string m_endpoint;
    FwSizeType m_chunk;
    std::atomic<bool> m_ready;
};

}  // namespace Wsts

#endif
