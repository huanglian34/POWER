#include "control_app.h"
#include "../Communicator/uf4com/protocol/Inc/uf4com.h"
#include "../Communicator/uf4com/protocol/Inc/uf4com_cmd.h"
#include "../Communicator/uf4com/register/Inc/uf4com_id_table.h"
#include "../Communicator/uf4com/transport/uf4com_usb_cdc/Inc/uf4com_usb_cdc.h"
#include "../Storager/Driver/Flash_Init.h"
#include "../Storager/Param/param_manager.h"
#include "../Storager/Param/param_uf4com.h"
#include "Loop/current_loop.h"
#include "Loop/current_loop_schedule.h"
#include "Loop/voltage_loop.h"
#include "adc.h"
#include "control_config.h"
#include "hrtim.h"
#include "iwdg.h"
#include "power_mode.h"
#include "protection_control.h"
#include "pwm_control.h"
#include "stm32g4xx_hal.h"
#include "temperature_sensor.h"
#include "tim.h"
#include <string.h>

#define CONTROL_VOUT_CAL_MAX_POINTS 6u

typedef struct {
  uint16_t vset_mV, iset_mA, ocp_mA, otp_centi_c;
  uint16_t vin_ovp_mV, vin_uvp_mV, vout_ovp_mV;
  uint16_t direction_mode, adc_vref_mV;
  uint16_t vin_r_upper, vin_r_lower, vout_r_upper, vout_r_lower;
  uint16_t shunt_in, shunt_out, gain_in, gain_out, current_offset;
  uint16_t vin_gain, vin_offset, vout_gain, vout_offset;
  uint16_t iin_gain, iin_offset, iout_gain, iout_offset, fan_set_permille;
  uint16_t cal_vout_count;
  uint16_t cal_vout_raw[CONTROL_VOUT_CAL_MAX_POINTS];
  uint16_t cal_vout_mv[CONTROL_VOUT_CAL_MAX_POINTS];
  uint8_t direction, enable;
} control_settings_t;
typedef struct {
  uint16_t raw;
  uint16_t mv;
} vout_cal_point_t;
typedef struct {
  uint8_t count;
  vout_cal_point_t pts[CONTROL_VOUT_CAL_MAX_POINTS];
  int32_t slope_q16[CONTROL_VOUT_CAL_MAX_POINTS];
} vout_cal_lut_t;
control_settings_t s_settings;
static vout_cal_lut_t s_vout_cal_lut;
power_mode_t s_mode;
static volatile uint8_t s_save_pending;
static uint16_t s_input_voltage_value, s_input_voltage_raw_value;
uint16_t s_input_current_value;
static uint16_t s_input_current_raw_value;
static uint16_t s_output_voltage_value, s_output_voltage_raw_value;
uint16_t s_flags_value;
static uint16_t s_state_value, s_fault_value, s_direction_value;
static uint16_t s_cc_cv_value, s_converter_mode_value;
static uint16_t s_fmac_first_value, s_fmac_second_value, s_fmac_stage_value,
    s_fmac_status_value;
static uint16_t s_dac3_value, s_dac4_value, s_fan_speed_value;
static uint16_t s_otp_value, s_ovp_value, s_ovp_set_value, s_prev_ovp_set_value,
    s_ocp_value;
static uint16_t s_pwm_a_compare_value, s_pwm_d_compare_value;
static uint16_t s_cfg_current_kp, s_cfg_current_ki, s_cfg_current_kd;
static uint16_t s_cfg_voltage_kp, s_cfg_voltage_ki, s_cfg_voltage_kd;
uint16_t s_isr_last_value, s_isr_max_value, s_isr_overrun_value;
static uint16_t s_state_machine_value, s_self_check_value,
    s_latched_fault_value;
uint16_t s_core_temperature_value, s_temp1_value, s_temp2_value;
static uint16_t s_output_current_raw_value;
uint16_t s_output_current_value;
static uint16_t s_current_feedback_value;
static uint16_t s_current_reference_value, s_voltage_current_reference_value;
uint16_t s_duty_command_value;
static volatile uint16_t s_adc_samples[4];
static volatile uint16_t s_adc2_sample, s_adc3_sample, s_adc3_raw_filtered;
volatile uint16_t s_adc4_sample, s_adc4_filtered;
static volatile uint16_t s_adc5_samples[2];
static volatile uint32_t s_adc1_last_sample_tick;
volatile uint32_t s_adc1_sample_count;
static volatile uint32_t s_adc5_sample_count, s_adc5_last_sample_tick;
volatile uint32_t s_fast_tick_count, s_outer_tick_count;
static volatile uint16_t s_fast_early_max_cycles;
volatile uint32_t s_input_voltage_mV, s_output_voltage_mV;
volatile uint32_t s_vref_ramp_mV, s_vin_baseline_mV;
volatile uint16_t s_adc_fault_detail;
volatile uint16_t s_current_reference_mA;
volatile uint16_t s_voltage_duty_limit_q15;
volatile int16_t s_current_b0_q15;
static volatile uint32_t s_fwd_current_scale_q16, s_rev_current_scale_q16;
volatile uint16_t s_protection_zero_adc;
static uint16_t s_zero_candidate_adc;
static uint32_t s_zero_candidate_tick;
static volatile uint8_t s_control_ready, s_fast_enabled, s_adc5_ready;
static volatile power_fault_reason_t s_pending_fault;
static control_direction_t s_fast_direction;
static uint32_t s_last_frame_tick, s_adc5_start_tick;
static uint32_t s_adc5_processed_count;
static uint8_t s_previous_enable;
static uint32_t s_fault_enter_tick, s_fault_healthy_since, s_run_stable_tick;
static uint8_t s_fault_retry_count, s_fault_healthy_armed;
static uint8_t s_was_in_fault, s_was_in_run;

#define CONTROL_COMM_TIMEOUT_MS 500u
#define CONTROL_ADC_TIMEOUT_MS 20u
#define CONTROL_VOFA_PERIOD_MS 2u
#define CONTROL_ZERO_DRIFT_LIMIT_ADC 128u
#define CONTROL_ZERO_STABLE_LIMIT_ADC 16u
#define CONTROL_ZERO_SETTLE_MS 50u
#define CONTROL_FAULT_RETRY_COOLDOWN_MS 1000u
#define CONTROL_FAULT_CLEAR_STABLE_MS 300u
#define CONTROL_FAULT_RUN_RESET_MS 5000u
#define CONTROL_FAULT_MAX_RETRIES 5u
#define CONTROL_FAULT_VIN_HYST_MV 1000u
#define CONTROL_FAULT_OVP_HYST_MV 1000u
#define CONTROL_FAULT_OTP_HYST_CENTI_C 1000u
#define CONTROL_ISR_BUDGET_CYCLES (SystemCoreClock / CONTROL_CURRENT_LOOP_HZ)

static uint16_t current_vref_adc(void) {
  return (s_adc4_filtered != 0u) ? s_adc4_filtered : s_adc4_sample;
}

static bool current_zero_stable(void) {
  int32_t delta = (int32_t)current_vref_adc() - s_protection_zero_adc;
  return delta >= -(int32_t)CONTROL_ZERO_DRIFT_LIMIT_ADC &&
         delta <= (int32_t)CONTROL_ZERO_DRIFT_LIMIT_ADC;
}

CONTROL_RAMFUNC static uint32_t voltage_mV(uint16_t raw, uint16_t upper,
                                           uint16_t lower, uint16_t gain_centi,
                                           uint16_t offset_mV) {
  uint64_t denom;
  uint64_t numerator;
  if (lower == 0u || upper == 0u)
    return 0u;
  numerator =
      (uint64_t)raw * s_settings.adc_vref_mV * (uint32_t)upper * gain_centi;
  denom = (uint64_t)CONTROL_V_ADC_FS * lower * 100u;
  return (uint32_t)((numerator + (denom >> 1)) / denom) + (uint32_t)offset_mV;
}

static void vout_cal_rebuild(void) {
  vout_cal_lut_t next;
  vout_cal_point_t sorted[CONTROL_VOUT_CAL_MAX_POINTS];
  uint8_t raw_n = (s_settings.cal_vout_count > CONTROL_VOUT_CAL_MAX_POINTS)
                      ? CONTROL_VOUT_CAL_MAX_POINTS
                      : (uint8_t)s_settings.cal_vout_count;
  uint8_t collected = 0u;
  uint8_t i, j;
  uint32_t primask;

  memset(&next, 0, sizeof(next));
  for (i = 0u; i < raw_n; ++i) {
    uint16_t r = s_settings.cal_vout_raw[i];
    uint16_t m = s_settings.cal_vout_mv[i];
    if (r > 0u && m > 0u) {
      sorted[collected].raw = r;
      sorted[collected].mv = m;
      collected++;
    }
  }
  for (i = 1u; i < collected; ++i) {
    vout_cal_point_t key = sorted[i];
    j = i;
    while (j > 0u && sorted[j - 1u].raw > key.raw) {
      sorted[j] = sorted[j - 1u];
      j--;
    }
    sorted[j] = key;
  }
  for (i = 0u; i < collected; ++i) {
    if (next.count == 0u) {
      next.pts[next.count++] = sorted[i];
    } else if (sorted[i].raw == next.pts[next.count - 1u].raw) {
      if (next.count == 1u || sorted[i].mv > next.pts[next.count - 2u].mv)
        next.pts[next.count - 1u] = sorted[i];
    } else if (sorted[i].mv > next.pts[next.count - 1u].mv) {
      next.pts[next.count++] = sorted[i];
    }
  }
  if (next.count == 1u) {
    int64_t r0 = (int64_t)next.pts[0].raw;
    int64_t m0 = (int64_t)next.pts[0].mv;
    next.slope_q16[0] = (int32_t)(((m0 << 16) + (r0 >> 1)) / r0);
  } else if (next.count >= 2u) {
    for (i = 0u; i < (uint8_t)(next.count - 1u); ++i) {
      int64_t dx = (int64_t)next.pts[i + 1u].raw - (int64_t)next.pts[i].raw;
      int64_t dy = (int64_t)next.pts[i + 1u].mv - (int64_t)next.pts[i].mv;
      next.slope_q16[i] = (int32_t)(((dy << 16) + (dx >> 1)) / dx);
    }
  }
  primask = __get_PRIMASK();
  __disable_irq();
  s_vout_cal_lut = next;
  if (primask == 0u)
    __enable_irq();
}

