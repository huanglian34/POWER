#include "uf4com_usb_cdc.h"
#include "stm32g4xx_hal.h"
#include "usbd_core.h"
#include "usbd_cdc_acm.h"
#include <string.h>

#define USBD_VID               0xCAFEU
#define USBD_PID               0x4741U
#define USBD_MAX_POWER         100U

#define CDC_IN_EP              0x81U
#define CDC_OUT_EP             0x02U
#define CDC_INT_EP             0x83U
#define CDC_MAX_MPS            64U

#define USB_CONFIG_SIZE        (9U + CDC_ACM_DESCRIPTOR_LEN)

#define USB_CDC_RX_RING_SIZE   512U
#define USB_CDC_RX_RING_MASK   (USB_CDC_RX_RING_SIZE - 1U)
#define USB_CDC_TX_RING_SIZE   1024U
#define USB_CDC_TX_RING_MASK   (USB_CDC_TX_RING_SIZE - 1U)

static const uint8_t s_device_descriptor[] = {
    USB_DEVICE_DESCRIPTOR_INIT(USB_2_0, 0xEFU, 0x02U, 0x01U, USBD_VID, USBD_PID, 0x0100U, 0x01U)
};

static const uint8_t s_config_descriptor[] = {
    USB_CONFIG_DESCRIPTOR_INIT(USB_CONFIG_SIZE, 0x02U, 0x01U, USB_CONFIG_BUS_POWERED, USBD_MAX_POWER),
    CDC_ACM_DESCRIPTOR_INIT(0x00U, CDC_INT_EP, CDC_OUT_EP, CDC_IN_EP, CDC_MAX_MPS, 0x02U)
};

static const uint8_t s_device_quality_descriptor[] = {
    0x0AU,
    USB_DESCRIPTOR_TYPE_DEVICE_QUALIFIER,
    0x00U,
    0x02U,
    0x00U,
    0x00U,
    0x00U,
    0x40U,
    0x00U,
    0x00U,
};

static const char *s_string_descriptors[] = {
    (const char[]){ 0x09, 0x04 }, /* Langid: en-US */
    "UF4",                        /* Manufacturer */
    "UF4 DigitalPower CDC",       /* Product */
    "UF4G474-0001",               /* Serial Number */
};

static const uint8_t *device_descriptor_callback(uint8_t speed)
{
    (void)speed;
    return s_device_descriptor;
}

static const uint8_t *config_descriptor_callback(uint8_t speed)
{
    (void)speed;
    return s_config_descriptor;
}

static const uint8_t *device_quality_descriptor_callback(uint8_t speed)
{
    (void)speed;
    return s_device_quality_descriptor;
}

static const char *string_descriptor_callback(uint8_t speed, uint8_t index)
{
    (void)speed;
    if (index >= (sizeof(s_string_descriptors) / sizeof(s_string_descriptors[0]))) {
        return NULL;
    }
    return s_string_descriptors[index];
}

static const struct usb_descriptor s_cdc_descriptor = {
    .device_descriptor_callback = device_descriptor_callback,
    .config_descriptor_callback = config_descriptor_callback,
    .device_quality_descriptor_callback = device_quality_descriptor_callback,
    .string_descriptor_callback = string_descriptor_callback
};

USB_MEM_ALIGNX static uint8_t s_usb_rx_pkt[CDC_MAX_MPS];
USB_MEM_ALIGNX static uint8_t s_usb_tx_pkt[CDC_MAX_MPS];

static uint8_t s_rx_ring[USB_CDC_RX_RING_SIZE];
static volatile uint16_t s_rx_head;
static volatile uint16_t s_rx_tail;

static uint8_t s_tx_ring[USB_CDC_TX_RING_SIZE];
static volatile uint16_t s_tx_head;
static volatile uint16_t s_tx_tail;

static volatile uint8_t s_usb_inited;
static volatile uint8_t s_usb_configured;
static volatile uint8_t s_usb_suspended;
static volatile uint8_t s_tx_busy;
static volatile uint8_t s_tx_need_zlp;

static struct cdc_line_coding s_line_coding = {
    .dwDTERate = 115200U,
    .bCharFormat = 0U,
    .bParityType = 0U,
    .bDataBits = 8U,
};

static struct usbd_interface s_intf0;
static struct usbd_interface s_intf1;

