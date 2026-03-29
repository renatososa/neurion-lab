#pragma once
#include <Arduino.h>
#include "ads_config_storage.h"
#include "system_state.h"

// Procesa entrada por Serial: comandos de estado (p/i/c/t/x) o CH/BIAS/TEST/SAVE/LOAD/DUMP.
// Debe llamarse de forma periódica desde loop.
// changeStateFn: callback para cambiar el estado global.
void serialCommands_process(AdsPersistentConfig& cfg, uint8_t numAds, DeviceState currentState, void (*changeStateFn)(DeviceState newState));

// Variables globales para ploteo (definidas en neurion.ino)
extern bool     g_plotEnable;
extern uint8_t  g_plotDevice;
extern uint8_t  g_plotChannel;

// Credenciales WiFi recibidas por serial (se llenan con comando SET_WIFI)
extern String g_serialWifiSsid;
extern String g_serialWifiPassword;
extern bool   g_serialWifiUpdated;
