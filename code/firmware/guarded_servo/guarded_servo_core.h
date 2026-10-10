// guarded_servo_core.h -- protocol v2 state machine, free of Arduino headers.
//
// The sketch (guarded_servo.ino) and the host-side test harness
// (tests/firmware_host/harness.cpp) both include this file, so the logic that
// the tests exercise is the logic that is compiled for the board.
//
// Safety posture:
//   * boots DISARMED, OE held HIGH (outputs disabled), HARDWARE_VERIFIED=false
//   * a command is only a request; the device answers REJECTED /
//     ACCEPTED_NOT_EXECUTED / EXECUTED, and "applied" is NOT a measured position
//   * the mechanical DC stop is independent of this software
//
// Protocol v2 (ASCII lines, '\n' terminated, <= 95 printable characters):
//   HELLO                       -> HELLO proto=2 fw=... epoch=N ...   (new epoch)
//   ARM <epoch>                 -> ACCEPTED_NOT_EXECUTED ARM | REJECTED ARM reason=...
//   SET <epoch> <seq> <us> <valid_for_ms>
//                               -> ACCEPTED_NOT_EXECUTED SET seq=.. | REJECTED SET reason=..
//                                  later: EXECUTED SET seq=.. applied_us=.. mcu_ms=..
//   HEARTBEAT <epoch>           -> ACCEPTED_NOT_EXECUTED HEARTBEAT
//   STATUS | DISARM | ESTOP | CLEAR
// A SET lease (valid_for_ms) and the ARM grace period are judged on the
// receiver's clock; when a lease runs out the device stops and latches a fault.
// Rejections never latch a fault and never extend a lease.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

namespace gs {

#define GS_FW_VERSION "0.1.0"
constexpr uint8_t PROTO_VERSION = 2;

constexpr uint8_t PCA_ADDR = 0x40;
constexpr uint16_t PULSE_MIN = 1450, PULSE_MAX = 1550, PULSE_CENTER = 1500;
constexpr uint32_t GRACE_MS = 300;        // ARM -> first SET must arrive within this
constexpr uint32_t MIN_VALID_MS = 20, MAX_VALID_MS = 500;
constexpr uint32_t TICK_MS = 10;
constexpr uint16_t MAX_STEP_US = 5;       // slew limit: 5 us / 10 ms = 500 us/s
constexpr float PWM_HZ = 50.0f;
// Datasheet nominal oscillator; calibrate actual pulse timing before widening travel.
constexpr float OSC_HZ = 25000000.0f;
constexpr size_t LINE_CAP = 96;

// PCA9685 register addresses (NXP PCA9685 data sheet Rev. 4, section 7.3 / Table 4)
constexpr uint8_t REG_MODE1 = 0x00;
constexpr uint8_t REG_LED0_ON_L = 0x06;   // LED0_ON_L, LED0_ON_H, LED0_OFF_L, LED0_OFF_H
constexpr uint8_t REG_ALL_LED_OFF_H = 0xFD;
constexpr uint8_t REG_PRESCALE = 0xFE;

struct Hal {
  virtual uint32_t millis() = 0;
  virtual void delay_ms(uint32_t ms) = 0;
  // Write n bytes starting at register `reg` (auto-increment). true on ACK.
  virtual bool i2c_write(uint8_t reg, const uint8_t* data, size_t n) = 0;
  virtual void oe_write(bool high) = 0;
  virtual void out_line(const char* s) = 0;
  virtual uint32_t random32() = 0;
  virtual ~Hal() {}
};

class Core {
 public:
  Core(Hal* hal, const char* board, bool hardware_verified)
      : hal_(hal), board_(board), hw_verified_(hardware_verified) {}

  // Call once at boot, after the pins are set. OE must already be HIGH.
  void begin() {
    hal_->oe_write(true);
    fault_ = !initializePWM();
    armed_ = false;
    output_enabled_ = false;
    epoch_ = 0;
    hal_->out_line("BOOT DISARMED; commanded/applied pulse is NOT measured position");
  }

  // Feed one received byte.
  void feed(char c) {
    if (c == '\r') return;
    if (c == '\n') {
      if (discarding_) {
        discarding_ = false;
        used_ = 0;
        reject("LINE", "bad_line", false, 0);
        return;
      }
      line_[used_] = '\0';
      size_t n = used_;
      used_ = 0;
      if (n) handle(line_);
      return;
    }
    if (discarding_) return;
    if (c >= 32 && c <= 126 && used_ < LINE_CAP - 1) {
      line_[used_++] = c;
      return;
    }
    // Overflow or a non-printable byte: drop the rest of the line, stop once.
    discarding_ = true;
    used_ = 0;
    if (armed_) stopOutput("bad_line", true);
  }

