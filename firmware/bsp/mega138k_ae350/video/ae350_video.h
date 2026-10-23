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
    VIDEO_REQUEST_OVERFLOW=AE350_VIDEO_REQUEST_OVERFLOW
};

struct ae350_video_status {
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
