#pragma once
#include "WiFi.h"
struct HTTPClient {
  void setConnectTimeout(int) {}
  void setTimeout(uint16_t) {}
  bool begin(WiFiClient&, const String&) { return true; }
  void addHeader(const String&, const String&) {}
  int POST(uint8_t*, size_t) { return 0; }
  String getString() { return String(); }
  void end() {}
};
