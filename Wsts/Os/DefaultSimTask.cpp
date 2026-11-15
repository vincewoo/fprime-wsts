// ======================================================================
// \title  DefaultSimTask.cpp
// \brief  Makes Wsts::SimTask the Os::Task of a deployment that chooses Wsts_Os_Task
// ======================================================================
#include <Os/Delegate.hpp>
#include <Os/Task.hpp>
#include <Wsts/Os/SimTask.hpp>

namespace Os {
TaskInterface* TaskInterface::getDelegate(TaskHandleStorage& aligned_new_memory) {
    return Os::Delegate::makeDelegate<TaskInterface, Wsts::SimTask>(aligned_new_memory);
}
}  // namespace Os