CONTROL_RAMFUNC static uint32_t vout_voltage_mV(uint16_t raw) {
  uint8_t n = s_vout_cal_lut.count;
  uint8_t seg = 0u;
  if (n == 0u) {
    return voltage_mV(raw, s_settings.vout_r_upper, s_settings.vout_r_lower,
                      s_settings.vout_gain, s_settings.vout_offset);
  }
  if (n == 1u) {
    int64_t v =
        ((int64_t)raw * (int64_t)s_vout_cal_lut.slope_q16[0] + 32768LL) >> 16;
    return (v <= 0) ? 0u : (uint32_t)v;
  }
  if (raw <= s_vout_cal_lut.pts[0].raw) {
    seg = 0u;
  } else if (raw >= s_vout_cal_lut.pts[n - 1u].raw) {
    seg = (uint8_t)(n - 2u);
  } else {
    while ((uint8_t)(seg + 1u) < (uint8_t)(n - 1u) &&
           raw > s_vout_cal_lut.pts[seg + 1u].raw) {
      seg++;
    }
  }
  {
    int32_t x0 = (int32_t)s_vout_cal_lut.pts[seg].raw;
    int32_t y0 = (int32_t)s_vout_cal_lut.pts[seg].mv;
    int64_t prod =
        (int64_t)((int32_t)raw - x0) * (int64_t)s_vout_cal_lut.slope_q16[seg];
    int32_t delta = (int32_t)((prod + (prod >= 0 ? 32768LL : -32768LL)) >> 16);
    int32_t v = y0 + delta;
    return (v <= 0) ? 0u : (uint32_t)v;
  }
}

static int32_t current_mA(uint16_t raw, uint16_t vref, bool reverse,
                          uint16_t shunt, uint16_t gain) {
  int32_t delta = reverse ? (int32_t)raw - vref : (int32_t)vref - raw;
  int64_t numerator = (int64_t)delta * s_settings.adc_vref_mV * 100000000LL;
  int64_t denominator = (int64_t)4095 * shunt * 10u * gain;
  if (denominator == 0)
    return 0;
  numerator /= denominator;
  if (numerator > INT32_MAX)
    return INT32_MAX;
  if (numerator < INT32_MIN)
    return INT32_MIN;
  return (int32_t)numerator;
}

static uint32_t compute_current_scale_q16(uint16_t shunt_10uohm,
                                          uint16_t gain_centi) {
  uint64_t denom = (uint64_t)4095u * shunt_10uohm * 10u * gain_centi;
  uint64_t num;
  if (denom == 0u || s_settings.adc_vref_mV == 0u)
    return 0u;
  /* mA per ADC count in Q16: (Vdda_mV * 1e8 * 65536) / (4095 * shunt_10uohm * 10 * gain_centi) */
  num = (uint64_t)s_settings.adc_vref_mV * 100000000ULL * 65536ULL;
  return (uint32_t)((num + (denom >> 1)) / denom);
}

static void current_scale_rebuild(void) {
  s_fwd_current_scale_q16 =
      compute_current_scale_q16(s_settings.shunt_out, s_settings.gain_out);
  s_rev_current_scale_q16 =
      compute_current_scale_q16(s_settings.shunt_in, s_settings.gain_in);
}

static uint16_t milli_u16(int32_t milli) {
  if (milli <= 0)
    return 0u;
  if (milli >= 65535)
    return 65535u;
  return (uint16_t)milli;
}

static uint16_t calibrated_current_mA(int32_t current, uint16_t gain,
                                      uint16_t offset_mA) {
  int64_t adjusted = ((int64_t)current * gain + 50) / 100 + (int32_t)offset_mA;
  if (adjusted <= 0)
    return 0u;
  if (adjusted >= 65535)
    return 65535u;
  return (uint16_t)adjusted;
}

static protection_settings_t protection_settings(void) {
  protection_settings_t settings;
  settings.ocp_mA = s_settings.ocp_mA;
  settings.adc_vref_mV = s_settings.adc_vref_mV;
  settings.current_zero_adc =
      s_protection_zero_adc != 0u ? s_protection_zero_adc : s_adc4_sample;
  settings.vin_ovp_mV = s_settings.vin_ovp_mV;
  settings.vout_ovp_mV = s_settings.vout_ovp_mV;
  settings.vin_r_upper_100ohm = s_settings.vin_r_upper;
  settings.vin_r_lower_100ohm = s_settings.vin_r_lower;
  settings.vout_r_upper_100ohm = s_settings.vout_r_upper;
  settings.vout_r_lower_100ohm = s_settings.vout_r_lower;
  settings.shunt_in_10uohm = s_settings.shunt_in;
  settings.shunt_out_10uohm = s_settings.shunt_out;
  settings.gain_in_centi = s_settings.gain_in;
  settings.gain_out_centi = s_settings.gain_out;
  return settings;
}

#define APP_RW_U16(id, name, group, min, max, def, ptr)                        \
  {id,                                                                         \
   name,                                                                       \
   PARAM_TYPE_U16,                                                             \
   group,                                                                      \
   PARAM_FLAG_READ | PARAM_FLAG_WRITE | PARAM_FLAG_PERSISTENT,                 \
   PARAM_STORAGE_INTERNAL,                                                     \
   min,                                                                        \
   max,                                                                        \
   def,                                                                        \
   2,                                                                          \
   ptr,                                                                        \
   NULL,                                                                       \
   NULL,                                                                       \
   NULL}
#define APP_RO_U16(id, name, group, min, max, ptr)                             \
  {id,                                                                         \
   name,                                                                       \
   PARAM_TYPE_U16,                                                             \
   group,                                                                      \
   PARAM_FLAG_READ | PARAM_FLAG_RUNTIME,                                       \
   PARAM_STORAGE_INTERNAL,                                                     \
   min,                                                                        \
   max,                                                                        \
   0,                                                                          \
   2,                                                                          \
   ptr,                                                                        \
   NULL,                                                                       \
   NULL,                                                                       \
   NULL}
#define APP_RW_U16_RUNTIME(id, name, group, min, max, def, ptr)                \
  {id,                                                                         \
   name,                                                                       \
   PARAM_TYPE_U16,                                                             \
   group,                                                                      \
   PARAM_FLAG_READ | PARAM_FLAG_WRITE | PARAM_FLAG_RUNTIME,                    \
   PARAM_STORAGE_INTERNAL,                                                     \
   min,                                                                        \
   max,                                                                        \
   def,                                                                        \
   2,                                                                          \
   ptr,                                                                        \
   NULL,                                                                       \
   NULL,                                                                       \
   NULL}
#define APP_RW_U8_RUNTIME(id, name, group, ptr)                                \
  {id,                                                                         \
   name,                                                                       \
   PARAM_TYPE_U8,                                                              \
   group,                                                                      \
   PARAM_FLAG_READ | PARAM_FLAG_WRITE | PARAM_FLAG_RUNTIME,                    \
   PARAM_STORAGE_INTERNAL,                                                     \
   0,                                                                          \
   1,                                                                          \
   0,                                                                          \
   1,                                                                          \
   ptr,                                                                        \
   NULL,                                                                       \
   NULL,                                                                       \
   NULL}

