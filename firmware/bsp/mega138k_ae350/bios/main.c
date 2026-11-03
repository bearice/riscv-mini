#include "ae350_bios.h"
#include "ae350_sd.h"
#include "ethernet_tftp.h"
#include "ae350_address_map.h"
#include "board_management.h"
#include "ff.h"
#include <string.h>

extern int payload_main(const struct bios_info *info);

/* Loader image (48-byte little-endian header + payload): the format the ROM
 * accepts over UART and from the user-flash boot slot. */
enum { IMAGE_MAGIC = 0x354d5652u, IMAGE_VERSION = 1, IMAGE_HEADER = 48, IMAGE_HEADER_CRC = 44 };
/* `boot sd` loads OpenSBI+U-Boot (with its embedded FDT) from the SD root;
 * U-Boot then loads the Image and the Linux DTB itself. */
static const char sd_payload[] = "fw_payload.bin";
enum { AUTOBOOT_MS = 3000 };

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

/* TFTP targets are the DDR load ranges of the address contract; returns the
 * bytes available from addr to the end of its range, or 0. */
static unsigned load_capacity(uint32_t addr) {
    static const uint32_t ranges[][2] = {
        {AE350_OS_KERNEL_BASE, AE350_OS_KERNEL_BYTES},
        /* os.uboot directly follows os.sbi: a FW_PAYLOAD image spans both. */
        {AE350_OS_SBI_BASE, AE350_OS_SBI_BYTES + AE350_OS_UBOOT_BYTES},
        {AE350_OS_DTB_BASE, AE350_OS_DTB_BYTES},
        {AE350_BIOS_STAGING_BASE, AE350_BIOS_STAGING_BYTES},
        {AE350_BIOS_APP_BASE, AE350_BIOS_APP_LIMIT - AE350_BIOS_APP_BASE},
    };
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; i++)
        if (addr >= ranges[i][0] && addr - ranges[i][0] < ranges[i][1])
            return ranges[i][1] - (addr - ranges[i][0]);
    return 0;
}

/* Memory owned by the BIOS is handed to the next stage: no device may keep
 * writing into it and no BIOS interrupt may fire. */
static int handoff_prepare(void) {
    __asm__ volatile("csrci mstatus, 8\n csrw mie, zero" ::: "memory");
    if (ae350_net_stop()) { bios_puts("ERR NET STOP\r\n"); return -1; }
    return 0;
}

static void boot_opensbi(void) {
    bios_puts("BOOTING OPENSBI "); hex32(AE350_OS_SBI_BASE);
    bios_puts(" dtb="); hex32(AE350_OS_DTB_BASE); bios_puts(" caches off\r\n");
    if (handoff_prepare()) return;
    /* Andes L1D writeback/invalidate all before disabling I/D caches.
     * Linux's initial page-table walker must see DDR writes directly. */
    __asm__ volatile("csrw 0x7cc,%0\n fence\n csrc 0x7ca,%1\n fence.i"
                     :: "r"(6u), "r"(3u) : "memory");
    ((void (*)(uint32_t, uint32_t))(uintptr_t)AE350_OS_SBI_BASE)(0, AE350_OS_DTB_BASE);
}

/* SD file -> contract load range; prints the length and CRC32 it loaded. */
static int load_file(const char *name, uint32_t addr) {
    unsigned capacity = load_capacity(addr);
    if (!capacity) { bios_puts("ERR ADDR not a load range\r\n"); return -1; }
    int size = bios_file_size(name);
    if (size <= 0 || (unsigned)size > capacity) { bios_puts("LOAD FAIL size "); bios_puts(name); bios_puts("\r\n"); return -1; }
    int n = bios_file_read(name, 0, (void *)(uintptr_t)addr, (unsigned)size);
    if (n != size) { bios_puts("LOAD FAIL read "); bios_puts(name); bios_puts("\r\n"); return -1; }
    bios_puts("LOAD OK "); bios_puts(name); bios_puts(" bytes="); decimal((unsigned)n);
    bios_puts(" crc="); hex32(ae350_crc32((const void *)(uintptr_t)addr, (unsigned)n)); bios_puts("\r\n");
    return 0;
}

