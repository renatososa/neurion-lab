#pragma once
#include <Arduino.h>
#include "ads_config_storage.h"
#include "ads1299.h"
#include "system_state.h"

// Fuente del comando (para gating de estados)
enum class CommandSource {
    Serial,
    Udp,
};

// Callbacks especificos del canal por el que llego el comando
typedef void (*CommandResponder)(const char* msg, void* userCtx);
typedef void (*CommandSimpleCb)(void* userCtx);
typedef bool (*CommandDiscoveryCb)(const char* ipStr, void* userCtx);

struct CommandCallbacks {
    CommandResponder   respond          = nullptr; // imprime respuesta
    CommandSimpleCb    onStart          = nullptr; // START
    CommandSimpleCb    onStop           = nullptr; // STOP
    CommandSimpleCb    onConnectivity   = nullptr; // CONNECTIVITY
    CommandDiscoveryCb onDiscoveryReply = nullptr; // DISCOVERY_REPLY <ip>
    void*              userCtx          = nullptr;
};

// Procesa una linea de comando ya terminada en '\0'.
// Modifica config/ADS segun corresponda, marca configDirty si hay cambios pendientes
// y usa callbacks para responder o para eventos especiales (START/STOP/...).
// Devuelve true si pudo interpretar (aunque sea un ERR); false si la linea estaba vacia.
bool commandInterpreter_handleLine(
    char* line,
    AdsPersistentConfig& cfg,
    AdsManager& ads,
    uint8_t numAds,
    DeviceState currentState,
    CommandSource source,
    void (*changeStateFn)(DeviceState newState),
    const CommandCallbacks& callbacks,
    bool& configDirty);
