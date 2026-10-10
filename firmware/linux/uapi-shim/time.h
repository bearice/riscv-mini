#ifndef MINI_UAPI_TIME_H
#define MINI_UAPI_TIME_H
/* Match RV32 Linux time64 userspace, rather than host x86 libc headers. */
typedef long long time_t;
struct timespec { time_t tv_sec; long long tv_nsec; };
#endif
