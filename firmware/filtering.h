#pragma once
#include <Arduino.h>
#include "ads1299.h"

// Inicializa estado de filtrado (placeholder sin filtros activos).
void filtering_init();

// Placeholders para compatibilidad
void filtering_setHighpassEnabled(bool enabled);
bool filtering_isHighpassEnabled();
void filtering_setLowpassEnabled(bool enabled);
bool filtering_isLowpassEnabled();

// Procesa una muestra a FS_ADC_HZ; devuelve true cuando hay una muestra decimada lista.
bool filtering_processSample(uint8_t deviceIndex,
                             const AdsSample& inSample,
                             AdsSample& outSample);