static const ParamDescriptor_t s_param_table[] = {
    APP_RW_U16(UF4_ID_SET_VOLTAGE_LIMIT, "set_voltage_mV", PARAM_GROUP_OUTPUT,
               0, 65000, 12000, &s_settings.vset_mV),
    APP_RW_U16(UF4_ID_SET_CURRENT_LIMIT, "set_current_mA", PARAM_GROUP_OUTPUT,
               0, 10000, 1000, &s_settings.iset_mA),
    APP_RW_U16(UF4_ID_OCP_SET_VALUE, "ocp_mA", PARAM_GROUP_PROTECTION, 1, 12000,
               12000, &s_settings.ocp_mA),
    APP_RW_U16(UF4_ID_OTP_SET_VALUE, "otp_centi_c", PARAM_GROUP_PROTECTION,
               4000, 15000, 12000, &s_settings.otp_centi_c),
    APP_RW_U16(UF4_ID_CFG_VIN_OVP, "vin_ovp_mV", PARAM_GROUP_PROTECTION, 1,
               65000, 65000, &s_settings.vin_ovp_mV),
    APP_RW_U16(UF4_ID_CFG_VIN_UVP, "vin_uvp_mV", PARAM_GROUP_PROTECTION, 0,
               65000, 12000, &s_settings.vin_uvp_mV),
    APP_RW_U16(UF4_ID_CFG_VOUT_OVP, "vout_ovp_mV", PARAM_GROUP_PROTECTION, 1,
               65000, 65000, &s_settings.vout_ovp_mV),
    APP_RW_U16(UF4_ID_CFG_POWER_DIRECTION_MODE, "direction_mode",
               PARAM_GROUP_OUTPUT, 0, 1, 1, &s_settings.direction_mode),
    APP_RW_U16(UF4_ID_CFG_ADC_VREF, "adc_vref_mV", PARAM_GROUP_CALIBRATION,
               2500, 3600, 3300, &s_settings.adc_vref_mV),
    APP_RW_U16(UF4_ID_CFG_VIN_R_UPPER, "vin_r_upper_100ohm",
               PARAM_GROUP_CALIBRATION, 1, 65535, 750, &s_settings.vin_r_upper),
    APP_RW_U16(UF4_ID_CFG_VIN_R_LOWER, "vin_r_lower_100ohm",
               PARAM_GROUP_CALIBRATION, 1, 65535, 30, &s_settings.vin_r_lower),
    APP_RW_U16(UF4_ID_CFG_VOUT_R_UPPER, "vout_r_upper_100ohm",
               PARAM_GROUP_CALIBRATION, 1, 65535, 750,
               &s_settings.vout_r_upper),
    APP_RW_U16(UF4_ID_CFG_VOUT_R_LOWER, "vout_r_lower_100ohm",
               PARAM_GROUP_CALIBRATION, 1, 65535, 30, &s_settings.vout_r_lower),
    APP_RW_U16(UF4_ID_CFG_SHUNT_IN, "shunt_in_10uohm", PARAM_GROUP_CALIBRATION,
               1, 65535, 600, &s_settings.shunt_in),
    APP_RW_U16(UF4_ID_CFG_SHUNT_OUT, "shunt_out_10uohm",
               PARAM_GROUP_CALIBRATION, 1, 65535, 600, &s_settings.shunt_out),
    APP_RW_U16(UF4_ID_CFG_GAIN_IN, "gain_in_centi", PARAM_GROUP_CALIBRATION,
               2000, 2000, 2000, &s_settings.gain_in),
    APP_RW_U16(UF4_ID_CFG_GAIN_OUT, "gain_out_centi", PARAM_GROUP_CALIBRATION,
               2000, 2000, 2000, &s_settings.gain_out),
    APP_RW_U16(UF4_ID_CFG_CURRENT_OFFSET, "current_offset_mA",
               PARAM_GROUP_CALIBRATION, 0, 65535, 0,
               &s_settings.current_offset),
    APP_RW_U16(UF4_ID_CFG_VIN_GAIN, "vin_gain_centi", PARAM_GROUP_CALIBRATION,
               10, 300, 100, &s_settings.vin_gain),
    APP_RW_U16(UF4_ID_CFG_VIN_OFFSET, "vin_offset_mV", PARAM_GROUP_CALIBRATION,
               0, 65535, 0, &s_settings.vin_offset),
    APP_RW_U16(UF4_ID_CFG_VOUT_GAIN, "vout_gain_centi", PARAM_GROUP_CALIBRATION,
               10, 300, 100, &s_settings.vout_gain),
    APP_RW_U16(UF4_ID_CFG_VOUT_OFFSET, "vout_offset_mV",
               PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_settings.vout_offset),
    APP_RW_U16(UF4_ID_CFG_IIN_GAIN, "iin_gain_centi", PARAM_GROUP_CALIBRATION,
               10, 300, 100, &s_settings.iin_gain),
    APP_RW_U16(UF4_ID_CFG_IIN_OFFSET, "iin_offset_mA", PARAM_GROUP_CALIBRATION,
               0, 65535, 0, &s_settings.iin_offset),
    APP_RW_U16(UF4_ID_CFG_IOUT_GAIN, "iout_gain_centi", PARAM_GROUP_CALIBRATION,
               10, 300, 100, &s_settings.iout_gain),
    APP_RW_U16(UF4_ID_CFG_IOUT_OFFSET, "iout_offset_mA",
               PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_settings.iout_offset),
    APP_RW_U16(UF4_ID_FAN_SET_VALUE, "fan_set_permille", PARAM_GROUP_SYSTEM, 0,
               1000, 1000, &s_settings.fan_set_permille),
    APP_RW_U8_RUNTIME(UF4_ID_POWER_DIRECTION, "power_direction",
                      PARAM_GROUP_OUTPUT, &s_settings.direction),
    APP_RW_U8_RUNTIME(UF4_ID_OUTPUT_ENABLE, "output_enable", PARAM_GROUP_OUTPUT,
                      &s_settings.enable),
    APP_RO_U16(UF4_ID_INPUT_VOLTAGE, "input_voltage_mV", PARAM_GROUP_SYSTEM, 0,
               65535, &s_input_voltage_value),
    APP_RO_U16(UF4_ID_INPUT_VOLTAGE_RAW, "input_voltage_raw",
               PARAM_GROUP_SYSTEM, 0, 65520, &s_input_voltage_raw_value),
    APP_RO_U16(UF4_ID_INPUT_CURRENT, "input_current_mA", PARAM_GROUP_SYSTEM, 0,
               65535, &s_input_current_value),
    APP_RO_U16(UF4_ID_INPUT_CURRENT_RAW, "input_current_raw",
               PARAM_GROUP_SYSTEM, 0, 4095, &s_input_current_raw_value),
    APP_RO_U16(UF4_ID_OUTPUT_VOLTAGE, "output_voltage_mV", PARAM_GROUP_SYSTEM,
               0, 65535, &s_output_voltage_value),
    APP_RO_U16(UF4_ID_OUTPUT_VOLTAGE_RAW, "output_voltage_raw",
               PARAM_GROUP_SYSTEM, 0, 65520, &s_output_voltage_raw_value),
    APP_RO_U16(UF4_ID_OUTPUT_CURRENT, "output_current_mA", PARAM_GROUP_SYSTEM,
               0, 65535, &s_output_current_value),
    APP_RO_U16(UF4_ID_OUTPUT_CURRENT_RAW, "output_current_raw",
               PARAM_GROUP_SYSTEM, 0, 4095, &s_output_current_raw_value),
    APP_RO_U16(UF4_ID_DUTY_CMD, "duty_permille", PARAM_GROUP_SYSTEM, 0, 1000,
               &s_duty_command_value),
    APP_RO_U16(UF4_ID_FMAC_SELFTEST_FIRST_RAW, "fmac_first_raw",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_fmac_first_value),
    APP_RO_U16(UF4_ID_LOOP_CURRENT_FEEDBACK, "loop_current_feedback_mA",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_current_feedback_value),
    APP_RO_U16(UF4_ID_LOOP_CURRENT_REFERENCE, "loop_current_reference_mA",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_current_reference_value),
    APP_RO_U16(UF4_ID_VOLTAGE_LOOP_CURRENT_REFERENCE,
               "voltage_loop_reference_mA", PARAM_GROUP_SYSTEM, 0, 65535,
               &s_voltage_current_reference_value),
    APP_RO_U16(UF4_ID_FMAC_SELFTEST_SECOND_RAW, "fmac_second_raw",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_fmac_second_value),
    APP_RO_U16(UF4_ID_FMAC_DIAGNOSTIC_STAGE, "fmac_diagnostic_stage",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_fmac_stage_value),
    APP_RO_U16(UF4_ID_FMAC_DIAGNOSTIC_STATUS, "fmac_diagnostic_status",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_fmac_status_value),
    APP_RO_U16(UF4_ID_DAC3_CH1_OUTPUT_CODE, "dac3_ch1_output",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_dac3_value),
    APP_RO_U16(UF4_ID_DAC4_CH1_OUTPUT_CODE, "dac4_ch1_output",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_dac4_value),
    APP_RO_U16(UF4_ID_SELF_CHECK_STATUS, "self_check_status",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_self_check_value),
    APP_RO_U16(UF4_ID_LATCHED_FAULT_REASON, "latched_fault_reason",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_latched_fault_value),
    APP_RO_U16(UF4_ID_POWER_STATE, "power_state", PARAM_GROUP_SYSTEM, 0, 65535,
               &s_state_value),
    APP_RO_U16(UF4_ID_FAULT_STATE, "fault_state", PARAM_GROUP_SYSTEM, 0, 65535,
               &s_fault_value),
    APP_RO_U16(UF4_ID_STATE_MACHINE_FLAG_BITS, "protection_status",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_flags_value),
    APP_RO_U16(UF4_ID_CC_CV_MODE, "cc_cv_mode", PARAM_GROUP_SYSTEM, 0, 65535,
               &s_cc_cv_value),
    APP_RO_U16(UF4_ID_POWER_DIRECTION_STATUS, "direction_status",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_direction_value),
    APP_RO_U16(UF4_ID_POWER_CONVERTER_MODE, "converter_mode",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_converter_mode_value),
    APP_RO_U16(UF4_ID_CONTROL_ISR_LAST_CYCLES, "control_isr_last_cycles",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_isr_last_value),
    APP_RO_U16(UF4_ID_CONTROL_ISR_MAX_CYCLES, "control_isr_max_cycles",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_isr_max_value),
    APP_RO_U16(UF4_ID_CONTROL_ISR_OVERRUN_COUNT, "control_isr_overrun_count",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_isr_overrun_value),
    APP_RO_U16(UF4_ID_FAN_SPEED, "fan_speed_rpm", PARAM_GROUP_SYSTEM, 0, 65535,
               &s_fan_speed_value),
    APP_RO_U16(UF4_ID_CORE_TEMPERATURE, "core_temperature_centi_c",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_core_temperature_value),
    APP_RO_U16(UF4_ID_TEMP1_TEMPERATURE, "temp1_temperature_centi_c",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_temp1_value),
    APP_RO_U16(UF4_ID_TEMP2_TEMPERATURE, "temp2_temperature_centi_c",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_temp2_value),
    APP_RO_U16(UF4_ID_STATE_MACHINE_STATE, "state_machine_state",
               PARAM_GROUP_SYSTEM, 0, 65535, &s_state_machine_value),
    APP_RO_U16(UF4_ID_OTP_VALUE, "otp_value_centi_c", PARAM_GROUP_PROTECTION, 0,
               65535, &s_otp_value),
    APP_RO_U16(UF4_ID_OVP_VALUE, "ovp_value_mV", PARAM_GROUP_PROTECTION, 0,
               65535, &s_ovp_value),
    APP_RW_U16_RUNTIME(UF4_ID_OVP_SET_VALUE, "ovp_set_mV",
                       PARAM_GROUP_PROTECTION, 1, 65000, 65000,
                       &s_ovp_set_value),
    APP_RO_U16(UF4_ID_OCP_VALUE, "ocp_value_mA", PARAM_GROUP_PROTECTION, 0,
               65535, &s_ocp_value),
    APP_RO_U16(UF4_ID_PWM_A_COMPARE, "pwm_a_compare", PARAM_GROUP_SYSTEM, 0,
               65535, &s_pwm_a_compare_value),
    APP_RO_U16(UF4_ID_PWM_D_COMPARE, "pwm_d_compare", PARAM_GROUP_SYSTEM, 0,
               65535, &s_pwm_d_compare_value),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_CURRENT_KP, "cfg_current_kp",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_current_kp),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_CURRENT_KI, "cfg_current_ki",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_current_ki),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_CURRENT_KD, "cfg_current_kd",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_current_kd),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_VOLTAGE_KP, "cfg_voltage_kp",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_voltage_kp),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_VOLTAGE_KI, "cfg_voltage_ki",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_voltage_ki),
    APP_RW_U16_RUNTIME(UF4_ID_CFG_VOLTAGE_KD, "cfg_voltage_kd",
                       PARAM_GROUP_CALIBRATION, 0, 65535, 0, &s_cfg_voltage_kd),
    /* Keep multi-point calibration table after index 64 so empty-payload
       streaming stays unaffected. */
    APP_RW_U16(UF4_ID_CAL_VOUT_COUNT, "cal_vout_count", PARAM_GROUP_CALIBRATION,
               0, CONTROL_VOUT_CAL_MAX_POINTS, 0, &s_settings.cal_vout_count),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_0, "cal_vout_raw_0", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[0]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_0, "cal_vout_mv_0", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[0]),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_1, "cal_vout_raw_1", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[1]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_1, "cal_vout_mv_1", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[1]),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_2, "cal_vout_raw_2", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[2]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_2, "cal_vout_mv_2", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[2]),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_3, "cal_vout_raw_3", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[3]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_3, "cal_vout_mv_3", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[3]),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_4, "cal_vout_raw_4", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[4]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_4, "cal_vout_mv_4", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[4]),
    APP_RW_U16(UF4_ID_CAL_VOUT_RAW_5, "cal_vout_raw_5", PARAM_GROUP_CALIBRATION,
               0, 65520, 0, &s_settings.cal_vout_raw[5]),
    APP_RW_U16(UF4_ID_CAL_VOUT_MV_5, "cal_vout_mv_5", PARAM_GROUP_CALIBRATION,
               0, 65000, 0, &s_settings.cal_vout_mv[5])};

