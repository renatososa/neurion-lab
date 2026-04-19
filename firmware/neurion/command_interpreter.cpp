#include "command_interpreter.h"
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <stdio.h>
#include "wifi_comm.h"
#include "config_pins.h"
#include "filtering.h"
#include "battery_monitor.h"
#include <WiFi.h>

// Variables de ploteo definidas en neurion.ino
extern bool    g_plotEnable;
extern uint8_t g_plotDevice;
extern uint8_t g_plotChannel;
// Ultimas credenciales recibidas por serial
extern String g_serialWifiSsid;
extern String g_serialWifiPassword;
extern bool   g_serialWifiUpdated;

static bool isValidGain(uint8_t g) {
    return g == 1 || g == 2 || g == 4 || g == 6 || g == 8 || g == 12 || g == 24;
}

static bool parseFilterProfile(const char* text, FilterProfile& outProfile) {
    if (!text) return false;
    if (strcasecmp(text, "ECG") == 0) { outProfile = FILTER_PROFILE_ECG; return true; }
    if (strcasecmp(text, "EOG") == 0) { outProfile = FILTER_PROFILE_EOG; return true; }
    if (strcasecmp(text, "EMG") == 0) { outProfile = FILTER_PROFILE_EMG; return true; }
    if (strcasecmp(text, "EEG") == 0) { outProfile = FILTER_PROFILE_EEG; return true; }
    return false;
}

static void skipWhitespace(char*& p) {
    while (*p == ' ' || *p == '\t') ++p;
}

static bool parseWifiToken(char*& p, char* out, size_t outSize) {
    if (!out || outSize == 0) return false;
    skipWhitespace(p);
    if (*p == '\0') return false;

    size_t len = 0;
    bool quoted = false;
    if (*p == '"') {
        quoted = true;
        ++p;
        while (*p && *p != '"') {
            char c = *p++;
            if (c == '\\' && (*p == '"' || *p == '\\')) {
                c = *p++;
            }
            if (len < outSize - 1) out[len++] = c;
        }
        if (*p != '"') return false;
        ++p;
    } else {
        while (*p && *p != ' ' && *p != '\t') {
            if (len < outSize - 1) out[len++] = *p;
            ++p;
        }
    }
    out[len] = '\0';
    return quoted || len > 0;
}

static const char* wifiSignalBars(int32_t rssi) {
    if (rssi >= -55) return "[||||]";
    if (rssi >= -67) return "[||| ]";
    if (rssi >= -75) return "[||  ]";
    if (rssi >= -85) return "[|   ]";
    return "[    ]";
}

static bool wifiIsOpen(wifi_auth_mode_t mode) {
    return mode == WIFI_AUTH_OPEN;
}

static void respond(const CommandCallbacks& cbs, const char* msg) {
    if (cbs.respond) cbs.respond(msg, cbs.userCtx);
}

