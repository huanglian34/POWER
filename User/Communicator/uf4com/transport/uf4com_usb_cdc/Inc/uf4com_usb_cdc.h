#ifndef UF4COM_USB_CDC_H
#define UF4COM_USB_CDC_H

#include <stdbool.h>
#include <stdint.h>

void UF4_USB_CDC_Init(void);
void UF4_USB_CDC_Deinit(void);
bool UF4_USB_CDC_IsConfigured(void);
bool UF4_USB_CDC_PopByte(uint8_t *byte);
bool UF4_USB_CDC_Write(const uint8_t *data, uint16_t len);
void UF4_USB_CDC_IrqHandler(void);

#endif /* UF4COM_USB_CDC_H */
