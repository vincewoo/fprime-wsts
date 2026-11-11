// ======================================================================
// \title  Bridge.hpp
// \brief  Socket bridge exposing the WSTS bus and control core to external processes
//
// Protocol: newline-delimited flat JSON objects in both directions.
//
//   client -> sim   {"id": 7, "op": "<action>", ...}       any Control action, plus:
//                     status                               -> tick, paused, counters, endpoints, links
//                     subscribe                            start streaming bus records and events
//                     attach   endpoint kind(i2c|spi) [addr]   become the device model
//                     reply    rid status data             answer an i2c_req / spi_req
//                     gpio_get endpoint                    -> level flight software last wrote
//   sim -> client   {"id": 7, "ok": true, ...} | {"id": 7, "ok": false, "error": "..."}
//                   {"ev": "rec", ...}        bus transaction (after subscribe)
//                   {"ev": "sim_event", ...}  simulation event (after subscribe)
//                   {"ev": "i2c_req", "rid": 3, "endpoint": .., "addr": .., "write": hex, "rlen": n}
//                   {"ev": "spi_req", "rid": 4, "endpoint": .., "write": hex, "rlen": n}
// ======================================================================
#ifndef WSTS_CORE_BRIDGE_HPP
#define WSTS_CORE_BRIDGE_HPP

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace Wsts {

class Bridge {
  public:
    static Bridge& get();

    //! Start listening. Address forms: "tcp:<port>", "tcp:<host>:<port>", "unix:<path>".
    //! An empty address or "none" leaves the bridge off. Returns false if it cannot listen.
    bool start(const std::string& address);
    void stop();
    bool running() const { return m_running.load(); }
    //! TCP port actually bound (useful with "tcp:0"); 0 for unix sockets
    int port() const { return m_port; }

    //! How long a bridged I2C/SPI transaction waits for the external model
    void setRpcTimeoutMs(unsigned ms) { m_rpcTimeoutMs = ms; }

    class Client;

  private:
    Bridge() = default;
    void acceptLoop();
    void reap();

    std::atomic<bool> m_running{false};
    int m_listenFd = -1;
    int m_port = 0;
    std::string m_unixPath;
    unsigned m_rpcTimeoutMs = 2000;
    std::thread m_acceptThread;
    std::mutex m_mutex;
    std::vector<std::shared_ptr<Client>> m_clients;
};

}  // namespace Wsts

#endif
