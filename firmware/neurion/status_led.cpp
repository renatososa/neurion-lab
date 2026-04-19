#include "status_led.h"
#include "config_pins.h"
#include "icled_wurt_spi.h"
#include <WiFi.h>

static DeviceState currentLedState = STATE_BOOT;
static bool blinkOn = true;
static uint8_t blinkR = 0, blinkG = 0, blinkB = 0;
static uint32_t lastBlinkMs = 0;
static const uint32_t BAT_BLINK_PERIOD_MS   = 700;
static const uint32_t CONN_BLINK_PERIOD_MS  = 500;
static const uint8_t LED_GAIN = 1; // reduce la corriente global del LED al mínimo

static void setColor(uint8_t r, uint8_t g, uint8_t b) {
    ICLED_SPI_setColor(r, g, b, LED_GAIN);
}

static bool isWifiStaLinked() {
    wifi_mode_t mode = WiFi.getMode();
    return (mode == WIFI_STA || mode == WIFI_AP_STA) && WiFi.status() == WL_CONNECTED;
}

void statusLed_init() {
    ICLED_SPI_init(ICLED_DIN_PIN, ICLED_CIN_PIN);
    setColor(0, 0, 0); // apagado
}

void statusLed_setState(DeviceState state) {
    currentLedState = state;
    blinkOn = true;
    lastBlinkMs = millis();
    blinkR = blinkG = blinkB = 0;

    switch (state) {
        case STATE_BOOT:              setColor(128, 128, 128); break; // blanco suave
        case STATE_IDLE:              setColor(0, 0, 128);     break; // azul
        case STATE_CALIBRATION:       setColor(255, 255, 0);   break; // amarillo
        case STATE_STREAMING_PC:      setColor(0, 255, 0);     break; // verde
        case STATE_STREAMING_EXTERNAL:setColor(0, 255, 255);   break; // cian
        case STATE_TEST:              setColor(255, 0, 255);   break; // magenta
        case STATE_WIFI_CONNECTED:    setColor(255, 165, 0);   break; // naranja sólido (GUI conectada)
        case STATE_CONNECTIVITY:
            blinkR = 0; blinkG = 0; blinkB = 128;
            if (isWifiStaLinked()) setColor(0, 0, 128);
            else                   setColor(blinkR, blinkG, blinkB);
            break; // azul fijo si STA conectado, si no parpadeo
        case STATE_FACTORY_MODE:
            blinkR = 0; blinkG = 128; blinkB = 0;
            setColor(blinkR, blinkG, blinkB);
            break;
        case STATE_BATTERY_LOW:       blinkR = 128; blinkG = 64;  blinkB = 0; setColor(blinkR, blinkG, blinkB); break; // naranja tenue
        case STATE_ERROR:             setColor(255, 0, 0);         break; // rojo fijo bien visible
    }
}

void statusLed_tick() {
    uint32_t now = millis();
    if (currentLedState == STATE_ERROR) {
        setColor(255, 0, 0);
    } else if (currentLedState == STATE_BATTERY_LOW) {
        if (now - lastBlinkMs >= BAT_BLINK_PERIOD_MS) {
            lastBlinkMs = now;
            blinkOn = !blinkOn;
            if (blinkOn) setColor(blinkR, blinkG, blinkB);
            else         setColor(0, 0, 0);
        }
    } else if (currentLedState == STATE_CONNECTIVITY) {
        if (isWifiStaLinked()) {
            setColor(0, 0, 128);
            return;
        }
        if (now - lastBlinkMs >= CONN_BLINK_PERIOD_MS) {
            lastBlinkMs = now;
            blinkOn = !blinkOn;
            if (blinkOn) setColor(blinkR, blinkG, blinkB);
            else         setColor(0, 0, 0);
        }
    } else if (currentLedState == STATE_FACTORY_MODE) {
        if (now - lastBlinkMs >= CONN_BLINK_PERIOD_MS) {
            lastBlinkMs = now;
            blinkOn = !blinkOn;
            if (blinkOn) setColor(blinkR, blinkG, blinkB);
            else         setColor(0, 0, 0);
        }
    }
}
