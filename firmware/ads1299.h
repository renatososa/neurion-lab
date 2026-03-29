#pragma once
#include <Arduino.h>
#include "config_pins.h"

// NA§mero mA­ximo de ADS1299
static const uint8_t ADS_MAX_DEVICES   = 4;
static const uint8_t ADS_NUM_CHANNELS  = 8;

// Comandos SPI del ADS1299
enum AdsCommand : uint8_t {
    ADS_CMD_WAKEUP  = 0x02,
    ADS_CMD_STANDBY = 0x04,
    ADS_CMD_RESET   = 0x06,
    ADS_CMD_START   = 0x08,
    ADS_CMD_STOP    = 0x0A,
    ADS_CMD_RDATAC  = 0x10,
    ADS_CMD_SDATAC  = 0x11,
    ADS_CMD_RDATA   = 0x12
};

// Direcciones de registros (parcial)
enum AdsRegister : uint8_t {
    ADS_REG_ID       = 0x00,
    ADS_REG_CONFIG1  = 0x01,
    ADS_REG_CONFIG2  = 0x02,
    ADS_REG_CONFIG3  = 0x03,
    ADS_REG_LOFF     = 0x04,
    ADS_REG_CH1SET   = 0x05, // CH1..CH8 = 0x05..0x0C
    ADS_REG_BIAS_SENSP = 0x0D,
    ADS_REG_BIAS_SENSN = 0x0E,
    ADS_REG_GPIO     = 0x14,
    ADS_REG_MISC1    = 0x15,
    ADS_REG_MISC2    = 0x16,
    ADS_REG_CONFIG4  = 0x17
};

// Una muestra de 8 canales de un ADS
struct AdsSample {
    int32_t ch[ADS_NUM_CHANNELS];
};

// Configuracion por canal
struct AdsChannelConfig {
    uint8_t gain;        // Ganancia del PGA. Valores vA­lidos: 1,2,4,6,8,12,24.
    bool    powerDown;   // true: canal apagado (bit PD); false: canal activo.
    bool    testSignal;  // true: usa seAal de test interna; false: entrada normal.
};

// Config de seAal de test interna
struct AdsTestSignal {
    bool    enable;        // INT_TEST: habilita la seAal de test interna
    bool    highAmplitude; // TEST_AMP: true=Vref/2.4, false=Vref/4.8
    uint8_t freqSel;       // TEST_FREQ: 0=DC,1=1Hz,2=Fs/2,3=Fs/4
};

class Ads1299Device {
public:
    Ads1299Device();

    // Asigna los pines CS/DRDY (debe llamarse antes de init).
    // Entradas: csPin, drdyPin (GPIO). No devuelve nada.
    void configurePins(uint8_t csPin, uint8_t drdyPin);

    // Inicializa registros del dispositivo (CONFIG, CHxSET, etc.) y lee ID.
    // Devuelve true si ID parece vA­lido (0x0E) y se configurA3 sin errores.
    bool init();

    // Arranca conversiones continuas (envA-a RDATAC).
    // Devuelve true si el dispositivo estA¡ inicializado.
    bool startConversions();
    // Detiene conversiones (SDATAC) y baja START comA-on desde el manager.
    bool stopConversions();

    // Lee una muestra (bloqueante, espera DRDY propio con timeout).
    // Salida: sample (8 canales int32 sign-extendidos). Devuelve false en timeout o error.
    bool readSample(AdsSample &sample);

    // Acceso de bajo nivel a registros (R/W uno).
    // Devuelve false si el dispositivo no estA¡ inicializado.
    bool writeRegister(AdsRegister reg, uint8_t value);
    bool readRegister(AdsRegister reg, uint8_t &value) const;

    // Configuracion dinA¡mica por canal: escribe CHxSET segAon AdsChannelConfig.
    // Devuelve false si el canal es invA¡lido o no estA¡ inicializado.
    bool setChannelConfig(uint8_t channel, const AdsChannelConfig& cfg);
    // Configura seAal de test interna escribiendo CONFIG2.
    bool setTestSignal(const AdsTestSignal& cfg);
    // Selecciona quA(c) canales aportan al modo comA-on (BIAS_SENSP/N).
    bool setBiasSelection(uint8_t senspMask, uint8_t sensnMask);
    // Activa/desactiva el driver de bias y elige referencia interna para bias (CONFIG3).
    // enable=true => PD_BIAS(bit2)=0. refInternal=true => BIASREF_INT(bit3)=1.
    bool setBiasDriverEnabled(bool enable, bool refInternal = true);
    // Escribe CONFIG3 completo (raw).
    bool setConfig3(uint8_t value);

    // Info/debug: volcar registros base y CHxSET a un Stream (Serial, etc.)
    bool dumpRegisters(Stream& out) const;
    uint8_t id() const { return _id; }
    bool initialized() const { return _initialized; }
    uint8_t drdyPin() const { return _drdyPin; }
    bool configured() const { return _csPin != 0; }

private:
    void select() const;
    void deselect() const;
    bool applyChannelConfig(uint8_t channel);
    uint8_t encodeChSet(const AdsChannelConfig& cfg) const;

    uint8_t _csPin;
    uint8_t _drdyPin;
    uint8_t _id;
    bool    _initialized;
    AdsChannelConfig _chCfg[ADS_NUM_CHANNELS];
};

class AdsManager {
public:
    AdsManager();

    // Inicializa el bus SPI A y pines de control (una vez en setup)
    void initBus();

    // Configura pines de un dispositivo antes de initAll
    bool setDevicePins(uint8_t index, uint8_t csPin, uint8_t drdyPin);

    // Cambia la configuraciA3n de un canal deteniendo y rearmando conversions si estaban activas
    bool setChannelConfig(uint8_t deviceIndex, uint8_t channel, const AdsChannelConfig& cfg);
    bool setTestSignal(uint8_t deviceIndex, const AdsTestSignal& cfg);
    // Máscaras de bias: cada bit = canal (bit0=CH1 .. bit7=CH8). 1 = incluir.
    bool setBiasSelection(uint8_t deviceIndex, uint8_t senspMask, uint8_t sensnMask);
    // Activa/desactiva driver de bias (PD_BIAS bit2) y referencia interna (BIASREF_INT bit3) del dispositivo indicado.
    bool setBiasDriverEnabled(uint8_t deviceIndex, bool enable, bool refInternal = true);
    // Escribe CONFIG3 completo en el dispositivo indicado.
    bool setConfig3(uint8_t deviceIndex, uint8_t value);

    // Inicializa todos los ADS (1..4). Devuelve false si alguno falla.
    bool initAll(uint8_t numDevices);

    // Start/stop conversiones continuas en todos
    void startAll(uint8_t numDevices);
    void stopAll(uint8_t numDevices);

    // Lee una muestra de todos los ADS
    bool readAll(uint8_t numDevices, AdsSample *samples);

    // Acceso a instancia concreta
    Ads1299Device* device(uint8_t index);
    uint8_t numDevices() const { return _numDevices; }

private:
    Ads1299Device _devices[ADS_MAX_DEVICES];
    uint8_t       _activeIndex[ADS_MAX_DEVICES]; // mapea indice activo -> indice real
    uint8_t       _numDevices;
    bool          _converting;
};

// Instancia global para uso sencillo
extern AdsManager Ads;

// Conversor 24 bits -> int32 (utility)
int32_t ads_convert24bit(const uint8_t data[3]);
