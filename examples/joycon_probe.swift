// macOS-only capability probe for game controllers (Joy-Con included). Never drives hardware.
//
//   swiftc -O examples/joycon_probe.swift -o joycon_probe
//   ./joycon_probe --seconds 20            # JSON lines on stdout, one event per line
//   ./joycon_probe --clock-check           # print one host timestamp and exit
//
// What it records:
//   * which controllers GameController reports, and which input profiles each one has
//     (extended gamepad / micro gamepad / physical input profile elements)
//   * for motion: whether the controller has a motion sensor object at all, whether the
//     sensors must be switched on by hand, whether attitude/rotation rate and
//     gravity/user acceleration are available (hasAttitude / hasRotationRate separately;
//     the combined property is deprecated)
//   * every input change with the host time in the same clock as Python's
//     time.monotonic_ns() on macOS (CLOCK_UPTIME_RAW, nanoseconds, integer)
//
// What it does NOT establish: that a Joy-Con paired to this Mac exposes a motion sensor. That
// is exactly what the run is for. At the time this file was written no controller was
// connected to the build machine (see docs/JOYCON.md).
import Foundation
import GameController

setvbuf(stdout, nil, _IOLBF, 0)   // one line, one write: safe to pipe into the recorder

func hostNs() -> UInt64 { clock_gettime_nsec_np(CLOCK_UPTIME_RAW) }

func emit(_ event: String, _ fields: [String: Any] = [:]) {
    var obj = fields
    obj["schema"] = "motion-io-lab/joycon-probe-v1"
    obj["event"] = event
    obj["clock_domain"] = "host_monotonic"
    obj["host_monotonic_ns"] = hostNs()
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
        fflush(stdout)
    }
}

var args = Array(CommandLine.arguments.dropFirst())
if args.contains("--clock-check") {
    emit("clock_check", [:])
    exit(0)
}
var seconds = 20.0
if let i = args.firstIndex(of: "--seconds"), i + 1 < args.count, let s = Double(args[i + 1]), s > 0, s <= 3600 {
    seconds = s
}

// A command-line process is not the foreground app; without this, input events may not arrive.
GCController.shouldMonitorBackgroundEvents = true

func describe(_ c: GCController) -> [String: Any] {
    var d: [String: Any] = [
        "name": c.vendorName ?? "unknown",
        "category": c.productCategory,
        "has_extended_gamepad": c.extendedGamepad != nil,
        "has_micro_gamepad": c.microGamepad != nil,
        "has_motion": c.motion != nil,
        "is_attached_to_device": c.isAttachedToDevice,
    ]
    let p = c.physicalInputProfile
    d["button_names"] = p.buttons.keys.sorted()
    d["axis_names"] = p.axes.keys.sorted()
    d["dpad_names"] = p.dpads.keys.sorted()
    if let m = c.motion {
        d["motion_sensors_require_manual_activation"] = m.sensorsRequireManualActivation
        d["motion_sensors_active"] = m.sensorsActive
        d["motion_has_attitude"] = m.hasAttitude
        d["motion_has_rotation_rate"] = m.hasRotationRate
        d["motion_has_gravity_and_user_acceleration"] = m.hasGravityAndUserAcceleration
    }
    return d
}

func attach(_ c: GCController) {
    emit("connected", describe(c))
    c.physicalInputProfile.valueDidChangeHandler = { profile, element in
        var f: [String: Any] = ["element": element.localizedName ?? "?"]
        if let b = element as? GCControllerButtonInput {
            f["kind"] = "button"; f["value"] = b.value; f["pressed"] = b.isPressed
        } else if let a = element as? GCControllerAxisInput {
            f["kind"] = "axis"; f["value"] = a.value
        } else if let d = element as? GCControllerDirectionPad {
            f["kind"] = "dpad"; f["x"] = d.xAxis.value; f["y"] = d.yAxis.value
        } else {
            f["kind"] = "other"
        }
        emit("input", f)
    }
    if let m = c.motion {
        if m.sensorsRequireManualActivation { m.sensorsActive = true }
        m.valueChangedHandler = { motion in
            var f: [String: Any] = [:]
            if motion.hasAttitude {
                let q = motion.attitude
                f["quaternion_xyzw"] = [q.x, q.y, q.z, q.w]
            }
            if motion.hasRotationRate {
                let r = motion.rotationRate
                f["rotation_rate_xyz"] = [r.x, r.y, r.z]
            }
            if motion.hasGravityAndUserAcceleration {
                let g = motion.gravity, a = motion.userAcceleration
                f["gravity_xyz"] = [g.x, g.y, g.z]
                f["user_acceleration_xyz"] = [a.x, a.y, a.z]
            }
            emit("motion", f)
        }
    }
}

let center = NotificationCenter.default
let connectObserver = center.addObserver(forName: .GCControllerDidConnect, object: nil, queue: .main) { note in
    if let c = note.object as? GCController { attach(c) }
}
let disconnectObserver = center.addObserver(forName: .GCControllerDidDisconnect, object: nil, queue: .main) { note in
    if let c = note.object as? GCController { emit("disconnected", ["name": c.vendorName ?? "unknown"]) }
}
_ = (connectObserver, disconnectObserver)

for c in GCController.controllers() { attach(c) }
emit("waiting", ["controllers_at_start": GCController.controllers().count, "seconds": seconds,
                 "note": "pair in macOS Bluetooth settings first; motion may not be exposed"])
DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
    emit("done", ["controllers_at_end": GCController.controllers().count])
    exit(0)
}
RunLoop.main.run()
