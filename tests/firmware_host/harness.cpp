// Host harness for guarded_servo_core.h. Reads a script on stdin, prints device lines.
//   T <ms>      set the clock (no ticking)
//   W <ms>      advance the clock by <ms>, calling tick() once per millisecond
//   L <text>    feed <text> + '\n'
//   R <hex>     feed raw bytes (hex pairs, no spaces)
//   E <n>       the next random32() returns n
//   F <0|1>     1 = every I2C write fails (returns false) until F 0
//   G <n>       the next n I2C writes fail (returns false), then the bus works again
// Output: "> <device line>", "# i2c <reg> <hex bytes>", "# oe <0|1>"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <deque>
#include "../../code/firmware/guarded_servo/guarded_servo_core.h"

struct HostHal : gs::Hal {
  uint32_t now = 0;
  bool fail_i2c = false;
  int fail_next = 0;
  std::deque<uint32_t> rnd;
  uint32_t millis() override { return now; }
  void delay_ms(uint32_t) override {}
  bool i2c_write(uint8_t reg, const uint8_t* d, size_t n) override {
    printf("# i2c %02x", reg);
    for (size_t i = 0; i < n; ++i) printf(" %02x", d[i]);
    printf("\n");
    if (fail_next > 0) {
      --fail_next;
      return false;
    }
    return !fail_i2c;
  }
  void oe_write(bool high) override { printf("# oe %d\n", high ? 1 : 0); }
  void out_line(const char* s) override { printf("> %s\n", s); }
  uint32_t random32() override {
    if (rnd.empty()) return 0x1000;
    uint32_t v = rnd.front();
    rnd.pop_front();
    return v;
  }
};

int main(int argc, char** argv) {
  bool verified = false;
  for (int i = 1; i < argc; ++i)
    if (!strcmp(argv[i], "--verified")) verified = true;
  HostHal hal;
  gs::Core core(&hal, "HOST", verified);
  core.begin();
  std::string ln;
  while (std::getline(std::cin, ln)) {
    if (ln.size() < 1) continue;
    char op = ln[0];
    std::string arg = ln.size() > 2 ? ln.substr(2) : "";
    if (op == 'T') hal.now = uint32_t(strtoul(arg.c_str(), nullptr, 10));
    else if (op == 'W') {
      uint32_t n = uint32_t(strtoul(arg.c_str(), nullptr, 10));
      for (uint32_t i = 0; i < n; ++i) { hal.now += 1; core.tick(); }
    } else if (op == 'L') {
      for (char c : arg) core.feed(c);
      core.feed('\n');
    } else if (op == 'R') {
      for (size_t i = 0; i + 1 < arg.size(); i += 2)
        core.feed(char(strtoul(arg.substr(i, 2).c_str(), nullptr, 16)));
    } else if (op == 'E') hal.rnd.push_back(uint32_t(strtoul(arg.c_str(), nullptr, 10)));
    else if (op == 'F') hal.fail_i2c = (arg == "1");
    else if (op == 'G') hal.fail_next = int(strtol(arg.c_str(), nullptr, 10));
    fflush(stdout);
  }
  return 0;
}
