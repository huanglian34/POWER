#ifndef TEMPERATURE_SENSOR_H
#define TEMPERATURE_SENSOR_H

#include <stdbool.h>
#include <stdint.h>

#define TEMPERATURE_SENSOR_INVALID UINT16_MAX

/* 10 kOhm NTC (103F3380), B=3380, 10 kOhm divider. */
uint16_t TemperatureSensor_FromAdc(uint16_t raw, bool ntc_to_ground);

#endif
