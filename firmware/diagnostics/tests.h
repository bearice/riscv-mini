#pragma once
#include <hal/hal.h>
/* Shared DDR-resident firmware diagnostics, invoked explicitly by commands. */
void tests_help(void);
void tests_poll(void);
void tests_video_stop(void);
int tests_command(const char *command);
/* BIOS observes the same input stream without consuming it twice. */
void tests_usb_key(const hal_usb_key_t *key);
void tests_usb_mouse(const hal_usb_mouse_t *mouse);
void tests_usb_report(const hal_usb_report_t *report);
