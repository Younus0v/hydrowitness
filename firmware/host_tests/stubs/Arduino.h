// Minimal stand-ins for the Arduino-ESP32 core, written from the published API, so that
// tests/test_firmware_node.py can type-check witness_node.ino on a normal computer. They catch typos, wrong
// types and printf mistakes. They CANNOT catch a mismatch with the real core: that needs an ESP32 build.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ESP_ARDUINO_VERSION_MAJOR 3
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2

typedef int esp_err_t;
#define ESP_OK 0

class String {
 public:
  String() {}
  String(const char*) {}
  const char* c_str() const { return ""; }
  int indexOf(const char*, unsigned from = 0) const { (void)from; return -1; }
  int indexOf(char, unsigned from = 0) const { (void)from; return -1; }
  unsigned length() const { return 0; }
  char operator[](unsigned) const { return 0; }
};

struct HardwareSerial {
  void begin(unsigned long) {}
  void println(const char*) {}
  void println() {}
  int printf(const char*, ...) __attribute__((format(printf, 2, 3))) { return 0; }
  int available() { return 0; }
  int read() { return -1; }
};
extern HardwareSerial Serial;

struct EspClass {
  String getSketchMD5() { return String(); }
  uint32_t getFreeHeap() { return 0; }
  uint32_t getMaxAllocHeap() { return 0; }
  uint32_t getMinFreeHeap() { return 0; }
  void restart() {}
};
extern EspClass ESP;

void pinMode(uint8_t, uint8_t);
void digitalWrite(uint8_t, uint8_t);
int digitalRead(uint8_t);
uint32_t millis();
uint32_t micros();
void delay(uint32_t);
uint32_t analogReadMilliVolts(uint8_t);

// FreeRTOS
typedef void* SemaphoreHandle_t;
typedef void* QueueHandle_t;
typedef void (*TaskFunction_t)(void*);
typedef uint32_t TickType_t;
#define pdTRUE 1
#define portMAX_DELAY 0xffffffffu
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
SemaphoreHandle_t xSemaphoreCreateMutex();
int xSemaphoreTake(SemaphoreHandle_t, TickType_t);
int xSemaphoreGive(SemaphoreHandle_t);
QueueHandle_t xQueueCreate(unsigned, unsigned);
int xQueueSend(QueueHandle_t, const void*, TickType_t);
int xQueueReceive(QueueHandle_t, void*, TickType_t);
void vTaskDelay(TickType_t);
int xTaskCreatePinnedToCore(TaskFunction_t, const char*, uint32_t, void*, unsigned, void*, int);
void configTime(long, int, const char*);
