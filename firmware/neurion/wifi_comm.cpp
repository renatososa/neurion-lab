#include "wifi_comm.h"
#include "config_pins.h"
#include <WiFi.h>
#include <WiFiUdp.h>
#include <string.h>
#include <stdlib.h>
#include "esp_wifi.h"
#include "battery_monitor.h"
#include <Preferences.h>
#include "command_interpreter.h"
#include <limits.h>

static WiFiUDP udp;
static bool s_cfgDirty = false;
static IPAddress s_destIp;       // sin inicializar hasta primer comando
static uint16_t s_destPort = 0;   // sin puerto destino hasta primer comando
static bool s_udpStarted = false;
static const char* WIFI_PREF_NS = "wifi_cfg";
static const char* WIFI_PREF_SSID_KEY = "ssid";
static const char* WIFI_PREF_PASS_KEY = "pass";
static IPAddress s_beaconIp;
static uint32_t s_beaconNextMs = 0;
static uint32_t s_beaconEndMs  = 0;
static bool s_beaconContinuous = false;
static bool s_startRequested = false;
static bool s_stopRequested  = false;
static bool s_connRequested  = false;
static uint8_t s_packetCounter = 0;
static int16_t s_lastGoodPacked[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static bool s_hasLastGoodPacked[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{false}};
static bool s_holdPackedActive[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{false}};
static int16_t s_holdPackedRef[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static uint8_t s_holdPackedRecoveryCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static bool isValidGain(uint8_t g);
static constexpr float VREF_VOLTS = 4.5f;
// Paso base en uV que se divide por la ganancia efectiva del canal.
static constexpr float BASE_UV_PER_GAIN = 24.0f;
// Factor para pasar de cuentas ADS (24 bits sign-extendidas) a unidades empacadas int16.
// packed = counts * COUNTS_TO_PACKED, donde cada LSB representa (BASE_UV_PER_GAIN / gain) uV en el receptor.
static constexpr float COUNTS_TO_PACKED = (VREF_VOLTS * 1e6f) / (8388607.0f * BASE_UV_PER_GAIN);
static constexpr int32_t PACKED_SAT_THRESHOLD = 32000;
static constexpr int32_t PACKED_SPIKE_THRESHOLD = 20000;
static constexpr int32_t PACKED_RECOVERY_THRESHOLD = 4000;
static constexpr uint8_t PACKED_RECOVERY_REQUIRED = 3;

static void resetPackedGuards() {
    memset(s_lastGoodPacked, 0, sizeof(s_lastGoodPacked));
    memset(s_hasLastGoodPacked, 0, sizeof(s_hasLastGoodPacked));
    memset(s_holdPackedActive, 0, sizeof(s_holdPackedActive));
    memset(s_holdPackedRef, 0, sizeof(s_holdPackedRef));
    memset(s_holdPackedRecoveryCount, 0, sizeof(s_holdPackedRecoveryCount));
}

static int16_t sanitizePackedSample(uint8_t dev, uint8_t ch, int32_t q) {
    if (dev >= ADS_MAX_DEVICES || ch >= ADS_NUM_CHANNELS) {
        if (q > INT16_MAX) q = INT16_MAX;
        else if (q < INT16_MIN) q = INT16_MIN;
        return (int16_t)q;
    }

    const bool hasRef = s_hasLastGoodPacked[dev][ch];
    const int16_t ref = s_holdPackedActive[dev][ch] ? s_holdPackedRef[dev][ch] : s_lastGoodPacked[dev][ch];

    if (abs(q) >= PACKED_SAT_THRESHOLD) {
        if (hasRef) {
            s_holdPackedActive[dev][ch] = true;
            s_holdPackedRef[dev][ch] = ref;
            s_holdPackedRecoveryCount[dev][ch] = 0;
            return ref;
        }
        if (q > INT16_MAX) q = INT16_MAX;
        else if (q < INT16_MIN) q = INT16_MIN;
        return (int16_t)q;
    }

    if (hasRef && abs(q - (int32_t)ref) >= PACKED_SPIKE_THRESHOLD) {
        s_holdPackedActive[dev][ch] = true;
        s_holdPackedRef[dev][ch] = ref;
        s_holdPackedRecoveryCount[dev][ch] = 0;
        return ref;
    }

    if (s_holdPackedActive[dev][ch]) {
        if (abs(q - (int32_t)s_holdPackedRef[dev][ch]) <= PACKED_RECOVERY_THRESHOLD) {
            s_holdPackedRecoveryCount[dev][ch]++;
            if (s_holdPackedRecoveryCount[dev][ch] < PACKED_RECOVERY_REQUIRED) {
                return s_holdPackedRef[dev][ch];
            }
            s_holdPackedActive[dev][ch] = false;
            s_holdPackedRecoveryCount[dev][ch] = 0;
        } else {
            s_holdPackedRecoveryCount[dev][ch] = 0;
            return s_holdPackedRef[dev][ch];
        }
    }

    if (q > INT16_MAX) q = INT16_MAX;
    else if (q < INT16_MIN) q = INT16_MIN;
    int16_t packed = (int16_t)q;
    s_lastGoodPacked[dev][ch] = packed;
    s_hasLastGoodPacked[dev][ch] = true;
    return packed;
}

static String wifiStatusDetail(wl_status_t status) {
    switch (status) {
        case WL_NO_SSID_AVAIL:
            return "ERR WIFI SSID_NO_VISIBLE Verifica nombre exacto, alcance y red 2.4GHz";
        case WL_CONNECT_FAILED:
            return "ERR WIFI AUTH Fallo de autenticacion; revisa password o tipo de seguridad";
#ifdef WL_WRONG_PASSWORD
        case WL_WRONG_PASSWORD:
            return "ERR WIFI WRONG_PASSWORD La password parece incorrecta";
#endif
        case WL_CONNECTION_LOST:
            return "ERR WIFI CONNECTION_LOST Se perdio la conexion durante la asociacion";
        case WL_DISCONNECTED:
            return "ERR WIFI TIMEOUT No logro asociarse; revisa hotspot, senal o credenciales";
        case WL_IDLE_STATUS:
            return "ERR WIFI TIMEOUT El modulo quedo esperando asociacion";
        default:
            return String("ERR WIFI STATUS_") + (int)status + " Fallo al conectar en modo STA";
    }
}

static bool wifiModeHasAp(wifi_mode_t mode) {
    return mode == WIFI_AP || mode == WIFI_AP_STA;
}

static const char* wifiSnapshotState(DeviceState currentState, bool factoryModeActive) {
    (void)currentState;
    if (factoryModeActive) {
        return "FACTORY";
    }
    wifi_mode_t mode = WiFi.getMode();
    if ((mode == WIFI_STA || mode == WIFI_AP_STA) && WiFi.status() == WL_CONNECTED) {
        return "STA_CONNECTED";
    }
    if (wifiModeHasAp(mode)) {
        return "AP_CONFIG";
    }
    return "UNKNOWN";
}

bool wifiComm_init() {
    resetPackedGuards();
    // Intentar usar credenciales guardadas para modo STA
    String storedSsid, storedPass;
    IPAddress ip;
    if (wifiComm_loadCredentials(storedSsid, storedPass)) {
        if (wifiComm_connectSta(storedSsid.c_str(), storedPass.c_str(), ip)) {
            Serial.print("WiFi STA OK: ");
            Serial.println(ip.toString());
            // Anuncio en broadcast continuo hasta que un cliente responda
            wifiComm_beginDiscoveryBeacon(ip, 0);
            return true;
        }
    }
    // Fallback a modo AP
    if (wifiComm_startAp(ip)) {
        Serial.print("WiFi AP: ");
        Serial.print(WIFI_AP_SSID);
        Serial.print(" ");
        Serial.print(WIFI_AP_PASSWORD);
        Serial.print(" IP ");
        Serial.println(ip.toString());
        wifiComm_beginDiscoveryBeacon(ip);
        return true;
    }
    return false;
}

// Paquete simple: [numDevices][countPerDevice][packetIdx][battery][datos...]
// datos: por muestra, por dispositivo, 8 canales x int16 (little endian).
// Cada int16 es la seAal en pasos de (BASE_UV_PER_GAIN / gain) uV; se satura a +-32767 pasos.
void wifiComm_sendSamples(const AdsSample* samples,
                          const AdsPersistentConfig& cfg,
                          size_t countPerDevice,
                          uint8_t numDevices) {
    if (!samples || numDevices == 0 || countPerDevice == 0) return;
    (void)cfg; // la ganancia influye en las cuentas leídas; no se usa directamente al empacar

    if (!s_destIp || s_destPort == 0) return; // no enviar si no hay destino
    uint8_t devCount = numDevices;
    if (devCount > ADS_MAX_DEVICES) devCount = ADS_MAX_DEVICES;
    udp.beginPacket(s_destIp, s_destPort);

    uint8_t header[4];
    header[0] = devCount;
    header[1] = (uint8_t)countPerDevice;
    header[2] = s_packetCounter++;
    header[3] = batteryMonitor_readLevelByte(); // 0..255
    udp.write(header, 4);

    // Layout: para cada muestra k, para cada dispositivo d, 8 canales int16 (pasos de uV dependientes de ganancia)
    for (size_t k = 0; k < countPerDevice; ++k) {
        for (uint8_t d = 0; d < devCount; ++d) {
            const AdsSample& s = samples[d + k * devCount];
            for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
                float qf = (float)s.ch[ch] * COUNTS_TO_PACKED;
                int32_t q = (int32_t)(qf >= 0.0f ? qf + 0.5f : qf - 0.5f); // redondeo simple
                int16_t packed = sanitizePackedSample(d, ch, q);
                udp.write((uint8_t*)&packed, sizeof(packed));
            }
        }
    }

    udp.endPacket();
}

static bool isValidGain(uint8_t g) {
    return g == 1 || g == 2 || g == 4 || g == 6 || g == 8 || g == 12 || g == 24;
}

void wifiComm_applyConfig(const AdsPersistentConfig& cfg, AdsManager& ads, uint8_t numDevices) {
    for (uint8_t d = 0; d < cfg.numDevices && d < numDevices; ++d) {
        bool anyChannelUsesTestSignal = false;
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            AdsChannelConfig cfgCh;
            cfgCh.gain = cfg.dev[d].ch[ch].gain;
            cfgCh.powerDown = cfg.dev[d].ch[ch].powerDown;
            cfgCh.testSignal = cfg.dev[d].ch[ch].testSignal;
            anyChannelUsesTestSignal = anyChannelUsesTestSignal || cfgCh.testSignal;
            if (!isValidGain(cfgCh.gain)) cfgCh.gain = 24;
            ads.setChannelConfig(d, ch, cfgCh);
            filtering_setChannelProfile(d, ch, cfg.dev[d].ch[ch].filterProfile);
        }
        ads.setBiasSelection(d, cfg.dev[d].biasSensP, cfg.dev[d].biasSensN);
        AdsTestSignal testCfg = cfg.dev[d].testSignal;
        if (anyChannelUsesTestSignal) {
            testCfg.enable = true;
        }
        ads.setTestSignal(d, testCfg);
    }
}

