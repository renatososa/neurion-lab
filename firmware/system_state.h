#pragma once

// Estados globales del dispositivo
enum DeviceState {
    STATE_BOOT = 0,
    STATE_IDLE,
    STATE_CALIBRATION,
    STATE_STREAMING_PC,
    STATE_STREAMING_EXTERNAL,
    STATE_TEST,
    STATE_BATTERY_LOW,
    STATE_WIFI_CONNECTED,
    STATE_CONNECTIVITY,
    STATE_ERROR
};
