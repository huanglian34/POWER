/**
 ******************************************************************************
 * @file    current_loop.c
 * @author  UF4
 * @date    26-9-26 下午5:23
 * @brief
 ******************************************************************************
 * @attention
 *
 * Copyright (c) 2026 UF4.
 * All rights reserved.
 *
 * This software is provided "as is", without warranty of any kind.
 *
 ******************************************************************************
 */
#include "current_loop.h"
#include "control_config.h"
#include <stdbool.h>

typedef struct {
  int32_t error_q15;
  int32_t output_q15;
  uint16_t reference_mA;
  control_direction_t direction;
  control_region_t region;
} current_loop_state_t;

static current_loop_state_t s_current_loop;
static current_loop_result_t s_last_result;

CONTROL_FORCE_INLINE int32_t clamp_i32(int32_t value, int32_t low,
                                       int32_t high) {
  if (value < low)
    return low;
  if (value > high)
    return high;
  return value;
}

CONTROL_FORCE_INLINE int16_t
adc_to_local_output_mA(const current_loop_sample_t *sample) {
  int32_t delta;
  uint16_t adc = (sample->direction == CONTROL_DIRECTION_FORWARD)
                     ? sample->forward_iout_adc
                     : sample->reverse_iout_adc;

  /* FWD sensor is positive below VREF; REV sensor is positive above VREF. */
  if (sample->direction == CONTROL_DIRECTION_FORWARD) {
    delta = (int32_t)sample->vref_adc - (int32_t)adc;
  } else {
    delta = (int32_t)adc - (int32_t)sample->vref_adc;
  }

  /* INA240A1 at gain 20 with a 6 mOhm shunt: 120 mV/A (fallback:
   * 1000/(4095*120) = 5/2457). When current_scale_q16 is provided, honor
   * configured shunt/gain/Vdda dynamically. */
  if (sample->current_scale_q16 != 0u) {
    int64_t prod = (int64_t)delta * (int64_t)sample->current_scale_q16;
    delta = (int32_t)((prod + (prod >= 0 ? 32768LL : -32768LL)) >> 16);
  } else {
    delta = (delta * (int32_t)sample->adc_vdda_mV * 5) / 2457;
  }
  return (int16_t)clamp_i32(delta, INT16_MIN, INT16_MAX);
}

CONTROL_FORCE_INLINE int32_t current_error_q15(int32_t reference_mA,
                                               int32_t measured_mA) {
  int32_t error_mA = reference_mA - measured_mA;
  return clamp_i32((error_mA * 32768L) / CONTROL_I_BASE_MA, -32768, 32767);
}

CONTROL_RAMFUNC void CurrentLoop_Init(void) {
  s_current_loop.direction = CONTROL_DIRECTION_FORWARD;
  s_current_loop.region = CONTROL_REGION_BUCK;
  s_current_loop.reference_mA = 0u;
  CurrentLoop_Reset();
}

CONTROL_RAMFUNC void CurrentLoop_Reset(void) {
  s_current_loop.error_q15 = 0;
  s_current_loop.output_q15 = 0;
  s_last_result.duty_q15 = 0u;
  s_last_result.output_current_mA = 0;
  s_last_result.error_mA = 0;
  s_last_result.controller_q15 = 0;
  s_last_result.saturated = 0u;
}

CONTROL_RAMFUNC void CurrentLoop_SetMode(control_direction_t direction,
                                         control_region_t region) {
  s_current_loop.region = region;
  if (direction != s_current_loop.direction) {
    s_current_loop.direction = direction;
    CurrentLoop_Reset();
  }
}

CONTROL_RAMFUNC void CurrentLoop_SetReference_mA(uint16_t reference_mA) {
  s_current_loop.reference_mA = (reference_mA > CONTROL_I_SOFT_LIMIT_MA)
                                    ? CONTROL_I_SOFT_LIMIT_MA
                                    : reference_mA;
}

