#include "ads1299.h"
#include <SPI.h>

// Clock SPI conservador para validar integridad de lectura en prototipos/cableado largo
static const SPISettings adsSpiSettings(2000000, MSBFIRST, SPI_MODE1);
static constexpr bool ENABLE_SPI_FRAME_DEBUG = false;
static uint32_t s_lastSpiFrameDebugMs[ADS_MAX_DEVICES] = {0};
static constexpr uint32_t SPI_FRAME_DEBUG_COOLDOWN_MS = 250;

// ---- Ads1299Device -------------------------------------------------------

Ads1299Device::Ads1299Device()
: _csPin(0), _drdyPin(0), _id(0), _initialized(false) {
    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        _chCfg[ch] = {24, false, false};
    }
}

void Ads1299Device::configurePins(uint8_t csPin, uint8_t drdyPin) {
    _csPin   = csPin;
    _drdyPin = drdyPin;
}

void Ads1299Device::select() const {
    digitalWrite(_csPin, LOW);
}

void Ads1299Device::deselect() const {
    digitalWrite(_csPin, HIGH);
}

bool Ads1299Device::writeRegister(AdsRegister reg, uint8_t value) {
    if (_csPin == 0) return false;

    select();
    SPI.beginTransaction(adsSpiSettings);

    uint8_t addr = static_cast<uint8_t>(reg) & 0x1F;
    SPI.transfer(0x40 | addr); // WREG
    SPI.transfer(0x00);        // 1 registro
    SPI.transfer(value);

    SPI.endTransaction();
    deselect();
    delayMicroseconds(4);
    return true;
}

bool Ads1299Device::readRegister(AdsRegister reg, uint8_t &value) const {
    if (_csPin == 0) return false;

    select();
    SPI.beginTransaction(adsSpiSettings);

    uint8_t addr = static_cast<uint8_t>(reg) & 0x1F;
    SPI.transfer(0x20 | addr); // RREG
    SPI.transfer(0x00);        // 1 registro
    value = SPI.transfer(0x00);

    SPI.endTransaction();
    deselect();
    delayMicroseconds(4);
    return true;
}

static void sendCommandToDevice(uint8_t csPin, uint8_t cmd) {
    digitalWrite(csPin, LOW);
    SPI.beginTransaction(adsSpiSettings);
    SPI.transfer(cmd);
    SPI.endTransaction();
    digitalWrite(csPin, HIGH);
    delayMicroseconds(4);
}

