module Components {

  @ Takes frames from the payload camera. The camera is driven through its vendor's SDK,
  @ which exists for the flight board only: unit tests of this component build a stub of
  @ the camera in its place (see CMakeLists.txt).
  passive component Imager {

    event port logOut
    text event port logTextOut
    telemetry port tlmOut
    time get port timeCaller
    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut

    @ Capture one frame
    sync command CAPTURE()

    @ A frame was captured
    event FrameCaptured(
      bytes: U32 @< Size of the frame
      level: U8 @< Mean pixel level
      exposure: U32 @< Exposure, in sensor ticks
      board: U8 @< Revision of the board the software was built for
    ) severity activity high format "Frame captured: {} bytes, mean level {}, exposure {} ticks, board revision {}"

    @ The camera reported an error
    event CameraError(code: I32) \
      severity warning high format "Camera error {}"

    telemetry FramesCaptured: U32

  }

}