static int boot_sd(void) {
    if (bios_mount()) { bios_puts("SD unavailable\r\n"); return -1; }
    if (load_file(sd_payload, AE350_OS_SBI_BASE)) { bios_puts("BOOT SD FAIL\r\n"); return -1; }
    boot_opensbi();
    return -1;
}

static const char *image_check(const uint8_t *image, unsigned len) {
    const uint32_t *h = (const uint32_t *)image;
    if (len < IMAGE_HEADER) return "short";
    if (h[0] != IMAGE_MAGIC || h[1] != IMAGE_VERSION || h[2] != IMAGE_HEADER || h[8] || h[9] || h[10] ||
        ae350_crc32(image, IMAGE_HEADER_CRC) != h[11]) return "header";
    if (h[3] != *(volatile uint32_t *)AE350_BOOT_IMAGE_ABI_ADDR) return "ABI";
    if (h[4] < 4u || h[4] > AE350_DDR_CODE_MAX || h[5] != AE350_DDR_CODE || h[6] != AE350_DDR_CODE ||
        len != IMAGE_HEADER + h[4] || len > AE350_USER_FLASH_BOOT_BYTES) return "range";
    if (ae350_crc32(image + IMAGE_HEADER, h[4]) != h[7]) return "CRC";
    return 0;
}

/* CRC32 of a user-flash range, read back through the array read path. */
static int flash_crc(uint32_t offset, unsigned len, uint32_t *crc) {
    static uint8_t block[BOARD_FLASH_SECTOR];
    uint32_t c = 0;
    for (unsigned done = 0; done < len; ) {
        unsigned n = len - done > sizeof block ? sizeof block : len - done;
        if (board_flash_read(offset + done, block, n)) return -1;
        c = ae350_crc32_update(c, block, n);
        done += n;
    }
    *crc = c;
    return 0;
}

static void flash_info(void) {
    uint8_t header[IMAGE_HEADER];
    if (bios_flash_status) { bios_puts("FLASH FAIL init\r\n"); return; }
    bios_puts("FLASH jedec="); hex32(bios_flash.jedec); bios_puts(" bytes="); decimal(bios_flash.bytes);
    bios_puts(" boot-slot="); hex32(AE350_USER_FLASH_BOOT_OFFSET); bios_puts("+"); hex32(AE350_USER_FLASH_BOOT_BYTES);
    bios_puts("\r\n");
    if (board_flash_read(AE350_USER_FLASH_BOOT_OFFSET, header, sizeof header)) { bios_puts("FLASH FAIL read\r\n"); return; }
    const uint32_t *h = (const uint32_t *)header;
    if (h[0] == IMAGE_MAGIC) {
        bios_puts("FLASH BOOT IMAGE abi="); hex32(h[3]); bios_puts(" bytes="); decimal(h[4]);
        bios_puts(" crc="); hex32(h[7]); bios_puts("\r\n");
    } else {
        unsigned blank = 1;
        for (unsigned i = 0; i < sizeof header; i++) blank &= header[i] == 0xffu;
        bios_puts(blank ? "FLASH BOOT BLANK\r\n" : "FLASH BOOT OTHER\r\n");
    }
}

/* Image already in DDR: check it as the ROM will, erase the slot, program the
 * header page last (an interrupted install leaves no valid header), read back. */