void wifiComm_processCommands(AdsPersistentConfig& cfg, AdsManager& ads, uint8_t numDevices, DeviceState currentState, void (*changeStateFn)(DeviceState newState)) {
    char buf[256];
    while (true) {
        int packetSize = udp.parsePacket();
        if (packetSize <= 0) return;
        if (packetSize >= (int)sizeof(buf)) {
            while (udp.available()) udp.read(); // descartar si no cabe
            continue;
        }
        IPAddress remoteIp = udp.remoteIP();
        uint16_t remotePort = udp.remotePort();
        int len = udp.read((uint8_t*)buf, sizeof(buf) - 1);
        buf[len] = '\0';

        struct UdpContext {
            IPAddress ip;
            uint16_t port;
        } ctx{remoteIp, remotePort};

        auto responder = [](const char* msg, void* user) {
            UdpContext* c = static_cast<UdpContext*>(user);
            udp.beginPacket(c->ip, c->port);
            udp.write((const uint8_t*)msg, strlen(msg));
            udp.endPacket();
        };
        CommandCallbacks cbs{};
        cbs.respond = responder;
        cbs.userCtx = &ctx;
        cbs.onStart = [](void* user) {
            UdpContext* c = static_cast<UdpContext*>(user);
            s_destIp = c->ip;
            s_destPort = c->port;
            s_startRequested = true;
        };
        cbs.onStop = [](void* user) {
            (void)user;
            s_destIp = IPAddress();
            s_destPort = 0;
            s_stopRequested = true;
        };
        cbs.onConnectivity = [](void* user) {
            (void)user;
            s_connRequested = true;
        };
        cbs.onDiscoveryReply = [](const char* ipStr, void* user) -> bool {
            UdpContext* c = static_cast<UdpContext*>(user);
            IPAddress pcIp;
            if (!pcIp.fromString(ipStr)) return false;
            s_destIp = pcIp;
            s_destPort = c->port;
            return true;
        };

        bool dirty = s_cfgDirty;
        commandInterpreter_handleLine(
            buf,
            cfg,
            ads,
            numDevices,
            currentState,
            CommandSource::Udp,
            changeStateFn,
            cbs,
            dirty);
        s_cfgDirty = dirty;
    }
}