bool Ads1299Device::init() {
    _initialized = false;

    pinMode(_csPin, OUTPUT);
    pinMode(_drdyPin, INPUT);
    digitalWrite(_csPin, HIGH);

    // Asegurar SDATAC (no enviamos RESET por comando para evitar clones problemáticos)
    sendCommandToDevice(_csPin, ADS_CMD_SDATAC);
    delay(2);

    // CONFIG1: Fs según FS_ADC_HZ, modo alta resolución, sin daisy
    uint8_t drBits = 0x04;
    switch (FS_ADC_HZ) {
        case 250:  drBits = 0x06; break;
        case 500:  drBits = 0x05; break;
        case 1000: drBits = 0x04; break;
        default:   drBits = 0x04; break;
    }
    uint8_t config1 = 0x90 | (drBits & 0x07); // bit7 HR=1, DR=bits2..0
    // CONFIG2: test signals OFF (se ajusta luego si se activa test)
    uint8_t config2 = 0xC0;
    // CONFIG3: refbuf ON, bias interna OFF (alineado con ads_test)
    uint8_t config3 = 0x60;

    // Escribimos registros base
    {
        select();
        SPI.beginTransaction(adsSpiSettings);
        // WREG burst desde CONFIG1 (3 regs)
        SPI.transfer(0x40 | (static_cast<uint8_t>(ADS_REG_CONFIG1) & 0x1F));
        SPI.transfer(0x02); // 3 registros
        SPI.transfer(config1);
        SPI.transfer(config2);
        SPI.transfer(config3);
        SPI.endTransaction();
        deselect();
        delayMicroseconds(4);
    }

    writeRegister(ADS_REG_LOFF, 0x00);
    writeRegister(ADS_REG_CONFIG4, 0x00);
    // BIAS: deshabilitada por defecto
    writeRegister(ADS_REG_BIAS_SENSP, 0x00);
    writeRegister(ADS_REG_BIAS_SENSN, 0x00);

    // MISC1: SRB1 off
    writeRegister(ADS_REG_MISC1, 0x00);
    writeRegister(ADS_REG_MISC2, 0x00);
    writeRegister(ADS_REG_GPIO,  0x00);

    // Config default de cada canal
    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        applyChannelConfig(ch);
    }

    // Comprobar ID
    uint8_t id = 0;
    {
        select();
        SPI.beginTransaction(adsSpiSettings);
        SPI.transfer(0x20 | (static_cast<uint8_t>(ADS_REG_ID) & 0x1F)); // RREG
        SPI.transfer(0x00);
        id = SPI.transfer(0x00);
        SPI.endTransaction();
        deselect();
        delayMicroseconds(4);
    }

    _id = id;
    Serial.print("ADS ID leido: 0x");
    Serial.println(id, HEX);
    bool idOk = (id == 0x3E);
    if (id == 0x00 || !idOk) {
        // Reintentar con reset HW global y SDATAC, como en el sketch de prueba
        Serial.println("Reintentando lectura de ID tras reset HW...");
        digitalWrite(ADS_PWDN_PIN, HIGH);
        digitalWrite(ADS_RESET_PIN, LOW);
        delay(5);
        digitalWrite(ADS_RESET_PIN, HIGH);
        delay(150);
        sendCommandToDevice(_csPin, ADS_CMD_SDATAC);
        delay(2);

        select();
        SPI.beginTransaction(adsSpiSettings);
        SPI.transfer(0x20 | (static_cast<uint8_t>(ADS_REG_ID) & 0x1F)); // RREG
        SPI.transfer(0x00);
        id = SPI.transfer(0x00);
        SPI.endTransaction();
        deselect();
        delayMicroseconds(4);

        _id = id;
        Serial.print("ADS ID reintento: 0x");
        Serial.println(id, HEX);
        idOk = (id == 0x3E);
    }

    if (!idOk) {
        Serial.println("ERROR: ID no valido, abortando init");
        return false; // no parece ADS1299
    }

    _initialized = true;
    return true;
}

bool Ads1299Device::startConversions() {
    if (!_initialized) return false;
    sendCommandToDevice(_csPin, ADS_CMD_SDATAC);
    return true;
}

bool Ads1299Device::stopConversions() {
    if (!_initialized) return false;
    sendCommandToDevice(_csPin, ADS_CMD_SDATAC);
    return true;
}

bool Ads1299Device::readSample(AdsSample &sample) {
    if (!_initialized) return false;

    // Esperar DRDY propio en LOW
    uint32_t t0 = millis();
    while (digitalRead(_drdyPin) == HIGH) {
        // busy wait; se puede pasar a interrupciones
        if (millis() - t0 > 20) return false; // timeout ~20 ms
    }

    select();
    SPI.beginTransaction(adsSpiSettings);

    const uint8_t totalBytes = 3 + ADS_NUM_CHANNELS * 3;
    uint8_t buffer[3 + ADS_NUM_CHANNELS * 3];

    SPI.transfer(ADS_CMD_RDATA);
    delayMicroseconds(4);
    for (uint8_t i = 0; i < totalBytes; ++i) {
        buffer[i] = SPI.transfer(0x00);
    }

    SPI.endTransaction();
    deselect();

    bool shouldDebugFrame = false;
    uint8_t *p = buffer + 3; // saltar STATUS
    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        sample.ch[ch] = ads_convert24bit(p);
        if (sample.ch[ch] == 8388607L || sample.ch[ch] == (-8388607L - 1L)) {
            shouldDebugFrame = true;
        }
        p += 3;
    }

    if (ENABLE_SPI_FRAME_DEBUG && shouldDebugFrame) {
        const uint32_t now = millis();
        const uint8_t debugSlot = (_drdyPin < ADS_MAX_DEVICES) ? _drdyPin : 0;
        if ((uint32_t)(now - s_lastSpiFrameDebugMs[debugSlot]) >= SPI_FRAME_DEBUG_COOLDOWN_MS) {
            s_lastSpiFrameDebugMs[debugSlot] = now;
            Serial.print("ADS FRAME DBG ms=");
            Serial.print(now);
            Serial.print(" cs=");
            Serial.print(_csPin);
            Serial.print(" drdy=");
            Serial.print(_drdyPin);
            Serial.print(" STATUS=");
            Serial.print(buffer[0], HEX);
            Serial.print(" ");
            Serial.print(buffer[1], HEX);
            Serial.print(" ");
            Serial.println(buffer[2], HEX);
            p = buffer + 3;
            for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
                const int32_t value = sample.ch[ch];
                Serial.print("  CH");
                Serial.print(ch + 1);
                Serial.print(" bytes=");
                Serial.print(p[0], HEX);
                Serial.print(" ");
                Serial.print(p[1], HEX);
                Serial.print(" ");
                Serial.print(p[2], HEX);
                Serial.print(" conv=");
                Serial.println(value);
                p += 3;
            }
        }
    }

    return true;
}