void usb_dc_low_level_init(void)
{
    RCC_PeriphCLKInitTypeDef periph_clk = { 0 };
    RCC_CRSInitTypeDef crs_init = { 0 };
    volatile uint32_t delay;

    if (__HAL_RCC_GET_FLAG(RCC_FLAG_HSI48RDY) == RESET) {
        __HAL_RCC_HSI48_ENABLE();
        while (__HAL_RCC_GET_FLAG(RCC_FLAG_HSI48RDY) == RESET) {
        }
    }

    periph_clk.PeriphClockSelection = RCC_PERIPHCLK_USB;
    periph_clk.UsbClockSelection = RCC_USBCLKSOURCE_HSI48;
    (void)HAL_RCCEx_PeriphCLKConfig(&periph_clk);

    __HAL_RCC_CRS_CLK_ENABLE();
    crs_init.Prescaler = RCC_CRS_SYNC_DIV1;
    crs_init.Source = RCC_CRS_SYNC_SOURCE_USB;
    crs_init.Polarity = RCC_CRS_SYNC_POLARITY_RISING;
    crs_init.ReloadValue = __HAL_RCC_CRS_RELOADVALUE_CALCULATE(48000000U, 1000U);
    crs_init.ErrorLimitValue = RCC_CRS_ERRORLIMIT_DEFAULT;
    crs_init.HSI48CalibrationValue = RCC_CRS_HSI48CALIBRATION_DEFAULT;
    HAL_RCCEx_CRSConfig(&crs_init);

    __HAL_RCC_USB_CLK_ENABLE();

    /* Power up the USB analog transceiver (clear PDWN while holding FRES) and wait > t_STARTUP. */
    USB->CNTR = USB_CNTR_FRES;
    for (delay = 600U; delay > 0U; --delay) {
        __NOP();
    }

    HAL_NVIC_SetPriority(USB_LP_IRQn, 9U, 0U);
    HAL_NVIC_EnableIRQ(USB_LP_IRQn);
}

void usb_dc_low_level_deinit(void)
{
    HAL_NVIC_DisableIRQ(USB_LP_IRQn);
    __HAL_RCC_USB_CLK_DISABLE();
}

static void start_next_tx_locked(void)
{
    uint16_t head;
    uint16_t tail;
    uint16_t avail;
    uint16_t chunk;
    uint16_t i;

    if (s_usb_configured == 0U || s_usb_suspended != 0U) {
        s_tx_busy = 0U;
        s_tx_need_zlp = 0U;
        return;
    }

    head = s_tx_head;
    tail = s_tx_tail;
    avail = (uint16_t)((head - tail) & USB_CDC_TX_RING_MASK);

    if (avail > 0U) {
        chunk = (avail > CDC_MAX_MPS) ? (uint16_t)CDC_MAX_MPS : avail;
        for (i = 0U; i < chunk; ++i) {
            s_usb_tx_pkt[i] = s_tx_ring[(tail + i) & USB_CDC_TX_RING_MASK];
        }
        s_tx_tail = (uint16_t)((tail + chunk) & USB_CDC_TX_RING_MASK);
        s_tx_busy = 1U;
        s_tx_need_zlp = (chunk == CDC_MAX_MPS && avail == chunk) ? 1U : 0U;
        if (usbd_ep_start_write(0U, CDC_IN_EP, s_usb_tx_pkt, chunk) < 0) {
            s_tx_busy = 0U;
            s_tx_need_zlp = 0U;
        }
    } else if (s_tx_need_zlp != 0U) {
        s_tx_need_zlp = 0U;
        s_tx_busy = 1U;
        if (usbd_ep_start_write(0U, CDC_IN_EP, NULL, 0U) < 0) {
            s_tx_busy = 0U;
        }
    } else {
        s_tx_busy = 0U;
    }
}

static void usbd_event_handler(uint8_t busid, uint8_t event)
{
    switch (event) {
        case USBD_EVENT_RESET:
        case USBD_EVENT_DISCONNECTED:
            s_usb_configured = 0U;
            s_usb_suspended = 0U;
            s_tx_busy = 0U;
            s_tx_need_zlp = 0U;
            s_tx_tail = s_tx_head;
            break;

        case USBD_EVENT_SUSPEND:
            s_usb_suspended = 1U;
            s_tx_busy = 0U;
            s_tx_need_zlp = 0U;
            break;

        case USBD_EVENT_RESUME:
            s_usb_suspended = 0U;
            break;

        case USBD_EVENT_CONFIGURED:
            s_usb_configured = 1U;
            s_usb_suspended = 0U;
            s_tx_busy = 0U;
            s_tx_need_zlp = 0U;
            s_tx_tail = s_tx_head;
            (void)usbd_ep_start_read(busid, CDC_OUT_EP, s_usb_rx_pkt, sizeof(s_usb_rx_pkt));
            break;

        default:
            break;
    }
}

static void usbd_cdc_acm_bulk_out(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    uint32_t i;
    uint16_t head = s_rx_head;
    uint16_t tail = s_rx_tail;

    (void)ep;
    if (nbytes > sizeof(s_usb_rx_pkt)) {
        nbytes = sizeof(s_usb_rx_pkt);
    }

    for (i = 0U; i < nbytes; ++i) {
        uint16_t next = (uint16_t)((head + 1U) & USB_CDC_RX_RING_MASK);
        if (next == tail) {
            break;
        }
        s_rx_ring[head] = s_usb_rx_pkt[i];
        head = next;
    }
    __DMB();
    s_rx_head = head;

    (void)usbd_ep_start_read(busid, CDC_OUT_EP, s_usb_rx_pkt, sizeof(s_usb_rx_pkt));
}

