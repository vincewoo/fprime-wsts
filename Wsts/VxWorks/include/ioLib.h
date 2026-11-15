/*
 * ioLib.h: the VxWorks API shim of fprime-wsts. Basic I/O is the host's: open(), close(),
 * read(), write() and ioctl() act on the host's files and devices, with target paths used
 * as written. VxWorks-specific ioctl codes (FIOSETOPTIONS, SIO_*) are not defined.
 */
#ifndef WSTS_VXWORKS_IOLIB_H
#define WSTS_VXWORKS_IOLIB_H

#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <vxWorks.h>

#endif
