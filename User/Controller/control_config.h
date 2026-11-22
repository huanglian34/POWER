/**
  ******************************************************************************
  * @file    control_config.h
  * @author  UF4
  * @date    26-9-26 下午5:24
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
#ifndef CONTROL_CONFIG_H
#define CONTROL_CONFIG_H

#include <stdint.h>

#if defined(__GNUC__)
#define CONTROL_RAMFUNC __attribute__((section(".RamFunc")))
#define CONTROL_FORCE_INLINE static inline __attribute__((always_inline))
#else
#define CONTROL_RAMFUNC
#define CONTROL_FORCE_INLINE static inline
#endif

#define CONTROL_PWM_HZ             200000u
#define CONTROL_CURRENT_LOOP_HZ    100000u
#define CONTROL_VOLTAGE_LOOP_HZ     10000u

/* Standalone bring-up test mode (no NTC soldered, no UART host, default 12V auto-start).
 * Set to 0 once NTCs and UART host control are connected. */
#define CONTROL_TEST_STANDALONE    1
#define CONTROL_TEST_VSET_MV       12000u

/* Host communication transport: 1 = CherryUSB CDC ACM, 0 = USART1. */
#define CONTROL_COMM_USE_USB_CDC   1

/* Outer-loop defaults: approximately 0.5 Hz crossover on 470 uF. */
/* Kp is mA/mV in Q16; Ki*Ts is (mA/mV)/sample in Q30. */
#define CONTROL_VOLTAGE_KP_Q16       96900L
#define CONTROL_VOLTAGE_KI_TS_Q30   100000L
#define CONTROL_VOLTAGE_CURRENT_MAX_MA 10000u

#define CONTROL_I_BASE_MA          10000
#define CONTROL_I_SOFT_LIMIT_MA    10000
#define CONTROL_I_OCP_HW_MA        11000
#define CONTROL_ADC_FS             4095
#define CONTROL_V_ADC_FS           65520u   /* 12-bit ADC2/ADC3 with 16x oversampling, no right-shift */
#define CONTROL_ADC_MA_PER_COUNT_NUM 6716L  /* INA240A1 (20x) + 6 mOhm = 120 mV/A -> 6.7155 mA/LSB */
#define CONTROL_ADC_MA_PER_COUNT_DEN 1000L

#define CONTROL_DUTY_MIN_Q15       0
#define CONTROL_DUTY_MAX_Q15       32767
#define CONTROL_DUTY_SCALE_Q15     6554

/* Unified monotonic voltage gain M = Vout/Vin in Q14 (16384 = 1.00x). */
#define CONTROL_GAIN_ONE_Q14       16384u
#define CONTROL_GAIN_MAX_Q14       57344u   /* 3.50x max ratio (boost duty <= 71.4%) */
#define CONTROL_GAIN_SCALE_Q14     3277     /* +/-0.20x inner-loop trim around M_base */

#define CONTROL_VIN_MIN_MV         12000u
#define CONTROL_VIN_MAX_MV         65500u
#define CONTROL_VOUT_MIN_MV         1000u
#define CONTROL_VOUT_MAX_MV        65500u
#define CONTROL_BOOST_RATIO_MAX       2u

/* Runtime bidirectional voltage reference slew-rate limits at 10 kHz (100 us/tick).
 * Up:   4 mV/tick = 40 V/s (20 V -> 30 V in 0.25 s, ~19 mA into 470 uF).
 * Down: 2 mV/tick = 20 V/s (32 V -> 12 V in 1.00 s, prevents reverse bus pump-up). */
#define CONTROL_VREF_SLEW_UP_MV_PER_TICK     4u
#define CONTROL_VREF_SLEW_DOWN_MV_PER_TICK   2u
#define CONTROL_VIN_SAG_HOLD_MARGIN_MV    1500u
#define CONTROL_VIN_PUMP_HOLD_MARGIN_MV   1500u

#endif /* CONTROL_CONFIG_H */
