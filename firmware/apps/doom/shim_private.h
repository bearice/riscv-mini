/* Internal interface between shim.c and dg_bios.c. */
#ifndef SHIM_PRIVATE_H
#define SHIM_PRIVATE_H
/* exit() longjmps back here when armed; returns 0 on save, nonzero on return. */
int shim_setjmp(void);
int shim_exit_armed(void);
#endif
