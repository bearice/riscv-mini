#include "ae350_bios.h"
#include "ae350_sd.h"
#include "ethernet_tftp.h"
#include "ff.h"
#include <string.h>

extern int payload_main(const struct bios_info *info);

static uint8_t server_ip[4] = {169, 254, 25, 153};
static uint8_t board_ip[4] = {169, 254, 20, 20};

static void decimal(unsigned value) {
    char s[12]; unsigned n = 0;
    do { s[n++] = (char)('0' + value % 10); value /= 10; } while (value);
    while (n) bios_putc(s[--n]);
}

static void *my_memmove(void *dst, const void *src, unsigned n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    if (d < s) {
        for (unsigned i = 0; i < n; i++) d[i] = s[i];
    } else if (d > s) {
        for (unsigned i = n; i > 0; i--) d[i - 1] = s[i - 1];
    }
    return dst;
}

static void hex32(uint32_t val) {
    static const char h[] = "0123456789abcdef";
    for (int i = 28; i >= 0; i -= 4) bios_putc(h[(val >> i) & 15]);
}

static int parse_ip(const char *s, uint8_t out[4]) {
    for (int i = 0; i < 4; i++) {
        unsigned val = 0;
        if (*s < '0' || *s > '9') return 0;
        while (*s >= '0' && *s <= '9') {
            val = val * 10 + (*s++ - '0');
            if (val > 255) return 0;
        }
        out[i] = (uint8_t)val;
        if (i < 3) {
            if (*s++ != '.') return 0;
        }
    }
    return 1;
}

static const char *parse_hex(const char *s, uint32_t *out) {
    while (*s == ' ' || *s == '\t') s++;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    uint32_t val = 0; unsigned digits = 0;
    while (*s) {
        char c = *s; uint32_t d;
        if (c >= '0' && c <= '9') d = (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') d = (uint32_t)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') d = (uint32_t)(c - 'A' + 10);
        else break;
        val = (val << 4) | d;
        digits++; s++;
    }
    if (!digits) return 0;
    *out = val;
    return s;
}

static void boot_linux_smode(uintptr_t kernel_entry, uintptr_t dtb_addr) {
    bios_puts("BOOTING S-MODE LINUX kernel="); hex32(kernel_entry);
    bios_puts(" dtb="); hex32(dtb_addr); bios_puts("\r\n");

    /* Set mstatus.MPP = 1 (Supervisor mode), disable interrupts */
    uint32_t mstatus;
    __asm__ volatile("csrr %0, mstatus" : "=r"(mstatus));
    mstatus = (mstatus & ~0x1800u) | 0x0800u;
    __asm__ volatile("csrw mstatus, %0" :: "r"(mstatus));

    /* Delegate standard interrupts and exceptions to S-mode */
    __asm__ volatile("csrw mideleg, %0" :: "r"(0xffffu));
    __asm__ volatile("csrw medeleg, %0" :: "r"(0xffffu));

    /* PMP: grant S/U mode full access to memory */
    __asm__ volatile("csrw pmpaddr0, %0" :: "r"(0xffffffffu));
    __asm__ volatile("csrw pmpcfg0, %0" :: "r"(0x0fu)); /* TOR, R, W, X */

    /* Ensure satp = 0 (MMU off) */
    __asm__ volatile("csrw satp, zero");

    /* mepc = kernel entry point */
    __asm__ volatile("csrw mepc, %0" :: "r"(kernel_entry));

    /* a0 = hartid (0), a1 = dtb_addr */
    register uint32_t a0 asm("a0") = 0;
    register uint32_t a1 asm("a1") = dtb_addr;

    __asm__ volatile("fence.i; mret" :: "r"(a0), "r"(a1));
}

