"""Build the AE350 TinyCore-style RV32IMFD (no-A) Xfbdev + OpenGL/GLX rootfs."""
import gzip
import hashlib
import json
import shlex
import shutil
import subprocess
import urllib.request
from pathlib import Path

from scripts.modular.storage import digest, file_hash, source_file_hash, SOURCE_HASH_FORMAT
from scripts.modular.workspace import location, working
from scripts.modular.machines.mega138k_ae350.upstream import archive

SOURCES = {
    'musl-1.2.5.tar.gz': (
        'https://musl.libc.org/releases/musl-1.2.5.tar.gz',
        'a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4',
    ),
    'busybox-1.37.0.tar.bz2': (
        'https://busybox.net/downloads/busybox-1.37.0.tar.bz2',
        '3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4',
    ),
    'zlib-1.3.1.tar.xz': (
        'https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.xz',
        '38ef96b8dfe510d42707d9c781877914792541133e1870841463bfa73f883e32',
    ),
    'xorgproto-2024.1.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/proto/xorgproto-2024.1.tar.xz',
        '372225fd40815b8423547f5d890c5debc72e88b91088fbfb13158c20495ccb59',
    ),
    'xtrans-1.5.0.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/xtrans-1.5.0.tar.xz',
        '1ba4b703696bfddbf40bacf25bce4e3efb2a0088878f017a50e9884b0c8fb1bd',
    ),
    'libXau-1.0.11.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libXau-1.0.11.tar.xz',
        'f3fa3282f5570c3f6bd620244438dbfbdd580fc80f02f549587a0f8ab329bbeb',
    ),
    'libXdmcp-1.1.5.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libXdmcp-1.1.5.tar.xz',
        'd8a5222828c3adab70adf69a5583f1d32eb5ece04304f7f8392b6a353aa2228c',
    ),
    'xcb-proto-1.17.0.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/proto/xcb-proto-1.17.0.tar.xz',
        '2c1bacd2110f4799f74de6ebb714b94cf6f80fb112316b1219480fd22562148c',
    ),
    'libxcb-1.17.0.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libxcb-1.17.0.tar.xz',
        '599ebf9996710fea71622e6e184f3a8ad5b43d0e5fa8c4e407123c88a59a6d55',
    ),
    'libX11-1.8.10.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libX11-1.8.10.tar.xz',
        '2b3b3dad9347db41dca56beb7db5878f283bde1142f04d9f8e478af435dfdc53',
    ),
    'libXext-1.3.6.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libXext-1.3.6.tar.xz',
        'edb59fa23994e405fdc5b400afdf5820ae6160b94f35e3dc3da4457a16e89753',
    ),
    'libfontenc-1.1.8.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libfontenc-1.1.8.tar.xz',
        '7b02c3d405236e0d86806b1de9d6868fe60c313628b38350b032914aa4fd14c6',
    ),
    'libXfont2-2.0.7.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libXfont2-2.0.7.tar.xz',
        '8b7b82fdeba48769b69433e8e3fbb984a5f6bf368b0d5f47abeec49de3e58efb',
    ),
    'pixman-0.42.2.tar.gz': (
        'https://www.cairographics.org/releases/pixman-0.42.2.tar.gz',
        'ea1480efada2fd948bc75366f7c349e1c96d3297d09a3fe62626e38e234a625e',
    ),
    'libxkbfile-1.1.3.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/lib/libxkbfile-1.1.3.tar.xz',
        'a9b63eea997abb9ee6a8b4fbb515831c841f471af845a09de443b28003874bec',
    ),
    'libmd-1.1.0.tar.xz': (
        'https://archive.hadrons.org/software/libmd/libmd-1.1.0.tar.xz',
        '1bd6aa42275313af3141c7cf2e5b964e8b1fd488025caf2f971f43b00776b332',
    ),
    'xkbcomp-1.4.7.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/app/xkbcomp-1.4.7.tar.xz',
        '0a288114e5f44e31987042c79aecff1ffad53a8154b8ec971c24a69a80f81f77',
    ),
    'xkeyboard-config-2.35.1.tar.xz': (
        'https://xorg.freedesktop.org/archive/individual/data/xkeyboard-config/xkeyboard-config-2.35.1.tar.xz',
        '18ce50ff0c74ae6093062bce1aeab3d363913ea35162fe271f8a0ce399de85cc',
    ),
    'xorg-server-1.19.7.tar.bz2': (
        'https://xorg.freedesktop.org/archive/individual/xserver/xorg-server-1.19.7.tar.bz2',
        '7112f7128a4f5b06ceb8bba1bdc5e5c9e0fae682a42d35218bc12ba693f4c80c',
    ),
}

