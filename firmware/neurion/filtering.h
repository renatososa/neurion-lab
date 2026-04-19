#pragma once
#include <Arduino.h>
#include "ads1299.h"

enum FilterProfile : uint8_t {
    FILTER_PROFILE_ECG = 0,
    FILTER_PROFILE_EOG = 1,
    FILTER_PROFILE_EMG = 2,
    FILTER_PROFILE_EEG = 3,
};

struct FilterProfileSpec {
    FilterProfile profile;
    const char* name;
    float highpassHz;
    float lowpassHz;
};

// Inicializa estado de filtrado para el pipeline fijo a 1 kHz.
void filtering_init();

// Metadatos nominales de perfiles de filtrado definidos en firmware.
const FilterProfileSpec& filtering_getProfileSpec(FilterProfile profile);
const char* filtering_getProfileName(FilterProfile profile);
void filtering_setChannelProfile(uint8_t deviceIndex, uint8_t channel, FilterProfile profile);
FilterProfile filtering_getChannelProfile(uint8_t deviceIndex, uint8_t channel);

// Placeholders para compatibilidad
void filtering_setHighpassEnabled(bool enabled);
bool filtering_isHighpassEnabled();
void filtering_setLowpassEnabled(bool enabled);
bool filtering_isLowpassEnabled();

// Procesa una muestra a 1 kHz y siempre devuelve una salida filtrada.
bool filtering_processSample(uint8_t deviceIndex,
                             const AdsSample& inSample,
                             AdsSample& outSample);
