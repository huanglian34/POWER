#include "current_loop_schedule.h"
#include "control_config.h"

static const uint32_t s_vin_mV[13] = {
    12000u, 15000u, 20000u, 24000u, 28000u, 32000u, 36000u,
    40000u, 48000u, 56000u, 60000u, 64000u, 70000u
};

static const uint32_t s_vout_mV[18] = {
    1000u, 2000u, 3300u, 5000u, 8000u, 12000u, 16000u, 20000u,
    24000u, 28000u, 32000u, 36000u, 40000u, 48000u, 56000u,
    60000u, 64000u, 70000u
};

static const uint32_t s_iout_mA[8] = {
    500u, 1000u, 2000u, 3000u, 5000u, 7000u, 8000u, 10000u
};

#include "current_loop_schedule_data.inc"

CONTROL_FORCE_INLINE uint8_t nearest_index_u32(const uint32_t *values,
                                               uint8_t count,
                                               uint32_t value)
{
    uint8_t best = 0u;
    uint32_t best_distance = UINT32_MAX;
    uint8_t i;

    for (i = 0u; i < count; ++i) {
        uint32_t distance = (values[i] > value) ?
                            (values[i] - value) :
                            (value - values[i]);
        if (distance < best_distance) {
            best_distance = distance;
            best = i;
        }
    }
    return best;
}

CONTROL_RAMFUNC int16_t CurrentLoopSchedule_B0(control_region_t region,
                                                uint32_t vin_mV,
                                                uint32_t vout_mV,
                                                uint16_t iout_mA)
{
    uint8_t r = (region == CONTROL_REGION_BOOST) ? 1u : 0u;
    uint8_t iv = nearest_index_u32(s_vin_mV, 13u, vin_mV);
    uint8_t ov = nearest_index_u32(s_vout_mV, 18u, vout_mV);
    uint8_t ii = nearest_index_u32(s_iout_mA, 8u, iout_mA);
    int16_t b0 = s_b0_q15[r][iv][ov][ii];

    if (b0 != 0) return b0;

    /* Fast O(18) neighbor lookup along Vout (never brute-force 3744 cells in 10kHz ISR!). */
    for (uint8_t step = 1u; step < 18u; ++step) {
        if (ov >= step) {
            int16_t cand = s_b0_q15[r][iv][ov - step][ii];
            if (cand != 0) return cand;
        }
        if ((uint8_t)(ov + step) < 18u) {
            int16_t cand = s_b0_q15[r][iv][ov + step][ii];
            if (cand != 0) return cand;
        }
    }
    /* Fallback to the opposite region at nearest Vout if this region has no entry. */
    {
        uint8_t alt_r = r ^ 1u;
        if (s_b0_q15[alt_r][iv][ov][ii] != 0) return s_b0_q15[alt_r][iv][ov][ii];
        for (uint8_t step = 1u; step < 18u; ++step) {
            if (ov >= step) {
                int16_t cand = s_b0_q15[alt_r][iv][ov - step][ii];
                if (cand != 0) return cand;
            }
            if ((uint8_t)(ov + step) < 18u) {
                int16_t cand = s_b0_q15[alt_r][iv][ov + step][ii];
                if (cand != 0) return cand;
            }
        }
    }

    return 512;
}