MAX_INITRD_GZ_BYTES = int(5.2 * 1024 * 1024)

NIX_TOOLS = (
    'nixpkgs#pkg-config',
    'nixpkgs#gnum4',
    'nixpkgs#autoconf',
    'nixpkgs#automake',
    'nixpkgs#libtool',
    'nixpkgs#bzip2',
    'nixpkgs#xz',
    'nixpkgs#patch',
    'nixpkgs#gawk',
    'nixpkgs#gcc',
    'nixpkgs#findutils',
    'nixpkgs#gnused',
    'nixpkgs#gnugrep',
    'nixpkgs#meson',
    'nixpkgs#ninja',
    'nixpkgs#libxslt',
    'nixpkgs#perl',
    'nixpkgs#gettext',
    'nixpkgs#file',
    'nixpkgs#gzip',
    'nixpkgs#cpio',
    'nixpkgs#gnutar',
    'nixpkgs#coreutils',
)


def _ensure_sources(store):
    cache = store.build / 'cache/upstream'
    cache.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in SOURCES.items():
        path = cache / name
        if not path.exists():
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
            with urllib.request.urlopen(req, timeout=120) as resp:
                path.write_bytes(resp.read())
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Source checksum mismatch for {name}: got {actual}, expected {expected}')
    return cache


def _recipe(store):
    root = store.root
    files = [
        *sorted((root / 'firmware/linux/x11-gl').rglob('*')),
        *sorted((root / 'firmware/vendor/tinygl').rglob('*')),
        root / 'firmware/linux/ae350_gfx3d_uapi.h',
        root / 'firmware/linux/ae350/patches/40-up-cmpxchg-syscall.patch',
        Path(__file__).resolve(),
    ]
    return {
        **{
            str(p.relative_to(root)).replace('\\', '/'): source_file_hash(p)
            for p in files if p.is_file()
        },
        'sources': {k: v[1] for k, v in SOURCES.items()},
    }


