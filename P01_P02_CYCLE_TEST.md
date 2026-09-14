# P01 / P02 temporary cycle test settings

Applied for the user's cycle trial on 2026-09-14.

## P01 timing

- All UART packets share a minimum 50 ms idle interval after the previous packet flush, increased from 10 ms.
- P01 axis start gap is 0.05 seconds. These are minimum delays, not exact simultaneous start guarantees; command acknowledgement waits also apply.
- Relative movement commands are still sent once. Missing acknowledgements do not cause automatic movement retries.
- Recent logs: 00:14 all three axes completed; 00:15 E0 start acknowledgement and E1 completion were missing; 00:16 E1 reverse acknowledgement was missing.
- The longer interval is a comparison setting, not a verified fix for electrical RX errors or overlapping unsolicited completion responses. Earlier UART frame errors continued while idle.

## P02 camera-only gate

- Current installation setting: p02.inspection_mode = CAMERA_CHECK.
- The default for configurations without this field remains AI.
- REAL CAMERA_CHECK passes only while connected and a recent video frame is available. Missing, stale or disconnected video blocks the step.
- Successful trial result: CAMERA_OK, no AI score. The UI and log explicitly show AI was not evaluated.
- The vision_ng fault-injection scenario still fails intentionally.
- To end the trial, choose AI inspection in SETTING P02. AI inference is not implemented yet, so that mode continues to block REAL inspection until integration.
- No sensor, robot, press or relay readiness conditions are bypassed by this setting.

Validation: Python regression tests and browser logic tests use simulated devices. Hardware motor cycle validation was not performed during this change.