CONTROL_RAMFUNC uint16_t
CurrentLoop_Update(const current_loop_sample_t *sample) {
  int32_t measured_mA;
  int32_t error_mA;
  int32_t error_q15;
  int32_t integ_cand_q15;
  int32_t integ_next_q15;
  int64_t controller_sum;
  int32_t neg_scale;
  int32_t scale;
  int32_t duty_i;
  int32_t duty_p;
  int32_t duty_fold;
  int32_t duty_pi;
  int32_t duty_q15;
  int32_t duty_hi;
  int32_t v_scale;
  int16_t b0;

  if (sample == 0)
    return s_last_result.duty_q15;

  CurrentLoop_SetMode(sample->direction, sample->region);
  CurrentLoop_SetReference_mA(sample->iref_mA);

  measured_mA = adc_to_local_output_mA(sample);
  error_mA = (int32_t)s_current_loop.reference_mA - measured_mA;
  error_q15 =
      current_error_q15((int32_t)s_current_loop.reference_mA, measured_mA);

  b0 = sample->b0_q15;
  duty_hi = (sample->duty_max_q15 < CONTROL_GAIN_MAX_Q14)
                ? (int32_t)sample->duty_max_q15
                : (int32_t)CONTROL_GAIN_MAX_Q14;

  /* Integral branch: y_i[n] = y_i[n-1] + (b0 / 16384) * (e[n] + e[n-1]). */
  controller_sum = (int64_t)b0 * error_q15 +
                   (int64_t)b0 * s_current_loop.error_q15 +
                   16384LL * s_current_loop.output_q15;
  integ_cand_q15 = clamp_i32((int32_t)(controller_sum >> 14), -32768, 32767);

  neg_scale = ((int32_t)sample->duty_base_q15 > CONTROL_GAIN_SCALE_Q14)
                  ? (int32_t)sample->duty_base_q15
                  : CONTROL_GAIN_SCALE_Q14;
  scale = (integ_cand_q15 >= 0) ? CONTROL_GAIN_SCALE_Q14 : neg_scale;
  duty_i = (integ_cand_q15 * scale) >> 15;

  /* Proportional damping branch (virtual resistance Rv = 0.25 Ohm, 4096 = 0.25
   * * 16384). Restores ~60-70 deg phase margin even into a 0.05 Ohm dead short
   * where the RL pole drops below 1 kHz, while scaling by max(Vin, Vout) keeps
   * Boost mode well damped. */
  v_scale = (sample->vin_mV >= CONTROL_VIN_MIN_MV)
                ? (int32_t)sample->vin_mV
                : (int32_t)CONTROL_VIN_MIN_MV;
  if ((int32_t)sample->vout_mV > v_scale)
    v_scale = (int32_t)sample->vout_mV;
  duty_p = (error_mA * 4096) / v_scale;
  duty_p = clamp_i32(duty_p, -(int32_t)CONTROL_GAIN_ONE_Q14,
                     (int32_t)CONTROL_GAIN_SCALE_Q14);

  /* Emergency fast overcurrent foldback (> ref + 1.5 A only) catches sudden
   * hard shorts from high VOUT before the 10 A hardware comparator trips,
   * WITHOUT firing during steady-state CC regulation or wiping the PI
   * integrator. */
  duty_fold = 0;
  if (sample->enable &&
      measured_mA > (int32_t)s_current_loop.reference_mA + 1500) {
    int32_t excess_mA =
        measured_mA - (int32_t)s_current_loop.reference_mA - 1500;
    duty_fold = excess_mA * 4;
  }

  duty_pi = (int32_t)sample->duty_base_q15 + duty_i + duty_p;
  duty_q15 = duty_pi - duty_fold;

  if (!sample->enable) {
    duty_q15 = sample->duty_base_q15;
    integ_next_q15 = 0;
    s_last_result.saturated = 0u;
  } else {
    /* Conditional anti-windup on the PI output (never wipe integrator on
     * foldback). */
    bool windup_hi = (duty_pi >= duty_hi) && (error_q15 > 0);
    bool windup_lo = (duty_pi <= 0) && (error_q15 < 0);
    integ_next_q15 =
        (windup_hi || windup_lo) ? s_current_loop.output_q15 : integ_cand_q15;

    /* Keep integrator state itself within [0, duty_hi] around duty_base so
     * recovery after a sudden VOUT collapse (short circuit) is immediate with
     * zero dead-zone lag. */
    {
      int32_t min_duty_i = -(int32_t)sample->duty_base_q15;
      int32_t max_duty_i = duty_hi - (int32_t)sample->duty_base_q15;
      int32_t min_integ = (min_duty_i * 32768) / neg_scale;
      int32_t max_integ = (max_duty_i >= 0)
                              ? (max_duty_i * 32768) / CONTROL_GAIN_SCALE_Q14
                              : (max_duty_i * 32768) / neg_scale;
      min_integ = clamp_i32(min_integ, -32768, 32767);
      max_integ = clamp_i32(max_integ, -32768, 32767);
      if (min_integ > max_integ)
        min_integ = max_integ;
      integ_next_q15 = clamp_i32(integ_next_q15, min_integ, max_integ);
    }

    s_last_result.saturated = (duty_q15 <= 0 || duty_q15 >= duty_hi) ? 1u : 0u;
    duty_q15 = clamp_i32(duty_q15, 0, duty_hi);
  }

  s_current_loop.error_q15 = sample->enable ? error_q15 : 0;
  s_current_loop.output_q15 = integ_next_q15;
  s_last_result.duty_q15 = (uint16_t)duty_q15;
  s_last_result.output_current_mA =
      (int16_t)clamp_i32(measured_mA, INT16_MIN, INT16_MAX);
  s_last_result.error_mA = (int16_t)clamp_i32(error_mA, INT16_MIN, INT16_MAX);
  s_last_result.controller_q15 = (int16_t)integ_next_q15;
  return s_last_result.duty_q15;
}

const current_loop_result_t *CurrentLoop_GetLastResult(void) {
  return &s_last_result;
}