static void flash_install(uint32_t addr, unsigned len) {
    const uint8_t *image = (const uint8_t *)(uintptr_t)addr;
    if (bios_flash_status) { bios_puts("FLASH FAIL init\r\n"); return; }
    if (load_capacity(addr) < len) { bios_puts("ERR ADDR not a load range\r\n"); return; }
    const char *bad = image_check(image, len);
    if (bad) { bios_puts("FLASH INSTALL REJECTED "); bios_puts(bad); bios_puts("\r\n"); return; }
    for (uint32_t off = 0; off < len; off += BOARD_FLASH_SECTOR)
        if (board_flash_erase(AE350_USER_FLASH_BOOT_OFFSET + off)) { bios_puts("FLASH FAIL erase\r\n"); return; }
    for (uint32_t off = BOARD_FLASH_PAGE; off < len; off += BOARD_FLASH_PAGE) {
        unsigned n = len - off > BOARD_FLASH_PAGE ? BOARD_FLASH_PAGE : len - off;
        if (board_flash_program(AE350_USER_FLASH_BOOT_OFFSET + off, image + off, n)) { bios_puts("FLASH FAIL program\r\n"); return; }
        if (!(off & (BOARD_FLASH_SECTOR - 1))) bios_poll();
    }
    if (board_flash_program(AE350_USER_FLASH_BOOT_OFFSET, image, len < BOARD_FLASH_PAGE ? len : BOARD_FLASH_PAGE)) {
        bios_puts("FLASH FAIL program\r\n"); return;
    }
    uint32_t crc;
    if (flash_crc(AE350_USER_FLASH_BOOT_OFFSET, len, &crc)) { bios_puts("FLASH FAIL read\r\n"); return; }
    if (crc != ae350_crc32(image, len)) { bios_puts("FLASH FAIL verify\r\n"); return; }
    bios_puts("FLASH INSTALL OK bytes="); decimal(len); bios_puts(" crc="); hex32(crc); bios_puts("\r\n");
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
                  "boot net [FILE], fetch FILE, fetchraw FILE, go ADDR, boot opensbi, boot linux KERNEL DTB,\r\n"
                  "boot sd, load FILE ADDR, flash, flash crc OFFSET LEN, flash install ADDR LEN,\r\n"
                  "ls [DIR], cat FILE, size FILE, mkdir DIR, rm FILE, mv FROM TO, unmount, basic\r\n");
        return;
    }
    if (!strcmp(line, "flash")) { flash_info(); return; }
    if (!strncmp(line, "flash crc ", 10) || !strncmp(line, "flash install ", 14)) {
        int install = line[6] == 'i';
        uint32_t first = 0, len = 0, crc;
        const char *p = parse_hex(line + (install ? 14 : 10), &first);
        if (p) p = parse_hex(p, &len);
        if (!p || !len) { bios_puts("ERR ARGS\r\n"); return; }
        if (install) { flash_install(first, len); return; }
        if (bios_flash_status || first >= bios_flash.bytes || len > bios_flash.bytes - first ||
            flash_crc(first, len, &crc)) { bios_puts("FLASH FAIL read\r\n"); return; }
        bios_puts("==> "); hex32(crc); bios_puts("\r\n");
        return;
    }
    if (!strcmp(line, "boot sd")) { boot_sd(); return; }
    if (!strncmp(line, "load ", 5)) {
        char *addr = strchr(line + 5, ' ');
        uint32_t base = 0;
        if (!addr || !parse_hex(addr, &base)) { bios_puts("ERR ARGS\r\n"); return; }
        *addr = 0;
        if (bios_mount()) { bios_puts("SD unavailable\r\n"); return; }
        load_file(line + 5, base);
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
        unsigned len = 0, capacity = load_capacity(addr);
        if (!capacity) { bios_puts("ERR ADDR not a load range\r\n"); return; }
        int rc = ae350_tftp_get(server_ip, p, (void *)(uintptr_t)addr, capacity, &len);
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
        uint32_t staging = AE350_BIOS_STAGING_BASE;
        unsigned len = 0;
        int rc = ae350_tftp_get(server_ip, file, (void *)(uintptr_t)staging, AE350_BIOS_STAGING_BYTES, &len);
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
        uint32_t staging = AE350_BIOS_STAGING_BASE;
        unsigned len = 0;
        int rc = ae350_tftp_get(server_ip, file, (void *)(uintptr_t)staging, AE350_BIOS_STAGING_BYTES, &len);
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
    if (!strcmp(line, "boot opensbi")) { boot_opensbi(); return; }
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
        if (handoff_prepare()) return;
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

/* source is the ROM's a0 (start.S passes it through): only a BIOS started
 * from user flash boots the SD system unattended. */
void main(uint32_t source) {
    bios_runtime_init();
    ae350_net_init(0, board_ip);
    bios_puts("AE350 BIOS READY\r\n");
    if (source == AE350_BOOT_SOURCE_USER_FLASH) {
        bios_puts("AUTOBOOT SD in 3 s; press any key for the prompt\r\n");
        uint32_t start = bios_time_ms();
        int key = -1;
        while (key < 0 && bios_time_ms() - start < AUTOBOOT_MS) { key = bios_getc(); bios_poll(); }
        if (key < 0) boot_sd();
        else bios_puts("AUTOBOOT STOPPED\r\n");
    }
    bios_editor();
}