void wifiComm_saveConfigIfDirty(const AdsPersistentConfig& cfg) {
    if (!s_cfgDirty) return;
    if (adsConfig_save(cfg)) {
        s_cfgDirty = false;
    }
}

IPAddress wifiComm_getDestinationIp() {
    return s_destIp;
}

bool wifiComm_hasDestination() {
    return (bool)s_destIp && s_destPort != 0;
}

// Conectar como estación a una red WiFi; devuelve true y la IP obtenida.
bool wifiComm_connectSta(const char* ssid, const char* password, IPAddress& outIp, String* outError) {
    if (!ssid || !password) {
        if (outError) *outError = "ERR WIFI PARAMS Faltan SSID o password";
        return false;
    }
    if (ssid[0] == '\0') {
        if (outError) *outError = "ERR WIFI PARAMS SSID vacio";
        return false;
    }
    udp.stop();
    s_udpStarted = false;
    s_destIp = IPAddress();
    s_destPort = 0;
    resetPackedGuards();

    WiFi.disconnect(true, true);
    WiFi.mode(WIFI_STA);
    if (password[0] == '\0') {
        WiFi.begin(ssid);
    } else {
        WiFi.begin(ssid, password);
    }

    unsigned long start = millis();
    const unsigned long timeoutMs = 8000; // tiempo estándar de asociación
    while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
        delay(100);
    }
    if (WiFi.status() != WL_CONNECTED) {
        if (outError) *outError = wifiStatusDetail(WiFi.status());
        return false;
    }

    outIp = WiFi.localIP();
    udp.begin(PC_UDP_PORT);
    s_udpStarted = true;
    s_startRequested = false;
    s_stopRequested  = false;
    return true;
}

