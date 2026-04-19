#pragma once
#include <Arduino.h>
#include "ads1299.h"
#include "filtering.h"

// Versión de estructura de configuración persistente
static const uint8_t ADS_CONFIG_VERSION = 2;

// Parámetros persistentes por canal
struct AdsChannelPersist {
    uint8_t gain;   // 1,2,4,6,8,12,24
    bool    powerDown;
    bool    testSignal;
    FilterProfile filterProfile;
};

// Parámetros persistentes por dispositivo
struct AdsDevicePersist {
    AdsChannelPersist ch[ADS_NUM_CHANNELS];
    AdsTestSignal     testSignal; // normalmente OFF; se puede habilitar si se desea
    uint8_t           biasSensP;  // bits CH1..CH8 que aportan al modo común
    uint8_t           biasSensN;  // bits CH1..CH8
};

// Configuración completa
struct AdsPersistentConfig {
    uint8_t  version;
    uint8_t  numDevices;
    AdsDevicePersist dev[ADS_MAX_DEVICES];
    uint32_t crc32; // calculado sobre todo menos crc32
};

// Inicializa una configuración por defecto (ganancia 24x, bias en todos, test off).
void adsConfig_setDefaults(AdsPersistentConfig& cfg, uint8_t numDevices);

// Guarda configuración en NVS (flash). Devuelve true si ok.
bool adsConfig_save(const AdsPersistentConfig& cfg);

// Carga configuración desde NVS. Devuelve true si la lectura y CRC son válidos.
bool adsConfig_load(AdsPersistentConfig& cfg);

// Borra la configuración persistente guardada en NVS.
bool adsConfig_clearStored();
