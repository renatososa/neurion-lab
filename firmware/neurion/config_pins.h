#pragma once
#include <Arduino.h>

// ============ PARÁMETROS GENERALES ============
// Firmware fijado a una Fs unica sin diezmado para simplificar debug.
static const uint32_t FS_ADC_HZ    = 1000;
static const uint32_t FS_OUTPUT_HZ = 1000;
static_assert(FS_ADC_HZ == FS_OUTPUT_HZ, "Este firmware de debug usa la misma Fs en ADC y salida");

// ============ BUS SPI A: ADS1299 (principal) ============
// Pines de bus compartido
static const uint8_t ADS_SCLK_PIN  = 10;
static const uint8_t ADS_MISO_PIN  = 9;
static const uint8_t ADS_MOSI_PIN  = 5;

// CS de cada ADS (ajusta según tu PCB)
static const uint8_t ADS1_CS_PIN   = 7;
static const uint8_t ADS2_CS_PIN   = 16;
static const uint8_t ADS3_CS_PIN   = 17;
static const uint8_t ADS4_CS_PIN   = 18;

// DRDY de cada ADS (AJUSTAR según el ruteo real)
static const uint8_t ADS1_DRDY_PIN = 38;
static const uint8_t ADS2_DRDY_PIN = 40;
static const uint8_t ADS3_DRDY_PIN = 41;
static const uint8_t ADS4_DRDY_PIN = 44;

// Pines de control comunes START / RESET (compartidos por todos)
static const uint8_t ADS_RESET_PIN = 6;
static const uint8_t ADS_START_PIN = 8;
static const uint8_t ADS_PWDN_PIN  = 4;  

// ============ BUS SPI B: WL-ICLED (secundario) ============

static const uint8_t ICLED_DIN_PIN = 1;   // MOSI
static const uint8_t ICLED_CIN_PIN = 2;   // SCK

// ============ WIFI (AP + UDP) ============
// El ESP32 crea su propio AP; la PC se conecta a esta red
static const char* WIFI_AP_SSID     = "NEURION_AP";   
static const char* WIFI_AP_PASSWORD = "clave1234";     

// IP esperada de la PC dentro de la red AP (default 192.168.4.x). Ajusta según DHCP.
static const char* PC_UDP_IP   = "192.168.4.2";  // IP PC
static const uint16_t PC_UDP_PORT = 5000;

// ============ UART EXTERNA (prótesis, futuro) ============

static const uint8_t EXT_SERIAL_TX_PIN = 12;
static const uint8_t EXT_SERIAL_RX_PIN = 11;

// ============ MONITOREO DE BATERIA ============
static const uint8_t BATTERY_ADC_PIN = 3;           // GPIO3 (ajusta si usas otro)
static const uint16_t BATTERY_LOW_THRESHOLD = 1800; // umbral ADC crudo (ajustar segun divisor)
static const uint32_t BATTERY_CHECK_MS = 1000;      // periodo de chequeo

// ============ FACTORY RESET ============
static const uint8_t FACTORY_RESET_BUTTON_PIN = 0;          // boton BOOT / GPIO0
static const uint32_t FACTORY_RESET_HOLD_MS = 5000;         // mantener presionado 5 s
