#include "battery_monitor.h"
#include "config_pins.h"

static bool     s_batteryLow = false;
static uint32_t s_lastCheckMs = 0;
static DeviceState s_beforeBattery = STATE_IDLE;

void batteryMonitor_init() {
    pinMode(BATTERY_ADC_PIN, INPUT);
    s_batteryLow = false;
    s_lastCheckMs = millis();
    s_beforeBattery = STATE_IDLE;
}

DeviceState batteryMonitor_update(DeviceState currentState) {
    // Si estamos en error, no cambiamos estado por batería para evitar parpadeos cruzados
    if (currentState == STATE_ERROR) {
        return currentState;
    }

    uint32_t nowMs = millis();
    if (nowMs - s_lastCheckMs < BATTERY_CHECK_MS) {
        return currentState;
    }
    s_lastCheckMs = nowMs;

uint16_t raw = analogRead(BATTERY_ADC_PIN);
    if (raw < BATTERY_LOW_THRESHOLD) {
        if (!s_batteryLow) {
            s_batteryLow = true;
            s_beforeBattery = currentState;
            return STATE_BATTERY_LOW;
        }
    } else {
        if (s_batteryLow) {
            s_batteryLow = false;
            return s_beforeBattery;
        }
    }
    return currentState;
}

uint8_t batteryMonitor_readLevelByte() {
    // Lectura cruda -> aproximación de voltaje usando la calibración actual:
    // El byte previo (raw*255/4095) da ~146 a 4.05 V y ~137 a 3.80 V.
    // Ajuste lineal: V ≈ m*byte + b.
    const double m = (4.05 - 3.80) / (146.0 - 137.0); // ~0.02778 V por byte
    const double b = 4.05 - m * 146.0;                // ~-0.00514 V

    uint16_t raw = analogRead(BATTERY_ADC_PIN); // 0..4095 en ESP32
    uint32_t byteRaw = (uint32_t)raw * 255u / 4095u;  // 0..255
    double volts = m * (double)byteRaw + b;

    // Mapear 3.5 V -> 10, 4.15 V -> 100 (clamp), lineal en el medio
    const double v_min = 3.50;
    const double v_max = 4.15;
    uint8_t level = 0;
    if (volts <= v_min) {
        level = 10;
    } else if (volts >= v_max) {
        level = 100;
    } else {
        double frac = (volts - v_min) / (v_max - v_min);
        double val = 10.0 + frac * (100.0 - 10.0);
        if (val < 0.0) val = 0.0;
        if (val > 255.0) val = 255.0;
        level = (uint8_t)(val + 0.5);
    }
    return level;
}
