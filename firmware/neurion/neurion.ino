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
#include <driver/gpio.h>
#include <string.h>

// Variables de ploteo configurables en runtime (arrancan con los valores de config_pins)
bool     g_plotEnable  = 0;
uint8_t  g_plotDevice  = 0;
uint8_t  g_plotChannel = 0;
static const uint8_t PLOT_ALL_CHANNELS = 0xFF;
AdsManager Ads; // instancia global en el sketch

// Número real de ADS en esta placa
static const uint8_t NUM_ADS = 1;   // cambia a 1, 2, etc. según el prototipo

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
static bool g_factoryModeActive = false;
static bool g_factoryResetArmed = false;
static bool g_factoryResetHandled = false;
static uint32_t g_factoryResetPressedAtMs = 0;
static uint32_t g_factoryResetLastProgressLogMs = 0;
static uint32_t g_lastSpikeDebugLogMs[ADS_MAX_DEVICES] = {0};
static int32_t g_lastValidRawCounts[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static bool g_hasLastValidRawCounts[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static int32_t g_lastValidFilteredCounts[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static bool g_hasLastValidFilteredCounts[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static uint32_t g_rejectedRawSampleCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static uint32_t g_lastReportedRejectedRawSampleCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static uint32_t g_lastRawRejectLogMs[ADS_MAX_DEVICES] = {0};
static uint32_t g_lastRawRejectSummaryMs = 0;
static uint32_t g_rejectedFilteredSampleCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static uint32_t g_lastReportedRejectedFilteredSampleCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {};
static constexpr bool ENABLE_SPIKE_DEBUG_LOG = false;
static constexpr bool ENABLE_RAW_FIX_DETAIL_LOG = false;
static constexpr float SPIKE_DEBUG_THRESHOLD_UV = 8000.0f;
static constexpr uint32_t SPIKE_DEBUG_COOLDOWN_MS = 250;
static constexpr int32_t RAW_SAMPLE_POS_RAIL = 8388607;
static constexpr int32_t RAW_SAMPLE_NEG_RAIL = (-8388607 - 1);
static constexpr int32_t RAW_SAMPLE_NEAR_RAIL_COUNTS = 8200000;
static constexpr int32_t RAW_SAMPLE_LARGE_GLITCH_COUNTS = 500000;
static constexpr int32_t RAW_SAMPLE_LARGE_JUMP_COUNTS = 300000;
static constexpr int32_t RAW_SAMPLE_SUDDEN_JUMP_COUNTS = 1000000;
static constexpr uint32_t RAW_SAMPLE_REJECT_LOG_COOLDOWN_MS = 1000;
static constexpr uint32_t RAW_SAMPLE_REJECT_SUMMARY_MS = 15000;
static constexpr int32_t FILTERED_SAMPLE_LARGE_GLITCH_COUNTS = 250000;
static constexpr int32_t FILTERED_SAMPLE_LARGE_JUMP_COUNTS = 180000;

void changeState(DeviceState newState);

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

static int32_t absCounts32(int32_t value) {
    return (value < 0) ? (value == INT32_MIN ? INT32_MAX : -value) : value;
}

static void resetRawSampleSanitizerState() {
    memset(g_lastValidRawCounts, 0, sizeof(g_lastValidRawCounts));
    memset(g_hasLastValidRawCounts, 0, sizeof(g_hasLastValidRawCounts));
    memset(g_lastValidFilteredCounts, 0, sizeof(g_lastValidFilteredCounts));
    memset(g_hasLastValidFilteredCounts, 0, sizeof(g_hasLastValidFilteredCounts));
    memset(g_rejectedRawSampleCount, 0, sizeof(g_rejectedRawSampleCount));
    memset(g_lastReportedRejectedRawSampleCount, 0, sizeof(g_lastReportedRejectedRawSampleCount));
    memset(g_rejectedFilteredSampleCount, 0, sizeof(g_rejectedFilteredSampleCount));
    memset(g_lastReportedRejectedFilteredSampleCount, 0, sizeof(g_lastReportedRejectedFilteredSampleCount));
    memset(g_lastRawRejectLogMs, 0, sizeof(g_lastRawRejectLogMs));
    g_lastRawRejectSummaryMs = 0;
}

static bool shouldRejectRawSample(uint8_t deviceIndex, uint8_t channel, int32_t counts) {
    if (deviceIndex >= NUM_ADS || channel >= ADS_NUM_CHANNELS) return false;
    if (g_adsConfig.dev[deviceIndex].ch[channel].powerDown) return false;

    if (counts == RAW_SAMPLE_POS_RAIL || counts == RAW_SAMPLE_NEG_RAIL) {
        return true;
    }

    const int32_t absCounts = absCounts32(counts);
    if (absCounts >= RAW_SAMPLE_LARGE_GLITCH_COUNTS && !g_hasLastValidRawCounts[deviceIndex][channel]) {
        return true;
    }

    if (absCounts < RAW_SAMPLE_NEAR_RAIL_COUNTS) {
        if (!g_hasLastValidRawCounts[deviceIndex][channel]) {
            return false;
        }

        const int32_t previous = g_lastValidRawCounts[deviceIndex][channel];
        const int32_t absPrevious = absCounts32(previous);
        int64_t diff = (int64_t)counts - (int64_t)previous;
        if (diff < 0) diff = -diff;

        if (absCounts >= RAW_SAMPLE_LARGE_GLITCH_COUNTS && diff >= RAW_SAMPLE_LARGE_JUMP_COUNTS) {
            return true;
        }

        return false;
    }

    if (!g_hasLastValidRawCounts[deviceIndex][channel]) {
        return true;
    }

    const int32_t previous = g_lastValidRawCounts[deviceIndex][channel];
    const int32_t absPrevious = absCounts32(previous);
    int64_t diff = (int64_t)counts - (int64_t)previous;
    if (diff < 0) diff = -diff;

    return absPrevious < (RAW_SAMPLE_NEAR_RAIL_COUNTS / 2) && diff >= RAW_SAMPLE_SUDDEN_JUMP_COUNTS;
}

static void maybeLogRawSampleReject(uint8_t deviceIndex, uint8_t channel, int32_t rawCounts, int32_t replacementCounts) {
    if (!ENABLE_RAW_FIX_DETAIL_LOG) return;
    if (deviceIndex >= NUM_ADS || channel >= ADS_NUM_CHANNELS) return;

    const uint32_t now = millis();
    if ((uint32_t)(now - g_lastRawRejectLogMs[deviceIndex]) < RAW_SAMPLE_REJECT_LOG_COOLDOWN_MS) {
        return;
    }

    g_lastRawRejectLogMs[deviceIndex] = now;
    Serial.print("RAW FIX ms=");
    Serial.print(now);
    Serial.print(" dev=");
    Serial.print(deviceIndex);
    Serial.print(" ch=");
    Serial.print(channel + 1);
    Serial.print(" raw_counts=");
    Serial.print(rawCounts);
    Serial.print(" repl_counts=");
    Serial.print(replacementCounts);
    Serial.print(" rejected=");
    Serial.println(g_rejectedRawSampleCount[deviceIndex][channel]);
}

static void maybeLogRawSampleRejectSummary() {
    const uint32_t now = millis();
    if ((uint32_t)(now - g_lastRawRejectSummaryMs) < RAW_SAMPLE_REJECT_SUMMARY_MS) {
        return;
    }

    bool anyDelta = false;
    for (uint8_t d = 0; d < NUM_ADS && !anyDelta; ++d) {
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            if (g_rejectedRawSampleCount[d][ch] != g_lastReportedRejectedRawSampleCount[d][ch]) {
                anyDelta = true;
                break;
            }
        }
    }

    g_lastRawRejectSummaryMs = now;
    if (!anyDelta) return;

    Serial.print("RAW FIX SUMMARY ms=");
    Serial.print(now);
    for (uint8_t d = 0; d < NUM_ADS; ++d) {
        bool devicePrinted = false;
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            const uint32_t rawTotal = g_rejectedRawSampleCount[d][ch];
            const uint32_t rawPrev = g_lastReportedRejectedRawSampleCount[d][ch];
            const uint32_t filtTotal = g_rejectedFilteredSampleCount[d][ch];
            const uint32_t filtPrev = g_lastReportedRejectedFilteredSampleCount[d][ch];
            if (rawTotal == rawPrev && filtTotal == filtPrev) continue;
            if (!devicePrinted) {
                Serial.print(" dev=");
                Serial.print(d);
                devicePrinted = true;
            }
            Serial.print(" ch");
            Serial.print(ch + 1);
            if (rawTotal != rawPrev) {
                Serial.print(" raw+");
                Serial.print(rawTotal - rawPrev);
                Serial.print(" (");
                Serial.print(rawTotal);
                Serial.print(")");
                g_lastReportedRejectedRawSampleCount[d][ch] = rawTotal;
            }
            if (filtTotal != filtPrev) {
                Serial.print(" filt+");
                Serial.print(filtTotal - filtPrev);
                Serial.print(" (");
                Serial.print(filtTotal);
                Serial.print(")");
                g_lastReportedRejectedFilteredSampleCount[d][ch] = filtTotal;
            }
        }
    }
    Serial.println();
}

static void sanitizeRawSample(uint8_t deviceIndex, AdsSample& sample) {
    if (deviceIndex >= NUM_ADS) return;

    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        if (g_adsConfig.dev[deviceIndex].ch[ch].powerDown) {
            sample.ch[ch] = 0;
            g_hasLastValidRawCounts[deviceIndex][ch] = false;
            continue;
        }

        const int32_t rawCounts = sample.ch[ch];
        if (shouldRejectRawSample(deviceIndex, ch, rawCounts)) {
            const int32_t replacementCounts =
                g_hasLastValidRawCounts[deviceIndex][ch] ? g_lastValidRawCounts[deviceIndex][ch] : 0;
            sample.ch[ch] = replacementCounts;
            ++g_rejectedRawSampleCount[deviceIndex][ch];
            maybeLogRawSampleReject(deviceIndex, ch, rawCounts, replacementCounts);
            continue;
        }

        g_lastValidRawCounts[deviceIndex][ch] = rawCounts;
        g_hasLastValidRawCounts[deviceIndex][ch] = true;
    }
}

static void sanitizeFilteredSample(uint8_t deviceIndex, AdsSample& sample) {
    if (deviceIndex >= NUM_ADS) return;

    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        if (g_adsConfig.dev[deviceIndex].ch[ch].powerDown) {
            sample.ch[ch] = 0;
            g_hasLastValidFilteredCounts[deviceIndex][ch] = false;
            continue;
        }

        const int32_t filteredCounts = sample.ch[ch];
        bool reject = false;
        if (!g_hasLastValidFilteredCounts[deviceIndex][ch]) {
            reject = absCounts32(filteredCounts) >= FILTERED_SAMPLE_LARGE_GLITCH_COUNTS;
        } else {
            const int32_t previous = g_lastValidFilteredCounts[deviceIndex][ch];
            int64_t diff = (int64_t)filteredCounts - (int64_t)previous;
            if (diff < 0) diff = -diff;
            reject = absCounts32(filteredCounts) >= FILTERED_SAMPLE_LARGE_GLITCH_COUNTS &&
                     diff >= FILTERED_SAMPLE_LARGE_JUMP_COUNTS;
        }

        if (reject) {
            sample.ch[ch] = g_hasLastValidFilteredCounts[deviceIndex][ch] ? g_lastValidFilteredCounts[deviceIndex][ch] : 0;
            ++g_rejectedFilteredSampleCount[deviceIndex][ch];
            continue;
        }

        g_lastValidFilteredCounts[deviceIndex][ch] = filteredCounts;
        g_hasLastValidFilteredCounts[deviceIndex][ch] = true;
    }
}