bool commandInterpreter_handleLine(
    char* line,
    AdsPersistentConfig& cfg,
    AdsManager& ads,
    uint8_t numAds,
    DeviceState currentState,
    CommandSource source,
    void (*changeStateFn)(DeviceState newState),
    const CommandCallbacks& cbs,
    bool& configDirty) {
    if (!line) return false;
    // trim inicial
    while (*line == ' ' || *line == '\t') ++line;
    if (!*line) return false;

    // SET_WIFI: soporta comillas para SSID/password con espacios
    if (strncmp(line, "SET_WIFI", 8) == 0 || strncmp(line, "set_wifi", 8) == 0) {
        char* p = line + 8;
        char ssidBuf[64];
        char pwdBuf[64];
        if (!parseWifiToken(p, ssidBuf, sizeof(ssidBuf)) ||
            !parseWifiToken(p, pwdBuf, sizeof(pwdBuf))) {
            respond(cbs, "ERR WIFI FORMAT Usa SET_WIFI \"ssid\" \"password\"; para red abierta usa \"\"");
            return true;
        }
        skipWhitespace(p);
        if (*p != '\0') {
            respond(cbs, "ERR WIFI FORMAT Usa SET_WIFI \"ssid\" \"password\"; para red abierta usa \"\"");
            return true;
        }

        g_serialWifiSsid     = ssidBuf;
        g_serialWifiPassword = pwdBuf;
        IPAddress staIp;
        IPAddress apIp;
        String wifiErr;
        if (wifiComm_connectStaKeepAp(ssidBuf, pwdBuf, staIp, &apIp, &wifiErr)) {
            wifiComm_saveCredentials(ssidBuf, pwdBuf);
            g_serialWifiUpdated = true;
            String msg = "OK WIFI STA " + staIp.toString() + " AP " + apIp.toString();
            respond(cbs, msg.c_str());
        } else {
            g_serialWifiUpdated = false;
            if (wifiErr.length() == 0) wifiErr = "ERR WIFI";
            respond(cbs, wifiErr.c_str());
        }
        return true;
    }

    // Tokenizar
    char* tokens[8] = {0};
    uint8_t ntok = 0;
    char* p = strtok(line, " \t\r\n");
    while (p && ntok < 8) {
        tokens[ntok++] = p;
        p = strtok(nullptr, " \t\r\n");
    }
    if (ntok == 0) return false;

    // Comandos cortos de estado (solo para serial)
    if (ntok == 1 && tokens[0][1] == '\0') {
        char c = tokens[0][0];
        if (c == 'p') { changeStateFn(STATE_STREAMING_PC); respond(cbs, "OK STATE STREAMING_PC"); return true; }
        if (c == 'i') { changeStateFn(STATE_IDLE); respond(cbs, "OK STATE IDLE"); return true; }
        if (c == 'c') { changeStateFn(STATE_CALIBRATION); respond(cbs, "OK STATE CALIBRATION"); return true; }
        if (c == 't') { changeStateFn(STATE_TEST); respond(cbs, "OK STATE TEST"); return true; }
        if (c == 'x') { changeStateFn(STATE_ERROR); respond(cbs, "OK STATE ERROR"); return true; }
    }

    const bool stateLocked = (currentState == STATE_ERROR || currentState == STATE_BATTERY_LOW);

    // Handshake/red de UDP
    if (strcmp(tokens[0], "START") == 0) {
        if (stateLocked) {
            respond(cbs, "ERR STATE");
            return true;
        }
        if (cbs.onStart) cbs.onStart(cbs.userCtx);
        respond(cbs, "OK START");
        return true;
    }
    if (strcmp(tokens[0], "STOP") == 0) {
        if (stateLocked) {
            respond(cbs, "ERR STATE");
            return true;
        }
        if (cbs.onStop) cbs.onStop(cbs.userCtx);
        respond(cbs, "OK STOP");
        return true;
    }
    if (strcmp(tokens[0], "CONNECTIVITY") == 0) {
        if (stateLocked) {
            respond(cbs, "ERR STATE");
            return true;
        }
        if (cbs.onConnectivity) cbs.onConnectivity(cbs.userCtx);
        respond(cbs, "OK CONNECTIVITY");
        return true;
    }
    if (strcmp(tokens[0], "DISCOVERY_REPLY") == 0 && ntok >= 2) {
        if (stateLocked) {
            respond(cbs, "ERR STATE");
            return true;
        }
        if (cbs.onDiscoveryReply) {
            bool ok = cbs.onDiscoveryReply(tokens[1], cbs.userCtx);
            respond(cbs, ok ? "OK DISCOVERY" : "ERR DISCOVERY");
        } else {
            respond(cbs, "ERR DISCOVERY");
        }
        return true;
    }
    if (strcmp(tokens[0], "BATT") == 0) {
        char msg[24];
        snprintf(msg, sizeof(msg), "OK BATT %u", batteryMonitor_readLevelByte());
        respond(cbs, msg);
        return true;
    }

    // En estado de error, rechazar comandos (solo UDP)
    if (currentState == STATE_ERROR) {
        respond(cbs, "ERR STATE");
        return true;
    }

    if (strcmp(tokens[0], "CH") == 0 && ntok >= 6) {
        uint8_t dev = (uint8_t)atoi(tokens[1]);
        uint8_t ch  = (uint8_t)atoi(tokens[2]);
        uint8_t gain= (uint8_t)atoi(tokens[3]);
        bool pd    = atoi(tokens[4]) != 0;
        bool test  = atoi(tokens[5]) != 0;
        if (dev >= cfg.numDevices || dev >= numAds || ch >= ADS_NUM_CHANNELS || !isValidGain(gain)) {
            respond(cbs, "ERR CH");
            return true;
        }
        AdsChannelConfig chCfg{gain, pd, test};
        cfg.dev[dev].ch[ch].gain = gain;
        cfg.dev[dev].ch[ch].powerDown = pd;
        cfg.dev[dev].ch[ch].testSignal = test;
        ads.setChannelConfig(dev, ch, chCfg);
        configDirty = true;
        respond(cbs, "OK CH");
        return true;
    }
    if (strcmp(tokens[0], "FILTER") == 0 && ntok >= 4) {
        uint8_t dev = (uint8_t)atoi(tokens[1]);
        uint8_t ch  = (uint8_t)atoi(tokens[2]);
        FilterProfile profile = FILTER_PROFILE_ECG;
        if (dev >= cfg.numDevices || dev >= numAds || ch >= ADS_NUM_CHANNELS || !parseFilterProfile(tokens[3], profile)) {
            respond(cbs, "ERR FILTER");
            return true;
        }
        cfg.dev[dev].ch[ch].filterProfile = profile;
        filtering_setChannelProfile(dev, ch, profile);
        configDirty = true;
        respond(cbs, "OK FILTER");
        return true;
    }
    if (strcmp(tokens[0], "BIAS") == 0 && ntok >= 4) {
        uint8_t dev = (uint8_t)atoi(tokens[1]);
        uint8_t sp  = (uint8_t)strtoul(tokens[2], nullptr, 0);
        uint8_t sn  = (uint8_t)strtoul(tokens[3], nullptr, 0);
        if (dev >= cfg.numDevices || dev >= numAds) { respond(cbs, "ERR BIAS"); return true; }
        cfg.dev[dev].biasSensP = sp;
        cfg.dev[dev].biasSensN = sn;
        ads.setBiasSelection(dev, sp, sn);
        configDirty = true;
        respond(cbs, "OK BIAS");
        return true;
    }
    if (strcmp(tokens[0], "BIASDRV") == 0 && ntok >= 3) {
        uint8_t dev = (uint8_t)atoi(tokens[1]);
        if (dev >= cfg.numDevices || dev >= numAds) { respond(cbs, "ERR BIASDRV"); return true; }
        // Modo raw: escribir CONFIG3 directamente si el tercer argumento es 0x..
        if (tokens[2][0] == '0' && (tokens[2][1] == 'x' || tokens[2][1] == 'X')) {
            uint8_t val = (uint8_t)strtoul(tokens[2], nullptr, 0);
            bool ok = ads.setConfig3(dev, val);
            respond(cbs, ok ? "OK BIASDRV RAW" : "ERR BIASDRV");
            return true;
        }
        int en = atoi(tokens[2]);
        int refInt = (ntok >= 4) ? atoi(tokens[3]) : 1;
        if ((en != 0 && en != 1) || (refInt != 0 && refInt != 1)) {
            respond(cbs, "ERR BIASDRV");
            return true;
        }
        bool ok = ads.setBiasDriverEnabled(dev, en != 0, refInt != 0);
        respond(cbs, ok ? "OK BIASDRV" : "ERR BIASDRV");
        return true;
    }
    if (strcmp(tokens[0], "TEST") == 0 && ntok >= 5) {
        uint8_t dev = (uint8_t)atoi(tokens[1]);
        AdsTestSignal tcfg;
        tcfg.enable = atoi(tokens[2]) != 0;
        tcfg.highAmplitude = atoi(tokens[3]) != 0;
        tcfg.freqSel = (uint8_t)atoi(tokens[4]);
        if (dev >= cfg.numDevices || dev >= numAds || tcfg.freqSel > 3) { respond(cbs, "ERR TEST"); return true; }
        cfg.dev[dev].testSignal = tcfg;
        ads.setTestSignal(dev, tcfg);
        configDirty = true;
        respond(cbs, "OK TEST");
        return true;
    }
    if (strcmp(tokens[0], "SAVE") == 0) {
        if (adsConfig_save(cfg)) { configDirty = false; respond(cbs, "OK SAVE"); }
        else respond(cbs, "ERR SAVE");
        return true;
    }
    if (strcmp(tokens[0], "LOAD") == 0) {
        if (adsConfig_load(cfg)) {
            wifiComm_applyConfig(cfg, ads, numAds);
            configDirty = false;
            respond(cbs, "OK LOAD");
        } else {
            respond(cbs, "ERR LOAD");
        }
        return true;
    }
    if (strcmp(tokens[0], "DUMP") == 0) {
        if (ads.device(0) && ads.device(0)->initialized()) {
            if (source == CommandSource::Udp) {
                // Pausar conversiones para leer registros coherentes (igual que dump por Serial)
                ads.stopAll(numAds);

                struct RegInfo { AdsRegister reg; const char* name; };
                const RegInfo regs[] = {
                    { ADS_REG_ID,        "ID" },
                    { ADS_REG_CONFIG1,   "CONFIG1" },
                    { ADS_REG_CONFIG2,   "CONFIG2" },
                    { ADS_REG_CONFIG3,   "CONFIG3" },
                    { ADS_REG_LOFF,      "LOFF" },
                    { ADS_REG_BIAS_SENSP,"BIAS_SENSP" },
                    { ADS_REG_BIAS_SENSN,"BIAS_SENSN" },
                    { ADS_REG_CONFIG4,   "CONFIG4" },
                    { ADS_REG_MISC1,     "MISC1" },
                    { ADS_REG_MISC2,     "MISC2" },
                    { ADS_REG_GPIO,      "GPIO" }
                };

                String msg = "OK DUMP\n";
                uint8_t maxDev = cfg.numDevices < numAds ? cfg.numDevices : numAds;
                for (uint8_t d = 0; d < maxDev; ++d) {
                    auto devPtr = ads.device(d);
                    if (!devPtr || !devPtr->initialized()) {
                        msg += "DEV ";
                        msg += d;
                        msg += " ERR\n";
                        continue;
                    }
                    msg += "DEV ";
                    msg += d;
                    msg += "\n";
                    for (const auto& ri : regs) {
                        uint8_t v = 0;
                        devPtr->readRegister(ri.reg, v);
                        char line[64];
                        snprintf(line, sizeof(line), "%s (0x%02X) = 0x%02X\n",
                                 ri.name, static_cast<uint8_t>(ri.reg), v);
                        msg += line;
                    }
                    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
                        uint8_t v = 0;
                        devPtr->readRegister(static_cast<AdsRegister>(ADS_REG_CH1SET + ch), v);
                        char line[32];
                        snprintf(line, sizeof(line), "CH%uSET = 0x%02X\n", ch + 1, v);
                        msg += line;
                        msg += "CH";
                        msg += String(ch + 1);
                        msg += "FILTER = ";
                        msg += filtering_getProfileName(cfg.dev[d].ch[ch].filterProfile);
                        msg += "\n";
                    }
                }
                if (msg.length() == 0) {
                    msg = "OK DUMP (sin datos)";
                }
                respond(cbs, msg.c_str());
                ads.startAll(numAds);
            } else {
                ads.device(0)->dumpRegisters(Serial);
                respond(cbs, "OK DUMP");
            }
        } else {
            respond(cbs, "ERR DUMP");
        }
        return true;
    }
    if ((strcmp(tokens[0], "T") == 0 || strcmp(tokens[0], "t") == 0) && ntok >= 4) {
        int plotEn = atoi(tokens[1]);
        uint8_t dev = (uint8_t)atoi(tokens[2]);
        uint8_t ch = 0;
        bool allCh = (strcmp(tokens[3], "all") == 0 || strcmp(tokens[3], "ALL") == 0);
        if (!allCh) ch = (uint8_t)atoi(tokens[3]);
        if (ch == 8) allCh = true; // alias
        if (plotEn < 0 || plotEn > 1 || dev >= numAds || (!allCh && ch >= ADS_NUM_CHANNELS)) {
            respond(cbs, "ERR T");
            return true;
        }
        g_plotEnable  = plotEn != 0;
        g_plotDevice  = dev;
        g_plotChannel = allCh ? 0xFF : ch;
        respond(cbs, "OK T");
        return true;
    }
    if (strcmp(tokens[0], "APMode") == 0 || strcmp(tokens[0], "APMODE") == 0 || strcmp(tokens[0], "apmode") == 0) {
        IPAddress apIp;
        if (wifiComm_startAp(apIp)) {
            String msg = String("OK AP ") + WIFI_AP_SSID + " " + WIFI_AP_PASSWORD + " " + apIp.toString();
            respond(cbs, msg.c_str());
        } else {
            respond(cbs, "ERR AP");
        }
        return true;
    }
    if (strcmp(tokens[0], "SCAN_WIFI") == 0 || strcmp(tokens[0], "scan_wifi") == 0) {
        wifi_mode_t prevMode = WiFi.getMode();
        bool restoreMode = false;

        if (prevMode == WIFI_AP) {
            WiFi.mode(WIFI_AP_STA);
            restoreMode = true;
            delay(100);
        } else if (prevMode == WIFI_MODE_NULL) {
            WiFi.mode(WIFI_STA);
            restoreMode = true;
            delay(100);
        }

        int networks = WiFi.scanNetworks(false, true);
        if (networks < 0) {
            WiFi.scanDelete();
            if (restoreMode) {
                WiFi.mode(prevMode);
            }
            respond(cbs, "ERR SCAN_WIFI");
            return true;
        }

        String header = String("OK SCAN_WIFI ") + networks;
        respond(cbs, header.c_str());
        if (networks == 0) {
            respond(cbs, "No se encontraron redes visibles.");
        } else {
            for (int i = 0; i < networks; ++i) {
                String ssid = WiFi.SSID(i);
                if (ssid.length() == 0) ssid = "<oculta>";
                wifi_auth_mode_t auth = WiFi.encryptionType(i);
                String line = String(i + 1) + ". " + ssid + " " + wifiSignalBars(WiFi.RSSI(i));
                if (wifiIsOpen(auth)) {
                    line += " abierta";
                }
                respond(cbs, line.c_str());
            }
        }
        WiFi.scanDelete();
        if (restoreMode) {
            WiFi.mode(prevMode);
        }
        return true;
    }
    if (strcmp(tokens[0], "HPF") == 0 && ntok >= 2) {
        int en = atoi(tokens[1]);
        if (en != 0 && en != 1) {
            respond(cbs, "ERR HPF");
            return true;
        }
        filtering_setHighpassEnabled(en != 0);
        respond(cbs, en ? "OK HPF ON" : "OK HPF OFF");
        return true;
    }
    if (strcmp(tokens[0], "LPF") == 0 && ntok >= 2) {
        int en = atoi(tokens[1]);
        if (en != 0 && en != 1) {
            respond(cbs, "ERR LPF");
            return true;
        }
        filtering_setLowpassEnabled(en != 0);
        respond(cbs, en ? "OK LPF ON" : "OK LPF OFF");
        return true;
    }

    respond(cbs, "ERR CMD");
    return true;
}
