#include "filtering.h"
#include "config_pins.h"
#include <math.h>
#include <limits.h>

// Contadores de diezmado y flags
static uint16_t decimCounter[ADS_MAX_DEVICES] = {0};
static bool s_hpfEnabled = true;  // HPF activo por defecto
static bool s_lpfEnabled = true;  // LPF activo por defecto

// HPF Butterworth 1er orden (fc=0.5 Hz @ fs=2000 Hz) en µV.
// Coeficientes calculados con SciPy: butter(1, 0.5/(2000/2), btype="highpass")
// b = [ 0.99921522, -0.99921522], a = [1.0, -0.99843044]
static constexpr double HPF_B0 = 0.99921522;
static constexpr double HPF_B1 = -0.99921522;
static constexpr double HPF_A1 = -0.99843044;

// LPF sencillo de 1er orden (EMA) a ~200 Hz sobre la señal diezmada.
// alpha = 1 - exp(-2*pi*fc/fs_out)
static constexpr double LPF_CUTOFF_HZ = 200.0;
static constexpr double LPF_ALPHA = 1.0 - exp(-2.0 * 3.141592653589793 * LPF_CUTOFF_HZ / (double)FS_OUTPUT_HZ);

// Conversión cuentas -> microvoltios (asumiendo Vref=4.5 V y ganancia 24).
static constexpr double ADS_VREF_VOLTS = 4.5;
static constexpr double ADS_GAIN_ASSUMED = 24.0;
static constexpr double LSB_UV = (ADS_VREF_VOLTS / (ADS_GAIN_ASSUMED * 8388607.0)) * 1e6;

static double xPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double yPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double lpPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};

static inline int32_t saturateToInt32(double v) {
    if (v > (double)INT32_MAX) return INT32_MAX;
    if (v < (double)INT32_MIN) return INT32_MIN;
    return (int32_t)lrint(v);
}

void filtering_init() {
    for (uint8_t d = 0; d < ADS_MAX_DEVICES; ++d) {
        decimCounter[d] = 0;
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            xPrevUv[d][ch] = 0.0;
            yPrevUv[d][ch] = 0.0;
            lpPrevUv[d][ch] = 0.0;
        }
    }
}

void filtering_setHighpassEnabled(bool enabled) { s_hpfEnabled = enabled; }
bool filtering_isHighpassEnabled() { return s_hpfEnabled; }
void filtering_setLowpassEnabled(bool enabled) { s_lpfEnabled = enabled; }
bool filtering_isLowpassEnabled() { return s_lpfEnabled; }

bool filtering_processSample(uint8_t deviceIndex,
                             const AdsSample& inSample,
                             AdsSample& outSample) {
    if (deviceIndex >= ADS_MAX_DEVICES) return false;

    // Decimación: solo filtramos/actualizamos estado cuando se cumple el factor.
    decimCounter[deviceIndex]++;
    if (decimCounter[deviceIndex] < DECIMATION_FACTOR) {
        return false;
    }
    decimCounter[deviceIndex] = 0;

    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        double x_uv = (double)inSample.ch[ch] * LSB_UV;
        double y_uv = HPF_B0 * x_uv + HPF_B1 * xPrevUv[deviceIndex][ch]
                      - HPF_A1 * yPrevUv[deviceIndex][ch];
        xPrevUv[deviceIndex][ch] = x_uv;
        yPrevUv[deviceIndex][ch] = y_uv;

        double hp_out_uv = s_hpfEnabled ? y_uv : x_uv;

        double lp_out_uv = lpPrevUv[deviceIndex][ch] + LPF_ALPHA * (hp_out_uv - lpPrevUv[deviceIndex][ch]);
        lpPrevUv[deviceIndex][ch] = lp_out_uv;

        double final_uv = s_lpfEnabled ? lp_out_uv : hp_out_uv;
        double final_counts = final_uv / LSB_UV;
        outSample.ch[ch] = saturateToInt32(final_counts);
    }

    return true; // muestra decimada lista
}
