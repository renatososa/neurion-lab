#include <Arduino.h>
#include "config_pins.h"
#include "system_state.h"
#include "status_led.h"
#include "ads1299.h"
#include "wifi_comm.h"
#include "filtering.h"
#include "user_hooks.h"
#include "ads_config_storage.h"
#include "battery_monitor.h"
#include "serial_commands.h"
#include <WiFi.h>
#include <string.h>

// Variables de ploteo configurables en runtime (arrancan con los valores de config_pins)
bool     g_plotEnable  = 0;
uint8_t  g_plotDevice  = 0;
uint8_t  g_plotChannel = 0;
static const uint8_t PLOT_ALL_CHANNELS = 0xFF;
AdsManager Ads; // instancia global en el sketch

// Número real de ADS en esta placa
static const uint8_t NUM_ADS = 2;   // cambia a 1, 2, etc. según el prototipo

DeviceState g_state     = STATE_BOOT;
DeviceState g_prevState = STATE_BOOT;
static bool g_configSent = false;

// Buffer para agrupar muestras filtradas antes de enviar
static const size_t NET_BLOCK_SAMPLES = 10; // bloques más pequeños para latencia baja en Fs altas
AdsSample netBuffer[NET_BLOCK_SAMPLES * NUM_ADS];
AdsSample userCopyBuffer[NET_BLOCK_SAMPLES * NUM_ADS]; // copia para hooks de usuario
size_t    netBufferCount = 0;
AdsPersistentConfig g_adsConfig;
static uint8_t g_prevBiasP[ADS_MAX_DEVICES] = {0};
static uint8_t g_prevBiasN[ADS_MAX_DEVICES] = {0};
static uint8_t g_prevMisc1[ADS_MAX_DEVICES] = {0};

static bool isValidGain(uint8_t g) {
    return g == 1 || g == 2 || g == 4 || g == 6 || g == 8 || g == 12 || g == 24;
}

static uint8_t currentGainForChannel(uint8_t dev, uint8_t ch) {
    if (dev >= g_adsConfig.numDevices || ch >= ADS_NUM_CHANNELS) return 24;
    uint8_t g = g_adsConfig.dev[dev].ch[ch].gain;
    return isValidGain(g) ? g : 24;
}

static float adsCountsToVolts(int32_t counts, uint8_t gain) {
    const float VREF = 4.5f;
    const float LSB  = VREF / (gain * 8388607.0f); // HR mode, 24 bits
    return counts * LSB;
}

void changeState(DeviceState newState) {
    if (newState == g_state) return;
    g_state = newState;
    statusLed_setState(g_state);
    userOnStateChange(g_state);
    Serial.print("Nuevo estado: ");
    Serial.println((int)g_state);
}

void setup() {
    Serial.begin(230400);
    delay(500);
    Serial.println("\n=== NeurionLab ESP32-S3 + ADS1299 ===");

    statusLed_init();
    statusLed_setState(STATE_BOOT);
    Ads.initBus();
    // Asignar pines de cada ADS antes de inicializar
    Ads.setDevicePins(0, ADS1_CS_PIN, ADS1_DRDY_PIN);
    Ads.setDevicePins(1, ADS2_CS_PIN, ADS2_DRDY_PIN);
    Ads.setDevicePins(2, ADS3_CS_PIN, ADS3_DRDY_PIN);
    Ads.setDevicePins(3, ADS4_CS_PIN, ADS4_DRDY_PIN);

    // Config persistente: cargar de flash o establecer por defecto
    adsConfig_setDefaults(g_adsConfig, NUM_ADS);
    if (!adsConfig_load(g_adsConfig)) {
        adsConfig_setDefaults(g_adsConfig, NUM_ADS);
        adsConfig_save(g_adsConfig);
    }

    filtering_init();
    batteryMonitor_init();

    bool okAds  = Ads.initAll(NUM_ADS);
    bool okWifi = wifiComm_init();

    if (!okAds || !okWifi) {
        if(!okAds)
            Serial.println("Error inicializando ADS");
        if(!okWifi)
            Serial.println("Error inicializando WiFi");
        changeState(STATE_ERROR);
        return;
    }

    // Aplicar configuración persistente a hardware
    wifiComm_applyConfig(g_adsConfig, Ads, NUM_ADS);

    Ads.startAll(NUM_ADS);
    changeState(STATE_CONNECTIVITY);
}