static void maybeLogSpikeDebug(uint8_t deviceIndex, const AdsSample& rawSample, const AdsSample& filteredSample) {
    if (!ENABLE_SPIKE_DEBUG_LOG) return;
    if (deviceIndex >= NUM_ADS) return;

    const uint32_t now = millis();
    if ((uint32_t)(now - g_lastSpikeDebugLogMs[deviceIndex]) < SPIKE_DEBUG_COOLDOWN_MS) {
        return;
    }

    int8_t triggerChannel = -1;
    float triggerUv = 0.0f;
    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        const uint8_t gain = currentGainForChannel(deviceIndex, ch);
        const float filteredUv = adsCountsToVolts(filteredSample.ch[ch], gain) * 1e6f;
        if (fabsf(filteredUv) >= SPIKE_DEBUG_THRESHOLD_UV) {
            triggerChannel = (int8_t)ch;
            triggerUv = filteredUv;
            break;
        }
    }

    if (triggerChannel < 0) return;

    g_lastSpikeDebugLogMs[deviceIndex] = now;
    Serial.print("SPIKE DBG ms=");
    Serial.print(now);
    Serial.print(" dev=");
    Serial.print(deviceIndex);
    Serial.print(" trig_ch=");
    Serial.print(triggerChannel + 1);
    Serial.print(" filt_uv=");
    Serial.println(triggerUv, 1);

    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        const uint8_t gain = currentGainForChannel(deviceIndex, ch);
        const float rawUv = adsCountsToVolts(rawSample.ch[ch], gain) * 1e6f;
        const float filteredUv = adsCountsToVolts(filteredSample.ch[ch], gain) * 1e6f;
        Serial.print("  CH");
        Serial.print(ch + 1);
        Serial.print(" raw=");
        Serial.print(rawUv, 1);
        Serial.print("uV filt=");
        Serial.print(filteredUv, 1);
        Serial.print("uV raw_counts=");
        Serial.print(rawSample.ch[ch]);
        Serial.print(" filt_counts=");
        Serial.println(filteredSample.ch[ch]);
    }
}

