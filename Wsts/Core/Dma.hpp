// ======================================================================
// \title  Dma.hpp
// \brief  Physical and virtual addresses of memory that flight software shares with hardware
//
// A flight driver that uses DMA allocates a buffer, writes its *physical* address into a
// register, and reads the data through the buffer's *virtual* address. On a host there is
// no physical memory to speak of: the flight allocator's host variant takes the memory
// from the heap and remembers the physical base it was configured with. A device model
// that is handed such an address in a register write needs the host pointer behind it.
//
// The toolkit does not know a project's allocator. The project tells it, at start-up
// (`setup:` in the manifest), in one of two ways:
//
//   Wsts::Dma::addRegion(hostBase, physicalBase, size);       // once per mapped region
//   Wsts::Dma::setTranslator(toPhysical, toVirtual);          // or: the project's own functions
//
// and a model then calls Wsts::Dma::toVirtual(address, size).
// ======================================================================
#ifndef WSTS_CORE_DMA_HPP
#define WSTS_CORE_DMA_HPP

#include <cstddef>
#include <cstdint>

namespace Wsts {
namespace Dma {

//! The host pointer for a physical address, or nullptr when `size` bytes from it are not
//! inside memory the translator knows
using ToVirtual = void* (*)(std::uint64_t physical, std::size_t size);
//! The physical address of a host pointer. Returns false when the pointer is not in a region.
using ToPhysical = bool (*)(const void* host, std::uint64_t& physical);

//! Use the project's own translation functions (those of its allocator or its
//! virtual-to-physical utility). Either may be nullptr to keep the region table for that
//! direction. Call before the runtime starts.
void setTranslator(ToPhysical toPhysical, ToVirtual toVirtual);

//! Record a region: `size` bytes at `hostBase` stand for the physical range starting at
//! `physicalBase`. Returns false when it overlaps a region already recorded.
bool addRegion(void* hostBase, std::uint64_t physicalBase, std::size_t size);
//! Forget a region (the allocator freed it)
void removeRegion(void* hostBase);
//! Forget everything, translator included (unit tests)
void reset();

//! Host pointer for `size` bytes at a physical address; nullptr if they are not all inside
//! one known region
void* toVirtual(std::uint64_t physical, std::size_t size = 1);
//! Physical address of a host pointer; false if it is not inside a known region
bool toPhysical(const void* host, std::uint64_t& physical);

}  // namespace Dma
}  // namespace Wsts

#endif
