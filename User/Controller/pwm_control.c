#include "pwm_control.h"
#include "control_config.h"
#include "main.h"

#define PWM_PERIOD 27200u
/* STM32G474 HRTIM at MUL32 requires CMPx >= 0x0060 (96) and <= PER - 0x0020
 * for a compare match to fire; values < 96 are ignored by hardware and cause
 * 100% duty pass-through. Use 128 ticks margin on both ends. */
#define PWM_CMP_MIN 128u
#define PWM_CMP_MAX (PWM_PERIOD - 128u)
/* 3-mode FSBB transition thresholds in Q14 (16384 = 1.00x) with hysteresis. */
#define PWM_GAIN_BUCK_TO_BB   14746u  /* 0.90x */
#define PWM_GAIN_BB_TO_BUCK   13763u  /* 0.84x */
#define PWM_GAIN_BB_TO_BOOST  19005u  /* 1.16x */
#define PWM_GAIN_BOOST_TO_BB  18022u  /* 1.10x */
#define PWM_BB_FIXED_CMP      21216u  /* 0.78 * 27200: well clear of deadtime zone */
#define PWM_BOOST_MIN_HIGH    6800u   /* 0.25 * 27200: limits boost low-side <= 75% */
#define PWM_OUTPUTS (HRTIM_OUTPUT_TE1 | HRTIM_OUTPUT_TE2 | HRTIM_OUTPUT_TF1 | HRTIM_OUTPUT_TF2)
#define PWM_TIMERS (HRTIM_TIMERID_MASTER | HRTIM_TIMERID_TIMER_E | HRTIM_TIMERID_TIMER_F)

/* Gate drive remains inhibited until the four waveforms and fault response
   are checked on the actual board with a current-limited supply. */
#ifndef CONTROL_POWER_STAGE_VALIDATED
#define CONTROL_POWER_STAGE_VALIDATED CONTROL_TEST_STANDALONE
#endif

static HRTIM_HandleTypeDef *s_hrtim;
static bool s_enabled;
static bool s_pulse_skipped;
static control_direction_t s_direction;
static control_region_t s_region;
static pwm_stage_t s_stage;

void PwmControl_Init(HRTIM_HandleTypeDef *h)
{
    s_hrtim = h;
    s_enabled = false;
    s_pulse_skipped = false;
    s_direction = CONTROL_DIRECTION_FORWARD;
    s_region = CONTROL_REGION_BUCK;
    s_stage = PWM_STAGE_BUCK;
    if (h != NULL)
    {
        (void)HAL_HRTIM_WaveformOutputStop(h, PWM_OUTPUTS);
        h->Instance->sMasterRegs.MCMP1R = PWM_CMP_MIN;
        h->Instance->sMasterRegs.MCMP4R = PWM_CMP_MIN;
        if (HAL_HRTIM_WaveformCountStart_IT(h, PWM_TIMERS) != HAL_OK)
            Error_Handler();
    }
}

CONTROL_RAMFUNC void PwmControl_SetMode(control_direction_t direction, control_region_t region)
{
    s_direction = direction;
    s_region = region;
}

HAL_StatusTypeDef PwmControl_Enable(void)
{
    HAL_StatusTypeDef status;
    if (s_hrtim == NULL || !CONTROL_POWER_STAGE_VALIDATED)
        return HAL_ERROR;
    s_pulse_skipped = false;
    status = HAL_HRTIM_WaveformOutputStart(s_hrtim, PWM_OUTPUTS);
    s_enabled = (status == HAL_OK);
    if (status != HAL_OK)
        (void)HAL_HRTIM_WaveformOutputStop(s_hrtim, PWM_OUTPUTS);
    return status;
}

HAL_StatusTypeDef PwmControl_Disable(void)
{
    HAL_StatusTypeDef status = HAL_OK;
    if (s_hrtim != NULL)
        status = HAL_HRTIM_WaveformOutputStop(s_hrtim, PWM_OUTPUTS);
    s_enabled = false;
    s_pulse_skipped = false;
    s_stage = PWM_STAGE_BUCK;
    return status;
}

bool PwmControl_IsEnabled(void) { return s_enabled; }
pwm_stage_t PwmControl_GetStage(void) { return s_stage; }