static DeviceState connectivityIdleState() {
    return g_factoryModeActive ? STATE_FACTORY_MODE : STATE_CONNECTIVITY;
}

static bool isFactoryResetButtonPressed() {
    return gpio_get_level((gpio_num_t)FACTORY_RESET_BUTTON_PIN) == 0;
}

static void performFactoryReset() {
    Serial.println("FACTORY RESET: borrando configuracion persistente y credenciales WiFi...");

    const bool clearedAds = adsConfig_clearStored();
    const bool clearedWifi = wifiComm_clearCredentials();

    adsConfig_setDefaults(g_adsConfig, NUM_ADS);
    const bool savedDefaults = adsConfig_save(g_adsConfig);
    wifiComm_applyConfig(g_adsConfig, Ads, NUM_ADS);

    netBufferCount = 0;
    g_configSent = false;
    g_factoryModeActive = true;

    IPAddress apIp;
    if (!wifiComm_startAp(apIp)) {
        Serial.println("FACTORY RESET: no se pudo iniciar modo AP");
        changeState(STATE_ERROR);
        g_prevState = g_state;
        return;
    }

    changeState(connectivityIdleState());
    g_prevState = g_state;

    Serial.print("FACTORY RESET: ads_cfg=");
    Serial.print(clearedAds ? "OK" : "FAIL");
    Serial.print(" wifi_cfg=");
    Serial.print(clearedWifi ? "OK" : "FAIL");
    Serial.print(" defaults=");
    Serial.print(savedDefaults ? "OK" : "FAIL");
    Serial.print(" AP IP=");
    Serial.println(apIp.toString());
}

