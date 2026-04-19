#pragma once
#include <Arduino.h>
#include "system_state.h"

// Inicializa el LED de estado (WL-ICLED)
void statusLed_init();

// Cambia el color segA§n el estado global (parpadeo en error/baterA-a baja).
// Entrada: DeviceState actual; no devuelve nada.
void statusLed_setState(DeviceState state);

// Llamar periA3dicamente en loop() para gestionar parpadeos
void statusLed_tick();
