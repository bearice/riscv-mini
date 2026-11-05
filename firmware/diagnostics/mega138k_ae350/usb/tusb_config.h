#pragma once
#define CFG_TUSB_MCU OPT_MCU_NONE
#define CFG_TUSB_OS OPT_OS_NONE
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_HOST | OPT_MODE_FULL_SPEED)
#define CFG_TUD_ENABLED 0
#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG 0
#endif
#if CFG_TUSB_DEBUG
#include <stdio.h>
#include <ctype.h>
/* TinyUSB's hex dump only needs printable ASCII, without a libc table. */
#undef isprint
#define isprint(c) ((unsigned)(c)-0x20u < 0x5fu)
#define CFG_TUSB_DEBUG_PRINTF usb_debug_printf
#endif
#define CFG_TUH_DEVICE_MAX 4
#define CFG_TUH_HUB 2
#define CFG_TUH_ENUMERATION_BUFSIZE 512
#define CFG_TUH_HID 8
#ifndef USB_TEST_MSC
#define USB_TEST_MSC 0
#endif
#define CFG_TUH_MSC USB_TEST_MSC
#ifndef USB_RESET_PROBE
#define USB_RESET_PROBE 0
#endif
#define CFG_TUH_HID_EPIN_BUFSIZE 64
#define CFG_TUH_HID_EPOUT_BUFSIZE 64
#define CFG_TUH_MEM_ALIGN __attribute__((aligned(4)))

#define TUP_USBIP_OHCI
#define TUP_USBIP_OHCI_LITEX
#define TUP_OHCI_RHPORTS 1
