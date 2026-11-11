module SensorSim {

  @ The simulated sensor
  instance sensorSim: SensorSim.SerialSensorSim base id 0x70000000 \
    queue size 10 \
  {
    phase Fpp.ToCpp.Phases.configComponents """
    SensorSim::sensorSim.configure("$TEMP");
    """
  }

  @ Transmit buffers of the simulated sensor
  instance txBuffers: Svc.BufferManager base id 0x70001000 \
  {
    phase Fpp.ToCpp.Phases.configObjects """
    Svc::BufferManager::BufferBins bins;
    """
    phase Fpp.ToCpp.Phases.configComponents """
    memset(&ConfigObjects::SensorSim_txBuffers::bins, 0, sizeof(ConfigObjects::SensorSim_txBuffers::bins));
    ConfigObjects::SensorSim_txBuffers::bins.bins[0].bufferSize = SensorSim::TX_BUFFER_SIZE;
    ConfigObjects::SensorSim_txBuffers::bins.bins[0].numBuffers = SensorSim::TX_BUFFER_COUNT;
    SensorSim::txBuffers.setup(
        SensorSim::TX_BUFFER_MANAGER_ID,
        0,
        SensorSim::Allocation::memAllocator(),
        ConfigObjects::SensorSim_txBuffers::bins
    );
    """
    phase Fpp.ToCpp.Phases.tearDownComponents """
    SensorSim::txBuffers.cleanup();
    """
  }

  @ The sensor's end of the serial line: the device on the WSTS bus endpoint that the
  @ flight UART driver's surrogate is bound to. Which endpoint is said in the manifest.
  instance sensorPort: Wsts.BusByteStreamDevice base id 0x70002000

  @ Parameter database for the simulator. The flight deployment has none, and a component
  @ with parameters needs one to load them from.
  instance prmDb: Svc.PrmDb base id 0x70003000 \
    queue size 10 \
    stack size 64 * 1024 \
    priority 20 \
  {
    phase Fpp.ToCpp.Phases.configComponents """
    SensorSim::prmDb.configure("SensorSimPrmDb.dat");
    """
    phase Fpp.ToCpp.Phases.readParameters """
    SensorSim::prmDb.readParamFile();
    """
  }

  @ The simulated sensor with what it needs. Nothing here names a flight instance: the
  @ manifest connects it to the deployment it is used with.
  topology Simulator {

    instance sensorSim
    instance txBuffers
    instance sensorPort
    instance prmDb

    connections Parameters {
      sensorSim.prmGetOut -> prmDb.getPrm
      sensorSim.prmSetOut -> prmDb.setPrm
    }

    connections Device {
      sensorPort.ready               -> sensorSim.drvConnected
      sensorPort.$recv               -> sensorSim.drvReceiveIn
      sensorSim.drvReceiveReturnOut  -> sensorPort.recvReturnIn
      sensorSim.drvSendOut           -> sensorPort.$send
      sensorSim.bufferGetOut         -> txBuffers.bufferGetCallee
      sensorSim.bufferReturnOut      -> txBuffers.bufferSendIn
    }

    @ Schedules the simulator: one report per call
    port schedIn = sensorSim.schedIn

  }

}
