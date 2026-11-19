#include "temperature_sensor.h"

/* Nominal ADC codes at 0..150 C in 5 C steps with 10k B=3380 NTC to ground and 10k pull-up. */
static const uint16_t s_ntc_codes[] = {
    3024, 2839, 2644, 2445, 2245, 2048, 1857, 1675,
    1505, 1347, 1203, 1072,  954,  849,  755,  672,
     598,  533,  476,  425,  380,  341,  306,  276,
     249,  224,  203,  184,  167,  152,  139
};

uint16_t TemperatureSensor_FromAdc(uint16_t raw, bool ntc_to_ground)
{
    uint16_t code;
    unsigned i;
    if (raw < 8u || raw > 4087u) return TEMPERATURE_SENSOR_INVALID;
    code = ntc_to_ground ? raw : (uint16_t)(4095u - raw);
    if (code >= s_ntc_codes[0]) return 0u;
    if (code <= s_ntc_codes[30]) return 15000u;
    for (i = 0u; i < 30u; ++i)
    {
        if (code >= s_ntc_codes[i + 1u])
            return (uint16_t)(i * 500u +
                (uint32_t)(s_ntc_codes[i] - code) * 500u /
                (s_ntc_codes[i] - s_ntc_codes[i + 1u]));
    }
    return TEMPERATURE_SENSOR_INVALID;
}