CONTROL_RAMFUNC HAL_StatusTypeDef PwmControl_SetGainQ14(uint16_t gain_q14)
{
    uint32_t e_compare, f_compare;
    const uint32_t pass = PWM_PERIOD + 1u;
    if (s_hrtim == NULL) return HAL_ERROR;
    if (gain_q14 > CONTROL_GAIN_MAX_Q14) gain_q14 = CONTROL_GAIN_MAX_Q14;

    /* Hysteretic 3-mode FSBB stage selection: Buck / Buck-Boost / Boost. */
    if (s_stage == PWM_STAGE_BUCK)
    {
        if (gain_q14 > PWM_GAIN_BUCK_TO_BB) s_stage = PWM_STAGE_BUCK_BOOST;
    }
    else if (s_stage == PWM_STAGE_BUCK_BOOST)
    {
        if (gain_q14 < PWM_GAIN_BB_TO_BUCK) s_stage = PWM_STAGE_BUCK;
        else if (gain_q14 > PWM_GAIN_BB_TO_BOOST) s_stage = PWM_STAGE_BOOST;
    }
    else
    {
        if (gain_q14 < PWM_GAIN_BOOST_TO_BB) s_stage = PWM_STAGE_BUCK_BOOST;
    }

    if (s_stage == PWM_STAGE_BUCK)
    {
        e_compare = ((uint32_t)gain_q14 * PWM_PERIOD + (CONTROL_GAIN_ONE_Q14 / 2u)) >> 14;
        if (e_compare < PWM_CMP_MIN) e_compare = PWM_CMP_MIN;
        if (e_compare > PWM_CMP_MAX) e_compare = PWM_CMP_MAX;
        f_compare = pass;
    }
    else if (s_stage == PWM_STAGE_BUCK_BOOST)
    {
        e_compare = PWM_BB_FIXED_CMP;
        f_compare = (PWM_BB_FIXED_CMP * CONTROL_GAIN_ONE_Q14) / gain_q14;
        if (f_compare < PWM_CMP_MIN) f_compare = PWM_CMP_MIN;
        if (f_compare > PWM_CMP_MAX) f_compare = PWM_CMP_MAX;
    }
    else
    {
        e_compare = pass;
        f_compare = (PWM_PERIOD * CONTROL_GAIN_ONE_Q14) / gain_q14;
        if (f_compare < PWM_BOOST_MIN_HIGH) f_compare = PWM_BOOST_MIN_HIGH;
        if (f_compare > PWM_CMP_MAX) f_compare = PWM_CMP_MAX;
    }

    if (s_direction == CONTROL_DIRECTION_REVERSE)
    {
        uint32_t tmp = e_compare;
        e_compare = f_compare;
        f_compare = tmp;
    }

    s_hrtim->Instance->sMasterRegs.MCMP4R = e_compare;
    s_hrtim->Instance->sMasterRegs.MCMP1R = f_compare;

    /* True 0% duty pulse-skipping with hysteresis for near-zero CC / emergency foldback. */
    if (s_enabled)
    {
        if (s_stage == PWM_STAGE_BUCK && gain_q14 < 48u)
        {
            if (!s_pulse_skipped)
            {
                uint32_t leg = (s_direction == CONTROL_DIRECTION_FORWARD) ?
                               (HRTIM_OUTPUT_TE1 | HRTIM_OUTPUT_TE2) :
                               (HRTIM_OUTPUT_TF1 | HRTIM_OUTPUT_TF2);
                s_hrtim->Instance->sCommonRegs.ODISR = leg;
                s_pulse_skipped = true;
            }
        }
        else if (s_pulse_skipped && (s_stage != PWM_STAGE_BUCK || gain_q14 >= 96u))
        {
            s_hrtim->Instance->sCommonRegs.OENR = PWM_OUTPUTS;
            s_pulse_skipped = false;
        }
    }
    return HAL_OK;
}

HAL_StatusTypeDef PwmControl_SetDutyQ15(uint16_t duty_q15)
{
    uint32_t active, e_compare, f_compare;
    const uint32_t pass = PWM_PERIOD + 1u;
    if (s_hrtim == NULL) return HAL_ERROR;
    if (duty_q15 > 32767u) duty_q15 = 32767u;
    active = ((uint32_t)duty_q15 * PWM_PERIOD + 16384u) / 32768u;
    if (active < PWM_CMP_MIN) active = PWM_CMP_MIN;
    if (active > PWM_CMP_MAX) active = PWM_CMP_MAX;

    /* E1/F1 drive the high sides. Boost switches the complementary low side. */
    if (s_direction == CONTROL_DIRECTION_FORWARD)
    {
        e_compare = (s_region == CONTROL_REGION_BUCK) ? active : pass;
        f_compare = (s_region == CONTROL_REGION_BUCK) ? pass : PWM_PERIOD - active;
    }
    else
    {
        e_compare = (s_region == CONTROL_REGION_BUCK) ? pass : PWM_PERIOD - active;
        f_compare = (s_region == CONTROL_REGION_BUCK) ? active : pass;
    }
    s_hrtim->Instance->sMasterRegs.MCMP4R = e_compare;
    s_hrtim->Instance->sMasterRegs.MCMP1R = f_compare;
    return HAL_OK;
}