static void handleFactoryResetButton() {
    const bool pressed = isFactoryResetButtonPressed();
    const uint32_t now = millis();

    if (!pressed) {
        if (g_factoryResetArmed && !g_factoryResetHandled) {
            Serial.println("FACTORY RESET: boton liberado antes del tiempo requerido");
        }
        g_factoryResetArmed = false;
        g_factoryResetHandled = false;
        g_factoryResetPressedAtMs = 0;
        g_factoryResetLastProgressLogMs = 0;
        return;
    }

    if (!g_factoryResetArmed) {
        g_factoryResetArmed = true;
        g_factoryResetHandled = false;
        g_factoryResetPressedAtMs = now;
        g_factoryResetLastProgressLogMs = now;
        Serial.println("FACTORY RESET: boton detectado, mantener 5 s para restaurar");
        return;
    }

    if (!g_factoryResetHandled && now - g_factoryResetLastProgressLogMs >= 1000) {
        g_factoryResetLastProgressLogMs = now;
        const uint32_t heldMs = now - g_factoryResetPressedAtMs;
        Serial.print("FACTORY RESET: boton presionado por ");
        Serial.print(heldMs);
        Serial.println(" ms");
    }

    if (!g_factoryResetHandled && (uint32_t)(now - g_factoryResetPressedAtMs) >= FACTORY_RESET_HOLD_MS) {
        g_factoryResetHandled = true;
        Serial.println("FACTORY RESET: tiempo cumplido, ejecutando restauracion");
        performFactoryReset();
    }
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
    gpio_config_t io_conf = {};
    io_conf.pin_bit_mask = 1ULL << FACTORY_RESET_BUTTON_PIN;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io_conf);
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

    String storedSsid;
    String storedPass;
    g_factoryModeActive = !wifiComm_loadCredentials(storedSsid, storedPass);

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
    changeState(connectivityIdleState());
}