static void tx_usb_cdc(const uint8_t *data, uint16_t len, void *user) {
  (void)user;
  (void)UF4_USB_CDC_Write(data, len);
}
static void save_request(void *user) {
  (void)user;
  s_save_pending = 1u;
}
static void write_applied(void *user) {
  (void)user;
  if (s_ovp_set_value != s_prev_ovp_set_value && s_ovp_set_value != 0u) {
    s_settings.vout_ovp_mV = s_ovp_set_value;
  }
  s_ovp_set_value = s_settings.vout_ovp_mV;
  s_prev_ovp_set_value = s_ovp_set_value;
  vout_cal_rebuild();
  current_scale_rebuild();
  const protection_settings_t settings = protection_settings();
  if (!ProtectionControl_Apply(&settings)) {
    PowerMode_ReportFault(&s_mode, POWER_FAULT_EXTERNAL);
    return;
  }
  s_protection_zero_adc = settings.current_zero_adc;
  if (!ProtectionControl_HasCurrentHeadroom())
    PowerMode_ReportFault(&s_mode, POWER_FAULT_EXTERNAL);
  /* Allow clearing a latched fault via either enable=0 (reset to IDLE) or
   * enable=1 (reset and smoothly re-enable) once hardware trip conditions
   * clear. */
  if (s_mode.state == POWER_MODE_FAULT &&
      ProtectionControl_HasCurrentHeadroom() && current_zero_stable() &&
      !ProtectionControl_IsTripActive() &&
      (uint32_t)(HAL_GetTick() - s_adc1_last_sample_tick) <=
          CONTROL_ADC_TIMEOUT_MS) {
    s_adc_fault_detail = 0u;
    s_fault_retry_count = 0u;
    s_fault_healthy_armed = 0u;
    PowerMode_ClearFault(&s_mode, HAL_GetTick());
  }
  s_previous_enable = s_settings.enable;
  PowerMode_RequestEnable(&s_mode, s_settings.enable != 0u);
  PowerMode_RequestDirection(&s_mode,
                             (s_settings.direction_mode == 0u)
                                 ? CONTROL_DIRECTION_FORWARD
                                 : (control_direction_t)s_settings.direction);
  VoltageLoop_SetCurrentLimit_mA(s_settings.iset_mA);
}

static void update_protection_zero(uint32_t now) {
  uint16_t zero = current_vref_adc();
  int32_t candidate_delta = (int32_t)zero - s_zero_candidate_adc;
  uint32_t i;

  if (candidate_delta < -(int32_t)CONTROL_ZERO_STABLE_LIMIT_ADC ||
      candidate_delta > (int32_t)CONTROL_ZERO_STABLE_LIMIT_ADC) {
    s_zero_candidate_adc = zero;
    s_zero_candidate_tick = now;
    return;
  }
  if ((s_settings.enable != 0u && s_mode.state != POWER_MODE_FAULT) ||
      PowerMode_IsPwmEnabled(&s_mode) || zero == 0u || current_zero_stable() ||
      (uint32_t)(now - s_zero_candidate_tick) < CONTROL_ZERO_SETTLE_MS ||
      (uint32_t)(now - s_adc1_last_sample_tick) > CONTROL_ADC_TIMEOUT_MS)
    return;
  for (i = 0u; i < 4u; ++i) {
    int32_t delta = (int32_t)s_adc_samples[i] - zero;
    if (delta < -(int32_t)CONTROL_ZERO_DRIFT_LIMIT_ADC ||
        delta > (int32_t)CONTROL_ZERO_DRIFT_LIMIT_ADC)
      return;
  }
  {
    protection_settings_t settings = protection_settings();
    settings.current_zero_adc = zero;
    if (!ProtectionControl_Apply(&settings)) {
      PowerMode_ReportFault(&s_mode, POWER_FAULT_EXTERNAL);
      return;
    }
    s_protection_zero_adc = zero;
  }
}

static bool fault_is_auto_recoverable(power_fault_reason_t fault) {
  return fault != POWER_FAULT_NONE && fault != POWER_FAULT_PWM &&
         fault != POWER_FAULT_EXTERNAL;
}

static bool fault_conditions_healthy(uint32_t now) {
  uint16_t vref_adc = current_vref_adc();
  uint32_t vin = s_input_voltage_mV;
  uint32_t vout = s_output_voltage_mV;
  uint32_t vin_ovp_clear =
      (s_settings.vin_ovp_mV > CONTROL_FAULT_OVP_HYST_MV)
          ? (uint32_t)(s_settings.vin_ovp_mV - CONTROL_FAULT_OVP_HYST_MV)
          : (uint32_t)s_settings.vin_ovp_mV;
  uint32_t vout_ovp_clear =
      (s_settings.vout_ovp_mV > CONTROL_FAULT_OVP_HYST_MV)
          ? (uint32_t)(s_settings.vout_ovp_mV - CONTROL_FAULT_OVP_HYST_MV)
          : (uint32_t)s_settings.vout_ovp_mV;

  if (!s_control_ready || !ProtectionControl_IsReady() ||
      !ProtectionControl_HasCurrentHeadroom() ||
      ProtectionControl_IsTripActive() || !current_zero_stable() ||
      vref_adc <= 1000u || vref_adc >= 3000u ||
      vin < (CONTROL_VIN_MIN_MV + CONTROL_FAULT_VIN_HYST_MV) ||
      vin > CONTROL_VIN_MAX_MV || vin > vin_ovp_clear ||
      vout > CONTROL_VOUT_MAX_MV || vout > vout_ovp_clear ||
      s_current_b0_q15 == 0 ||
      (int32_t)(now - s_adc1_last_sample_tick) > (int32_t)CONTROL_ADC_TIMEOUT_MS)
    return false;

#if !CONTROL_TEST_STANDALONE
  {
    uint16_t otp_clear =
        (s_settings.otp_centi_c > CONTROL_FAULT_OTP_HYST_CENTI_C)
            ? (uint16_t)(s_settings.otp_centi_c - CONTROL_FAULT_OTP_HYST_CENTI_C)
            : s_settings.otp_centi_c;
    if ((uint32_t)(now - s_last_frame_tick) > CONTROL_COMM_TIMEOUT_MS ||
        (uint32_t)(now - s_adc5_last_sample_tick) > 500u ||
        s_temp1_value == TEMPERATURE_SENSOR_INVALID ||
        s_temp2_value == TEMPERATURE_SENSOR_INVALID ||
        s_temp1_value >= otp_clear || s_temp2_value >= otp_clear)
      return false;
  }
#endif
  return true;
}

