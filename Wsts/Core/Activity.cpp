// ======================================================================
// \title  Activity.cpp
// \brief  Count of the work flight software has been given and not yet finished
// ======================================================================
#include <Wsts/Core/Activity.hpp>

namespace Wsts {

namespace {
thread_local std::uint64_t t_scopes = 0;  // scopes this thread holds
}

Activity& Activity::get() {
    // Never destroyed: queues and threads report to it until the process is gone
    static Activity* instance = new Activity;
    return *instance;
}

void Activity::begin() {
    m_total++;
    m_pending++;
}

void Activity::end() {
    std::uint64_t now = m_pending.load();
    // Never below zero: work begun before a reset may end after it
    while (now > 0 && !m_pending.compare_exchange_weak(now, now - 1)) {
    }
    if (now <= 1) {
        // Taken so that a waiter cannot miss the notification between its check and its wait
        std::lock_guard<std::mutex> lock(m_mutex);
        m_quiet.notify_all();
    }
}

std::uint64_t Activity::pendingElsewhere() const {
    const std::uint64_t pending = m_pending.load();
    return pending > t_scopes ? pending - t_scopes : 0;
}

bool Activity::waitQuiescent(std::chrono::milliseconds timeout) {
    std::unique_lock<std::mutex> lock(m_mutex);
    return m_quiet.wait_for(lock, timeout, [this]() { return m_pending.load() == 0; });
}

void Activity::setReporter(Reporter reporter) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_reporter = std::move(reporter);
}

std::string Activity::holders() {
    Reporter reporter;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        reporter = m_reporter;
    }
    return reporter ? reporter() : std::string();
}

void Activity::reset() {
    m_pending.store(0);
    m_total.store(0);
    m_counting.store(false);
    std::lock_guard<std::mutex> lock(m_mutex);
    m_quiet.notify_all();
}

Activity::Scope::Scope() {
    t_scopes++;
    Activity::get().begin();
}

Activity::Scope::~Scope() {
    Activity::get().end();
    t_scopes--;
}

}  // namespace Wsts
