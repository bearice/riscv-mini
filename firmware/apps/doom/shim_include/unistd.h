/* BIOS payload shim: POSIX surface DOOM includes but does not need at run
 * time on the BIOS target. */
#ifndef SHIM_UNISTD_H
#define SHIM_UNISTD_H
typedef int off_t;
typedef int ssize_t;
#endif
