#include "icled_wurt_spi.h"
#include "config_pins.h"
#include <SPI.h>

// Bus B sólo para el LED
static SPIClass ledSPI(HSPI);
static int8_t _pinDIN = -1;
static int8_t _pinCIN = -1;

// Construye frame de 32 bits para el WL-ICLED
static uint32_t buildFrame(uint8_t r, uint8_t g, uint8_t b, uint8_t gain) {
    // Formato APA102: 0b111xxxxx (global brightness) + B + G + R
    uint8_t header = 0xE0 | (gain & 0x1F);
    uint32_t frame = 0;
    frame |= (uint32_t)header << 24;
    frame |= (uint32_t)b << 16; // orden físico BGR en el módulo
    frame |= (uint32_t)g << 8;
    frame |= (uint32_t)r;
    return frame;
}

void ICLED_SPI_init(int8_t pinDIN, int8_t pinCIN) {
    _pinDIN = pinDIN;
    _pinCIN = pinCIN;

    // Iniciar bus HSPI para el LED
    ledSPI.begin(_pinCIN,   // SCK -> CIN
                 -1,        // MISO no usado
                 _pinDIN);  // MOSI -> DIN

    // Configuración básica (modo 0, ~1 MHz, MSB first)
    ledSPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
}

void ICLED_SPI_setColor(uint8_t r, uint8_t g, uint8_t b, uint8_t gain) {
    if (_pinDIN < 0 || _pinCIN < 0) return;

    // Start frame: 32 bits de 0
    uint8_t startFrame[4] = {0x00, 0x00, 0x00, 0x00};

    // LED frame: 32 bits
    uint32_t frame = buildFrame(r, g, b, gain);
    uint8_t ledFrame[4] = {
        (uint8_t)((frame >> 24) & 0xFF),
        (uint8_t)((frame >> 16) & 0xFF),
        (uint8_t)((frame >> 8)  & 0xFF),
        (uint8_t)( frame        & 0xFF)
    };

    // End frame: 32 bits de 1. Añadimos uno extra para asegurar latch y limpiar artefactos.
    uint8_t endFrame[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    uint8_t endFrame2[4] = {0xFF, 0xFF, 0xFF, 0xFF};

    ledSPI.transfer(startFrame, 4);
    ledSPI.transfer(ledFrame, 4);
    ledSPI.transfer(endFrame, 4);
    ledSPI.transfer(endFrame2, 4);
}
