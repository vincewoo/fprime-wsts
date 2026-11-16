// ======================================================================
// \title  SimFile.hpp
// \brief  The Os::File of a simulated deployment: every path inside the sandbox
//
// Chosen by a simulated deployment in place of Os_File_Posix (CHOOSES_IMPLEMENTATIONS
// Wsts_Os_File). What flight software names is a target path or a relative name; open()
// maps it into the sandbox of the run (SimPath.hpp) and hands the mapped path to the
// POSIX implementation it extends. A path that would land outside the sandbox is refused
// with OUTSIDE_SANDBOX, so no run touches the host outside its own directory.
// ======================================================================
#ifndef WSTS_OS_SIMFILE_HPP
#define WSTS_OS_SIMFILE_HPP

#include <Os/Posix/File.hpp>

namespace Wsts {

class SimFile : public Os::Posix::File::PosixFile {
  public:
    SimFile() = default;

    //! \brief copy constructor: the Os::File wrapper copy-constructs its delegate
    //!
    SimFile(const SimFile& other) : Os::Posix::File::PosixFile(other) {}

    ~SimFile() override = default;

    //! \brief open file with supplied path and mode
    //!
    //! The path is mapped into the sandbox first. In a mode that writes, the directories
    //! in front of the file are made if they are missing: the target had /ram0 and
    //! /tffs0 mounted, and the sandbox has what the run needs instead.
    //!
    //! \param path: c-string of path to open, as flight software names it
    //! \param mode: file operation mode
    //! \param overwrite: overwrite existing file on create
    //! \return: status of the open
    //!
    Status open(const char* path, Mode mode, OverwriteType overwrite) override;
};

}  // namespace Wsts

#endif