bool wifiComm_connectStaKeepAp(const char* ssid, const char* password, IPAddress& outStaIp, IPAddress* outApIp, String* outError) {
    if (!ssid || !password) {
        if (outError) *outError = "ERR WIFI PARAMS Faltan SSID o password";
        return false;
    }
    if (ssid[0] == '\0') {
        if (outError) *outError = "ERR WIFI PARAMS SSID vacio";
        return false;
    }

    const IPAddress prevDestIp = s_destIp;
    const uint16_t prevDestPort = s_destPort;
    const bool prevStartRequested = s_startRequested;
    const bool prevStopRequested = s_stopRequested;
    const bool prevConnRequested = s_connRequested;

    wifi_mode_t prevMode = WiFi.getMode();
    if (prevMode != WIFI_AP_STA) {
        WiFi.mode(WIFI_AP_STA);
        delay(100);
    }
    if (!wifiModeHasAp(prevMode)) {
        if (!WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD)) {
            if (outError) *outError = "ERR AP No se pudo mantener el AP activo";
            return false;
        }
        delay(100);
    }

    WiFi.disconnect(false, true);
    delay(100);
    if (password[0] == '\0') {
        WiFi.begin(ssid);
    } else {
        WiFi.begin(ssid, password);
    }

    unsigned long start = millis();
    const unsigned long timeoutMs = 10000;
    while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
        delay(100);
    }
    if (WiFi.status() != WL_CONNECTED) {
        if (outApIp) *outApIp = WiFi.softAPIP();
        if (outError) *outError = wifiStatusDetail(WiFi.status());
        return false;
    }

    outStaIp = WiFi.localIP();
    if (outApIp) *outApIp = WiFi.softAPIP();

    if (!s_udpStarted) {
        udp.begin(PC_UDP_PORT);
        s_udpStarted = true;
    }
    s_destIp = prevDestIp;
    s_destPort = prevDestPort;
    s_startRequested = prevStartRequested;
    s_stopRequested = prevStopRequested;
    s_connRequested = prevConnRequested;
    return true;
}

// Arranca modo AP con las credenciales configuradas; devuelve IP del AP.
bool wifiComm_startAp(IPAddress& outIp) {
    udp.stop();
    s_udpStarted = false;
    s_destIp = IPAddress();
    s_destPort = 0;
    s_startRequested = false;
    s_stopRequested  = false;
    resetPackedGuards();

    WiFi.disconnect(true, true);
    WiFi.mode(WIFI_AP);
    if (!WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD)) {
        return false;
    }
    udp.begin(PC_UDP_PORT);
    s_udpStarted = true;
    outIp = WiFi.softAPIP();
    wifiComm_beginDiscoveryBeacon(outIp, 0); // continuo hasta que un cliente responda
    return true;
}

bool wifiComm_saveCredentials(const String& ssid, const String& password) {
    if (ssid.isEmpty()) return false;
    Preferences prefs;
    if (!prefs.begin(WIFI_PREF_NS, false)) return false;
    prefs.putString(WIFI_PREF_SSID_KEY, ssid);
    prefs.putString(WIFI_PREF_PASS_KEY, password);
    prefs.end();
    return true;
}

