#pragma once
#include <stdint.h>
#include "ae350_address_map.h"

enum {
    AE350_VIDEO_OK=0, AE350_VIDEO_ALIGN=1, AE350_VIDEO_RANGE=2,
    AE350_VIDEO_BUSY=3, AE350_VIDEO_TIMEOUT=4, AE350_VIDEO_DEVICE=5,
    AE350_VIDEO_STATE=6, AE350_VIDEO_CACHE=7
};
enum {
    VIDEO_CONTROL=AE350_VIDEO_CONTROL,
    VIDEO_STATUS=AE350_VIDEO_STATUS,
    VIDEO_IRQ_STATUS=AE350_VIDEO_IRQ_STATUS,
    VIDEO_IRQ_ENABLE=AE350_VIDEO_IRQ_ENABLE,
    VIDEO_LAST_ERROR=AE350_VIDEO_LAST_ERROR,
    VIDEO_BASE0=AE350_VIDEO_BASE0,
    VIDEO_BASE1=AE350_VIDEO_BASE1,
    VIDEO_STRIDE=AE350_VIDEO_STRIDE,
    VIDEO_COMMIT=AE350_VIDEO_COMMIT,
    VIDEO_FRAMES=AE350_VIDEO_FRAMES,
    VIDEO_UNDERFLOWS=AE350_VIDEO_UNDERFLOWS,
    VIDEO_ADDRESS_ERROR=AE350_VIDEO_ADDRESS_ERROR,
    VIDEO_TIMEOUT_ERROR=AE350_VIDEO_TIMEOUT_ERROR,
    VIDEO_ACTIVE_BASE=AE350_VIDEO_ACTIVE_BASE,
    VIDEO_PIXEL_HZ=AE350_VIDEO_PIXEL_HZ,
    VIDEO_GEOMETRY=AE350_VIDEO_GEOMETRY,
    VIDEO_EPOCH=AE350_VIDEO_EPOCH,
    VIDEO_FIFO_MIN=AE350_VIDEO_FIFO_MIN,
    VIDEO_MAX_WAIT=AE350_VIDEO_MAX_WAIT,
    VIDEO_READS=AE350_VIDEO_READS,
    VIDEO_CPU_COMMANDS=AE350_VIDEO_CPU_COMMANDS,
    VIDEO_MISSING=AE350_VIDEO_MISSING,
    VIDEO_SEQUENCE=AE350_VIDEO_SEQUENCE,
    VIDEO_ACTIVE_SEQUENCE=AE350_VIDEO_ACTIVE_SEQUENCE,
    VIDEO_CANCELLED=AE350_VIDEO_CANCELLED,
    VIDEO_ERRORS=AE350_VIDEO_ERRORS,
    VIDEO_CLEAR_ERRORS=AE350_VIDEO_CLEAR_ERRORS,
    VIDEO_ENABLE=AE350_VIDEO_ENABLE,
    VIDEO_HPD=AE350_VIDEO_HPD,
    VIDEO_MODE=AE350_VIDEO_MODE,
    VIDEO_FORMAT=AE350_VIDEO_FORMAT,
    VIDEO_MODE_CONFIG=AE350_VIDEO_MODE_CONFIG,
    VIDEO_MODE_STATUS=AE350_VIDEO_MODE_STATUS,
    VIDEO_MODE_COUNT=AE350_VIDEO_MODE_COUNT,
    VIDEO_MODE_INFO_BASE=AE350_VIDEO_MODE_INFO_BASE,
    VIDEO_REQUEST_OVERFLOW=AE350_VIDEO_REQUEST_OVERFLOW
};

enum ae350_video_format {
    AE350_VIDEO_RGB565=0, AE350_VIDEO_RGB888=1, AE350_VIDEO_XRGB8888=2
};
/* HPD is synchronized and filtered for 2ms. DEVICE on older gateware. */
int ae350_video_get_hpd(unsigned *connected);
/* RGB888 and XRGB8888 store B,G,R[,X] in increasing byte addresses.
 * X is ignored. RGB565 uses little-endian R[15:11],G[10:5],B[4:0]. */
struct ae350_video_mode {
    uint32_t width,height,pixel_hz,nominal_pixel_hz,refresh_millihz;
    uint32_t h_total,v_total,h_front,h_sync,h_back,v_front,v_sync,v_back;
    uint32_t h_positive,v_positive;
};
unsigned ae350_video_mode_count(void);
int ae350_video_get_mode(unsigned mode,struct ae350_video_mode *info);
/* Stops/drains scanout, changes PLL/timing/format and invalidates software
 * buffer configuration. Call init_buffers() and present() afterwards.
 * A timeout keeps ownership conservative and requires recovery. */
int ae350_video_set_mode(unsigned mode,enum ae350_video_format format);
/* Disabling drains scanout first. Enabling is permitted only while stopped
 * and mode-ready; present() enables output automatically before starting. */
int ae350_video_enable(unsigned enable);
int ae350_video_init_buffers(void *first,void *second,uint32_t stride);
void *ae350_video_frame_data(unsigned slot);

struct ae350_video_status {
    uint32_t mode,format,mode_busy,output_enabled;
    uint32_t width,height,stride,pixel_hz,enabled,active,pending,stopped,active_base;
    uint32_t frames,underflows,missing,address_error,timeout_error,errors;
    uint32_t fifo_min,max_wait,reads,cpu_commands,request_overflow;
};

/* Single caller. Buffers own full 32-byte lines, remain live through present/
 * stop completion, and must not overlap. Only a back buffer returned by frame()
 * may be modified. present() cleans it before vblank transfer of ownership.
 * A failed/timed-out stop never releases the buffers to the caller. */
int ae350_video_init(uint16_t *first,uint16_t *second,uint32_t stride);
int ae350_video_present(unsigned slot);
int ae350_video_stop(void);
uint16_t *ae350_video_frame(unsigned slot);
int ae350_video_status(struct ae350_video_status *status);

/* HPD event bits, independent of the current connection level. Events latch
 * while masked. W1C acknowledges only bits observed by the caller. */
enum { AE350_VIDEO_HPD_CONNECTED=1, AE350_VIDEO_HPD_DISCONNECTED=2,
       AE350_VIDEO_HPD_EVENTS=3 };
int ae350_video_hpd_events(unsigned *events);
int ae350_video_hpd_irq_enable(unsigned mask);
int ae350_video_hpd_ack(unsigned events);
