// ======================================================================
// \title  SpwDriver.cpp
// \brief  The payload board's SpaceWire controller (Drv.PassiveBufferDriver)
//
// On the board the controller's DMA descriptors take each packet and the receive interrupt
// delivers what arrives. That code needs the board: built for anything else, the link stays
// down, nothing is transmitted and nothing arrives, as the FPGA's register interface reads
// zeros. The simulation replaces this driver (Wsts.SimSpaceWire).
// ======================================================================
#include "Components/SpwDriver/SpwDriver.hpp"

namespace Components {

SpwDriver::SpwDriver(const char* compName) : SpwDriverComponentBase(compName), m_link(0), m_logicalAddress(0) {}

bool SpwDriver::configure(U8 link, U8 logicalAddress) {
    m_link = link;
    m_logicalAddress = logicalAddress;
    return false;  // no controller here
}

void SpwDriver::bufferIn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    // The link is down: the packet goes nowhere, and its buffer goes back
    this->bufferInReturn_out(0, fwBuffer);
}

void SpwDriver::bufferOutReturn_handler(FwIndexType portNum, Fw::Buffer& fwBuffer) {
    (void)portNum;
    this->deallocate_out(0, fwBuffer);
}

}  // namespace Components
