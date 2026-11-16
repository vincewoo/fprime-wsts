// ======================================================================
// \title  SimFileTests.cpp
// \brief  The sandbox: where the paths of flight software land, and what is refused
//
// Every case makes a sandbox of its own under /tmp and points WSTS_FILES_DIR at it. The
// files of a case are left there for the system to clean up.
// ======================================================================
#include <gtest/gtest.h>

#include <sys/stat.h>
#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <set>
#include <string>

#include <Os/Directory.hpp>
#include <Os/File.hpp>
#include <Os/FileSystem.hpp>
#include <STest/Random/Random.hpp>
#include <Wsts/Os/SimPath.hpp>

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    STest::Random::seed();
    return RUN_ALL_TESTS();
}

class WithSandbox : public ::testing::Test {
  protected:
    void SetUp() override {
        char pattern[] = "/tmp/wsts-sandbox-XXXXXX";
        char* made = ::mkdtemp(pattern);
        ASSERT_NE(made, nullptr) << ::strerror(errno);
        this->sandboxPath = made;
        ::setenv("WSTS_FILES_DIR", this->sandboxPath.c_str(), 1);
        Wsts::SimPath::setWorkingDirectory("/");
    }

    void TearDown() override {
        ::unsetenv("WSTS_FILES_DIR");
        ::unsetenv("WSTS_DIR");
        Wsts::SimPath::setWorkingDirectory("/");
    }

    //! Where the host has what flight software named
    std::string host(const std::string& flightPath) const { return this->sandboxPath + flightPath; }

    bool hostExists(const std::string& flightPath) const {
        struct stat status = {};
        return ::stat(this->host(flightPath).c_str(), &status) == 0;
    }

    std::string sandboxPath;
};

// A write to a target path lands under the sandbox, with the mounts of the target made as
// directories
TEST_F(WithSandbox, AWriteToATargetPathLandsInTheSandbox) {
    Os::File file;
    const char* text = "hello";
    FwSizeType size = std::strlen(text);
    ASSERT_EQ(file.open("/ram0/logs/events.log", Os::File::OPEN_CREATE), Os::File::OP_OK);
    ASSERT_EQ(file.write(reinterpret_cast<const U8*>(text), size, Os::File::WAIT), Os::File::OP_OK);
    file.close();
    std::ifstream stream(this->host("/ram0/logs/events.log"));
    ASSERT_TRUE(stream.is_open());
    std::string line;
    ASSERT_TRUE(std::getline(stream, line));
    ASSERT_EQ(line, text);
}

// A read of what is not there says so
TEST_F(WithSandbox, AReadOfWhatIsNotThereSaysSo) {
    Os::File file;
    ASSERT_EQ(file.open("/tffs0/absent.dat", Os::File::OPEN_READ), Os::File::DOESNT_EXIST);
}

// Climbing above the root of the flight file system stays at its root, like a mount of
// the target: nothing leaves the sandbox
TEST_F(WithSandbox, ClimbingAboveTheRootStaysInTheSandbox) {
    Os::File file;
    ASSERT_EQ(file.open("/ram0/../escape.log", Os::File::OPEN_CREATE), Os::File::OP_OK);
    file.close();
    ASSERT_TRUE(this->hostExists("/escape.log"));
}

// A relative name lands at the working directory of flight software, which starts at the
// root of the sandbox
TEST_F(WithSandbox, ARelativeNameLandsAtTheWorkingDirectory) {
    Os::File file;
    ASSERT_EQ(file.open("PrmDb.dat", Os::File::OPEN_CREATE), Os::File::OP_OK);
    file.close();
    ASSERT_TRUE(this->hostExists("/PrmDb.dat"));
}

