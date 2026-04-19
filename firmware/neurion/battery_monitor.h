#pragma once
#include <Arduino.h>
#include "system_state.h"

// Inicializa el pin ADC de baterA-a.
void batteryMonitor_init();

// Actualiza el estado de baterA-a. Devuelve:
// - STATE_BATTERY_LOW si se detecta baterA-a baja y se debe cambiar a ese estado.
// - El estado anterior guardado si la baterA-a se recuperA3 y estabas en STATE_BATTERY_LOW.
// - currentState en caso contrario (sin cambio).
DeviceState batteryMonitor_update(DeviceState currentState);

// Lectura actual de batería escalada a 0-255 (raw ADC mapeado a un byte).
uint8_t batteryMonitor_readLevelByte();
