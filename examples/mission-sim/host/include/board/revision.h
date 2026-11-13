// The flight toolchain's sysroot has <board/revision.h> from the board support package; a
// workstation does not. This is the simulation's copy: host.cmake puts this directory on
// the include path of the simulated build.
#ifndef BOARD_REVISION_H
#define BOARD_REVISION_H

#define BOARD_REVISION 2

#endif
