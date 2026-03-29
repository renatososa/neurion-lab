#pragma once
#include <Arduino.h>
#include "ads1299.h"
#include "system_state.h"

// Punto de extensión: el usuario puede procesar bloques de muestras decimadas.
// Se invoca después de armar un bloque completo y antes de reutilizar el buffer.
// Las muestras se entregan en una copia para que no afecten el flujo principal.
// Parámetros:
//  - samples: copia del bloque (longitud = countPerDevice * numDevices)
//             layout: para cada muestra k, dispositivos 0..numDevices-1, 8 canales int32
//  - countPerDevice: cantidad de muestras por ADS en el bloque (ej. 25)
//  - numDevices: cuántos ADS incluye el bloque
void userProcessSamples(const AdsSample* samples, size_t countPerDevice, uint8_t numDevices);

// Punto de extensión para reaccionar a cambios de estado global.
void userOnStateChange(DeviceState newState);
