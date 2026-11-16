// ======================================================================
// \title  SuiteSandboxCwd.cpp
// \brief  The directory and file system tests of the framework run in the sandbox of the process
//
// Those tests name their files relative to the working directory and make some of them
// with the calls of the host. With the sandbox at the working directory of the process,
// the two are the same place.
// ======================================================================
#include <unistd.h>

#include <cstdlib>

namespace {
struct SuiteSandbox {
    SuiteSandbox() {
        char cwd[512];
        if (::getcwd(cwd, sizeof cwd) != nullptr) {
            (void)::setenv("WSTS_FILES_DIR", cwd, 1);
        }
    }
};

// Before main(), so that every test of the suite finds it set
const SuiteSandbox suiteSandbox;
}  // namespace
