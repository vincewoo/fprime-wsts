module SensorLink {

  @ Device manager for the serial temperature sensor
  instance sensor: SensorLib.SerialSensor base id SensorLinkConfig.BASE_ID + 0x0000

  @ Serial port the sensor is on
  instance uart: VxWorksDrv.VxWorksUartDriver base id SensorLinkConfig.BASE_ID + 0x1000 \
  {
    phase Fpp.ToCpp.Phases.startTasks """
    // The sensor is on the second on-board serial port
    if (!SensorLink::uart.open("/tyCo/1", state.sensorLink.baud,
                               VxWorksDrv::VxWorksUartDriver::NO_FLOW,
                               VxWorksDrv::VxWorksUartDriver::PARITY_NONE,
                               SensorLinkConfig::UART_BUFFER_SIZE)) {
        Fw::Logger::log("[ERROR] Failed to open the sensor UART\\n");
    }
    SensorLink::uart.start(SensorLinkConfig::UART_PRIORITY, SensorLinkConfig::UART_STACK_SIZE);
    """
    phase Fpp.ToCpp.Phases.stopTasks """
    SensorLink::uart.quitReadThread();
    """
    phase Fpp.ToCpp.Phases.freeThreads """
    (void)SensorLink::uart.join();
    """
  }

  @ Receive buffers for the UART
  instance bufferManager: Svc.BufferManager base id SensorLinkConfig.BASE_ID + 0x2000 \
  {
    phase Fpp.ToCpp.Phases.configObjects """
    Svc::BufferManager::BufferBins bins;
    """
    phase Fpp.ToCpp.Phases.configComponents """
    memset(&ConfigObjects::SensorLink_bufferManager::bins, 0, sizeof(ConfigObjects::SensorLink_bufferManager::bins));
    ConfigObjects::SensorLink_bufferManager::bins.bins[0].bufferSize = SensorLinkConfig::UART_BUFFER_SIZE;
    ConfigObjects::SensorLink_bufferManager::bins.bins[0].numBuffers = SensorLinkConfig::BUFFER_COUNT;
    SensorLink::bufferManager.setup(
        SensorLinkConfig::BUFFER_MANAGER_ID,
        0,
        SensorLink::Allocation::memAllocator(),
        ConfigObjects::SensorLink_bufferManager::bins
    );
    """
    phase Fpp.ToCpp.Phases.tearDownComponents """
    SensorLink::bufferManager.cleanup();
    """
  }

  topology Subtopology {

    instance sensor
    instance uart
    instance bufferManager

    connections Sensor {
      uart.ready                 -> sensor.drvConnected
      uart.$recv                 -> sensor.drvReceiveIn
      sensor.drvReceiveReturnOut -> uart.recvReturnIn
      sensor.drvSendOut          -> uart.$send
      uart.allocate              -> bufferManager.bufferGetCallee
      uart.deallocate            -> bufferManager.bufferSendIn
    }

    @ Input port for the UART's telemetry (1 Hz)
    port uartRun = uart.run

    @ Input port for the buffer manager's telemetry (1 Hz)
    port bufferManagerRun = bufferManager.schedIn

  }

}
