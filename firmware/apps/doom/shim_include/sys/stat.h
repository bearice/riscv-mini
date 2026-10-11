#ifndef SHIM_SYS_STAT_H
#define SHIM_SYS_STAT_H
typedef int mode_t;
int shim_mkdir(const char*);
/* m_misc.c calls mkdir(path, 0755); read-only FS: always fail. */
#define mkdir(p, ...) shim_mkdir(p)
#endif
