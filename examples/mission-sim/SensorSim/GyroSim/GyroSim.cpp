#include "SensorSim/GyroSim/GyroSim.hpp"

#include <cstdio>
#include <cstring>

#include "SimCommon/Types/Profile.hpp"

namespace SensorSim {

GyroSim::GyroSim(const char* compName)
    : GyroSimComponentBase(compName), m_stepHz(1), m_steps(0), m_motion(SimCommon::Motion::STILL), m_source{0}, m_announced(false) {}

void GyroSim::configure(U32 stepHz, const char* profileFile) {
    m_stepHz = (stepHz > 0) ? stepHz : 1;
    (void)std::snprintf(m_source, sizeof(m_source), "%s", "built-in default");
    if (profileFile == nullptr || profileFile[0] == '\0') {
        return;  // nothing was asked for: the default profile
    }
    char name[32] = {0};
    std::FILE* file = std::fopen(profileFile, "r");
    if (file != nullptr) {
        if (std::fgets(name, sizeof(name), file) != nullptr) {
            name[std::strcspn(name, "\r\n")] = '\0';
            m_motion = SimCommon::motionOf(name);
            (void)std::snprintf(m_source, sizeof(m_source), "%s", profileFile);
        }
        (void)std::fclose(file);
    } else {
        (void)std::snprintf(m_source, sizeof(m_source), "%s", "built-in default (file not found)");
    }
}

void GyroSim::schedIn_handler(FwIndexType portNum, U32 context) {
    (void)portNum;
    (void)context;
    if (!m_announced) {
        // Events need the topology to be running: said on the first step
        m_announced = true;
        this->log_ACTIVITY_HI_ProfileLoaded(m_motion, Fw::String(m_source));
    }
    m_steps++;
    this->tlmWrite_Profile(m_motion);
    this->tlmWrite_Steps(m_steps);
    this->tlmWrite_ModelTime(static_cast<F32>(m_steps) / static_cast<F32>(m_stepHz));
}

}  // namespace SensorSim
