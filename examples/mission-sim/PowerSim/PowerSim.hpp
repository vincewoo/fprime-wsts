// ======================================================================
// \title  PowerSim.hpp
// \brief  Simulator of the power monitor that Components.PowerMonitor reads over I2C
// ======================================================================
#ifndef POWER_SIM_HPP
#define POWER_SIM_HPP

#include <array>
#include <mutex>

#include <PowerSim/PowerSimComponentAc.hpp>

namespace PowerSim {

class PowerSim final : public PowerSimComponentBase {
  public:
    explicit PowerSim(const char* const compName);
    ~PowerSim() = default;

  private:
    Drv::I2cStatus transaction_handler(FwIndexType portNum,
                                       U32 addr,
                                       Fw::Buffer& writeBuffer,
                                       Fw::Buffer& readBuffer) override;
    void poke_handler(FwIndexType portNum, U8 reg, U8 value) override;
    void SET_VOLTAGE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 millivolts) override;
    void SET_CURRENT_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 milliamps) override;

    //! The voltage the registers hold, as the flight driver reads it: 2000 counts per volt
    void setVoltageRaw(U32 raw);
    //! The current the registers hold, in counts of milliamps
    void setCurrentRaw(U32 raw);

    static constexpr FwSizeType REGISTER_COUNT = 16;
    static constexpr U8 REGISTER_VOLTAGE = 2;  //!< High byte of the voltage; low byte follows
    static constexpr U8 REGISTER_CURRENT = 4;  //!< High byte of the current; low byte follows

    std::array<U8, REGISTER_COUNT> m_registers;
    U8 m_pointer;
    U32 m_transactions;
    // The transaction runs on the flight thread; the poke and the commands come from other
    // threads of the simulation
    std::mutex m_lock;
};

}  // namespace PowerSim

#endif