bool wifiComm_loadCredentials(String& ssid, String& password) {
    Preferences prefs;
    if (!prefs.begin(WIFI_PREF_NS, true)) return false;
    ssid = prefs.getString(WIFI_PREF_SSID_KEY, "");
    password = prefs.getString(WIFI_PREF_PASS_KEY, "");
    prefs.end();
    return !ssid.isEmpty();
}

bool wifiComm_clearCredentials() {
    Preferences prefs;
    if (!prefs.begin(WIFI_PREF_NS, false)) return false;
    bool ok = prefs.clear();
    prefs.end();
    return ok;
}

// Enviar anuncio broadcast con la IP actual (para discovery en PC)
void wifiComm_sendDiscovery(const IPAddress& ip) {
    if (!s_udpStarted) return;
    IPAddress bcast(255, 255, 255, 255);
    udp.beginPacket(bcast, PC_UDP_PORT);
    String msg = "DISCOVERY " + ip.toString();
    udp.write((const uint8_t*)msg.c_str(), msg.length());
    udp.endPacket();
}

void wifiComm_beginDiscoveryBeacon(const IPAddress& ip, uint32_t durationMs) {
    s_beaconIp = ip;
    uint32_t now = millis();
    s_beaconNextMs = now;
    if (durationMs == 0) {
        s_beaconContinuous = true;
        s_beaconEndMs = 0;
    } else {
        s_beaconContinuous = false;
        s_beaconEndMs  = now + durationMs;
    }
}

void wifiComm_tick() {
    if (!s_udpStarted) return;
    if (!s_beaconContinuous && s_beaconEndMs == 0) return;
    uint32_t now = millis();
    if (!s_beaconContinuous) {
        if ((int32_t)(now - s_beaconEndMs) >= 0) {
            s_beaconEndMs = 0;
            return;
        }
    }
    if ((int32_t)(now - s_beaconNextMs) >= 0) {
        wifiComm_sendDiscovery(s_beaconIp);
        s_beaconNextMs = now + 1000; // cada 1s durante la ventana
    }
}

void wifiComm_stopDiscoveryBeacon() {
    s_beaconEndMs = 0;
    s_beaconContinuous = false;
}

bool wifiComm_hasClient(IPAddress& outIp) {
    return false;
}

bool wifiComm_takeStartRequest() {
    if (!s_startRequested) return false;
    s_startRequested = false;
    return true;
}

bool wifiComm_takeStopRequest() {
    if (!s_stopRequested) return false;
    s_stopRequested = false;
    resetPackedGuards();
    return true;
}

bool wifiComm_takeConnRequest() {
    if (!s_connRequested) return false;
    s_connRequested = false;
    s_destIp = IPAddress();
    s_destPort = 0;
    s_startRequested = false;
    s_stopRequested = false;
    resetPackedGuards();
    return true;
}

void wifiComm_sendConfigSnapshot(const AdsPersistentConfig& cfg, uint8_t numDevices, DeviceState currentState, bool factoryModeActive) {
    if (!s_udpStarted || !s_destIp || s_destPort == 0) return;
    WiFiUDP cfgUdp;
    cfgUdp.beginPacket(s_destIp, s_destPort);
    cfgUdp.printf("CFG NUM_DEV %u\n", numDevices);
    cfgUdp.printf("CFG FS %u\n", FS_OUTPUT_HZ);
    cfgUdp.printf("CFG BATT %u\n", batteryMonitor_readLevelByte());
    cfgUdp.printf("CFG WIFI_STATE %s\n", wifiSnapshotState(currentState, factoryModeActive));
    cfgUdp.printf("CFG STA_IP %s\n", WiFi.localIP().toString().c_str());
    cfgUdp.printf("CFG AP_IP %s\n", WiFi.softAPIP().toString().c_str());
    for (uint8_t d = 0; d < cfg.numDevices && d < numDevices; ++d) {
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            uint8_t gain = cfg.dev[d].ch[ch].gain;
            bool test = cfg.dev[d].ch[ch].testSignal;
            bool pd   = cfg.dev[d].ch[ch].powerDown;
            const char* profileName = filtering_getProfileName(cfg.dev[d].ch[ch].filterProfile);
            cfgUdp.printf("DEV %u CH %u GAIN %u TEST %u PD %u FILTER %s\n",
                          d, ch, gain, test ? 1 : 0, pd ? 1 : 0, profileName);
        }
    }
    cfgUdp.endPacket();
}
