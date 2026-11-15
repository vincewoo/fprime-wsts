// ======================================================================
// \title  Activity.cpp
// \brief  Count of the work flight software has been given and not yet finished
// ======================================================================
#include <Wsts/Core/Activity.hpp>

#include <algorithm>

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

// ----------------------------------------------------------------------
// One at a time
// ----------------------------------------------------------------------

void Activity::setExclusive(bool exclusive) {
    {
        std::lock_guard<std::mutex> lock(m_turnMutex);
        m_exclusive.store(exclusive);
    }
    m_turn.notify_all();
}

void Activity::holdTasks(bool hold) {
    {
        std::lock_guard<std::mutex> lock(m_turnMutex);
        m_holdTasks.store(hold);
    }
    m_turn.notify_all();
}

bool Activity::hasTurn() const {
    Activity* self = const_cast<Activity*>(this);
    std::lock_guard<std::mutex> lock(self->m_turnMutex);
    return m_depth > 0 && m_owner == std::this_thread::get_id();
}

void Activity::poke() {
    if (m_exclusive.load()) {
        // Taken so that a contender cannot miss it between its check and its wait
        { std::lock_guard<std::mutex> lock(m_turnMutex); }
        m_turn.notify_all();
    }
}

void Activity::releaseLocked() {
    if (m_depth > 0 && m_owner == std::this_thread::get_id() && --m_depth == 0) {
        m_owner = std::thread::id();
    }
}

bool Activity::grantedLocked(const Contender* self) {
    // The turn is free and this contender is the first of those that are ready
    if (m_depth != 0) {
        return false;
    }
    if (self->rank >= TASK_START_RANK && m_holdTasks.load()) {
        return false;  // start-up is not over
    }
    if (self->ready != nullptr && !(*self->ready)()) {
        return false;
    }
    for (const Contender* other : m_contenders) {
        if (other != self && other->rank < self->rank && (other->ready == nullptr || (*other->ready)())) {
            return false;
        }
    }
    return true;
}

bool Activity::enter(std::uint64_t rank) {
    if (!m_exclusive.load()) {
        return false;
    }
    std::unique_lock<std::mutex> lock(m_turnMutex);
    if (m_depth > 0 && m_owner == std::this_thread::get_id()) {
        m_depth++;
        return true;
    }
    const Contender self{rank, nullptr};
    m_contenders.push_back(&self);
    m_turn.wait(lock, [this, &self]() { return !m_exclusive.load() || this->grantedLocked(&self); });
    m_contenders.erase(std::find(m_contenders.begin(), m_contenders.end(), &self));
    if (!m_exclusive.load()) {
        lock.unlock();
        m_turn.notify_all();
        return false;
    }
    m_owner = std::this_thread::get_id();
    m_depth = 1;
    return true;
}

void Activity::leave() {
    {
        std::lock_guard<std::mutex> lock(m_turnMutex);
        this->releaseLocked();
    }
    m_turn.notify_all();
}

void Activity::abandon() {
    {
        std::lock_guard<std::mutex> lock(m_turnMutex);
        if (m_depth > 0 && m_owner == std::this_thread::get_id()) {
            m_depth = 0;
            m_owner = std::thread::id();
        }
    }
    m_turn.notify_all();
}

bool Activity::awaitTurn(std::uint64_t rank, const Ready& ready) {
    std::unique_lock<std::mutex> lock(m_turnMutex);
    // Whatever this thread held, from the message it has just finished, is given up here,
    // in the same step in which it queues for the next one
    if (m_depth > 0 && m_owner == std::this_thread::get_id()) {
        m_depth = 0;
        m_owner = std::thread::id();
    }
    if (!m_exclusive.load()) {
        lock.unlock();
        m_turn.notify_all();
        return false;
    }
    const Contender self{rank, &ready};
    m_contenders.push_back(&self);
    m_turn.notify_all();  // the turn is free: others look again
    m_turn.wait(lock, [this, &self]() { return !m_exclusive.load() || this->grantedLocked(&self); });
    m_contenders.erase(std::find(m_contenders.begin(), m_contenders.end(), &self));
    if (!m_exclusive.load()) {
        lock.unlock();
        m_turn.notify_all();
        return false;
    }
    m_owner = std::this_thread::get_id();
    m_depth = 1;
    return true;
}

void Activity::reset() {
    {
        std::lock_guard<std::mutex> lock(m_turnMutex);
        m_exclusive.store(false);
        m_holdTasks.store(false);
        m_owner = std::thread::id();
        m_depth = 0;
    }
    m_turn.notify_all();
    m_pending.store(0);
    m_total.store(0);
    m_counting.store(false);
    std::lock_guard<std::mutex> lock(m_mutex);
    m_quiet.notify_all();
}

Activity::Scope::Scope() : m_entered(false) {
    t_scopes++;
    Activity::get().begin();  // outstanding from here, also while it waits for the turn
    m_entered = Activity::get().enter();
}

Activity::Scope::~Scope() {
    if (m_entered) {
        Activity::get().leave();
    }
    Activity::get().end();
    t_scopes--;
}

}  // namespace Wsts