  // Call as often as possible (every loop iteration).
  void tick() {
    uint32_t now = hal_->millis();
    if (armed_ && leaseExpired(now)) {
      stopOutput("lease_expired", true);
      return;
    }
    if (armed_ && output_enabled_ && uint32_t(now - last_tick_) >= TICK_MS) {
      last_tick_ = now;
      int d = int(target_) - int(applied_);
      if (d > int(MAX_STEP_US)) d = MAX_STEP_US;
      if (d < -int(MAX_STEP_US)) d = -int(MAX_STEP_US);
      applied_ = uint16_t(int(applied_) + d);
      int ticks = pulseToTicks(applied_);
      if (ticks != written_ticks_) {
        if (!writeChannel(ticks)) {
          stopOutput("i2c", true);
          return;
        }
      }
      if (pending_seq_) {
        char b[96];
        snprintf(b, sizeof b, "EXECUTED SET seq=%lu applied_us=%u mcu_ms=%lu",
                 (unsigned long)pending_seq_, (unsigned)applied_, (unsigned long)now);
        hal_->out_line(b);
        pending_seq_ = 0;
      }
    }
  }

  // Read-only views for tests.
  bool armed() const { return armed_; }
  bool fault() const { return fault_; }
  bool outputEnabled() const { return output_enabled_; }
  uint32_t epoch() const { return epoch_; }
  uint16_t applied() const { return applied_; }
  uint16_t target() const { return target_; }

  static int pulseToTicks(uint16_t us) {
    return int(lroundf(us * PWM_HZ * 4096.0f / 1000000.0f));
  }

 private:
  Hal* hal_;
  const char* board_;
  bool hw_verified_;
  char line_[LINE_CAP];
  size_t used_ = 0;
  bool discarding_ = false;
  bool armed_ = false, fault_ = true, output_enabled_ = false;
  uint32_t epoch_ = 0, last_seq_ = 0, pending_seq_ = 0;
  uint32_t lease_start_ = 0, lease_len_ = 0, last_tick_ = 0;
  uint16_t applied_ = PULSE_CENTER, target_ = PULSE_CENTER;
  int written_ticks_ = -1;
  char last_reject_[24] = "none";
  char last_stop_[24] = "none";

  bool leaseExpired(uint32_t now) const { return uint32_t(now - lease_start_) > lease_len_; }

  bool reg(uint8_t r, uint8_t v) { return hal_->i2c_write(r, &v, 1); }

  // ALL_LED_OFF_H bit 4 = ALL_LED full OFF (data sheet Table 8). This loads bit 4
  // into every LEDn_OFF_H, i.e. forces all 16 outputs off.
  bool allOff() {
    written_ticks_ = -1;
    return reg(REG_ALL_LED_OFF_H, 0x10);
  }

  // Channel 0 only. ON=0, OFF=ticks. All four registers go in one transaction
  // because the outputs only change on the last ACK (data sheet 7.3.3). The OFF_H
  // write also clears channel 0's own full-OFF bit; channels 1..15 stay full-OFF.
  // (Writing ALL_LED_OFF_H=0 here would clear the full-OFF bit of every channel;
  // this code deliberately does not, see docs/DESIGN.md.)
  bool writeChannel(int ticks) {
    if (ticks <= 0 || ticks >= 4095) return false;
    uint8_t d[4] = {0, 0, uint8_t(ticks & 0xFF), uint8_t((ticks >> 8) & 0x0F)};
    if (!hal_->i2c_write(REG_LED0_ON_L, d, 4)) return false;
    written_ticks_ = ticks;
    return true;
  }

  bool initializePWM() {
    if (!reg(REG_MODE1, 0x10)) return false;  // SLEEP
    uint8_t pre = uint8_t(lroundf(OSC_HZ / (4096.0f * PWM_HZ)) - 1);
    if (!reg(REG_PRESCALE, pre)) return false;
    if (!reg(REG_MODE1, 0x20)) return false;  // auto-increment, awake, internal clock
    hal_->delay_ms(5);
    return allOff();
  }

