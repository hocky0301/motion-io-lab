# motion-io-lab demo report

**SYNTHETIC.** Virtual device, synthetic plant, rendered video. Not a hardware result.

Detector noise floor (a marker that does not move): SD 0.046 deg, so two independent readings differ by about 0.065 deg RMS.

## Per motion (5 replays of one recording)

| motion | lag cmd->obs ms | cmd vs applied RMS deg | applied vs observed RMS deg (lag aligned) | replay vs recording RMS deg: commanded / applied / observed | repeat SD deg (observed) | repeat SD minus noise floor | invalid frames | interpolated share |
|---|---|---|---|---|---|---|---|---|
| sweep | 130 | 0.170 | 0.243 | 0.092 / 0.017 / 0.061 | 0.048 | 0.013 | 30/419 | 13 % |
| step_hold | 125 | 0.314 | 0.282 | 0.115 / 0.033 / 0.062 | 0.051 | 0.021 | 15/298 | 9 % |
| look_wait_nod | 116 | 0.162 | 0.175 | 0.093 / 0.070 / 0.080 | 0.050 | 0.020 | 14/420 | 6 % |

## Reading the three stages

| motion | replay vs recording: commanded / applied / observed | same, after the first 0.5 s | replay vs replay (observed) | commanded, sample by sample | send lateness p95 ms |
|---|---|---|---|---|---|
| sweep | 0.092 / 0.017 / 0.061 | 0.089 / 0.017 / 0.062 | 0.053 | 0.000 | 0.40 |
| step_hold | 0.115 / 0.033 / 0.062 | 0.108 / 0.028 / 0.059 | 0.056 | 0.000 | 0.42 |
| look_wait_nod | 0.093 / 0.070 / 0.080 | 0.092 / 0.067 / 0.083 | 0.056 | 0.000 | 0.40 |

The commanded stage is measured on a 10 ms grid, so it reflects how far the send times wander (about 1 ms) against the grid step; compared sample by sample the replay sends the same values. Observed-stage differences close to the noise floor say nothing about the servo.

## Lag estimate against the lag a noiseless servo would give

| motion | commanded->observed estimated / expected ms | applied->observed estimated / expected ms |
|---|---|---|
| sweep | 130.4 / 130.8 | 113.4 / 113.8 |
| step_hold | 124.9 / 125.2 | 88.9 / 88.9 |
| look_wait_nod | 115.7 / 117.3 | 98.9 / 100.5 |

## Pacing (30 s, 20 ms period)

| pacing | p95 lateness ms | max ms | drift ppm |
|---|---|---|---|
| deadline | 0.388 | 4.238 | -0.3 |
| relative | 207.203 | 219.427 | 6914.8 |

## Failure cases

(`fault` and `armed` are read at the end of each case. For the re-plug cases the device state when the new connection found it is listed too: after the lease ran out the device had already stopped and latched by itself, so only the quick case shows the epoch check refusing old lines on an armed device.)

- `replayed_arm_and_set_after_quick_reconnect`: replies ['REJECTED ARM reason=epoch', 'REJECTED SET reason=epoch seq=3', 'REJECTED SET reason=epoch seq=4', 'REJECTED SET reason=epoch seq=5']; fault=0 armed=0 (before HELLO: fault=0 armed=1)
- `replayed_arm_and_set_after_reconnect`: replies ['REJECTED ARM reason=epoch', 'REJECTED SET reason=epoch seq=3', 'REJECTED SET reason=epoch seq=4', 'REJECTED SET reason=epoch seq=5']; fault=1 armed=0 (before HELLO: fault=1 armed=0)
- `replayed_arm_and_set_after_reconnect_device_reboot`: replies ['REJECTED ARM reason=epoch', 'REJECTED SET reason=epoch seq=3', 'REJECTED SET reason=epoch seq=4', 'REJECTED SET reason=epoch seq=5']; fault=0 armed=0 (before HELLO: fault=0 armed=0)
- `duplicate_seq_with_different_value`: replies ['ACCEPTED_NOT_EXECUTED SET seq=1 dup=1']; fault=0 armed=1
- `pulse_out_of_range_then_valid`: replies ['REJECTED SET reason=range seq=1', 'ACCEPTED_NOT_EXECUTED SET seq=2 target_us=1520']; fault=0 armed=1
- `negative_sequence_number`: replies ['REJECTED SET reason=parse', 'ACCEPTED_NOT_EXECUTED SET seq=1 target_us=1500']; fault=0 armed=1
- `lease_expired_then_set`: replies ['STOP lease_expired', 'REJECTED SET reason=epoch seq=2']; fault=1 armed=0
