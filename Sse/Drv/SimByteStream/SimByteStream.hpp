// ======================================================================
// \title  SimByteStream.hpp
// \brief  Simulation surrogate for byte stream drivers
// ======================================================================
#ifndef SSE_SIM_BYTE_STREAM_HPP
#define SSE_SIM_BYTE_STREAM_HPP

#include <atomic>
#include <string>

#include <Os/Mutex.hpp>
#include <Os/Task.hpp>
#include <Sse/Core/Json.hpp>
#include <Sse/Drv/SimByteStream/SimByteStreamComponentAc.hpp>

namespace Sse {

class SimByteStream final : public SimByteStreamComponentBase {
  public:
    // Configuration enumerations of the UART drivers this surrogate stands in for, so that
    // topology code written against them compiles unchanged.
    enum UartBaudRate {
        BAUD_9600 = 9600,
        BAUD_19200 = 19200,
        BAUD_38400 = 38400,
        BAUD_57600 = 57600,
        BAUD_115K = 115200,
        BAUD_230K = 230400,
        BAUD_460K = 460800,
        BAUD_921K = 921600,
        BAUD_1000K = 1000000,
        BAUD_1152K = 1152000,
        BAUD_1500K = 1500000,
        BAUD_2000K = 2000000,
        BAUD_2500K = 2500000,
        BAUD_3000K = 3000000,
        BAUD_3500K = 3500000,
        BAUD_4000K = 4000000
    };
    enum UartFlowControl { NO_FLOW, HW_FLOW };
    enum UartParity { PARITY_NONE, PARITY_ODD, PARITY_EVEN };

    explicit SimByteStream(const char* compName);
    ~SimByteStream();

    //! Bind this instance to an SSE bus endpoint. Called from generated topology code.
    //! Options: "chunk=<bytes>" sets the largest buffer delivered per recv call.
    void sseBind(const char* endpoint, const char* options = "");

    //! Stand-in for the driver's open(): the device arguments are accepted and ignored
    template <typename... Args>
    bool open(Args&&...) {
        this->signalReady();
        return true;
    }
    //! Stand-in for starting the driver's read thread: reception needs no thread here
    template <typename... Args>
    void start(Args&&...) {}
    void stop() {}
    void quitReadThread() {}
    Os::Task::Status join() { return Os::Task::OP_OK; }

  private:
    Drv::ByteStreamStatus send_handler(FwIndexType portNum, Fw::Buffer& sendBuffer) override;
    void recvReturnIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) override;
    void run_handler(FwIndexType portNum, U32 context) override;

    void signalReady();
    //! Deliver bytes arriving from the simulated device to flight software
    void receive(const Bytes& data);

    std::string m_endpoint;
    FwSizeType m_chunk;
    std::atomic<bool> m_ready;
    Os::Mutex m_receiveLock;
};

}  // namespace Sse

#endif