// The working directory is a path as flight software sees it, and relative names follow it
TEST_F(WithSandbox, TheWorkingDirectoryIsTheFlightOne) {
    char workingDirectory[256] = {0};
    ASSERT_EQ(Os::FileSystem::getWorkingDirectory(workingDirectory, sizeof workingDirectory),
              Os::FileSystem::OP_OK);
    ASSERT_STREQ(workingDirectory, "/");
    ASSERT_EQ(Os::FileSystem::createDirectory("/ram0/logs"), Os::FileSystem::OP_OK);
    ASSERT_EQ(Os::FileSystem::changeWorkingDirectory("/ram0/logs"), Os::FileSystem::OP_OK);
    ASSERT_EQ(Os::FileSystem::getWorkingDirectory(workingDirectory, sizeof workingDirectory),
              Os::FileSystem::OP_OK);
    ASSERT_STREQ(workingDirectory, "/ram0/logs");
    Os::File file;
    ASSERT_EQ(file.open("events.log", Os::File::OPEN_CREATE), Os::File::OP_OK);
    file.close();
    ASSERT_TRUE(this->hostExists("/ram0/logs/events.log"));
}

// A working directory that is not there, or is a file, is an error, and it stays what it
// was
TEST_F(WithSandbox, AWorkingDirectoryThatIsNotThereIsAnError) {
    ASSERT_EQ(Os::FileSystem::changeWorkingDirectory("/ram0/absent"), Os::FileSystem::DOESNT_EXIST);
    Os::File file;
    ASSERT_EQ(file.open("PrmDb.dat", Os::File::OPEN_CREATE), Os::File::OP_OK);
    file.close();
    ASSERT_TRUE(this->hostExists("/PrmDb.dat"));
    ASSERT_EQ(Os::FileSystem::changeWorkingDirectory("PrmDb.dat"), Os::FileSystem::NOT_DIR);
    char workingDirectory[256] = {0};
    ASSERT_EQ(Os::FileSystem::getWorkingDirectory(workingDirectory, sizeof workingDirectory),
              Os::FileSystem::OP_OK);
    ASSERT_STREQ(workingDirectory, "/");
}

// The operations of the file system stay in the sandbox
TEST_F(WithSandbox, TheFileSystemOperationsStayInTheSandbox) {
    ASSERT_EQ(Os::FileSystem::createDirectory("/ram0"), Os::FileSystem::OP_OK);
    ASSERT_TRUE(this->hostExists("/ram0"));
    ASSERT_EQ(Os::FileSystem::createDirectory("/ram0"), Os::FileSystem::OP_OK);
    ASSERT_EQ(Os::FileSystem::createDirectory("/ram0", true), Os::FileSystem::ALREADY_EXISTS);

    ASSERT_EQ(Os::FileSystem::touch("/ram0/a.dat"), Os::FileSystem::OP_OK);
    ASSERT_EQ(Os::FileSystem::getPathType("/ram0/a.dat"), Os::FileSystem::FILE);
    ASSERT_EQ(Os::FileSystem::getPathType("/ram0"), Os::FileSystem::DIRECTORY);
    ASSERT_EQ(Os::FileSystem::getPathType("/ram0/gone"), Os::FileSystem::NOT_EXIST);

    ASSERT_EQ(Os::FileSystem::rename("/ram0/a.dat", "/ram0/b.dat"), Os::FileSystem::OP_OK);
    ASSERT_FALSE(this->hostExists("/ram0/a.dat"));
    ASSERT_TRUE(this->hostExists("/ram0/b.dat"));

    ASSERT_EQ(Os::FileSystem::removeFile("/ram0/b.dat"), Os::FileSystem::OP_OK);
    ASSERT_FALSE(this->hostExists("/ram0/b.dat"));

    ASSERT_EQ(Os::FileSystem::removeDirectory("/ram0"), Os::FileSystem::OP_OK);
    ASSERT_FALSE(this->hostExists("/ram0"));
}

