#pragma once
#include <Arduino.h>

// Inicializa el bus SPI secundario (HSPI) para el WL-ICLED
void ICLED_SPI_init(int8_t pinDIN, int8_t pinCIN);

// Setea el color del WL-ICLED (0–255 por canal, gain 0–31)
void ICLED_SPI_setColor(uint8_t r, uint8_t g, uint8_t b, uint8_t gain = 31);