/* Industrial hiccup auto-recovery: after a recoverable fault, wait cooldown,
 * verify all rails/comparators/ADCs stay healthy continuously, then clear fault
 * and re-enter soft-start (up to CONTROL_FAULT_MAX_RETRIES consecutive tries;
 * resets retry budget after 5 s of clean POWER_MODE_RUN). */
static void auto_recover_fault(uint32_t now) {
  power_mode_state_t st = PowerMode_GetState(&s_mode);

  if (st == POWER_MODE_RUN) {
    if (!s_was_in_run) {
      s_run_stable_tick = now;
      s_was_in_run = 1u;
    } else if (s_fault_retry_count != 0u &&
               (uint32_t)(now - s_run_stable_tick) >=
                   CONTROL_FAULT_RUN_RESET_MS) {
      s_fault_retry_count = 0u;
    }
  } else {
    s_was_in_run = 0u;
  }

  if (st != POWER_MODE_FAULT) {
    s_was_in_fault = 0u;
    s_fault_healthy_armed = 0u;
    return;
  }

  if (!s_was_in_fault) {
    s_fault_enter_tick = now;
    s_fault_healthy_armed = 0u;
    s_was_in_fault = 1u;
  }

  if (s_settings.enable == 0u ||
      !fault_is_auto_recoverable(PowerMode_GetFault(&s_mode)) ||
      s_fault_retry_count >= CONTROL_FAULT_MAX_RETRIES) {
    s_fault_healthy_armed = 0u;
    return;
  }

  if ((uint32_t)(now - s_fault_enter_tick) < CONTROL_FAULT_RETRY_COOLDOWN_MS) {
    s_fault_healthy_armed = 0u;
    return;
  }

  if (!fault_conditions_healthy(now)) {
    s_fault_healthy_armed = 0u;
    return;
  }

  if (!s_fault_healthy_armed) {
    s_fault_healthy_since = now;
    s_fault_healthy_armed = 1u;
    return;
  }

  if ((uint32_t)(now - s_fault_healthy_since) >= CONTROL_FAULT_CLEAR_STABLE_MS) {
    s_fault_retry_count++;
    s_adc_fault_detail = 0u;
    s_fault_healthy_armed = 0u;
    s_was_in_fault = 0u;
    PowerMode_ClearFault(&s_mode, now);
    PowerMode_RequestEnable(&s_mode, s_settings.enable != 0u);
    PowerMode_RequestDirection(&s_mode,
                               (s_settings.direction_mode == 0u)
                                   ? CONTROL_DIRECTION_FORWARD
                                   : (control_direction_t)s_settings.direction);
  }
}