// The helpers of Os::FileSystem built on its files and directories stay in the sandbox
TEST_F(WithSandbox, TheFileHelpersStayInTheSandbox) {
    const char* text = "copy me";
    Os::File file;
    ASSERT_EQ(file.open("/ram0/a.dat", Os::File::OPEN_CREATE), Os::File::OP_OK);
    FwSizeType size = std::strlen(text);
    ASSERT_EQ(file.write(reinterpret_cast<const U8*>(text), size, Os::File::WAIT), Os::File::OP_OK);
    file.close();

    FwSizeType fileSize = 0;
    ASSERT_EQ(Os::FileSystem::getFileSize("/ram0/a.dat", fileSize), Os::FileSystem::OP_OK);
    ASSERT_EQ(fileSize, size);

    ASSERT_EQ(Os::FileSystem::copyFile("/ram0/a.dat", "/ram0/b.dat"), Os::FileSystem::OP_OK);
    ASSERT_TRUE(this->hostExists("/ram0/b.dat"));
    ASSERT_EQ(Os::FileSystem::appendFile("/ram0/a.dat", "/ram0/b.dat"), Os::FileSystem::OP_OK);
    ASSERT_EQ(Os::FileSystem::getFileSize("/ram0/b.dat", fileSize), Os::FileSystem::OP_OK);
    ASSERT_EQ(fileSize, 2 * static_cast<FwSizeType>(std::strlen(text)));

    FwSizeType totalBytes = 0, freeBytes = 0;
    ASSERT_EQ(Os::FileSystem::getFreeSpace("/ram0", totalBytes, freeBytes), Os::FileSystem::OP_OK);
    ASSERT_GT(totalBytes, static_cast<FwSizeType>(0));
}

// The directories of the sandbox can be read, and a creation of one that is there is fine
TEST_F(WithSandbox, TheDirectoriesOfTheSandboxCanBeRead) {
    Os::File file;
    for (const char* name : {"one.dat", "two.dat"}) {
        ASSERT_EQ(file.open((std::string("/ram0/") + name).c_str(), Os::File::OPEN_CREATE), Os::File::OP_OK);
        file.close();
    }
    Os::Directory directory;
    ASSERT_EQ(directory.open("/ram0", Os::Directory::READ), Os::Directory::OP_OK);
    std::set<std::string> names;
    char name[256];
    Os::Directory::Status status = Os::Directory::OP_OK;
    while ((status = directory.read(name, sizeof name)) == Os::Directory::OP_OK) {
        names.insert(name);
    }
    ASSERT_EQ(status, Os::Directory::NO_MORE_FILES);
    ASSERT_EQ(names, (std::set<std::string>{"one.dat", "two.dat"}));
    directory.close();
    ASSERT_EQ(directory.open("/ram0", Os::Directory::CREATE_IF_MISSING), Os::Directory::OP_OK);
    directory.close();
    ASSERT_EQ(directory.open("/ram0", Os::Directory::CREATE_EXCLUSIVE), Os::Directory::ALREADY_EXISTS);
    directory.close();
}

// WSTS_DIR is the sandbox when WSTS_FILES_DIR is not set, and the working directory of
// the process when neither is
TEST_F(WithSandbox, TheSandboxComesFromTheEnvironment) {
    ::unsetenv("WSTS_FILES_DIR");
    ::setenv("WSTS_DIR", this->sandboxPath.c_str(), 1);
    ASSERT_EQ(Wsts::SimPath::sandbox(), this->sandboxPath);
    Os::File file;
    ASSERT_EQ(file.open("from-wsts-dir.dat", Os::File::OPEN_CREATE), Os::File::OP_OK);
    file.close();
    ASSERT_TRUE(this->hostExists("/from-wsts-dir.dat"));

    ::unsetenv("WSTS_DIR");
    char cwd[512] = {0};
    ASSERT_NE(::getcwd(cwd, sizeof cwd), nullptr);
    ASSERT_EQ(Wsts::SimPath::sandbox(), std::string(cwd));
}
