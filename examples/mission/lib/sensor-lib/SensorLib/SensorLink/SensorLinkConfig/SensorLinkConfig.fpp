module SensorLinkConfig {

  @ Base ID of the SensorLink subtopology: its instances are offsets from it
  constant BASE_ID = 0x30000000

  @ Priority of the UART receive task
  constant UART_PRIORITY = 30

  @ Stack size of the UART receive task
  constant UART_STACK_SIZE = 64 * 1024

  @ Size of the UART receive buffers
  constant UART_BUFFER_SIZE = 256

  @ Number of receive buffers
  constant BUFFER_COUNT = 8

  @ Identifier of the subtopology's buffer manager
  constant BUFFER_MANAGER_ID = 300

}