static void pwm_on(void *user) {
  (void)user;
  if (ProtectionControl_IsTripActive()) {
    uint16_t status = ProtectionControl_GetStatus();
    PowerMode_ReportFault(&s_mode, ((status & 0x000au) != 0u)
                                       ? POWER_FAULT_OVERVOLTAGE
                                       : POWER_FAULT_OVERCURRENT);
    return;
  }
  if (!s_control_ready || !ProtectionControl_HasCurrentHeadroom() ||
      !current_zero_stable() || s_input_voltage_mV < CONTROL_VIN_MIN_MV ||
      s_input_voltage_mV > CONTROL_VIN_MAX_MV ||
      s_output_voltage_mV > CONTROL_VOUT_MAX_MV || s_current_b0_q15 == 0 ||
#if !CONTROL_TEST_STANDALONE
      s_temp1_value == TEMPERATURE_SENSOR_INVALID ||
      s_temp2_value == TEMPERATURE_SENSOR_INVALID ||
      s_temp1_value >= s_settings.otp_centi_c ||
      s_temp2_value >= s_settings.otp_centi_c ||
      (int32_t)(HAL_GetTick() - s_adc5_last_sample_tick) > 500 ||
      (int32_t)(HAL_GetTick() - s_last_frame_tick) >
          (int32_t)CONTROL_COMM_TIMEOUT_MS ||
#endif
      (int32_t)(HAL_GetTick() - s_adc1_last_sample_tick) >
          (int32_t)CONTROL_ADC_TIMEOUT_MS) {
    if (s_input_voltage_mV < CONTROL_VIN_MIN_MV)
      s_adc_fault_detail |= (1u << 1);
    if (s_input_voltage_mV > CONTROL_VIN_MAX_MV)
      s_adc_fault_detail |= (1u << 2);
    if (s_output_voltage_mV > CONTROL_VOUT_MAX_MV)
      s_adc_fault_detail |= (1u << 3);
    if (!current_zero_stable())
      s_adc_fault_detail |= (1u << 4);
    PowerMode_ReportFault(&s_mode, POWER_FAULT_ADC);
    return;
  }
  if (PwmControl_Enable() != HAL_OK)
    PowerMode_ReportFault(&s_mode, POWER_FAULT_PWM);
}
static void pwm_off(void *user) {
  (void)user;
  (void)PwmControl_Disable();
}
static bool self_check(void *user) {
  (void)user;
  return ProtectionControl_IsReady() &&
         ProtectionControl_HasCurrentHeadroom() && current_zero_stable() &&
         s_adc1_sample_count != 0u && s_adc4_sample > 1000u &&
         s_adc4_sample < 3000u && s_adc5_sample_count != 0u &&
#if !CONTROL_TEST_STANDALONE
         s_temp1_value != TEMPERATURE_SENSOR_INVALID &&
         s_temp2_value != TEMPERATURE_SENSOR_INVALID &&
         s_temp1_value < s_settings.otp_centi_c &&
         s_temp2_value < s_settings.otp_centi_c &&
#endif
         !ProtectionControl_IsTripActive() &&
         (int32_t)(HAL_GetTick() - s_adc1_last_sample_tick) <=
             (int32_t)CONTROL_ADC_TIMEOUT_MS;
}
static void update_telemetry(void) {
  const current_loop_result_t *current = CurrentLoop_GetLastResult();
  const voltage_loop_result_t *voltage = VoltageLoop_GetLastResult();
  uint16_t input_index =
      (PowerMode_GetDirection(&s_mode) == CONTROL_DIRECTION_FORWARD) ? 0u : 1u;
  uint16_t output_index =
      (PowerMode_GetDirection(&s_mode) == CONTROL_DIRECTION_FORWARD) ? 2u : 3u;
  s_state_value = (uint16_t)PowerMode_GetState(&s_mode);
  s_fault_value = (uint16_t)PowerMode_GetFault(&s_mode);
  s_flags_value = (uint16_t)(PowerMode_GetFlagBits(&s_mode) |
                             (ProtectionControl_GetStatus() << 8));
  s_state_machine_value = s_state_value;
  s_direction_value = (uint16_t)PowerMode_GetDirection(&s_mode);
  s_self_check_value = s_mode.self_check_passed ? 1u : 0u;
  s_latched_fault_value = s_fault_value;
  s_input_voltage_raw_value =
      (PowerMode_GetDirection(&s_mode) == CONTROL_DIRECTION_FORWARD)
          ? s_adc2_sample
          : s_adc3_raw_filtered;
  s_output_voltage_raw_value =
      (PowerMode_GetDirection(&s_mode) == CONTROL_DIRECTION_FORWARD)
          ? s_adc3_raw_filtered
          : s_adc2_sample;
  s_input_voltage_value = milli_u16((int32_t)s_input_voltage_mV);
  s_output_voltage_value = milli_u16((int32_t)s_output_voltage_mV);
  s_input_current_raw_value = s_adc_samples[input_index];
  s_output_current_raw_value = s_adc_samples[output_index];
  {
    bool reverse = PowerMode_GetDirection(&s_mode) == CONTROL_DIRECTION_REVERSE;
    int32_t input =
        current_mA(s_input_current_raw_value, s_adc4_sample, reverse,
                   s_settings.shunt_in, s_settings.gain_in);
    int32_t output =
        current_mA(s_output_current_raw_value, s_adc4_sample, reverse,
                   s_settings.shunt_out, s_settings.gain_out);
    s_input_current_value = calibrated_current_mA(input, s_settings.iin_gain,
                                                  s_settings.iin_offset);
    s_output_current_value = calibrated_current_mA(output, s_settings.iout_gain,
                                                   s_settings.iout_offset);
  }
  s_current_feedback_value = s_output_current_value;
  s_current_reference_value =
      milli_u16(current->error_mA + current->output_current_mA);
  s_voltage_current_reference_value = voltage->current_reference_mA;
  s_duty_command_value =
      (uint16_t)((HRTIM1->sMasterRegs.MCMP4R >= 27200u)
                     ? 1000u
                     : ((HRTIM1->sMasterRegs.MCMP4R * 1000u) / 27200u));
  s_dac3_value = ProtectionControl_GetDac3Ch1Code();
  s_dac4_value = ProtectionControl_GetDac4Ch1Code();
  s_cc_cv_value = (s_fast_enabled && (uint32_t)s_current_reference_mA >=
                                         (uint32_t)s_settings.iset_mA)
                      ? 1u
                      : 0u;
  s_converter_mode_value = (uint16_t)PwmControl_GetStage();
  s_ovp_value = (s_output_voltage_value > s_input_voltage_value)
                    ? s_output_voltage_value
                    : s_input_voltage_value;
  s_ovp_set_value = s_settings.vout_ovp_mV;
  s_prev_ovp_set_value = s_ovp_set_value;
  s_ocp_value = (s_output_current_value > s_input_current_value)
                    ? s_output_current_value
                    : s_input_current_value;
  if (s_temp1_value != TEMPERATURE_SENSOR_INVALID &&
      s_temp2_value != TEMPERATURE_SENSOR_INVALID)
    s_otp_value =
        (s_temp1_value > s_temp2_value) ? s_temp1_value : s_temp2_value;
  else if (s_temp1_value != TEMPERATURE_SENSOR_INVALID)
    s_otp_value = s_temp1_value;
  else if (s_temp2_value != TEMPERATURE_SENSOR_INVALID)
    s_otp_value = s_temp2_value;
  else
    s_otp_value = s_core_temperature_value;
  s_pwm_a_compare_value = (uint16_t)HRTIM1->sMasterRegs.MCMP4R;
  s_pwm_d_compare_value = (uint16_t)HRTIM1->sMasterRegs.MCMP1R;
}
void ControlApp_Init(void) {
  ParamStatus_t st;
  power_mode_config_t mode_config;
  uint32_t start_tick;
  CurrentLoop_Init();
  VoltageLoop_Init();
  (void)Flash_Init_RegisterInternal();
  st = Param_Init(s_param_table,
                  sizeof(s_param_table) / sizeof(s_param_table[0]));
  if (st != PARAM_OK)
    Error_Handler();
  st = Param_Load();
  if (st == PARAM_ERROR_NOT_FOUND)
    (void)Param_Commit();
  else if (st != PARAM_OK)
    Error_Handler();
  /* Migrate legacy 8 mOhm (800) Flash defaults to actual 6 mOhm (600) hardware
   * shunts while preserving any saved 6-point VOUT calibration in Flash. */
  if (s_settings.shunt_in == 800u || s_settings.shunt_out == 800u) {
    if (s_settings.shunt_in == 800u)
      s_settings.shunt_in = 600u;
    if (s_settings.shunt_out == 800u)
      s_settings.shunt_out = 600u;
    (void)Param_Commit();
  }
#if CONTROL_TEST_STANDALONE
  s_settings.vset_mV = CONTROL_TEST_VSET_MV;
  s_settings.enable = 1u;
#endif
  s_ovp_set_value = s_settings.vout_ovp_mV;
  s_prev_ovp_set_value = s_ovp_set_value;
  vout_cal_rebuild();
  current_scale_rebuild();
  if (HAL_IWDG_Refresh(&hiwdg) != HAL_OK)
    Error_Handler();
  if (ParamUF4COM_Bind() != PARAM_OK)
    Error_Handler();
  UF4_InitEx(NULL, tx_usb_cdc, NULL);
  UF4_SetSaveRequestCallback(save_request, NULL);
  UF4_SetWriteApplyCallback(write_applied, NULL);
  s_control_ready = 0u;
  s_fast_enabled = 0u;
  s_pending_fault = POWER_FAULT_NONE;
  s_adc1_sample_count = 0u;
  s_adc1_last_sample_tick = 0u;
  s_adc5_ready = 0u;
  s_adc5_sample_count = 0u;
  s_previous_enable = 0u;
  if (HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED) != HAL_OK ||
      HAL_ADCEx_Calibration_Start(&hadc2, ADC_SINGLE_ENDED) != HAL_OK ||
      HAL_ADCEx_Calibration_Start(&hadc3, ADC_SINGLE_ENDED) != HAL_OK ||
      HAL_ADCEx_Calibration_Start(&hadc4, ADC_SINGLE_ENDED) != HAL_OK ||
      HAL_ADCEx_Calibration_Start(&hadc5, ADC_SINGLE_ENDED) != HAL_OK)
    Error_Handler();
  if (HAL_ADC_Start_DMA(&hadc1, (uint32_t *)s_adc_samples, 4u) != HAL_OK ||
      HAL_ADC_Start_DMA(&hadc2, (uint32_t *)&s_adc2_sample, 1u) != HAL_OK ||
      HAL_ADC_Start_DMA(&hadc3, (uint32_t *)&s_adc3_sample, 1u) != HAL_OK ||
      HAL_ADC_Start_DMA(&hadc4, (uint32_t *)&s_adc4_sample, 1u) != HAL_OK)
    Error_Handler();
  __HAL_DMA_DISABLE_IT(&hdma_adc1, DMA_IT_HT);
  __HAL_DMA_DISABLE_IT(&hdma_adc2, DMA_IT_HT | DMA_IT_TC);
  __HAL_DMA_DISABLE_IT(&hdma_adc3, DMA_IT_HT | DMA_IT_TC);
  __HAL_DMA_DISABLE_IT(&hdma_adc4, DMA_IT_HT | DMA_IT_TC);
  if (HAL_ADC_Start_DMA(&hadc5, (uint32_t *)s_adc5_samples, 2u) != HAL_OK)
    Error_Handler();
  __HAL_DMA_DISABLE_IT(&hdma_adc5, DMA_IT_HT);
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0u;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  PwmControl_Init(&hhrtim1);
  start_tick = HAL_GetTick();
  while ((s_adc1_sample_count == 0u || s_adc5_sample_count == 0u ||
          s_adc4_sample == 0u) &&
         (uint32_t)(HAL_GetTick() - start_tick) < 20u)
    (void)HAL_IWDG_Refresh(&hiwdg);
  if (s_adc1_sample_count == 0u || s_adc5_sample_count == 0u ||
      s_adc4_sample == 0u)
    Error_Handler();
  s_adc4_filtered = s_adc4_sample;
  s_fault_retry_count = 0u;
  s_fault_healthy_armed = 0u;
  s_was_in_fault = 0u;
  s_was_in_run = 0u;
  {
    const protection_settings_t settings = protection_settings();
    if (!ProtectionControl_Init(&settings))
      Error_Handler();
    s_protection_zero_adc = settings.current_zero_adc;
  }
  s_temp2_value = TemperatureSensor_FromAdc(s_adc5_samples[0], false);
  s_temp1_value = TemperatureSensor_FromAdc(s_adc5_samples[1], false);
  s_adc5_processed_count = s_adc5_sample_count;
  s_adc5_start_tick = HAL_GetTick();
  s_zero_candidate_adc = s_adc4_sample;
  s_zero_candidate_tick = HAL_GetTick();
  memset(&mode_config, 0, sizeof(mode_config));
  mode_config.self_check_ms = 50u;
  mode_config.soft_start_ms = 100u;
  mode_config.stopping_ms = 10u;
  mode_config.direction_deadtime_ms = 20u;
  mode_config.pwm_enable = pwm_on;
  mode_config.pwm_disable = pwm_off;
  mode_config.self_check = self_check;
  PowerMode_Init(&s_mode, &mode_config, HAL_GetTick());
  write_applied(NULL);
  UF4_USB_CDC_Init();
  s_last_frame_tick = HAL_GetTick();
  s_control_ready = 1u;
  if (HAL_TIM_Base_Start_IT(&htim6) != HAL_OK)
    Error_Handler();
}

CONTROL_RAMFUNC void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc) {
  if (hadc == &hadc1) {
    s_adc1_last_sample_tick = HAL_GetTick();
    s_adc1_sample_count++;
  } else if (hadc == &hadc5) {
    s_adc5_ready = 1u;
    s_adc5_sample_count++;
    s_adc5_last_sample_tick = HAL_GetTick();
  }
}

