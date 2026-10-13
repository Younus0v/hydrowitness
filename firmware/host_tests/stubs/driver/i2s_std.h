// Field names and order follow ESP-IDF 5.1 driver/i2s_std.h, because the sketch uses designated initializers.
#pragma once
#include "Arduino.h"
typedef void* i2s_chan_handle_t;
typedef int gpio_num_t;
#define I2S_GPIO_UNUSED (-1)
#define I2S_NUM_AUTO (-1)
typedef enum { I2S_ROLE_MASTER, I2S_ROLE_SLAVE } i2s_role_t;
typedef enum { I2S_DATA_BIT_WIDTH_32BIT = 32 } i2s_data_bit_width_t;
typedef enum { I2S_SLOT_MODE_MONO = 1 } i2s_slot_mode_t;
typedef enum { I2S_STD_SLOT_LEFT = 1, I2S_STD_SLOT_RIGHT = 2 } i2s_std_slot_mask_t;

typedef struct {
  int id;
  i2s_role_t role;
  uint32_t dma_desc_num;
  uint32_t dma_frame_num;
  bool auto_clear;
} i2s_chan_config_t;
#define I2S_CHANNEL_DEFAULT_CONFIG(i2s_num, i2s_role) \
  { .id = i2s_num, .role = i2s_role, .dma_desc_num = 6, .dma_frame_num = 240, .auto_clear = false }

typedef struct { uint32_t sample_rate_hz; int clk_src; int mclk_multiple; } i2s_std_clk_config_t;
#define I2S_STD_CLK_DEFAULT_CONFIG(rate) { .sample_rate_hz = (uint32_t)(rate), .clk_src = 0, .mclk_multiple = 256 }

typedef struct {
  i2s_data_bit_width_t data_bit_width;
  int slot_bit_width;
  i2s_slot_mode_t slot_mode;
  i2s_std_slot_mask_t slot_mask;
  uint32_t ws_width;
  bool ws_pol, bit_shift, left_align, big_endian, bit_order_lsb;
} i2s_std_slot_config_t;
#define I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bits, mode) \
  { .data_bit_width = bits, .slot_bit_width = 0, .slot_mode = mode, .slot_mask = I2S_STD_SLOT_LEFT, .ws_width = 32, \
    .ws_pol = false, .bit_shift = true, .left_align = true, .big_endian = false, .bit_order_lsb = false }

typedef struct {
  gpio_num_t mclk, bclk, ws, dout, din;
  struct { uint32_t mclk_inv : 1; uint32_t bclk_inv : 1; uint32_t ws_inv : 1; } invert_flags;
} i2s_std_gpio_config_t;

typedef struct {
  i2s_std_clk_config_t clk_cfg;
  i2s_std_slot_config_t slot_cfg;
  i2s_std_gpio_config_t gpio_cfg;
} i2s_std_config_t;

esp_err_t i2s_new_channel(const i2s_chan_config_t*, i2s_chan_handle_t* tx, i2s_chan_handle_t* rx);
esp_err_t i2s_channel_init_std_mode(i2s_chan_handle_t, const i2s_std_config_t*);
esp_err_t i2s_channel_enable(i2s_chan_handle_t);
esp_err_t i2s_channel_read(i2s_chan_handle_t, void*, size_t, size_t*, uint32_t);