  void stopOutput(const char* reason, bool latch) {
    hal_->oe_write(true);
    output_enabled_ = false;
    armed_ = false;
    epoch_ = 0;            // any stop ends the session: re-arming needs a fresh HELLO
    pending_seq_ = 0;
    if (!allOff()) fault_ = true;
    if (latch) fault_ = true;
    copy(last_stop_, reason);
    char b[48];
    snprintf(b, sizeof b, "STOP %s", reason);
    hal_->out_line(b);
  }

  static void copy(char* dst, const char* src) {
    strncpy(dst, src, 23);
    dst[23] = '\0';
  }

  void reject(const char* cmd, const char* reason, bool has_seq, uint32_t seq) {
    copy(last_reject_, reason);
    char b[96];
    if (has_seq)
      snprintf(b, sizeof b, "REJECTED %s reason=%s seq=%lu", cmd, reason, (unsigned long)seq);
    else
      snprintf(b, sizeof b, "REJECTED %s reason=%s", cmd, reason);
    hal_->out_line(b);
  }

  // Strict unsigned decimal: 1..10 digits, no sign, no spaces, fits in uint32.
  static bool parseU32(const char* s, uint32_t* out) {
    size_t n = strlen(s);
    if (n == 0 || n > 10) return false;
    uint64_t v = 0;
    for (size_t i = 0; i < n; ++i) {
      if (s[i] < '0' || s[i] > '9') return false;
      v = v * 10 + uint64_t(s[i] - '0');
    }
    if (v > 0xFFFFFFFFull) return false;
    *out = uint32_t(v);
    return true;
  }

  void hello() {
    if (armed_) stopOutput("hello", false);
    uint32_t e = hal_->random32();
    if (e == 0 || e == last_epoch_issued_) e = last_epoch_issued_ + 1;
    if (e == 0) e = 1;
    last_epoch_issued_ = e;
    epoch_ = e;
    last_seq_ = 0;
    char b[200];
    snprintf(b, sizeof b,
             "HELLO proto=%u fw=%s board=%s min_us=%u max_us=%u center_us=%u step_us=%u "
             "tick_ms=%lu grace_ms=%lu max_valid_ms=%lu epoch=%lu hw_verified=%d fault=%d",
             unsigned(PROTO_VERSION), GS_FW_VERSION, board_, unsigned(PULSE_MIN),
             unsigned(PULSE_MAX), unsigned(PULSE_CENTER), unsigned(MAX_STEP_US),
             (unsigned long)TICK_MS, (unsigned long)GRACE_MS, (unsigned long)MAX_VALID_MS,
             (unsigned long)epoch_, hw_verified_ ? 1 : 0, fault_ ? 1 : 0);
    hal_->out_line(b);
  }

  void status() {
    uint32_t now = hal_->millis();
    long left = 0;
    if (armed_) {
      uint32_t used = uint32_t(now - lease_start_);
      left = used > lease_len_ ? 0 : long(lease_len_ - used);
    }
    char b[200];
    snprintf(b, sizeof b,
             "STATUS armed=%d fault=%d enabled=%d epoch=%lu commanded_us=%u applied_us=%u "
             "seq=%lu lease_left_ms=%ld last_reject=%s last_stop=%s mcu_ms=%lu",
             armed_ ? 1 : 0, fault_ ? 1 : 0, output_enabled_ ? 1 : 0, (unsigned long)epoch_,
             unsigned(target_), unsigned(applied_), (unsigned long)last_seq_, left, last_reject_,
             last_stop_, (unsigned long)now);
    hal_->out_line(b);
  }