void loop() {
    statusLed_tick();
    handleFactoryResetButton();
    if (g_factoryModeActive && (WiFi.getMode() == WIFI_STA || WiFi.getMode() == WIFI_AP_STA) && WiFi.status() == WL_CONNECTED) {
        g_factoryModeActive = false;
        if (g_state == STATE_FACTORY_MODE) {
            changeState(connectivityIdleState());
        }
    }
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
    const bool stateLocked = (g_state == STATE_ERROR || g_state == STATE_BATTERY_LOW);
    if (stateLocked) {
        // Error y bateria baja tienen prioridad sobre el resto: descartar transiciones pendientes.
        (void)wifiComm_takeStartRequest();
        (void)wifiComm_takeStopRequest();
        (void)wifiComm_takeConnRequest();
    } else {
        if (g_state == STATE_CONNECTIVITY || g_state == STATE_FACTORY_MODE) {
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
            changeState(connectivityIdleState());
        }
    }
    // Guardar config si hubo cambios pendientes (evita escribir en cada comando)
    wifiComm_saveConfigIfDirty(g_adsConfig);

    // Cambio de estado: habilitar/deshabilitar señal de test según proceda
    if (g_prevState != g_state) {
        if (g_state == STATE_STREAMING_PC || g_state == STATE_TEST ||
            g_prevState == STATE_STREAMING_PC || g_prevState == STATE_TEST) {
            resetRawSampleSanitizerState();
        }
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
        if (g_state == STATE_CONNECTIVITY || g_state == STATE_FACTORY_MODE) {
            IPAddress ip = (WiFi.getMode() == WIFI_AP || WiFi.getMode() == WIFI_AP_STA) ? WiFi.softAPIP() : WiFi.localIP();
            wifiComm_beginDiscoveryBeacon(ip, 0);
        }
        if (g_state == STATE_WIFI_CONNECTED && !g_configSent) {
            wifiComm_sendConfigSnapshot(g_adsConfig, NUM_ADS, g_state, g_factoryModeActive);
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

        case STATE_CONNECTIVITY:
            break;

        case STATE_FACTORY_MODE:
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
                    sanitizeRawSample(d, raw[d]);
                    AdsSample filtered;
                    if (filtering_processSample(d, raw[d], filtered)) {
                        sanitizeFilteredSample(d, filtered);
                        maybeLogSpikeDebug(d, raw[d], filtered);
                        newSampleReady = true;
                        netBuffer[netBufferCount * NUM_ADS + d] = filtered;
                    }
                }
                if (newSampleReady) {
                    maybeLogRawSampleRejectSummary();
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

                        wifiComm_sendSamples(netBuffer, g_adsConfig, NET_BLOCK_SAMPLES, NUM_ADS);
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