void loop() {
    statusLed_tick();
    // Comandos por USB: estados (p/i/c/t/x) o texto (CH/BIAS/TEST/SAVE/LOAD/DUMP/T)
    serialCommands_process(g_adsConfig, NUM_ADS, g_state, changeState);

    // Chequeo periódico de batería
    DeviceState batteryState = batteryMonitor_update(g_state);
    if (batteryState != g_state) {
        changeState(batteryState);
    }

    // Comandos UDP para configurar ADS
    wifiComm_processCommands(g_adsConfig, Ads, NUM_ADS, g_state, changeState);
    wifiComm_tick();
    if (g_state == STATE_CONNECTIVITY) {
        if (wifiComm_hasDestination()) {
            wifiComm_stopDiscoveryBeacon();
            changeState(STATE_WIFI_CONNECTED);
        }
    }
    // Manejo de comandos START/STOP recibidos por UDP
    if (wifiComm_takeStartRequest() && g_state != STATE_STREAMING_PC) {
        changeState(STATE_STREAMING_PC);
    }
    if (wifiComm_takeStopRequest() && g_state == STATE_STREAMING_PC) {
        changeState(STATE_WIFI_CONNECTED);
    }
    if (wifiComm_takeConnRequest()) {
        changeState(STATE_CONNECTIVITY);
    }
    // Guardar config si hubo cambios pendientes (evita escribir en cada comando)
    wifiComm_saveConfigIfDirty(g_adsConfig);

    // Cambio de estado: habilitar/deshabilitar señal de test según proceda
    if (g_prevState != g_state) {
        if (g_state == STATE_TEST) {
            AdsTestSignal testOn { true, false, 1 }; // amp baja, 1 Hz
            for (uint8_t i = 0; i < NUM_ADS; ++i) {
                // Guardar bias actual y MISC1 para restaurar al salir
                g_prevBiasP[i] = g_adsConfig.dev[i].biasSensP;
                g_prevBiasN[i] = g_adsConfig.dev[i].biasSensN;
                uint8_t misc = 0;
                if (Ads.device(i)) Ads.device(i)->readRegister(ADS_REG_MISC1, misc);
                g_prevMisc1[i] = misc;

                // Forzar bias off y SRB1 off para modo test limpio
                Ads.setBiasSelection(i, 0x00, 0x00);
                if (Ads.device(i)) Ads.device(i)->writeRegister(ADS_REG_MISC1, 0x00);

                Ads.setTestSignal(i, testOn);
            }
        } else if (g_prevState == STATE_TEST) {
            for (uint8_t i = 0; i < g_adsConfig.numDevices && i < NUM_ADS; ++i) {
                // Restaurar bias y SRB1 previos
                Ads.setBiasSelection(i, g_prevBiasP[i], g_prevBiasN[i]);
                if (Ads.device(i)) Ads.device(i)->writeRegister(ADS_REG_MISC1, g_prevMisc1[i]);
                // Restaurar señal de test configurada
                Ads.setTestSignal(i, g_adsConfig.dev[i].testSignal);
            }
        }
        if (g_state == STATE_CONNECTIVITY) {
            IPAddress ip = (WiFi.getMode() == WIFI_AP) ? WiFi.softAPIP() : WiFi.localIP();
            wifiComm_beginDiscoveryBeacon(ip, 0);
        }
        if (g_state == STATE_WIFI_CONNECTED && !g_configSent) {
            wifiComm_sendConfigSnapshot(g_adsConfig, NUM_ADS);
            g_configSent = true;
        } else if (g_state != STATE_WIFI_CONNECTED) {
            g_configSent = false;
        }
        g_prevState = g_state;
    }

    switch (g_state) {
        case STATE_BOOT:
            break;

        case STATE_IDLE:
            break;

        case STATE_WIFI_CONNECTED:
            break;

        case STATE_CALIBRATION:
            break;

        case STATE_BATTERY_LOW:
            break;

        case STATE_STREAMING_PC:
        case STATE_TEST: {
            AdsSample raw[NUM_ADS];
            if (Ads.readAll(NUM_ADS, raw)) {
                bool newSampleReady = false;
                for (uint8_t d = 0; d < NUM_ADS; ++d) {
                    AdsSample filtered;
                    if (filtering_processSample(d, raw[d], filtered)) {
                        newSampleReady = true;
                        netBuffer[netBufferCount * NUM_ADS + d] = filtered;
                    }
                }
                if (newSampleReady) {
                    if (g_plotEnable && g_plotDevice < NUM_ADS) {
                        const AdsSample& s = netBuffer[netBufferCount * NUM_ADS + g_plotDevice];
                        if (g_plotChannel == PLOT_ALL_CHANNELS) {
                            Serial.print("20\t-20\t");
                            for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
                                uint8_t gain = currentGainForChannel(g_plotDevice, ch);
                                float miliVolts = adsCountsToVolts(s.ch[ch], gain) * 1e3f;
                                Serial.print(miliVolts, 3);
                                if (ch < ADS_NUM_CHANNELS - 1) Serial.print("\t");
                            }
                            Serial.println();
                        } else if (g_plotChannel < ADS_NUM_CHANNELS) {
                            uint8_t gain = currentGainForChannel(g_plotDevice, g_plotChannel);
                            float miliVolts = adsCountsToVolts(s.ch[g_plotChannel], gain) * 1e3f;
                            Serial.print(" 20 -20 ");
                            Serial.println(miliVolts, 3);

                        }
                    }
                    ++netBufferCount;
                    if (netBufferCount >= NET_BLOCK_SAMPLES) {
                        memcpy(userCopyBuffer, netBuffer, sizeof(netBuffer));
                        userProcessSamples(userCopyBuffer, NET_BLOCK_SAMPLES, NUM_ADS);

                        if (FS_ADC_HZ <= 2000) {
                            wifiComm_sendSamples(netBuffer, g_adsConfig, NET_BLOCK_SAMPLES, NUM_ADS);
                        }
                        netBufferCount = 0;
                    }
                }
            } else {
                static uint32_t lastErrorLog = 0;
                uint32_t now = millis();
                if (now - lastErrorLog > 1000) {
                    lastErrorLog = now;
                    Serial.println("WARN: Ads.readAll timeout");
                }
            }
            break;
        }

        case STATE_STREAMING_EXTERNAL:
            break;

        case STATE_ERROR:
            break;
    }

    delay(0); // no añadimos retardo para sostener Fs altas
}