uint8_t Ads1299Device::encodeChSet(const AdsChannelConfig& cfg) const {
    uint8_t gainCode = 0x06; // default 24x
    switch (cfg.gain) {
        case 1:  gainCode = 0x00; break;
        case 2:  gainCode = 0x01; break;
        case 4:  gainCode = 0x02; break;
        case 6:  gainCode = 0x03; break;
        case 8:  gainCode = 0x04; break;
        case 12: gainCode = 0x05; break;
        case 24: gainCode = 0x06; break;
        default: gainCode = 0x06; break;
    }

    uint8_t mux = cfg.testSignal ? 0x05 : 0x00; // 0101 = test, 0000 = normal

    uint8_t value = 0;
    if (cfg.powerDown) value |= 0x80;
    value |= (gainCode << 4);
    value |= mux;
    return value;
}

bool Ads1299Device::applyChannelConfig(uint8_t channel) {
    if (channel >= ADS_NUM_CHANNELS) return false;
    uint8_t value = encodeChSet(_chCfg[channel]);
    return writeRegister(static_cast<AdsRegister>(ADS_REG_CH1SET + channel), value);
}

bool Ads1299Device::setChannelConfig(uint8_t channel, const AdsChannelConfig& cfg) {
    if (channel >= ADS_NUM_CHANNELS) return false;
    _chCfg[channel] = cfg;
    return applyChannelConfig(channel);
}

bool Ads1299Device::setTestSignal(const AdsTestSignal& cfg) {
    if (!_initialized) return false;
    if (cfg.freqSel > 3) return false;

    // Basado en CONFIG2 del sketch de prueba (0xD5 = INT_TEST + TEST_AMP + FREQ + 0x05)
    uint8_t val = 0x05; // mantener bits bajos como en ejemplo de referencia
    if (cfg.enable)       val |= 0x80;           // INT_TEST
    if (cfg.highAmplitude)val |= 0x40;           // TEST_AMP
    val |= (cfg.freqSel & 0x03) << 4;            // TEST_FREQ
    // Bits 3..0 se mantienen en 0x05 para que el generador funcione igual que en el test
    return writeRegister(ADS_REG_CONFIG2, val);
}

bool Ads1299Device::setBiasSelection(uint8_t senspMask, uint8_t sensnMask) {
    if (!_initialized) return false;
    writeRegister(ADS_REG_BIAS_SENSP, senspMask);
    writeRegister(ADS_REG_BIAS_SENSN, sensnMask);
    return true;
}

bool Ads1299Device::setBiasDriverEnabled(bool enable, bool refInternal) {
    if (!_initialized) return false;
    uint8_t cfg3 = 0;
    if (!readRegister(ADS_REG_CONFIG3, cfg3)) return false;
    const uint8_t PD_BIAS_MASK = 0x04;      // bit2 PD_BIAS (1=apaga driver)
    const uint8_t BIASREF_INT_MASK = 0x08;  // bit3: 1 usa ref interna para bias
    if (enable) {
        cfg3 &= ~PD_BIAS_MASK;
    } else {
        cfg3 |= PD_BIAS_MASK;
    }
    if (refInternal) {
        cfg3 |= BIASREF_INT_MASK;
    } else {
        cfg3 &= ~BIASREF_INT_MASK;
    }
    return writeRegister(ADS_REG_CONFIG3, cfg3);
}

