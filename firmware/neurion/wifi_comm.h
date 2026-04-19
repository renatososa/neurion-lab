#pragma once
#include <Arduino.h>
#include "ads1299.h"
#include "ads_config_storage.h"
#include "system_state.h"

// Inicializa WiFi en modo AP y UDP. Devuelve false si falla crear el AP.
// Usa WIFI_AP_SSID/WIFI_AP_PASSWORD definidos en config_pins.h.
bool wifiComm_init();

// Envia un bloque de muestras (por ejemplo, 25 muestras por ADS).
// Paquete UDP: [numDevices][countPerDevice][packetIdx][battery][datos int16 little endian].
// Cada int16 representa la señal filtrada en pasos de (BASE_UV_PER_GAIN / gain) uV.
// BASE_UV_PER_GAIN = 24: a ganancia 24 => 1 uV/LSB (±32.7 mV); a ganancia 12 => 2 uV/LSB; etc.
// Entradas: samples (array de AdsSample en cuentas), configuracion (para tomar la ganancia),
// countPerDevice (#muestras por ADS), numDevices (cuantos ADS incluyen en el paquete).
void wifiComm_sendSamples(const AdsSample* samples,
                          const AdsPersistentConfig& cfg,
                          size_t countPerDevice,
                          uint8_t numDevices);

// Procesa comandos UDP de configuracion. Lee paquetes entrantes y aplica cambios
// sobre cfg y los ADS. Usa el mismo puerto que streaming.
// currentState permite rechazar comandos si el dispositivo está en ERROR.
// changeStateFn permite que comandos de estado (p/i/c/t/x) funcionen igual por UDP y Serial.
void wifiComm_processCommands(AdsPersistentConfig& cfg, AdsManager& ads, uint8_t numDevices, DeviceState currentState, void (*changeStateFn)(DeviceState newState));

// Aplica la configuracion persistente actual al hardware (ganancias, bias, test).
void wifiComm_applyConfig(const AdsPersistentConfig& cfg, AdsManager& ads, uint8_t numDevices);

// Guarda la configuracion si hubo cambios pendientes (set por comandos).
void wifiComm_saveConfigIfDirty(const AdsPersistentConfig& cfg);

// Devuelve la IP de destino actual usada para streaming (PC detectada)
IPAddress wifiComm_getDestinationIp();

// Indica si ya se recibió un CONNECT y hay destino de streaming configurado
bool wifiComm_hasDestination();

// No se fija IP por defecto; se usa la que llegue en el primer comando UDP.

// Helpers para cambiar entre conectividad STA/AP desde serial o UDP
bool wifiComm_connectSta(const char* ssid, const char* password, IPAddress& outIp, String* outError = nullptr);
bool wifiComm_connectStaKeepAp(const char* ssid, const char* password, IPAddress& outStaIp, IPAddress* outApIp = nullptr, String* outError = nullptr);
bool wifiComm_startAp(IPAddress& outIp);
bool wifiComm_saveCredentials(const String& ssid, const String& password);
bool wifiComm_loadCredentials(String& ssid, String& password);
bool wifiComm_clearCredentials();
void wifiComm_sendDiscovery(const IPAddress& ip);
void wifiComm_beginDiscoveryBeacon(const IPAddress& ip, uint32_t durationMs = 5000);
void wifiComm_tick();
void wifiComm_stopDiscoveryBeacon();
bool wifiComm_hasClient(IPAddress& outIp);
bool wifiComm_takeStartRequest();
bool wifiComm_takeStopRequest();
bool wifiComm_takeConnRequest();
void wifiComm_sendConfigSnapshot(const AdsPersistentConfig& cfg, uint8_t numDevices, DeviceState currentState, bool factoryModeActive);
