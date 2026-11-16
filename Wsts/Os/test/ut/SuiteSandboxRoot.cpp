// ======================================================================
// \title  SuiteSandboxRoot.cpp
// \brief  The file tests of the framework run with the sandbox at the root
//
// The common file tests of the framework name files of the host directly (/tmp/fprime)
// and check them with the calls of the host. With the sandbox at the root the mapping is
// one-to-one, so the tests hold of this implementation what they hold of the POSIX one.
// The mapping itself is what SimFileTests.cpp is for.
// ======================================================================
#include <cstdlib>

namespace {
struct SuiteSandbox {
    SuiteSandbox() { (void)::setenv("WSTS_FILES_DIR", "/", 1); }
};

// Before main(), so that every test of the suite finds it set
const SuiteSandbox suiteSandbox;
}  // namespace
