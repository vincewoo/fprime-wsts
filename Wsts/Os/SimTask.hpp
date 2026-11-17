// ======================================================================
// \title  SimTask.hpp
// \brief  The Os::Task of a simulated deployment: a POSIX task that takes its turn
//
// Chosen by a simulated deployment in place of Os_Task_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_Task). In real time it is that implementation. In stepped time, where flight
// code runs on one thread at a time, a task takes the turn before its routine runs: what
// a task does before it first waits for a message (a rate group announcing itself) then
// happens in the order the tasks were started, in every run. A task that sleeps in
// Os::Task::delay does not keep the turn meanwhile. The threads of the framework's socket
// drivers take no turn at all: they block in the host until bytes arrive from outside.
// ======================================================================
#ifndef WSTS_OS_SIMTASK_HPP
#define WSTS_OS_SIMTASK_HPP

#include <Os/Posix/Task.hpp>

namespace Wsts {

class SimTask : public Os::Posix::Task::PosixTask {
  public:
    SimTask() = default;
    ~SimTask() = default;

    Status start(const Arguments& arguments) override;
    //! As the POSIX task, but a thread that has the turn gives it up while it sleeps and takes
    //! it back afterwards: a loop that polls with delay() (a socket driver's reconnect task)
    //! must not keep the tick, or any other task, from running meanwhile
    Status _delay(const Fw::TimeInterval& interval) override;
};

}  // namespace Wsts

#endif