void bios_command(char *line) {
    if (!strcmp(line, "help")) {
        bios_puts("help, status, set server IP, set ip IP, tftp ADDR FILE, crc32 ADDR LEN,\r\n"
                  "boot net [FILE], fetch FILE, fetchraw FILE, go ADDR, boot linux KERNEL DTB,\r\n"
                  "ls [DIR], cat FILE, size FILE, mkdir DIR, rm FILE, mv FROM TO, unmount, basic\r\n");
        return;
    }
    if (!strcmp(line, "basic")) {
        payload_main(&ae350_bios_info);
        bios_puts("APPLICATION RETURNED\r\n");
        return;
    }
    if (!strcmp(line, "status")) {
        struct ae350_sd_info sd;
        ae350_sd_info(&sd);
        bios_puts("AE350 CPU800/sys50 SD="); decimal(sd.hz);
        bios_puts(" Hz IP=");
        for (int i = 0; i < 4; i++) { if (i) bios_putc('.'); decimal(board_ip[i]); }
        bios_puts(" SERVER=");
        for (int i = 0; i < 4; i++) { if (i) bios_putc('.'); decimal(server_ip[i]); }
        bios_puts(" BIOS ABI=1\r\n");
        return;
    }
    if (!strncmp(line, "set server ", 11)) {
        if (parse_ip(line + 11, server_ip)) {
            bios_puts("SERVER IP SET OK\r\n");
        } else {
            bios_puts("ERR IP\r\n");
        }
        return;
    }
    if (!strncmp(line, "set ip ", 7)) {
        if (parse_ip(line + 7, board_ip)) {
            ae350_net_init(0, board_ip);
            bios_puts("BOARD IP SET OK\r\n");
        } else {
            bios_puts("ERR IP\r\n");
        }
        return;
    }
    if (!strncmp(line, "tftp ", 5)) {
        uint32_t addr = 0;
        const char *p = parse_hex(line + 5, &addr);
        if (!p) { bios_puts("ERR ADDR\r\n"); return; }
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) { bios_puts("ERR FILE\r\n"); return; }
        unsigned len = 0;
        int rc = ae350_tftp_get(server_ip, p, (void *)(uintptr_t)addr, 64 * 1024 * 1024, &len);
        if (rc == 0) {
            bios_puts("Bytes transferred = "); decimal(len); bios_puts("\r\nTFTP OK\r\n");
        } else {
            bios_puts("TFTP FAIL\r\n");
        }
        return;
    }
    if (!strncmp(line, "crc32 ", 6)) {
        uint32_t addr = 0, len = 0;
        const char *p = parse_hex(line + 6, &addr);
        if (p) p = parse_hex(p, &len);
        if (!p) { bios_puts("ERR ARGS\r\n"); return; }
        uint32_t c = ae350_crc32((const void *)(uintptr_t)addr, len);
        bios_puts("==> "); hex32(c); bios_puts("\r\n");
        return;
    }
    if (!strncmp(line, "boot net", 8)) {
        const char *file = "BOOT.RPB";
        if (line[8] == ' ' && line[9]) file = line + 9;
        uint32_t staging = 0x02000000u;
        unsigned len = 0;
        int rc = ae350_tftp_get(server_ip, file, (void *)(uintptr_t)staging, 16 * 1024 * 1024, &len);
        if (rc != 0 || len < 32) {
            bios_puts("BOOT NET FAIL\r\n");
            return;
        }
        const uint32_t *hdr = (const uint32_t *)(uintptr_t)staging;
        uint32_t entry = staging;
        /* Check RPB header magic: 0x31425052 ('RPB1') or 0x3142534f ('OSB1') */
        if (hdr[0] == 0x31425052u || hdr[0] == 0x3142534fu) {
            entry = hdr[5];
            uint32_t payload_len = hdr[3];
            my_memmove((void *)(uintptr_t)hdr[2], (const void *)(uintptr_t)(staging + 32), payload_len);
        }
        bios_puts("RUNNING NET PAYLOAD entry="); hex32(entry); bios_puts("\r\n");
        __asm__ volatile("fence.i" ::: "memory");
        void (*func)(void) = (void (*)(void))(uintptr_t)entry;
        func();
        bios_puts("PAYLOAD RETURNED\r\n");
        return;
    }
    if (!strncmp(line, "fetch ", 6) || !strncmp(line, "fetchraw ", 9)) {
        const char *file = line[5] == ' ' ? line + 6 : line + 9;
        uint32_t staging = 0x02000000u;
        unsigned len = 0;
        int rc = ae350_tftp_get(server_ip, file, (void *)(uintptr_t)staging, 16 * 1024 * 1024, &len);
        if (rc != 0 || !len) { bios_puts("FETCH FAIL\r\n"); return; }
        if (bios_mount()) { bios_puts("SD unavailable\r\n"); return; }
        int h = bios_fs_open(file, BIOS_FS_O_WRITE | BIOS_FS_O_CREATE);
        if (h < 0) { bios_puts("SD CREATE FAIL\r\n"); return; }
        int written = bios_fs_write(h, (const void *)(uintptr_t)staging, len);
        bios_fs_close(h);
        if (written == (int)len) {
            bios_puts("FETCH SAVED "); bios_puts(file); bios_puts("\r\n");
        } else {
            bios_puts("FETCH SD WRITE FAIL\r\n");
        }
        return;
    }
    if (!strncmp(line, "go ", 3)) {
        uint32_t addr = 0, arg1 = 0;
        const char *p = parse_hex(line + 3, &addr);
        if (!p) { bios_puts("ERR ADDR\r\n"); return; }
        if (*p) parse_hex(p, &arg1);
        bios_puts("JUMPING 0x"); hex32(addr);
        if (arg1) { bios_puts(" a1=0x"); hex32(arg1); }
        bios_puts("\r\n");
        __asm__ volatile("fence.i" ::: "memory");
        void (*func)(uint32_t, uint32_t) = (void (*)(uint32_t, uint32_t))(uintptr_t)addr;
        func(0, arg1);
        bios_puts("RETURNED\r\n");
        return;
    }
    if (!strncmp(line, "boot linux ", 11)) {
        uint32_t kaddr = 0, dtb = 0;
        const char *p = parse_hex(line + 11, &kaddr);
        if (p) p = parse_hex(p, &dtb);
        if (!p) { bios_puts("ERR ARGS\r\n"); return; }
        boot_linux_smode(kaddr, dtb);
        return;
    }
    if (!strcmp(line, "unmount")) {
        bios_puts(bios_unmount() ? "UNMOUNT FAIL\r\n" : "UNMOUNT OK\r\n");
        return;
    }
    if (bios_mount()) { bios_puts("SD unavailable\r\n"); return; }
    if (!strcmp(line, "ls") || !strncmp(line, "ls ", 3)) {
        DIR dir; FILINFO entry;
        FRESULT r = f_opendir(&dir, line[2] ? line + 3 : "/");
        if (r == FR_OK) {
            for (;;) {
                r = f_readdir(&dir, &entry);
                if (r != FR_OK || !entry.fname[0]) break;
                bios_puts(entry.fname);
                if (entry.fattrib & AM_DIR) bios_putc('/');
                bios_puts("\r\n");
                bios_poll();
            }
            FRESULT end = f_closedir(&dir);
            if (!r) r = end;
        }
        bios_puts(r == FR_OK ? "LS OK\r\n" : "LS FAIL\r\n");
        return;
    }
    if (!strncmp(line, "cat ", 4)) {
        int h = bios_fs_open(line + 4, BIOS_FS_O_READ);
        char data[256]; int n = -1;
        if (h >= 0) {
            unsigned shown = 0;
            while (shown < 4096 && (n = bios_fs_read(h, data, sizeof(data))) > 0) {
                for (int i = 0; i < n; i++) bios_putc(data[i]);
                shown += (unsigned)n;
                bios_poll();
            }
            if (bios_fs_close(h)) n = -1;
        }
        bios_puts(n < 0 ? "\r\nCAT FAIL\r\n" : "\r\nCAT OK\r\n");
        return;
    }
    if (!strncmp(line, "size ", 5)) {
        int n = bios_file_size(line + 5);
        if (n < 0) bios_puts("SIZE FAIL");
        else { bios_puts("SIZE "); decimal((unsigned)n); }
        bios_puts("\r\n");
        return;
    }
    FRESULT r = FR_INVALID_PARAMETER;
    if (!strncmp(line, "mkdir ", 6)) r = f_mkdir(line + 6);
    else if (!strncmp(line, "rm ", 3)) r = f_unlink(line + 3);
    else if (!strncmp(line, "mv ", 3)) {
        char *to = strchr(line + 3, ' ');
        if (to) { *to++ = 0; r = f_rename(line + 3, to); }
    } else {
        bios_puts("Unknown command\r\n");
        return;
    }
    bios_puts(r == FR_OK ? "FILE OK\r\n" : "FILE FAIL\r\n");
}

void main(void) {
    bios_runtime_init();
    ae350_net_init(0, board_ip);
    bios_puts("AE350 BIOS READY\r\n");
    bios_editor();
}