static void usbd_cdc_acm_bulk_in(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    (void)busid;
    (void)ep;
    (void)nbytes;
    start_next_tx_locked();
}

static struct usbd_endpoint s_cdc_out_ep = {
    .ep_addr = CDC_OUT_EP,
    .ep_cb = usbd_cdc_acm_bulk_out
};

static struct usbd_endpoint s_cdc_in_ep = {
    .ep_addr = CDC_IN_EP,
    .ep_cb = usbd_cdc_acm_bulk_in
};

void usbd_cdc_acm_set_line_coding(uint8_t busid, uint8_t intf, struct cdc_line_coding *line_coding)
{
    (void)busid;
    (void)intf;
    if (line_coding != NULL) {
        s_line_coding = *line_coding;
    }
}

void usbd_cdc_acm_get_line_coding(uint8_t busid, uint8_t intf, struct cdc_line_coding *line_coding)
{
    (void)busid;
    (void)intf;
    if (line_coding != NULL) {
        *line_coding = s_line_coding;
    }
}

void UF4_USB_CDC_Init(void)
{
    s_rx_head = 0U;
    s_rx_tail = 0U;
    s_tx_head = 0U;
    s_tx_tail = 0U;
    s_usb_configured = 0U;
    s_usb_suspended = 0U;
    s_tx_busy = 0U;
    s_tx_need_zlp = 0U;

    usbd_desc_register(0U, &s_cdc_descriptor);
    (void)usbd_add_interface(0U, usbd_cdc_acm_init_intf(0U, &s_intf0));
    (void)usbd_add_interface(0U, usbd_cdc_acm_init_intf(0U, &s_intf1));
    (void)usbd_add_endpoint(0U, &s_cdc_out_ep);
    (void)usbd_add_endpoint(0U, &s_cdc_in_ep);

    s_usb_inited = 1U;
    (void)usbd_initialize(0U, USB_BASE, usbd_event_handler);
}

void UF4_USB_CDC_Deinit(void)
{
    if (s_usb_inited != 0U) {
        s_usb_inited = 0U;
        s_usb_configured = 0U;
        s_usb_suspended = 0U;
        s_tx_busy = 0U;
        s_tx_need_zlp = 0U;
        (void)usbd_deinitialize(0U);
    }
}

bool UF4_USB_CDC_IsConfigured(void)
{
    return (s_usb_inited != 0U) && (s_usb_configured != 0U) && (s_usb_suspended == 0U);
}

bool UF4_USB_CDC_PopByte(uint8_t *byte)
{
    uint16_t head = s_rx_head;
    uint16_t tail = s_rx_tail;

    if (byte == NULL || head == tail) {
        return false;
    }

    __DMB();
    *byte = s_rx_ring[tail];
    s_rx_tail = (uint16_t)((tail + 1U) & USB_CDC_RX_RING_MASK);
    return true;
}

bool UF4_USB_CDC_Write(const uint8_t *data, uint16_t len)
{
    uint16_t head;
    uint16_t tail;
    uint16_t avail;
    uint16_t free_space;
    uint16_t i;

    if (data == NULL || len == 0U || s_usb_inited == 0U ||
        s_usb_configured == 0U || s_usb_suspended != 0U ||
        len > USB_CDC_TX_RING_MASK) {
        return false;
    }

    /* Mask ONLY USB_LP_IRQn so 100 kHz HRTIM1 and 10 kHz TIM6 control ISRs are unaffected. */
    HAL_NVIC_DisableIRQ(USB_LP_IRQn);
    __DSB();
    __ISB();

    if (s_usb_configured == 0U || s_usb_suspended != 0U) {
        HAL_NVIC_EnableIRQ(USB_LP_IRQn);
        return false;
    }

    head = s_tx_head;
    tail = s_tx_tail;
    avail = (uint16_t)((head - tail) & USB_CDC_TX_RING_MASK);
    free_space = (uint16_t)(USB_CDC_TX_RING_MASK - avail);

    if (free_space < len) {
        HAL_NVIC_EnableIRQ(USB_LP_IRQn);
        return false;
    }

    for (i = 0U; i < len; ++i) {
        s_tx_ring[(head + i) & USB_CDC_TX_RING_MASK] = data[i];
    }
    s_tx_head = (uint16_t)((head + len) & USB_CDC_TX_RING_MASK);

    if (s_tx_busy == 0U) {
        start_next_tx_locked();
    }

    HAL_NVIC_EnableIRQ(USB_LP_IRQn);
    return true;
}

void UF4_USB_CDC_IrqHandler(void)
{
    if (s_usb_inited != 0U) {
        USBD_IRQHandler(0U);
    }
}