bool Ads1299Device::setConfig3(uint8_t value) {
    if (!_initialized) return false;
    return writeRegister(ADS_REG_CONFIG3, value);
}

bool Ads1299Device::dumpRegisters(Stream& out) const {
    if (!_initialized) return false;
    // Detener RDATAC para poder leer registros con RREG
    sendCommandToDevice(_csPin, ADS_CMD_SDATAC);
    delay(2);

    struct RegInfo { AdsRegister reg; const char* name; };
    const RegInfo regs[] = {
        { ADS_REG_ID,       "ID" },
        { ADS_REG_CONFIG1,  "CONFIG1" },
        { ADS_REG_CONFIG2,  "CONFIG2" },
        { ADS_REG_CONFIG3,  "CONFIG3" },
        { ADS_REG_LOFF,     "LOFF" },
        { ADS_REG_BIAS_SENSP,"BIAS_SENSP" },
        { ADS_REG_BIAS_SENSN,"BIAS_SENSN" },
        { ADS_REG_CONFIG4,  "CONFIG4" },
        { ADS_REG_MISC1,    "MISC1" },
        { ADS_REG_MISC2,    "MISC2" },
        { ADS_REG_GPIO,     "GPIO" }
    };
    for (const auto& ri : regs) {
        uint8_t v = 0;
        const_cast<Ads1299Device*>(this)->readRegister(ri.reg, v);
        out.print(ri.name); out.print(" (0x");
        out.print(static_cast<uint8_t>(ri.reg), HEX);
        out.print(") = 0x"); out.println(v, HEX);
    }
    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        uint8_t v = 0;
        const_cast<Ads1299Device*>(this)->readRegister(static_cast<AdsRegister>(ADS_REG_CH1SET + ch), v);
        out.print("CH"); out.print(ch + 1); out.print("SET = 0x"); out.println(v, HEX);
    }

    // Reanudar RDATAC
    sendCommandToDevice(_csPin, ADS_CMD_RDATAC);
    delay(2);
    return true;
}

// ---- AdsManager ----------------------------------------------------------

AdsManager::AdsManager() : _numDevices(0), _converting(false) {}

void AdsManager::initBus() {
    // Bus SPI A
    SPI.begin(ADS_SCLK_PIN, ADS_MISO_PIN, ADS_MOSI_PIN);

    // Pines START / RESET / PWDN comunes
    pinMode(ADS_START_PIN, OUTPUT);
    pinMode(ADS_RESET_PIN, OUTPUT);
    pinMode(ADS_PWDN_PIN, OUTPUT);

    // Estado seguro: PWDN en HIGH (chip activo pero esperando reset), START en LOW
    digitalWrite(ADS_PWDN_PIN, HIGH);
    digitalWrite(ADS_START_PIN, LOW);
    digitalWrite(ADS_RESET_PIN, HIGH);
}

bool AdsManager::setDevicePins(uint8_t index, uint8_t csPin, uint8_t drdyPin) {
    if (index >= ADS_MAX_DEVICES) return false;
    _devices[index].configurePins(csPin, drdyPin);
    return true;
}

bool AdsManager::setChannelConfig(uint8_t deviceIndex, uint8_t channel, const AdsChannelConfig& cfg) {
    if (deviceIndex >= _numDevices) return false;
    uint8_t realIdx = _activeIndex[deviceIndex];
    bool wasConverting = _converting;
    if (wasConverting) {
        stopAll(_numDevices);
    }
    bool ok = _devices[realIdx].setChannelConfig(channel, cfg);
    if (wasConverting) {
        startAll(_numDevices);
    }
    return ok;
}

bool AdsManager::setTestSignal(uint8_t deviceIndex, const AdsTestSignal& cfg) {
    if (deviceIndex >= _numDevices) return false;
    uint8_t realIdx = _activeIndex[deviceIndex];
    bool wasConverting = _converting;
    if (wasConverting) {
        stopAll(_numDevices);
    }
    bool ok = _devices[realIdx].setTestSignal(cfg);
    if (wasConverting) {
        startAll(_numDevices);
    }
    return ok;
}

