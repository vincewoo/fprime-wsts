// ======================================================================
// \title  DefaultSimFile.cpp
// \brief  Makes Wsts::SimFile, SimFileSystem and SimDirectory the file system of a
//         deployment that chooses Wsts_Os_File
//
// One implementation covers the three interfaces, as the framework's own DefaultFile.cpp
// does: Os_File is the only one of them a deployment is required to implement.
// ======================================================================
#include <Os/Delegate.hpp>
#include <Os/Directory.hpp>
#include <Os/File.hpp>
#include <Os/FileSystem.hpp>
#include <Wsts/Os/SimDirectory.hpp>
#include <Wsts/Os/SimFile.hpp>
#include <Wsts/Os/SimFileSystem.hpp>

namespace Os {
FileInterface* FileInterface::getDelegate(FileHandleStorage& aligned_new_memory, const FileInterface* to_copy) {
    return Os::Delegate::makeDelegate<FileInterface, Wsts::SimFile>(aligned_new_memory, to_copy);
}
FileSystemInterface* FileSystemInterface::getDelegate(FileSystemHandleStorage& aligned_new_memory) {
    return Os::Delegate::makeDelegate<FileSystemInterface, Wsts::SimFileSystem>(aligned_new_memory);
}
DirectoryInterface* DirectoryInterface::getDelegate(DirectoryHandleStorage& aligned_new_memory) {
    return Os::Delegate::makeDelegate<DirectoryInterface, Wsts::SimDirectory>(aligned_new_memory);
}
}  // namespace Os
