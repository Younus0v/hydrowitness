#pragma once
#include "Arduino.h"
#define WIFI_STA 1
#define WL_CONNECTED 3
struct WiFiClient {};
struct WiFiClass {
  bool mode(int) { return true; }
  void setAutoReconnect(bool) {}
  int begin(const char*, const char*) { return 0; }
  int status() { return 0; }
  bool disconnect() { return true; }
  int8_t RSSI() { return 0; }
};
extern WiFiClass WiFi;