bool AdsManager::setBiasSelection(uint8_t deviceIndex, uint8_t senspMask, uint8_t sensnMask) {
    if (deviceIndex >= _numDevices) return false;
    uint8_t realIdx = _activeIndex[deviceIndex];
    bool wasConverting = _converting;
    if (wasConverting) stopAll(_numDevices);
    bool ok = _devices[realIdx].setBiasSelection(senspMask, sensnMask);
    if (wasConverting) startAll(_numDevices);
    return ok;
}

bool AdsManager::setBiasDriverEnabled(uint8_t deviceIndex, bool enable, bool refInternal) {
    if (deviceIndex >= _numDevices) return false;
    uint8_t realIdx = _activeIndex[deviceIndex];
    bool wasConverting = _converting;
    if (wasConverting) stopAll(_numDevices);
    bool ok = _devices[realIdx].setBiasDriverEnabled(enable, refInternal);
    if (wasConverting) startAll(_numDevices);
    return ok;
}

bool AdsManager::setConfig3(uint8_t deviceIndex, uint8_t value) {
    if (deviceIndex >= _numDevices) return false;
    uint8_t realIdx = _activeIndex[deviceIndex];
    bool wasConverting = _converting;
    if (wasConverting) stopAll(_numDevices);
    bool ok = _devices[realIdx].setConfig3(value);
    if (wasConverting) startAll(_numDevices);
    return ok;
}

bool AdsManager::initAll(uint8_t numDevices) {
    if (numDevices > ADS_MAX_DEVICES) numDevices = ADS_MAX_DEVICES;
    _numDevices = 0;
    _converting = false;

    // Reset hardware global siguiendo el flujo del test: PWDN alto, pulso RESET y espera larga
    digitalWrite(ADS_PWDN_PIN, HIGH);
    digitalWrite(ADS_RESET_PIN, LOW);
    delay(5);
    digitalWrite(ADS_RESET_PIN, HIGH);
    delay(150); // tiempo para que arranque reloj/ref interna

    bool ok = true;
    for (uint8_t i = 0; i < numDevices; ++i) {
        if (!_devices[i].configured()) {
            ok = false;
            continue;
        }
        if (_devices[i].init()) {
            _activeIndex[_numDevices] = i;
            _numDevices++;
        } else {
            ok = false;
        }
    }
    return ok && _numDevices > 0;
}

void AdsManager::startAll(uint8_t numDevices) {
    if (numDevices > _numDevices) numDevices = _numDevices;
    digitalWrite(ADS_START_PIN, HIGH);
    delayMicroseconds(2);
    for (uint8_t i = 0; i < numDevices; ++i) {
        _devices[_activeIndex[i]].startConversions();
    }
    _converting = true;
}

void AdsManager::stopAll(uint8_t numDevices) {
    if (numDevices > _numDevices) numDevices = _numDevices;
    for (uint8_t i = 0; i < numDevices; ++i) {
        _devices[_activeIndex[i]].stopConversions();
    }
    digitalWrite(ADS_START_PIN, LOW);
    delayMicroseconds(2);
    _converting = false;
}

bool AdsManager::readAll(uint8_t numDevices, AdsSample *samples) {
    if (numDevices > _numDevices) numDevices = _numDevices;
    if (!samples) return false;

    // Esperar a que todos los DRDY estén LOW
    uint32_t t0 = millis();
    while (true) {
        bool allLow = true;
        for (uint8_t i = 0; i < numDevices; ++i) {
            if (digitalRead(_devices[_activeIndex[i]].drdyPin()) == HIGH) {
                allLow = false;
                break;
            }
        }
        if (allLow) break;
        if (millis() - t0 > 20) return false; // timeout ~20 ms
    }

    for (uint8_t i = 0; i < numDevices; ++i) {
        _devices[_activeIndex[i]].readSample(samples[i]);
    }
    return true;
}

Ads1299Device* AdsManager::device(uint8_t index) {
    if (index >= _numDevices) return nullptr;
    return &_devices[_activeIndex[index]];
}

// ---- Utilidades ----------------------------------------------------------

int32_t ads_convert24bit(const uint8_t data[3]) {
    uint32_t raw = ((uint32_t)data[0] << 16) |
                   ((uint32_t)data[1] << 8)  |
                   ((uint32_t)data[2]);

    if (raw & 0x800000) {
        raw |= 0xFF000000; // extender signo
    }
    return (int32_t)raw;
}
