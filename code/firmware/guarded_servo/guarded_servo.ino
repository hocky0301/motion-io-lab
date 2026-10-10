/* Reference firmware, protocol v2. NOT flashed or run on a board yet
 * (see docs/FIRMWARE_BUILD.md for what was compiled and how).
 *
 * Default: no actuation. Boots DISARMED with OE held HIGH. HARDWARE_VERIFIED is false:
 * ARM is refused until the checklist in docs/HARDWARE_RUN.md is finished and the
 * constant is changed by hand. The mechanical DC stop is independent of this software; never auto-rearm.
 *
 * OE needs a physical pull-up to 3.3 V: GPIO setup cannot protect the time between
 * reset and setup(). Generic breakouts may pull OE low; check yours.
 *
 * Board selection (one of):
 *   -DBOARD_XIAO_ESP32S3   SDA D4/GPIO5, SCL D5/GPIO6, OE D0/GPIO1
 *                          https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/
 *   -DBOARD_ATOMS3_LITE    NOT AVAILABLE: see docs/BOARDS.md for why it is a TODO
 * With no -D flag the XIAO map is used (this is what arduino-cli compiles by FQBN
 * esp32:esp32:XIAO_ESP32S3, which defines ARDUINO_XIAO_ESP32S3).
 */
#include <Arduino.h>
#include <Wire.h>
#include "guarded_servo_core.h"

#if defined(BOARD_ATOMS3_LITE)
#error "ATOMS3 Lite pin map is not verified. See docs/BOARDS.md. Do not copy the XIAO map."
#else  // BOARD_XIAO_ESP32S3 (default)
#define GS_BOARD_NAME "XIAO_ESP32S3"
constexpr int SDA_PIN = 5, SCL_PIN = 6, OE_PIN = 1;
#endif

constexpr bool HARDWARE_VERIFIED = false;  // Human changes only after the checklist.

struct ArduinoHal : gs::Hal {
  uint32_t millis() override { return ::millis(); }
  void delay_ms(uint32_t ms) override { ::delay(ms); }
  bool i2c_write(uint8_t reg, const uint8_t* data, size_t n) override {
    Wire.beginTransmission(gs::PCA_ADDR);
    Wire.write(reg);
    for (size_t i = 0; i < n; ++i) Wire.write(data[i]);
    return Wire.endTransmission() == 0;
  }
  void oe_write(bool high) override { digitalWrite(OE_PIN, high ? HIGH : LOW); }
  void out_line(const char* s) override { Serial.println(s); }
  uint32_t random32() override { return esp_random(); }
};

ArduinoHal hal;
gs::Core core(&hal, GS_BOARD_NAME, HARDWARE_VERIFIED);

void setup() {
  pinMode(OE_PIN, OUTPUT);
  digitalWrite(OE_PIN, HIGH);
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  core.begin();
}

void loop() {
  while (Serial.available()) core.feed(char(Serial.read()));
  core.tick();
}