def _build_script(store, work, out, cache, linux_src):
    port = store.root / 'firmware/linux/x11-gl'
    tinygl = store.root / 'firmware/vendor/tinygl'
    uapi_h = store.root / 'firmware/linux/ae350_gfx3d_uapi.h'

    script = f'''#!/usr/bin/env bash
set -euo pipefail

ROOT={shlex.quote(str(store.root))}
WORK={shlex.quote(str(work))}
OUT={shlex.quote(str(out))}
CACHE={shlex.quote(str(cache))}
LINUX_SRC={shlex.quote(str(linux_src))}
PORT={shlex.quote(str(port))}
TINYGL={shlex.quote(str(tinygl))}
UAPI_H={shlex.quote(str(uapi_h))}

SRC="$WORK/src"
SYSROOT="$WORK/sysroot"
ROOTFS="$WORK/rootfs"
STAMPS="$WORK/stamps"
BIN="$WORK/bin"

mkdir -p "$WORK" "$OUT" "$SRC" "$SYSROOT/include" "$SYSROOT/lib/pkgconfig" "$STAMPS" "$BIN"

export PKG_CONFIG_PATH="$SYSROOT/lib/pkgconfig:$SYSROOT/share/pkgconfig"
export PKG_CONFIG_LIBDIR="$SYSROOT/lib/pkgconfig:$SYSROOT/share/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR=""
export MAKEFLAGS="-j$(nproc)"

# Symlink bare-metal binutils under target triple for autoconf --host=riscv32-unknown-linux-gnu
for tool in ar ranlib strip nm objdump readelf as ld; do
    ln -sf "$(command -v riscv-none-elf-$tool)" "$BIN/riscv32-unknown-linux-gnu-$tool"
done

cat > "$BIN/riscv32-pkg-config" <<EOF
#!/usr/bin/env bash
export PKG_CONFIG_PATH="$SYSROOT/lib/pkgconfig:$SYSROOT/share/pkgconfig"
export PKG_CONFIG_LIBDIR="$SYSROOT/lib/pkgconfig:$SYSROOT/share/pkgconfig"
unset PKG_CONFIG_SYSROOT_DIR
exec "$(command -v pkg-config)" --static "\\$@"
EOF
chmod +x "$BIN/riscv32-pkg-config"
export PKG_CONFIG="$BIN/riscv32-pkg-config"

# 0. Fresh Linux UAPI headers (includes patched __NR_riscv_up_cmpxchg = 257) + gen_init_cpio
UAPI_INC="$WORK/linux-uapi/include"
GEN_CPIO="$WORK/gen_init_cpio"
if [ ! -f "$STAMPS/linux-uapi.done" ] || ! grep -q riscv_up_cmpxchg "$UAPI_INC/asm/unistd_32.h" 2>/dev/null; then
    mkdir -p "$WORK/uapi-obj" "$WORK/linux-uapi"
    make -C "$LINUX_SRC" ARCH=riscv O="$WORK/uapi-obj" INSTALL_HDR_PATH="$WORK/linux-uapi" headers_install
    gcc -O2 "$LINUX_SRC/usr/gen_init_cpio.c" -o "$GEN_CPIO"
    touch "$STAMPS/linux-uapi.done"
fi
if [ ! -x "$GEN_CPIO" ]; then
    gcc -O2 "$LINUX_SRC/usr/gen_init_cpio.c" -o "$GEN_CPIO"
fi
cp "$UAPI_H" "$SYSROOT/include/ae350_gfx3d_uapi.h"

# Helper to extract once
unpack() {{
    local archive="$1"
    local dir="$2"
    if [ ! -d "$SRC/$dir" ]; then
        tar xf "$CACHE/$archive" -C "$SRC"
    fi
}}

# 1. Static musl 1.2.5 patched with syscall 257 UP CAS + libae350_atomic.a (ZERO A-extension!)
LIBGCC=$(riscv-none-elf-gcc -march=rv32imfd_zicsr_zifencei -mabi=ilp32d -print-libgcc-file-name)
if [ ! -f "$STAMPS/musl.done" ]; then
    rm -rf "$SRC/musl-1.2.5"
    unpack musl-1.2.5.tar.gz musl-1.2.5
    cat > "$SRC/musl-1.2.5/arch/riscv32/atomic_arch.h" <<'EOF'
#define a_barrier a_barrier
static inline void a_barrier(void)
{{
	__asm__ __volatile__("fence rw,rw" ::: "memory");
}}

#define a_cas a_cas
static inline int a_cas(volatile int *p, int t, int s)
{{
	register int a0 __asm__("a0") = (int)p;
	register int a1 __asm__("a1") = t;
	register int a2 __asm__("a2") = s;
	register int a7 __asm__("a7") = 257;
	__asm__ __volatile__("ecall"
			     : "+r"(a0)
			     : "r"(a1), "r"(a2), "r"(a7)
			     : "memory");
	return a0;
}}
EOF
    cat > "$BIN/gcc-rv32imfd-stage1" <<'EOF'
#!/usr/bin/env bash
exec riscv-none-elf-gcc -march=rv32imfd_zicsr_zifencei -mabi=ilp32d "$@"
EOF
    chmod +x "$BIN/gcc-rv32imfd-stage1"
    pushd "$SRC/musl-1.2.5" >/dev/null
    CC="$BIN/gcc-rv32imfd-stage1" AR=riscv-none-elf-ar RANLIB=riscv-none-elf-ranlib LIBCC="$LIBGCC" \
        ./configure --target=riscv32 --prefix="$SYSROOT" --disable-shared --enable-wrapper=gcc
    make
    make install
    popd >/dev/null
    sed -i 's/crtbeginS\\.o/crtbegin.o/; s/crtendS\\.o/crtend.o/' "$SYSROOT/lib/musl-gcc.specs"
    sed -i '/^\\*esp_link:/,$d' "$SYSROOT/lib/musl-gcc.specs"
    printf '*lib:\\n-lc -lae350_atomic\\n\\n' >> "$SYSROOT/lib/musl-gcc.specs"
    touch "$STAMPS/musl.done"
fi

# Always refresh libae350_atomic.a cleanly if its source changed
cat > "$BIN/riscv32-unknown-linux-gnu-gcc" <<EOF
#!/usr/bin/env bash
exec riscv-none-elf-gcc -march=rv32imfd_zicsr_zifencei -mabi=ilp32d -D__linux__=1 -D__unix__=1 -Wl,-m,elf32lriscv -specs "$SYSROOT/lib/musl-gcc.specs" -isystem "$UAPI_INC" -I"$SYSROOT/include" -L"$SYSROOT/lib" "\\$@"
EOF
chmod +x "$BIN/riscv32-unknown-linux-gnu-gcc"
export PATH="$BIN:$PATH"

riscv-none-elf-gcc -march=rv32imfd_zicsr_zifencei -mabi=ilp32d -O2 -Wall -Wextra -Wno-builtin-declaration-mismatch \
    -D__linux__=1 -D__unix__=1 -nostdinc -isystem "$SYSROOT/include" -isystem "$UAPI_INC" \
    -c "$PORT/libae350_atomic.c" -o "$WORK/libae350_atomic.o"
riscv-none-elf-ar rcs "$SYSROOT/lib/libae350_atomic.a" "$WORK/libae350_atomic.o"

CROSS_CC="$BIN/riscv32-unknown-linux-gnu-gcc"
CROSS_FLAGS="-O2 -D_GNU_SOURCE -Wno-error=incompatible-pointer-types -Wno-error=implicit-function-declaration -Wno-error=int-conversion"
CROSS_HOST="riscv32-unknown-linux-gnu"

build_autotools() {{
    local pkg="$1"
    local archive="$2"
    local dir="$3"
    shift 3
    if [ -f "$STAMPS/$pkg.done" ]; then
        return 0
    fi
    rm -rf "$SRC/$dir"
    unpack "$archive" "$dir"
    pushd "$SRC/$dir" >/dev/null
    CC="$CROSS_CC" AR="$BIN/riscv32-unknown-linux-gnu-ar" RANLIB="$BIN/riscv32-unknown-linux-gnu-ranlib" \
        LD="$BIN/riscv32-unknown-linux-gnu-ld" STRIP="$BIN/riscv32-unknown-linux-gnu-strip" \
        CFLAGS="$CROSS_FLAGS -I$SYSROOT/include" LDFLAGS="-static -L$SYSROOT/lib" \
        CC_FOR_BUILD="gcc" CFLAGS_FOR_BUILD="-O2" CPPFLAGS_FOR_BUILD="" LDFLAGS_FOR_BUILD="" \
        ./configure --host="$CROSS_HOST" --prefix="$SYSROOT" --disable-shared --enable-static "$@"
    make
    make install
    popd >/dev/null
    touch "$STAMPS/$pkg.done"
}}

# 2. zlib 1.3.1
if [ ! -f "$STAMPS/zlib.done" ]; then
    rm -rf "$SRC/zlib-1.3.1"
    unpack zlib-1.3.1.tar.xz zlib-1.3.1
    pushd "$SRC/zlib-1.3.1" >/dev/null
    CHOST="$CROSS_HOST" CC="$CROSS_CC" AR="$BIN/riscv32-unknown-linux-gnu-ar" RANLIB="$BIN/riscv32-unknown-linux-gnu-ranlib" \
        CFLAGS="-O2" ./configure --prefix="$SYSROOT" --static
    make
    make install
    popd >/dev/null
    touch "$STAMPS/zlib.done"
fi

# 3. X11 protocols & libraries (static)
build_autotools xorgproto xorgproto-2024.1.tar.xz xorgproto-2024.1
build_autotools xtrans xtrans-1.5.0.tar.xz xtrans-1.5.0
build_autotools libXau libXau-1.0.11.tar.xz libXau-1.0.11
build_autotools libXdmcp libXdmcp-1.1.5.tar.xz libXdmcp-1.1.5
build_autotools xcb-proto xcb-proto-1.17.0.tar.xz xcb-proto-1.17.0
build_autotools libxcb libxcb-1.17.0.tar.xz libxcb-1.17.0 --disable-build-docs --disable-devel-docs
build_autotools libX11 libX11-1.8.10.tar.xz libX11-1.8.10 --enable-malloc0returnsnull --disable-specs --disable-xf86bigfont --disable-loadable-i18n --disable-ipv6
build_autotools libXext libXext-1.3.6.tar.xz libXext-1.3.6 --enable-malloc0returnsnull --disable-specs
build_autotools libfontenc libfontenc-1.1.8.tar.xz libfontenc-1.1.8
build_autotools libXfont2 libXfont2-2.0.7.tar.xz libXfont2-2.0.7 --disable-freetype --disable-devel-docs --enable-builtins --disable-bzip2
build_autotools pixman pixman-0.42.2.tar.gz pixman-0.42.2 --disable-libpng --disable-gtk --disable-openmp --disable-arm-simd --disable-arm-neon --disable-mips-dspr2 --disable-loongson-mmi --disable-vmx
build_autotools libxkbfile libxkbfile-1.1.3.tar.xz libxkbfile-1.1.3
build_autotools libmd libmd-1.1.0.tar.xz libmd-1.1.0

# 4. xkbcomp (static target binary installed to $SYSROOT/bin/xkbcomp)
build_autotools xkbcomp xkbcomp-1.4.7.tar.xz xkbcomp-1.4.7

# 5. xkeyboard-config (build host-side rules & install minimal XKB subset)
if [ ! -f "$STAMPS/xkbdata.done" ]; then
    rm -rf "$SRC/xkeyboard-config-2.35.1" "$WORK/xkb-stage" "$WORK/xkb-build"
    unpack xkeyboard-config-2.35.1.tar.xz xkeyboard-config-2.35.1
    meson setup "$WORK/xkb-build" "$SRC/xkeyboard-config-2.35.1" --prefix=/usr
    meson compile -C "$WORK/xkb-build"
    DESTDIR="$WORK/xkb-stage" meson install -C "$WORK/xkb-build"
    touch "$STAMPS/xkbdata.done"
fi

# 6. xorg-server 1.19.7 (kdrive Xfbdev static)
if [ ! -f "$STAMPS/xserver.done" ]; then
    rm -rf "$SRC/xorg-server-1.19.7"
    unpack xorg-server-1.19.7.tar.bz2 xorg-server-1.19.7
    pushd "$SRC/xorg-server-1.19.7" >/dev/null
    sed -i 's/__uid_t/uid_t/g; s/__gid_t/gid_t/g' hw/kdrive/linux/linux.c
    CC="$CROSS_CC" AR="$BIN/riscv32-unknown-linux-gnu-ar" RANLIB="$BIN/riscv32-unknown-linux-gnu-ranlib" \
        LD="$BIN/riscv32-unknown-linux-gnu-ld" STRIP="$BIN/riscv32-unknown-linux-gnu-strip" \
        CFLAGS="$CROSS_FLAGS -fcommon -Wno-error=array-bounds -I$SYSROOT/include" \
        LDFLAGS="-static -L$SYSROOT/lib" \
        LIBS="-lXfont2 -lfontenc -lz -lpixman-1 -lxkbfile -lXau -lXdmcp -lmd -lm" \
        ./configure --host="$CROSS_HOST" --prefix=/usr --disable-strict-compilation \
        --enable-kdrive --enable-xfbdev --enable-kdrive-evdev --enable-kdrive-kbd --enable-kdrive-mouse \
        --disable-xorg --disable-dmx --disable-xvfb --disable-xnest --disable-xquartz --disable-xwin \
        --disable-xwayland --disable-xephyr --disable-glx --disable-dri --disable-dri2 --disable-dri3 \
        --disable-glamor --disable-xinerama --disable-record --disable-screensaver --disable-xv \
        --disable-xvmc --disable-dga --disable-xf86vidmode --disable-dpms --disable-config-udev \
        --disable-config-hal --disable-systemd-logind --disable-dbus --disable-libdrm --disable-present \
        --disable-composite --disable-xcsecurity --disable-xace --disable-xselinux --disable-unit-tests \
        --disable-devel-docs --disable-docs --enable-mitshm --with-sha1=libmd \
        --with-xkb-bin-directory=/usr/bin --with-xkb-path=/usr/share/X11/xkb --with-xkb-output=/tmp \
        --with-default-font-path=built-ins --disable-shared --enable-static
    make LD_EXPORT_SYMBOLS_FLAG=""
    cp hw/kdrive/fbdev/Xfbdev "$SYSROOT/bin/Xfbdev"
    popd >/dev/null
    touch "$STAMPS/xserver.done"
fi

# 7. BusyBox 1.37.0 (rebuild if busybox.config changed)
BB_CFG_HASH=$(sha256sum "$PORT/busybox.config" | cut -d' ' -f1)
if [ ! -f "$STAMPS/busybox.done" ] || [ "$(cat "$STAMPS/busybox.done" 2>/dev/null || true)" != "$BB_CFG_HASH" ]; then
    rm -rf "$SRC/busybox-1.37.0"
    unpack busybox-1.37.0.tar.bz2 busybox-1.37.0
    pushd "$SRC/busybox-1.37.0" >/dev/null
    cp "$PORT/busybox.config" .config
    make oldconfig </dev/null
    make ARCH=riscv CROSS_COMPILE="$CROSS_HOST-" CC="$CROSS_CC" \
        LD="riscv-none-elf-ld -m elf32lriscv" EXTRA_CFLAGS="-isystem $UAPI_INC -I$SYSROOT/include"
    cp busybox "$SYSROOT/bin/busybox"
    cp .config "$SYSROOT/bin/busybox.config"
    popd >/dev/null
    printf '%s\\n' "$BB_CFG_HASH" > "$STAMPS/busybox.done"
fi

# 8. Assemble fresh $ROOTFS and compile ae350-gl-apps
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
pushd "$SRC/busybox-1.37.0" >/dev/null
make ARCH=riscv CROSS_COMPILE="$CROSS_HOST-" CC="$CROSS_CC" \
    LD="riscv-none-elf-ld -m elf32lriscv" EXTRA_CFLAGS="-isystem $UAPI_INC -I$SYSROOT/include" \
    CONFIG_PREFIX="$ROOTFS" install
popd >/dev/null

mkdir -p "$ROOTFS"/{{dev,dev/pts,dev/shm,proc,sys,tmp,run,var/run,root,mnt,etc,usr/bin,usr/share/X11/xkb}}
cp -a "$PORT/rootfs/." "$ROOTFS/"
cp "$SYSROOT/bin/Xfbdev" "$ROOTFS/usr/bin/Xfbdev"
ln -sf Xfbdev "$ROOTFS/usr/bin/X"
cp "$SYSROOT/bin/xkbcomp" "$ROOTFS/usr/bin/xkbcomp"

# Minimal XKB rules/keymaps for evdev + US layout
XKB_SRC="$WORK/xkb-stage/usr/share/X11/xkb"
XKB_DST="$ROOTFS/usr/share/X11/xkb"
mkdir -p "$XKB_DST"/{{rules,keycodes,types,compat,geometry,symbols}}
cp "$XKB_SRC/rules/"{{evdev,base}} "$XKB_DST/rules/" 2>/dev/null || cp "$XKB_SRC/rules/evdev" "$XKB_DST/rules/"
cp -a "$XKB_SRC/keycodes/." "$XKB_DST/keycodes/"
cp -a "$XKB_SRC/types/." "$XKB_DST/types/"
cp -a "$XKB_SRC/compat/." "$XKB_DST/compat/"
cp "$XKB_SRC/geometry/pc" "$XKB_DST/geometry/pc"
for sym in pc us inet latin srvr_ctrl keypad altwin group capslock level3 ctrl eurosign compose terminate empty shift; do
    if [ -f "$XKB_SRC/symbols/$sym" ]; then
        cp "$XKB_SRC/symbols/$sym" "$XKB_DST/symbols/$sym"
    fi
done

# Compile TinyGL + GLX multi-call binary (static)
"$CROSS_CC" -O2 -Wall -Wextra -Wno-unused-parameter -static \
    -I"$PORT" -I"$TINYGL/include" -I"$SYSROOT/include" \
    "$PORT/glx_tinygl.c" "$PORT/apps_main.c" "$TINYGL/src/"*.c \
    -o "$ROOTFS/usr/bin/ae350-gl-apps" \
    -L"$SYSROOT/lib" -lXext -lX11 -lxcb -lXau -lXdmcp -lm

for app in ae350_wm xinfo glxgears glxmesh; do
    ln -sf ae350-gl-apps "$ROOTFS/usr/bin/$app"
done

# Strip all ELFs and run HARD ZERO-A AUDIT on every ELF inside $ROOTFS
find "$ROOTFS" -type f -executable -print0 | while IFS= read -r -d '' f; do
    if riscv-none-elf-readelf -h "$f" >/dev/null 2>&1; then
        riscv-none-elf-strip "$f"
        echo "Auditing ELF: $f"
        riscv-none-elf-readelf -h -l "$f" > "$OUT/$(basename "$f").elf.txt"
        ! grep -q INTERP "$OUT/$(basename "$f").elf.txt"
        grep -q ELF32 "$OUT/$(basename "$f").elf.txt"
        grep -q "double-float ABI" "$OUT/$(basename "$f").elf.txt"
        if riscv-none-elf-objdump -d "$f" | grep -E '\\b(lr|sc|amo)[a-z0-9.]*\\b'; then
            echo "FATAL: A-extension instruction found in $f" >&2
            exit 1
        fi
    fi
done

cp "$ROOTFS/bin/busybox" "$OUT/busybox"
cp "$SYSROOT/bin/busybox.config" "$OUT/.config"
cp "$ROOTFS/usr/bin/Xfbdev" "$OUT/Xfbdev"
cp "$ROOTFS/usr/bin/xkbcomp" "$OUT/xkbcomp"
cp "$ROOTFS/usr/bin/ae350-gl-apps" "$OUT/ae350-gl-apps"

find "$ROOTFS/etc" -type f -exec chmod 644 {{}} +
chmod 755 "$ROOTFS/init" "$ROOTFS/usr/bin/startx-ae350" "$ROOTFS/usr/bin/run-x11-gl-tests"
chmod 1777 "$ROOTFS/tmp" "$ROOTFS/dev/shm" "$ROOTFS/tmp/.X11-unix" 2>/dev/null || chmod 1777 "$ROOTFS/tmp" "$ROOTFS/dev/shm"
chmod 700 "$ROOTFS/root"

pushd "$ROOTFS" >/dev/null
find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --owner=0:0 --reproducible > "$OUT/initrd.cpio"
popd >/dev/null
printf 'nod /dev/console 0600 0 0 c 5 1\\n' > "$WORK/devices.list"
"$GEN_CPIO" "$WORK/devices.list" >> "$OUT/initrd.cpio"
riscv-none-elf-gcc --version > "$OUT/toolchain.txt"
echo "RootFS build & zero-A audit PASS"
'''
    return script


