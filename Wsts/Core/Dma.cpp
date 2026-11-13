// ======================================================================
// \title  Dma.cpp
// \brief  Physical and virtual addresses of memory that flight software shares with hardware
// ======================================================================
#include <Wsts/Core/Dma.hpp>

#include <mutex>
#include <vector>

namespace Wsts {
namespace Dma {

namespace {

struct Region {
    std::uintptr_t host;
    std::uint64_t physical;
    std::size_t size;
};

std::mutex s_mutex;
std::vector<Region> s_regions;
ToPhysical s_toPhysical = nullptr;
ToVirtual s_toVirtual = nullptr;

}  // namespace

void setTranslator(ToPhysical toPhysical, ToVirtual toVirtual) {
    std::lock_guard<std::mutex> lock(s_mutex);
    s_toPhysical = toPhysical;
    s_toVirtual = toVirtual;
}

bool addRegion(void* hostBase, std::uint64_t physicalBase, std::size_t size) {
    const auto host = reinterpret_cast<std::uintptr_t>(hostBase);
    if (hostBase == nullptr || size == 0) {
        return false;
    }
    std::lock_guard<std::mutex> lock(s_mutex);
    for (const Region& region : s_regions) {
        const bool hostOverlap = host < region.host + region.size && region.host < host + size;
        const bool physicalOverlap = physicalBase < region.physical + region.size && region.physical < physicalBase + size;
        if (hostOverlap || physicalOverlap) {
            return false;
        }
    }
    s_regions.push_back(Region{host, physicalBase, size});
    return true;
}

void removeRegion(void* hostBase) {
    const auto host = reinterpret_cast<std::uintptr_t>(hostBase);
    std::lock_guard<std::mutex> lock(s_mutex);
    for (auto it = s_regions.begin(); it != s_regions.end(); ++it) {
        if (it->host == host) {
            s_regions.erase(it);
            return;
        }
    }
}

void reset() {
    std::lock_guard<std::mutex> lock(s_mutex);
    s_regions.clear();
    s_toPhysical = nullptr;
    s_toVirtual = nullptr;
}

void* toVirtual(std::uint64_t physical, std::size_t size) {
    ToVirtual translator = nullptr;
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        translator = s_toVirtual;
        if (translator == nullptr) {
            for (const Region& region : s_regions) {
                if (physical >= region.physical && size <= region.size && physical - region.physical <= region.size - size) {
                    return reinterpret_cast<void*>(region.host + static_cast<std::uintptr_t>(physical - region.physical));
                }
            }
            return nullptr;
        }
    }
    return translator(physical, size);
}

bool toPhysical(const void* host, std::uint64_t& physical) {
    ToPhysical translator = nullptr;
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        translator = s_toPhysical;
        if (translator == nullptr) {
            const auto address = reinterpret_cast<std::uintptr_t>(host);
            for (const Region& region : s_regions) {
                if (address >= region.host && address - region.host < region.size) {
                    physical = region.physical + (address - region.host);
                    return true;
                }
            }
            return false;
        }
    }
    return translator(host, physical);
}

}  // namespace Dma
}  // namespace Wsts
