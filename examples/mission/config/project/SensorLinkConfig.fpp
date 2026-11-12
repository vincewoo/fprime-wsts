module SensorLinkConfig {

  @ Project override: SensorLink sits at 0x06000000 on this vehicle (library default 0x30000000)
  constant BASE_ID = MissionIds.SENSOR_LINK

  @ Priority of the UART receive task
  constant UART_PRIORITY = 31

  @ Stack size of the UART receive task
  constant UART_STACK_SIZE = 64 * 1024

  @ Size of the UART receive buffers
  constant UART_BUFFER_SIZE = 256

  @ Number of receive buffers
  constant BUFFER_COUNT = 8

  @ Identifier of the subtopology's buffer manager
  constant BUFFER_MANAGER_ID = 300

}
