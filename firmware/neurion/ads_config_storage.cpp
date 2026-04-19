#include "ads_config_storage.h"
#include <Preferences.h>

static uint32_t computeCrc32(const uint8_t* data, size_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (uint8_t b = 0; b < 8; ++b) {
            if (crc & 1) crc = (crc >> 1) ^ 0xEDB88320;
            else         crc >>= 1;
        }
    }
    return ~crc;
}

void adsConfig_setDefaults(AdsPersistentConfig& cfg, uint8_t numDevices) {
    memset(&cfg, 0, sizeof(cfg));
    if (numDevices > ADS_MAX_DEVICES) numDevices = ADS_MAX_DEVICES;
    cfg.version = ADS_CONFIG_VERSION;
    cfg.numDevices = numDevices;
    for (uint8_t d = 0; d < ADS_MAX_DEVICES; ++d) {
        cfg.dev[d].testSignal = {false, false, 1};
        cfg.dev[d].biasSensP = 0xFF;
        cfg.dev[d].biasSensN = 0xFF;
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            cfg.dev[d].ch[ch].gain = 24;
            cfg.dev[d].ch[ch].powerDown = false;
            cfg.dev[d].ch[ch].testSignal = false;
            cfg.dev[d].ch[ch].filterProfile = FILTER_PROFILE_ECG;
        }
    }
    cfg.crc32 = computeCrc32(reinterpret_cast<const uint8_t*>(&cfg), sizeof(cfg) - sizeof(cfg.crc32));
}

bool adsConfig_save(const AdsPersistentConfig& cfg) {
    Preferences prefs;
    if (!prefs.begin("ads_cfg", false)) return false;
    AdsPersistentConfig toWrite = cfg;
    toWrite.crc32 = computeCrc32(reinterpret_cast<const uint8_t*>(&toWrite), sizeof(toWrite) - sizeof(toWrite.crc32));
    size_t written = prefs.putBytes("blob", &toWrite, sizeof(toWrite));
    prefs.end();
    return written == sizeof(toWrite);
}

bool adsConfig_load(AdsPersistentConfig& cfg) {
    Preferences prefs;
    if (!prefs.begin("ads_cfg", true)) return false;
    size_t len = prefs.getBytesLength("blob");
    if (len != sizeof(cfg)) { prefs.end(); return false; }
    size_t read = prefs.getBytes("blob", &cfg, sizeof(cfg));
    prefs.end();
    if (read != sizeof(cfg)) return false;
    if (cfg.version != ADS_CONFIG_VERSION) return false;
    uint32_t crc = computeCrc32(reinterpret_cast<const uint8_t*>(&cfg), sizeof(cfg) - sizeof(cfg.crc32));
    if (crc != cfg.crc32) return false;
    if (cfg.numDevices == 0 || cfg.numDevices > ADS_MAX_DEVICES) return false;
    return true;
}

bool adsConfig_clearStored() {
    Preferences prefs;
    if (!prefs.begin("ads_cfg", false)) return false;
    bool ok = prefs.clear();
    prefs.end();
    return ok;
}
