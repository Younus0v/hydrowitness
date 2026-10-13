#pragma once
#include "WiFi.h"
struct WiFiClientSecure : public WiFiClient {
  void setCACert(const char*) {}
};
