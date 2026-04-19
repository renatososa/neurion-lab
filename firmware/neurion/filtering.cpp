#include "filtering.h"
#include "config_pins.h"
#include <math.h>
#include <limits.h>

// Estado de filtros por canal para el pipeline fijo a 1 kHz.
static bool s_hpfEnabled = true;
static bool s_lpfEnabled = true;
static bool s_medianEnabled = true;

static constexpr FilterProfileSpec FILTER_PROFILE_SPECS[] = {
    { FILTER_PROFILE_ECG, "ECG", 0.5f, 100.0f },
    { FILTER_PROFILE_EOG, "EOG", 0.1f, 30.0f },
    { FILTER_PROFILE_EMG, "EMG", 20.0f, 200.0f },
    { FILTER_PROFILE_EEG, "EEG", 0.5f, 40.0f },
};

// Conversión cuentas -> microvoltios (asumiendo Vref=4.5 V y ganancia 24).
static constexpr double ADS_VREF_VOLTS = 4.5;
static constexpr double ADS_GAIN_ASSUMED = 24.0;
static constexpr double LSB_UV = (ADS_VREF_VOLTS / (ADS_GAIN_ASSUMED * 8388607.0)) * 1e6;

static double xPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double yPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double lpPrevUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double medianUv[ADS_MAX_DEVICES][ADS_NUM_CHANNELS][3] = {{{0}}};
static uint8_t medianCount[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static uint8_t medianPos[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static FilterProfile s_channelProfile[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{FILTER_PROFILE_ECG}};
static double s_hpfB0[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double s_hpfB1[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double s_hpfA1[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};
static double s_lpfAlpha[ADS_MAX_DEVICES][ADS_NUM_CHANNELS] = {{0}};

static FilterProfile normalizeFilterProfile(FilterProfile profile) {
    switch (profile) {
        case FILTER_PROFILE_ECG:
        case FILTER_PROFILE_EOG:
        case FILTER_PROFILE_EMG:
        case FILTER_PROFILE_EEG:
            return profile;
        default:
            return FILTER_PROFILE_ECG;
    }
}

static void computeChannelCoeffs(uint8_t deviceIndex, uint8_t channel, FilterProfile profile) {
    const FilterProfileSpec& spec = filtering_getProfileSpec(profile);
    const double fs = (double)FS_OUTPUT_HZ;

    double hpFc = (double)spec.highpassHz;
    if (hpFc < 0.001) hpFc = 0.001;
    if (hpFc > fs * 0.45) hpFc = fs * 0.45;
    const double k = tan(3.141592653589793 * hpFc / fs);
    const double a0 = 1.0 / (1.0 + k);
    s_hpfB0[deviceIndex][channel] = a0;
    s_hpfB1[deviceIndex][channel] = -a0;
    s_hpfA1[deviceIndex][channel] = (k - 1.0) / (k + 1.0);

    double lpFc = (double)spec.lowpassHz;
    if (lpFc < 0.001) lpFc = 0.001;
    if (lpFc > fs * 0.45) lpFc = fs * 0.45;
    s_lpfAlpha[deviceIndex][channel] = 1.0 - exp(-2.0 * 3.141592653589793 * lpFc / fs);
}

static inline int32_t saturateToInt32(double v) {
    if (v > (double)INT32_MAX) return INT32_MAX;
    if (v < (double)INT32_MIN) return INT32_MIN;
    return (int32_t)lrint(v);
}

static inline double median3(double a, double b, double c) {
    if (a > b) {
        double t = a;
        a = b;
        b = t;
    }
    if (b > c) {
        double t = b;
        b = c;
        c = t;
    }
    if (a > b) {
        double t = a;
        a = b;
        b = t;
    }
    return b;
}

static double applyMedianFilter(uint8_t deviceIndex, uint8_t channel, double sampleUv) {
    if (!s_medianEnabled) {
        return sampleUv;
    }

    double* hist = medianUv[deviceIndex][channel];
    uint8_t pos = medianPos[deviceIndex][channel];
    hist[pos] = sampleUv;
    medianPos[deviceIndex][channel] = (uint8_t)((pos + 1) % 3);

    if (medianCount[deviceIndex][channel] < 3) {
        medianCount[deviceIndex][channel]++;
        return sampleUv;
    }

    return median3(hist[0], hist[1], hist[2]);
}

void filtering_init() {
    for (uint8_t d = 0; d < ADS_MAX_DEVICES; ++d) {
        for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
            xPrevUv[d][ch] = 0.0;
            yPrevUv[d][ch] = 0.0;
            lpPrevUv[d][ch] = 0.0;
            medianUv[d][ch][0] = 0.0;
            medianUv[d][ch][1] = 0.0;
            medianUv[d][ch][2] = 0.0;
            medianCount[d][ch] = 0;
            medianPos[d][ch] = 0;
            s_channelProfile[d][ch] = FILTER_PROFILE_ECG;
            computeChannelCoeffs(d, ch, FILTER_PROFILE_ECG);
        }
    }
}

const FilterProfileSpec& filtering_getProfileSpec(FilterProfile profile) {
    return FILTER_PROFILE_SPECS[(uint8_t)normalizeFilterProfile(profile)];
}

const char* filtering_getProfileName(FilterProfile profile) {
    return filtering_getProfileSpec(profile).name;
}

void filtering_setChannelProfile(uint8_t deviceIndex, uint8_t channel, FilterProfile profile) {
    if (deviceIndex >= ADS_MAX_DEVICES || channel >= ADS_NUM_CHANNELS) return;
    FilterProfile normalized = normalizeFilterProfile(profile);
    s_channelProfile[deviceIndex][channel] = normalized;
    xPrevUv[deviceIndex][channel] = 0.0;
    yPrevUv[deviceIndex][channel] = 0.0;
    lpPrevUv[deviceIndex][channel] = 0.0;
    medianUv[deviceIndex][channel][0] = 0.0;
    medianUv[deviceIndex][channel][1] = 0.0;
    medianUv[deviceIndex][channel][2] = 0.0;
    medianCount[deviceIndex][channel] = 0;
    medianPos[deviceIndex][channel] = 0;
    computeChannelCoeffs(deviceIndex, channel, normalized);
}

FilterProfile filtering_getChannelProfile(uint8_t deviceIndex, uint8_t channel) {
    if (deviceIndex >= ADS_MAX_DEVICES || channel >= ADS_NUM_CHANNELS) {
        return FILTER_PROFILE_ECG;
    }
    return s_channelProfile[deviceIndex][channel];
}

void filtering_setHighpassEnabled(bool enabled) { s_hpfEnabled = enabled; }
bool filtering_isHighpassEnabled() { return s_hpfEnabled; }
void filtering_setLowpassEnabled(bool enabled) { s_lpfEnabled = enabled; }
bool filtering_isLowpassEnabled() { return s_lpfEnabled; }

bool filtering_processSample(uint8_t deviceIndex,
                             const AdsSample& inSample,
                             AdsSample& outSample) {
    if (deviceIndex >= ADS_MAX_DEVICES) return false;

    for (uint8_t ch = 0; ch < ADS_NUM_CHANNELS; ++ch) {
        double x_uv = (double)inSample.ch[ch] * LSB_UV;
        double median_uv = applyMedianFilter(deviceIndex, ch, x_uv);
        double y_uv = s_hpfB0[deviceIndex][ch] * median_uv
                      + s_hpfB1[deviceIndex][ch] * xPrevUv[deviceIndex][ch]
                      - s_hpfA1[deviceIndex][ch] * yPrevUv[deviceIndex][ch];
        xPrevUv[deviceIndex][ch] = median_uv;
        yPrevUv[deviceIndex][ch] = y_uv;

        double hp_out_uv = s_hpfEnabled ? y_uv : median_uv;
        double lp_out_uv = lpPrevUv[deviceIndex][ch]
                           + s_lpfAlpha[deviceIndex][ch] * (hp_out_uv - lpPrevUv[deviceIndex][ch]);
        lpPrevUv[deviceIndex][ch] = lp_out_uv;

        double final_uv = s_lpfEnabled ? lp_out_uv : hp_out_uv;
        double final_counts = final_uv / LSB_UV;
        outSample.ch[ch] = saturateToInt32(final_counts);
    }

    return true;
}
