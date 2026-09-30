# FatFs provenance

FatFs R0.16, ChaN / ELM, upstream archive:
https://elm-chan.org/fsw/ff/arc/ff16.zip

Archive SHA256:
99f7dc1f7e095356e4a9e3dbe29959090d8b948afe2bbc5441e52fdf4b85449e

ff.c, ff.h, diskio.h and ffunicode.c are unmodified upstream sources.
LICENSE.txt and source copyright notices are retained.
ffconf.h is the project configuration: FAT/exFAT, static long filename buffer,
CP437, one volume, 512-byte sectors, shared sector buffer, no RTC, no formatting,
no trim, no threads. The default fixed date is 2025-01-01; timestamps do not
represent real time. SD writes are performed only by the sdtest monitor command,
using FA_CREATE_NEW; no format/erase/raw-write monitor operation exists.