CONTROL_RAMFUNC void HAL_HRTIM_RepetitionEventCallback(
    HRTIM_HandleTypeDef *hrtim, uint32_t timer_index) {
  static uint32_t previous_sample_count;
  static uint8_t missing_samples;
  static uint8_t bad_adc_samples;
  current_loop_sample_t sample;
  uint32_t start_cycles, vin, vout;
  uint16_t duty;

  if (hrtim != &hhrtim1 || timer_index != HRTIM_TIMERINDEX_MASTER ||
      !s_control_ready)
    return;
  start_cycles = DWT->CYCCNT;
  s_fast_tick_count++;
  if (!s_fast_enabled) {
    missing_samples = 0u;
    bad_adc_samples = 0u;
  } else if (s_adc1_sample_count == previous_sample_count) {
    if (missing_samples < 20u)
      missing_samples++;
    if (missing_samples >= 20u) {
      s_adc_fault_detail |= (1u << 0);
      s_pending_fault = POWER_FAULT_ADC;
      s_fast_enabled = 0u;
      (void)PwmControl_Disable();
    }
  } else
    missing_samples = 0u;
  previous_sample_count = s_adc1_sample_count;

  vin = s_input_voltage_mV;
  vout = s_output_voltage_mV;
  {
    uint16_t vref_adc = current_vref_adc();
    if (vin < CONTROL_VIN_MIN_MV || vin > CONTROL_VIN_MAX_MV ||
        vout > CONTROL_VOUT_MAX_MV || !current_zero_stable() ||
        vref_adc < 1000u || vref_adc > 3000u) {
      if (s_fast_enabled) {
        if (bad_adc_samples < 100u)
          bad_adc_samples++;
        if (bad_adc_samples >= 100u) {
          if (vin < CONTROL_VIN_MIN_MV)
            s_adc_fault_detail |= (1u << 1);
          if (vin > CONTROL_VIN_MAX_MV)
            s_adc_fault_detail |= (1u << 2);
          if (vout > CONTROL_VOUT_MAX_MV)
            s_adc_fault_detail |= (1u << 3);
          if (!current_zero_stable())
            s_adc_fault_detail |= (1u << 4);
          if (vref_adc < 1000u || vref_adc > 3000u)
            s_adc_fault_detail |= (1u << 5);
          s_pending_fault = POWER_FAULT_ADC;
          s_fast_enabled = 0u;
          (void)PwmControl_Disable();
        }
      }
      uint32_t cycles = DWT->CYCCNT - start_cycles;
      if (cycles > s_fast_early_max_cycles)
        s_fast_early_max_cycles = cycles > 65535u ? 65535u : (uint16_t)cycles;
      if (cycles > CONTROL_ISR_BUDGET_CYCLES && s_isr_overrun_value != 65535u)
        s_isr_overrun_value++;
      return;
    }
    bad_adc_samples = 0u;
    sample.direction = s_fast_direction;
    sample.region = (vout <= vin) ? CONTROL_REGION_BUCK : CONTROL_REGION_BOOST;
    sample.vin_mV = vin;
    sample.vout_mV = vout;
    sample.vref_adc = vref_adc;
  }
  sample.adc_vdda_mV = s_settings.adc_vref_mV;
  sample.current_scale_q16 = (sample.direction == CONTROL_DIRECTION_FORWARD)
                                 ? s_fwd_current_scale_q16
                                 : s_rev_current_scale_q16;
  sample.b0_q15 = s_current_b0_q15;
  sample.forward_iout_adc = s_adc_samples[2];
  sample.reverse_iout_adc = s_adc_samples[3];
  sample.iref_mA = s_current_reference_mA;
  {
    uint32_t m_base = ((uint32_t)vout * CONTROL_GAIN_ONE_Q14) / vin;
    if (m_base > CONTROL_GAIN_MAX_Q14)
      m_base = CONTROL_GAIN_MAX_Q14;
    sample.duty_base_q15 = (uint16_t)m_base;
  }
  sample.duty_max_q15 = s_voltage_duty_limit_q15;
  sample.enable = s_fast_enabled && s_pending_fault == POWER_FAULT_NONE;
  PwmControl_SetMode(sample.direction, sample.region);
  duty = sample.enable ? CurrentLoop_Update(&sample) : sample.duty_base_q15;
  if (PwmControl_SetGainQ14(duty) != HAL_OK && sample.enable)
    s_pending_fault = POWER_FAULT_PWM;
  {
    uint32_t cycles = DWT->CYCCNT - start_cycles;
    s_isr_last_value = (cycles > 65535u) ? 65535u : (uint16_t)cycles;
    if (cycles > s_isr_max_value)
      s_isr_max_value = s_isr_last_value;
    if (cycles > CONTROL_ISR_BUDGET_CYCLES && s_isr_overrun_value != 65535u)
      s_isr_overrun_value++;
  }
}

CONTROL_RAMFUNC void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *tim) {
  voltage_loop_sample_t sample;
  uint32_t vin_pin, vout_pin, elapsed;
  uint16_t current_limit;
  if (tim != &htim6 || !s_control_ready)
    return;
  s_outer_tick_count++;

  {
    /* Hardware EEV blanks MOSFETs cycle-by-cycle (~50 ns) on any comparator
     * trip. Debounce software fault latching so brief short-circuit cap
     * discharge spikes allow the CC current loop to take over cleanly instead
     * of nuisance-latching. OVP latches after 0.3 ms (3 ticks); persistent OCP
     * latches after 20 ms (200 ticks). */
    static uint16_t s_ovp_trip_ticks;
    static uint16_t s_ocp_trip_ticks;
    if (s_fast_enabled && ProtectionControl_IsTripActive()) {
      uint16_t status = ProtectionControl_GetStatus();
      if ((status & 0x000au) != 0u) {
        s_ovp_trip_ticks++;
        if (s_ovp_trip_ticks >= 3u) {
          s_pending_fault = POWER_FAULT_OVERVOLTAGE;
          s_fast_enabled = 0u;
          (void)PwmControl_Disable();
          s_ovp_trip_ticks = 0u;
          s_ocp_trip_ticks = 0u;
        }
      } else {
        s_ovp_trip_ticks = 0u;
        s_ocp_trip_ticks++;
        if (s_ocp_trip_ticks >= 200u) {
          s_pending_fault = POWER_FAULT_OVERCURRENT;
          s_fast_enabled = 0u;
          (void)PwmControl_Disable();
          s_ocp_trip_ticks = 0u;
        }
      }
    } else {
      s_ovp_trip_ticks = 0u;
      s_ocp_trip_ticks = 0u;
    }
  }
  {
    static uint32_t vin_filt_mV, vout_filt_mV;
    static uint32_t vout_raw_avg_q8, adc4_avg_q4;
    static uint8_t v_filt_init;
    uint16_t adc2_raw = s_adc2_sample;
    uint16_t adc3_raw = s_adc3_sample;
    uint16_t adc4_raw = s_adc4_sample;
    uint32_t vin_raw_mV =
        voltage_mV(adc2_raw, s_settings.vin_r_upper, s_settings.vin_r_lower,
                   s_settings.vin_gain, s_settings.vin_offset);
    uint32_t vout_raw_mV = vout_voltage_mV(adc3_raw);
    if (!v_filt_init) {
      vin_filt_mV = vin_raw_mV;
      vout_filt_mV = vout_raw_mV;
      vout_raw_avg_q8 = (uint32_t)adc3_raw << 8;
      adc4_avg_q4 = (uint32_t)adc4_raw << 4;
      v_filt_init = 1u;
    } else {
      int32_t raw_diff = (int32_t)adc3_raw - (int32_t)(vout_raw_avg_q8 >> 8);
      int32_t adc4_diff = (int32_t)adc4_raw - (int32_t)(adc4_avg_q4 >> 4);
      vin_filt_mV = (vin_filt_mV * 3u + vin_raw_mV + 2u) >> 2;
      vout_filt_mV = (vout_filt_mV * 3u + vout_raw_mV + 2u) >> 2;
      vout_raw_avg_q8 = (uint32_t)((int32_t)vout_raw_avg_q8 + raw_diff);
      adc4_avg_q4 = (uint32_t)((int32_t)adc4_avg_q4 + adc4_diff);
    }
    s_adc3_raw_filtered = (uint16_t)((vout_raw_avg_q8 + 128u) >> 8);
    s_adc4_filtered = (uint16_t)((adc4_avg_q4 + 8u) >> 4);
    vin_pin = vin_filt_mV;
    vout_pin = vout_filt_mV;
  }
  if (s_fast_direction == CONTROL_DIRECTION_FORWARD) {
    s_input_voltage_mV = vin_pin;
    s_output_voltage_mV = vout_pin;
  } else {
    s_input_voltage_mV = vout_pin;
    s_output_voltage_mV = vin_pin;
  }
  current_limit = s_settings.iset_mA;
  {
    uint32_t target_mV = (uint32_t)s_settings.vset_mV;
    uint32_t vref_mV;
    bool loop_enable = s_fast_enabled && current_limit != 0u &&
                       s_pending_fault == POWER_FAULT_NONE;

    if (s_mode.state == POWER_MODE_SOFT_START) {
      elapsed = (HAL_GetTick() - s_mode.state_tick) + 1u;
      if (elapsed < 100u) {
        current_limit = (uint16_t)((uint32_t)current_limit * elapsed / 100u);
      }
    }

    if (!loop_enable) {
      s_vref_ramp_mV = (s_output_voltage_mV >= CONTROL_VOUT_MIN_MV)
                           ? s_output_voltage_mV
                           : CONTROL_VOUT_MIN_MV;
      s_vin_baseline_mV = s_input_voltage_mV;
    } else {
      if (s_vin_baseline_mV == 0u)
        s_vin_baseline_mV = s_input_voltage_mV;
      if (s_vref_ramp_mV < target_mV) {
        /* Pause upward ramp if input supply is sagging close to UVLO. */
        if (s_input_voltage_mV >=
            (CONTROL_VIN_MIN_MV + CONTROL_VIN_SAG_HOLD_MARGIN_MV)) {
          uint32_t next = s_vref_ramp_mV + CONTROL_VREF_SLEW_UP_MV_PER_TICK;
          s_vref_ramp_mV = (next > target_mV) ? target_mV : next;
        }
        s_vin_baseline_mV =
            (s_vin_baseline_mV * 15u + s_input_voltage_mV + 8u) >> 4;
      } else if (s_vref_ramp_mV > target_mV) {
        /* Pause downward ramp if synchronous reverse energy pumps up VIN. */
        uint32_t vin_pump_limit =
            s_vin_baseline_mV + CONTROL_VIN_PUMP_HOLD_MARGIN_MV;
        uint32_t vin_ovp_guard = (s_settings.vin_ovp_mV > 3000u)
                                     ? (uint32_t)(s_settings.vin_ovp_mV - 3000u)
                                     : (uint32_t)s_settings.vin_ovp_mV;
        if (vin_pump_limit > vin_ovp_guard)
          vin_pump_limit = vin_ovp_guard;
        if (s_input_voltage_mV <= vin_pump_limit) {
          uint32_t step = CONTROL_VREF_SLEW_DOWN_MV_PER_TICK;
          s_vref_ramp_mV = (s_vref_ramp_mV > target_mV + step)
                               ? (s_vref_ramp_mV - step)
                               : target_mV;
        }
      } else {
        s_vin_baseline_mV =
            (s_vin_baseline_mV * 15u + s_input_voltage_mV + 8u) >> 4;
      }
    }

    vref_mV = s_vref_ramp_mV;
    sample.voltage_mV = s_output_voltage_mV;
    sample.reference_mV = vref_mV;
    sample.current_limit_mA = current_limit;
    sample.enable = loop_enable;
    s_current_reference_mA = VoltageLoop_Update(&sample);
    s_voltage_duty_limit_q15 = VoltageLoop_DutyLimit(
        s_input_voltage_mV, s_output_voltage_mV, vref_mV, sample.enable,
        (s_current_reference_mA >= current_limit && current_limit != 0u) ? 1u
                                                                         : 0u);
  }
  if (s_input_voltage_mV >= CONTROL_VIN_MIN_MV &&
      s_input_voltage_mV <= CONTROL_VIN_MAX_MV &&
      s_output_voltage_mV <= CONTROL_VOUT_MAX_MV)
    s_current_b0_q15 = CurrentLoopSchedule_B0(
        (s_output_voltage_mV <= s_input_voltage_mV) ? CONTROL_REGION_BUCK
                                                    : CONTROL_REGION_BOOST,
        s_input_voltage_mV, s_output_voltage_mV, s_current_reference_mA);
  else
    s_current_b0_q15 = 0;
}

