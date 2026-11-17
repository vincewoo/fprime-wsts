// ======================================================================
// \title  LinkRegistry.cpp
// \brief  Links started in this process, whatever their transport
// ======================================================================
#include <Wsts/Core/LinkRegistry.hpp>

#include <algorithm>
#include <mutex>
#include <vector>

namespace Wsts {

namespace {

std::mutex s_mutex;
std::vector<LinkEnd*> s_links;

}  // namespace

void LinkRegistry::add(LinkEnd* link) {
    std::lock_guard<std::mutex> lock(s_mutex);
    s_links.push_back(link);
}

void LinkRegistry::remove(LinkEnd* link) {
    std::lock_guard<std::mutex> lock(s_mutex);
    s_links.erase(std::remove(s_links.begin(), s_links.end(), link), s_links.end());
}

std::uint32_t LinkRegistry::started() {
    std::lock_guard<std::mutex> lock(s_mutex);
    return static_cast<std::uint32_t>(s_links.size());
}

std::uint32_t LinkRegistry::connected() {
    std::lock_guard<std::mutex> lock(s_mutex);
    return static_cast<std::uint32_t>(std::count_if(s_links.begin(), s_links.end(),
                                                    [](LinkEnd* link) { return link->isUp(); }));
}

LinkEnd* LinkRegistry::find(const std::string& name) {
    std::lock_guard<std::mutex> lock(s_mutex);
    for (LinkEnd* link : s_links) {
        if (link->name() == name) {
            return link;
        }
    }
    return nullptr;
}

void LinkRegistry::traffic(JsonObj& out) {
    std::lock_guard<std::mutex> lock(s_mutex);
    for (LinkEnd* link : s_links) {
        out.setNum("link." + link->name() + ".sent", static_cast<std::int64_t>(link->sent()));
        out.setNum("link." + link->name() + ".received", static_cast<std::int64_t>(link->received()));
    }
}

}  // namespace Wsts
