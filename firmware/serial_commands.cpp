#include "serial_commands.h"
#include <string.h>
#include "ads1299.h"
#include "command_interpreter.h"

// Credenciales capturadas por serial (expuestas en el header)
String g_serialWifiSsid;
String g_serialWifiPassword;
bool   g_serialWifiUpdated = false;

void serialCommands_process(AdsPersistentConfig& cfg, uint8_t numAds, DeviceState currentState, void (*changeStateFn)(DeviceState newState)) {
    static char serialBuf[128];
    static size_t serialLen = 0;
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            serialBuf[serialLen] = '\0';
            if (serialLen > 0) {
                CommandCallbacks cbs{};
                cbs.respond = [](const char* msg, void*) { Serial.println(msg); Serial.flush(); };
                bool dummyDirty = false;
                commandInterpreter_handleLine(
                    serialBuf,
                    cfg,
                    Ads,
                    numAds,
                    currentState,
                    CommandSource::Serial,
                    changeStateFn,
                    cbs,
                    dummyDirty);
                Serial.flush();
            }
            serialLen = 0;
        } else {
            if (serialLen < sizeof(serialBuf) - 1) {
                serialBuf[serialLen++] = c;
            }
        }
    }
}
