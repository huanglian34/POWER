#ifndef CHERRYUSB_CONFIG_H
#define CHERRYUSB_CONFIG_H

/* Do NOT include stm32g4xx_hal.h or stm32g474xx.h here:
 * port/fsdev/usb_dc_fsdev.c includes usb_fsdev_reg.h which defines its own
 * USB_TypeDef and USB register macros. */

#define CONFIG_USB_PRINTF(...)           ((void)0)
#define CONFIG_USB_DBG_LEVEL             USB_DBG_ERROR
#define CONFIG_USB_ALIGN_SIZE            4
#define USB_NOCACHE_RAM_SECTION
#define CONFIG_USB_MEMCPY_DISABLE

#define CONFIG_USBDEV_REQUEST_BUFFER_LEN 256
#define CONFIG_USBDEV_ADVANCE_DESC
#define CONFIG_USBDEV_MAX_BUS            1

#define CONFIG_USBDEV_FSDEV_PMA_ACCESS   1
#define CONFIG_USB_FSDEV_RAM_SIZE        1024

#ifndef usb_phyaddr2ramaddr
#define usb_phyaddr2ramaddr(addr)        (addr)
#endif

#ifndef usb_ramaddr2phyaddr
#define usb_ramaddr2phyaddr(addr)        (addr)
#endif

#endif /* CHERRYUSB_CONFIG_H */
