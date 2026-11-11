// ======================================================================
// \title  ImuModel.cpp
// \brief  Device model of the LinuxDemo IMU
// ======================================================================
#include <Models/ImuModel.hpp>

#include <cmath>
#include <cstdlib>

namespace Sim {

ImuModel::ImuModel() {
    m_registers[REG_WHO_AM_I] = 0x68;
    writeAxis(2, 1.0);
}

void ImuModel::writeAxis(int axis, double g) {
    long raw = std::lround(g * LSB_PER_G);
    raw = raw > 32767 ? 32767 : (raw < -32768 ? -32768 : raw);  // the sensor saturates
    const auto word = static_cast<std::uint16_t>(static_cast<std::int16_t>(raw));
    m_registers[REG_ACCEL_XOUT_H + 2 * axis] = static_cast<std::uint8_t>(word >> 8);
    m_registers[REG_ACCEL_XOUT_H + 2 * axis + 1] = static_cast<std::uint8_t>(word & 0xFF);
}

bool ImuModel::set(const std::string& key, const std::string& value) {
    char* end = nullptr;
    if (key == "who_am_i") {
        const long id = std::strtol(value.c_str(), &end, 0);
        if (end == value.c_str() || *end != '\0' || id < 0 || id > 255) {
            return false;
        }
        std::lock_guard<std::mutex> lock(m_mutex);
        m_registers[REG_WHO_AM_I] = static_cast<std::uint8_t>(id);
        return true;
    }
    const int axis = key == "accel_x" ? 0 : key == "accel_y" ? 1 : key == "accel_z" ? 2 : -1;
    if (axis < 0) {
        return false;
    }
    const double g = std::strtod(value.c_str(), &end);
    if (end == value.c_str() || *end != '\0' || !std::isfinite(g)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    writeAxis(axis, g);
    return true;
}

int ImuModel::transact(std::uint32_t addr, const Wsts::Bytes& write, Wsts::Bytes& read) {
    (void)addr;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!write.empty()) {
        m_pointer = write[0];  // register address, then data to write
        for (std::size_t i = 1; i < write.size(); i++) {
            m_registers[m_pointer] = write[i];
            m_pointer = static_cast<std::uint8_t>(m_pointer + 1);
        }
    }
    for (std::uint8_t& byte : read) {
        byte = m_registers[m_pointer];
        m_pointer = static_cast<std::uint8_t>(m_pointer + 1);
    }
    return 0;
}

bool ImuModel::pokeRegister(std::uint8_t reg, std::uint8_t value) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_registers[reg] = value;
    return true;
}

}  // namespace Sim