def build(store, run, args, dry):
    work = location(store, run, 'mega138k_ae350', 'busybox')
    out = work / 'out'
    linux_src = (store.build / 'vendor/linux-6.18.64').resolve()
    recipe = _recipe(store)
    run.result.update(work_dir=str(work), output_dir=str(out), recipe_sha256=recipe)
    if dry:
        run.finish(plan=True)
        return None
    cache = _ensure_sources(store)
    out.mkdir(parents=True, exist_ok=True)
    script_path = out / 'build.sh'
    script_path.write_text(_build_script(store, work, out, cache, linux_src), encoding='utf-8', newline='\n')
    script_path.chmod(0o755)

    with working(store, run, 'mega138k_ae350', 'busybox', digest(recipe)) as workspace:
        cmd = [
            'nix', 'shell', '--inputs-from', str(store.root),
            *NIX_TOOLS,
            '-c', 'bash', str(script_path),
        ]
        with (run.path / 'build.log').open('w') as log:
            subprocess.run(cmd, cwd=str(store.root), check=True, stdout=log, stderr=log)

        initrd = out / 'initrd.cpio'
        initrd_bytes = initrd.read_bytes()
        if initrd_bytes[:6] != b'070701':
            raise ValueError('Generated initrd.cpio does not start with newc magic 070701')
        initrd_gz = out / 'initrd.cpio.gz'
        with initrd_gz.open('wb') as stream:
            with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
                compressed.write(initrd_bytes)

        gz_size = initrd_gz.stat().st_size
        if gz_size > MAX_INITRD_GZ_BYTES:
            raise ValueError(
                f'initrd.cpio.gz ({gz_size} B) exceeds MAX_INITRD_GZ_BYTES ({MAX_INITRD_GZ_BYTES} B)'
            )

        manifest = {
            'arch': 'rv32imfd_zicsr_zifencei',
            'abi': 'ilp32d',
            'atomics': 'syscall-257-up-cmpxchg',
            'initrd_cpio_bytes': len(initrd_bytes),
            'initrd_cpio_gz_bytes': gz_size,
            'binaries': {
                name: {
                    'bytes': (out / name).stat().st_size,
                    'sha256': file_hash(out / name),
                }
                for name in ('busybox', 'Xfbdev', 'xkbcomp', 'ae350-gl-apps')
            },
            'sources': {name: meta[1] for name, meta in SOURCES.items()},
            'source_hash_format': SOURCE_HASH_FORMAT,
        }
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        run.result.update(manifest)

        artifact, delivery = archive(
            store, run, args, workspace,
            {
                'initrd.cpio': initrd,
                'initrd.cpio.gz': initrd_gz,
                'busybox': out / 'busybox',
                'Xfbdev': out / 'Xfbdev',
                'xkbcomp': out / 'xkbcomp',
                'ae350-gl-apps': out / 'ae350-gl-apps',
                'manifest.json': out / 'manifest.json',
                '.config': out / '.config',
            },
        )
        run.finish(
            artifact=artifact,
            binary=str(delivery / 'initrd.cpio'),
            sha256=file_hash(initrd),
            qualification='compiled-not-board-tested',
        )
        return artifact
