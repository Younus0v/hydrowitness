#pragma once
#include "Arduino.h"
struct Preferences {
  bool begin(const char*, bool = false) { return true; }
  uint32_t getUInt(const char*, uint32_t def = 0) { return def; }
  size_t putUInt(const char*, uint32_t) { return 4; }
  uint8_t getUChar(const char*, uint8_t def = 0) { return def; }
  size_t putUChar(const char*, uint8_t) { return 1; }
  size_t getBytesLength(const char*) { return 0; }
  size_t getBytes(const char*, void*, size_t) { return 0; }
  size_t putBytes(const char*, const void*, size_t) { return 0; }
  bool remove(const char*) { return true; }
};
