####
# CMake the simulation needs around the flight modules (`cmake.include` in the manifest).
# Included in the generated project after every flight, library and simulation module has
# been added, and before the deployment.
####

# SensorSim/GyroSim names this dependency as the project that first used it did
add_library(Flight_SerialSensor ALIAS SensorLib_SerialSensor)
