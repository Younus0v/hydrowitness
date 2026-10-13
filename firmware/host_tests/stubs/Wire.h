#pragma once
#include "Arduino.h"
struct TwoWire {
  bool begin(int, int, uint32_t) { return true; }
  void end() {}
  uint16_t setTimeOut(uint16_t) { return 0; }
  void beginTransmission(uint8_t) {}
  size_t write(uint8_t) { return 1; }
  uint8_t endTransmission(bool = true) { return 0; }
  size_t requestFrom(uint8_t, size_t, bool = true) { return 0; }
  int read() { return 0; }
};
extern TwoWire Wire;