  void handle(char* line) {
    char* tok[6];
    int argc = 0;
    char* p = line;
    for (;;) {
      if (argc == 6) {  // a seventh token (or a trailing separator after the sixth)
        reject("LINE", "too_many_tokens", false, 0);
        return;
      }
      tok[argc++] = p;
      char* sp = strchr(p, ' ');
      if (!sp) break;
      *sp = '\0';
      p = sp + 1;
    }
    for (int i = 0; i < argc; ++i)
      if (tok[i][0] == '\0') {
        reject("LINE", "empty_token", false, 0);
        return;
      }
    const char* cmd = tok[0];
    uint32_t now = hal_->millis();

    if (strcmp(cmd, "HELLO") == 0 && argc == 1) return hello();
    if (strcmp(cmd, "STATUS") == 0 && argc == 1) return status();
    if (strcmp(cmd, "ESTOP") == 0 && argc == 1) return stopOutput("estop", true);
    if (strcmp(cmd, "DISARM") == 0 && argc == 1) return stopOutput("disarm", false);
    if (strcmp(cmd, "CLEAR") == 0 && argc == 1) {
      if (armed_) return reject("CLEAR", "armed", false, 0);
      if (!initializePWM()) return stopOutput("i2c", true);
      fault_ = false;
      hal_->out_line("EXECUTED CLEAR");
      return;
    }

    if (strcmp(cmd, "ARM") == 0) {
      uint32_t e;
      if (argc != 2 || !parseU32(tok[1], &e)) return reject("ARM", "parse", false, 0);
      if (!hw_verified_) return reject("ARM", "hardware_verified_false", false, 0);
      if (epoch_ == 0 || e != epoch_) return reject("ARM", "epoch", false, 0);
      if (fault_) return reject("ARM", "fault", false, 0);
      if (armed_) {
        hal_->out_line("ACCEPTED_NOT_EXECUTED ARM dup=1");
        return;
      }
      armed_ = true;
      lease_start_ = now;
      lease_len_ = GRACE_MS;
      target_ = applied_;  // output stays disabled until the first valid SET
      char b[64];
      snprintf(b, sizeof b, "ACCEPTED_NOT_EXECUTED ARM epoch=%lu", (unsigned long)epoch_);
      hal_->out_line(b);
      return;
    }

    if (strcmp(cmd, "HEARTBEAT") == 0) {
      uint32_t e;
      if (argc != 2 || !parseU32(tok[1], &e)) return reject("HEARTBEAT", "parse", false, 0);
      if (epoch_ == 0 || e != epoch_) return reject("HEARTBEAT", "epoch", false, 0);
      if (!armed_) return reject("HEARTBEAT", "disarmed", false, 0);
      if (leaseExpired(now)) {
        stopOutput("lease_expired", true);
        return reject("HEARTBEAT", "lease_expired", false, 0);
      }
      lease_start_ = now;  // extend by the length of the last grant
      hal_->out_line("ACCEPTED_NOT_EXECUTED HEARTBEAT");
      return;
    }

    if (strcmp(cmd, "SET") == 0) {
      uint32_t e, seq, us, valid;
      if (argc != 5 || !parseU32(tok[1], &e) || !parseU32(tok[2], &seq) ||
          !parseU32(tok[3], &us) || !parseU32(tok[4], &valid))
        return reject("SET", "parse", false, 0);
      if (epoch_ == 0 || e != epoch_) return reject("SET", "epoch", true, seq);
      if (!armed_) return reject("SET", "disarmed", true, seq);
      if (fault_) return reject("SET", "fault", true, seq);
      if (leaseExpired(now)) {
        stopOutput("lease_expired", true);
        return reject("SET", "lease_expired", true, seq);
      }
      if (seq == 0) return reject("SET", "seq_zero", true, seq);
      if (seq == last_seq_) {  // duplicate: harmless acknowledgement, nothing changes
        char b[64];
        snprintf(b, sizeof b, "ACCEPTED_NOT_EXECUTED SET seq=%lu dup=1", (unsigned long)seq);
        hal_->out_line(b);
        return;
      }
      if (seq < last_seq_) return reject("SET", "seq_old", true, seq);
      if (valid < MIN_VALID_MS || valid > MAX_VALID_MS)
        return reject("SET", "valid_for", true, seq);
      if (us < PULSE_MIN || us > PULSE_MAX) return reject("SET", "range", true, seq);
      last_seq_ = seq;
      target_ = uint16_t(us);
      lease_start_ = now;
      lease_len_ = valid;
      pending_seq_ = seq;
      if (!output_enabled_) {
        if (!writeChannel(pulseToTicks(applied_))) return stopOutput("i2c", true);
        hal_->oe_write(false);
        output_enabled_ = true;
        last_tick_ = now;
      }
      char b[80];
      snprintf(b, sizeof b, "ACCEPTED_NOT_EXECUTED SET seq=%lu target_us=%u", (unsigned long)seq,
               unsigned(target_));
      hal_->out_line(b);
      return;
    }

    reject("UNKNOWN", "unknown_command", false, 0);
  }

  uint32_t last_epoch_issued_ = 0;
};

}  // namespace gs
