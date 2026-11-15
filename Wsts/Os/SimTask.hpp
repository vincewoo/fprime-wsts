// ======================================================================
// \title  SimTask.hpp
// \brief  The Os::Task of a simulated deployment: a POSIX task that takes its turn
//
// Chosen by a simulated deployment in place of Os_Task_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_Task). In real time it is that implementation. In stepped time, where flight
// code runs on one thread at a time, a task takes the turn before its routine runs: what
// a task does before it first waits for a message (a rate group announcing itself) then
// happens in the order the tasks were started, in every run.
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
};

}  // namespace Wsts

#endif
