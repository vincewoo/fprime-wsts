/*
 * ioLib.h: the VxWorks API shim of fprime-wsts. Basic I/O is the host's: open(), close(),
 * read(), write() and ioctl() act on the host's files and devices, with target paths used
 * as written. This is the raw API of the kernel, below F Prime's Os::File: the file system
 * of a simulated deployment is mapped into the sandbox of the run only through Os::File
 * and its file system and directory (see "Files" in docs/reference.md). VxWorks-specific
 * ioctl codes (FIOSETOPTIONS, SIO_*) are not defined.
 */
#ifndef WSTS_VXWORKS_IOLIB_H
#define WSTS_VXWORKS_IOLIB_H

#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <vxWorks.h>

#endif
