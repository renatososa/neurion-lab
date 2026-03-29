#include "user_hooks.h"

// Implementaciones débiles: el usuario puede sobreescribirlas en otro .cpp
__attribute__((weak)) void userProcessSamples(const AdsSample* samples, size_t countPerDevice, uint8_t numDevices) {
    (void)samples;
    (void)countPerDevice;
    (void)numDevices;
}

__attribute__((weak)) void userOnStateChange(DeviceState newState) {
    (void)newState;
}