static char *vofa_append_uint(char *p, uint32_t v) {
  char tmp[10];
  uint8_t n = 0u;
  do {
    tmp[n++] = (char)('0' + (v % 10u));
    v /= 10u;
  } while (v != 0u);
  while (n > 0u) {
    *p++ = tmp[--n];
  }
  return p;
}

static char *vofa_append_milli(char *p, int32_t milli) {
  uint32_t mag;
  uint32_t frac;
  if (milli < 0) {
    *p++ = '-';
    mag = (uint32_t)(-milli);
  } else {
    mag = (uint32_t)milli;
  }
  p = vofa_append_uint(p, mag / 1000u);
  *p++ = '.';
  frac = mag % 1000u;
  *p++ = (char)('0' + (frac / 100u));
  *p++ = (char)('0' + ((frac / 10u) % 10u));
  *p++ = (char)('0' + (frac % 10u));
  return p;
}

static char *vofa_append_deci(char *p, uint32_t deci) {
  p = vofa_append_uint(p, deci / 10u);
  *p++ = '.';
  *p++ = (char)('0' + (deci % 10u));
  return p;
}

/* Stream 8 channels at 500 Hz over USB CDC1 in VOFA+ FireWater format:
 * VOUT(V), IOUT(A), IREF(A), VIN(V), IIN(A), VREF(V), DUTY(%), STAGE(0/1/2)\n
 */
static void vofa_stream_process(uint32_t now) {
  static uint32_t s_last_vofa_ms;
  char line[96];
  char *p = line;
  const current_loop_result_t *cur;

  if (!UF4_USB_VofaIsReady() ||
      (uint32_t)(now - s_last_vofa_ms) < CONTROL_VOFA_PERIOD_MS)
    return;
  s_last_vofa_ms = now;
  cur = CurrentLoop_GetLastResult();

  p = vofa_append_milli(p, (int32_t)s_output_voltage_mV);
  *p++ = ',';
  p = vofa_append_milli(p, (int32_t)cur->output_current_mA);
  *p++ = ',';
  p = vofa_append_milli(p, (int32_t)s_current_reference_mA);
  *p++ = ',';
  p = vofa_append_milli(p, (int32_t)s_input_voltage_mV);
  *p++ = ',';
  p = vofa_append_milli(p, (int32_t)s_input_current_value);
  *p++ = ',';
  p = vofa_append_milli(p, (int32_t)s_vref_ramp_mV);
  *p++ = ',';
  p = vofa_append_deci(p, (uint32_t)s_duty_command_value);
  *p++ = ',';
  p = vofa_append_uint(p, (uint32_t)s_converter_mode_value);
  *p++ = '\n';

  (void)UF4_USB_VofaWrite((const uint8_t *)line, (uint16_t)(p - line));
}

void ControlApp_Process(void) {
  static uint8_t was_enabled;
  uint32_t now = HAL_GetTick();
  {
    uint8_t rx_byte;
    while (UF4_USB_CDC_PopByte(&rx_byte)) {
      if (UF4_InputByte(rx_byte) != 0u)
        s_last_frame_tick = now;
    }
  }
  UF4_ProcessMs(now);
  if (s_adc5_processed_count != s_adc5_sample_count) {
    s_temp2_value = TemperatureSensor_FromAdc(s_adc5_samples[0], false);
    s_temp1_value = TemperatureSensor_FromAdc(s_adc5_samples[1], false);
    s_adc5_processed_count = s_adc5_sample_count;
  }
  update_protection_zero(now);
  if (s_pending_fault != POWER_FAULT_NONE) {
    PowerMode_ReportFault(&s_mode, s_pending_fault);
    s_pending_fault = POWER_FAULT_NONE;
  }
#if !CONTROL_TEST_STANDALONE
  if (PowerMode_IsPwmEnabled(&s_mode) &&
      (uint32_t)(now - s_last_frame_tick) > CONTROL_COMM_TIMEOUT_MS)
    PowerMode_ReportFault(&s_mode, POWER_FAULT_COMMUNICATION);
#endif
  if (PowerMode_IsPwmEnabled(&s_mode) &&
      (int32_t)(now - s_adc1_last_sample_tick) >
          (int32_t)CONTROL_ADC_TIMEOUT_MS) {
    s_adc_fault_detail |= (1u << 6);
    PowerMode_ReportFault(&s_mode, POWER_FAULT_ADC);
  }
  /* current_zero_stable() is enforced with 1 ms (100-sample) debounce in the
   * 100 kHz ISR. */
#if !CONTROL_TEST_STANDALONE
  if (PowerMode_IsPwmEnabled(&s_mode) &&
      ((uint32_t)(now - s_adc5_last_sample_tick) > 500u ||
       s_temp1_value == TEMPERATURE_SENSOR_INVALID ||
       s_temp2_value == TEMPERATURE_SENSOR_INVALID))
    PowerMode_ReportFault(&s_mode, POWER_FAULT_ADC);
  if (PowerMode_IsPwmEnabled(&s_mode) &&
      (s_temp1_value >= s_settings.otp_centi_c ||
       s_temp2_value >= s_settings.otp_centi_c))
    PowerMode_ReportFault(&s_mode, POWER_FAULT_OVERTEMPERATURE);
#endif
  /* Debounced hardware OVP/OCP latching is handled in the 10 kHz TIM6 ISR. */
  auto_recover_fault(now);
  PowerMode_Process(&s_mode, now);
  s_fast_enabled = PowerMode_IsPwmEnabled(&s_mode);
  s_fast_direction = PowerMode_GetDirection(&s_mode);
  if (was_enabled && !s_fast_enabled) {
    CurrentLoop_Reset();
    s_current_reference_mA = 0u;
  }
  was_enabled = s_fast_enabled;
  if (s_adc5_ready && (uint32_t)(now - s_adc5_start_tick) >= 100u) {
    int32_t core_c;
    s_adc5_ready = 0u;
    s_adc5_start_tick = now;
    if (HAL_ADCEx_InjectedStart(&hadc5) == HAL_OK) {
      if (HAL_ADCEx_InjectedPollForConversion(&hadc5, 2u) == HAL_OK) {
        core_c = __HAL_ADC_CALC_TEMPERATURE(
            (uint32_t)s_settings.adc_vref_mV,
            HAL_ADCEx_InjectedGetValue(&hadc5, ADC_INJECTED_RANK_1),
            ADC_RESOLUTION_12B);
        s_core_temperature_value =
            core_c < 0 ? 0u
                       : (core_c > 655 ? 65535u : (uint16_t)(core_c * 100));
      }
      (void)HAL_ADCEx_InjectedStop(&hadc5);
    }
    if (HAL_ADC_Start_DMA(&hadc5, (uint32_t *)s_adc5_samples, 2u) != HAL_OK)
      s_adc5_ready = 1u;
    else
      __HAL_DMA_DISABLE_IT(&hdma_adc5, DMA_IT_HT);
  }
  update_telemetry();
  vofa_stream_process(now);
  if (s_save_pending) {
    uf4_error_t result =
        (Param_Commit() == PARAM_OK) ? UF4_OK : UF4_ERR_INTERNAL;
    s_save_pending = 0u;
    UF4_CompleteSave(result);
  }
  (void)HAL_IWDG_Refresh(&hiwdg);
}
