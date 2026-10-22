"""Build a pinned static RV32 BusyBox initramfs in WSL ext4; never accesses hardware."""
import gzip
import hashlib
import json
import shlex
import struct
import urllib.request
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.builders.linux import ROOT, wsl_path
from scripts.modular.storage import source_file_hash, file_hash, SOURCE_HASH_FORMAT

SOURCES = {
    'musl-1.2.5.tar.gz': ('https://musl.libc.org/releases/', 'a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4'),
    'busybox-1.37.0.tar.bz2': ('https://busybox.net/downloads/', '3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4'),
}


def validate_archive(data, busybox):
    """Check the executable actually inside all concatenated newc archives."""
    entries = {}
    pos = 0
    while pos < len(data):
        while pos < len(data) and data[pos] == 0:
            pos += 1
        if pos == len(data):
            break
        if data[pos:pos+6] != b'070701' or pos+110 > len(data):
            raise ValueError('Invalid newc header')
        fields = [int(data[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        length, name_length = fields[6], fields[11]
        if name_length < 1 or pos+110+name_length > len(data):
            raise ValueError('Truncated newc name')
        name = data[pos+110:pos+110+name_length-1].decode()
        pos = (pos+110+name_length+3) & ~3
        if pos+length > len(data):
            raise ValueError('Truncated newc contents')
        if name != 'TRAILER!!!':
            entries[name.removeprefix('./').lstrip('/')] = (fields[1], data[pos:pos+length])
        pos = (pos+length+3) & ~3
    for name in ('init','bin/busybox','bin/sh','sbin/init','etc/inittab','dev/console'):
        if name not in entries:
            raise ValueError('Initrd missing '+name)
    payload = entries['bin/busybox'][1]
    if payload != busybox:
        raise ValueError('Archived BusyBox differs from the qualified binary')
    if len(payload) < 52 or payload[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H',payload,18)[0] != 243 or struct.unpack_from('<I',payload,36)[0] != 0:
        raise ValueError('BusyBox must be ELF32 RISC-V with soft-float and no compressed instructions')
    if entries['init'][0] & 0o111 == 0:
        raise ValueError('/init must be executable')
    return len(entries)


def build(args, run=None):
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.rootfs_only:
        previous = json.loads((out/'validation.json').read_text())
        inputs = {k.replace('\\','/'):v for k,v in previous['inputs'].items()}
        config = ROOT/'firmware/linux/busybox/busybox.config'
        input_hash = source_file_hash(config) if previous.get('source_hash_format') == SOURCE_HASH_FORMAT else file_hash(config)
        if inputs.get('firmware/linux/busybox/busybox.config') != input_hash:
            raise ValueError('BusyBox config changed; a full rebuild is required')
    cache = ROOT/'build/cache/upstream'
    cache.mkdir(parents=True,exist_ok=True)
    for name, (url, digest) in SOURCES.items():
        path = cache/name
        if not path.exists():
            with urllib.request.urlopen(url+name, timeout=60) as response:
                path.write_bytes(response.read())
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source checksum mismatch: '+name)
    script = '''#!/bin/bash
set -euo pipefail
BASE="$HOME/riscv-mini-linux/busybox-build"
mkdir -p "$BASE"
cd "$BASE"
'''
    script += 'OUT='+shlex.quote(wsl_path(out))+'\nPORT='+shlex.quote(wsl_path(ROOT/'firmware/linux/busybox'))+'\n'
    for name, (_, digest) in SOURCES.items():
        script += 'cp '+shlex.quote(wsl_path(cache/name))+' .\n'
        script += f'echo "{digest}  {name}" | sha256sum -c -\n'
    script += '''
if [ "${1:-}" != "--package-only" ]; then
test -d musl-1.2.5 || tar xf musl-1.2.5.tar.gz
test -d busybox-1.37.0 || tar xf busybox-1.37.0.tar.bz2
LIBGCC=$(riscv64-elf-gcc -march=rv32im -mabi=ilp32 -print-libgcc-file-name)
cat > "$BASE/gcc-ima" <<'EOF'
#!/bin/sh
exec riscv64-elf-gcc -march=rv32ima -mabi=ilp32 "$@"
EOF
chmod +x "$BASE/gcc-ima"
cd musl-1.2.5
CC="$BASE/gcc-ima" AR=riscv64-elf-ar RANLIB=riscv64-elf-ranlib LIBCC="$LIBGCC" ./configure --target=riscv32 --prefix="$BASE/musl" --disable-shared --enable-wrapper=gcc
make -j8
make install
# Bare-metal GCC provides the ordinary RV32 crtbegin/crtend, not shared variants.
# This image is always static and does not need the shared-library variants.
sed -i 's/crtbeginS.o/crtbegin.o/; s/crtendS.o/crtend.o/' "$BASE/musl/lib/musl-gcc.specs"
# Override bare-metal newlib/libgloss defaults with the Linux musl libc.
# Remove empty ESP toolchain specs before appending: GCC 16 rejects an empty
# spec followed by another named spec (musl leaves these at EOF).
sed -i '/^\\*esp_link:/,$d' "$BASE/musl/lib/musl-gcc.specs"
printf '*lib:\\n-lc\\n\\n' >> "$BASE/musl/lib/musl-gcc.specs"
cat > "$BASE/gcc-musl" <<EOF
#!/bin/sh
exec riscv64-elf-gcc -march=rv32im -mabi=ilp32 -D__linux__=1 -D__unix__=1 -Wl,-m,elf32lriscv -specs "$BASE/musl/lib/musl-gcc.specs" "\$@"
EOF
chmod +x "$BASE/gcc-musl"
cd "$BASE/busybox-1.37.0"
make clean
cp "$PORT/busybox.config" .config
make oldconfig </dev/null
make -j8 ARCH=riscv CROSS_COMPILE=riscv64-elf- LD="riscv64-elf-ld -m elf32lriscv" CC="$BASE/gcc-musl" EXTRA_CFLAGS="-isystem $HOME/riscv-mini-linux/obj-minimal/uapi/include"
fi
cd "$BASE/busybox-1.37.0"
if [ "${1:-}" = "--package-only" ]; then cmp "$OUT/busybox" busybox; fi
riscv64-elf-readelf -h -A -l busybox > "$OUT/busybox-elf.txt"
! grep -q INTERP "$OUT/busybox-elf.txt"
grep -q ELF32 "$OUT/busybox-elf.txt"
grep -Eq 'Flags: +0x0$' "$OUT/busybox-elf.txt"
cp busybox .config "$OUT/"
rm -f "$BASE/rootfs.cpio"
mkdir -p "$BASE/rootfs"
find "$BASE/rootfs" -mindepth 1 -delete
make ARCH=riscv CROSS_COMPILE=riscv64-elf- LD="riscv64-elf-ld -m elf32lriscv" CC="$BASE/gcc-musl" EXTRA_CFLAGS="-isystem $HOME/riscv-mini-linux/obj-minimal/uapi/include" CONFIG_PREFIX="$BASE/rootfs" install
cmp "$OUT/busybox" "$BASE/rootfs/bin/busybox"
cp -r "$PORT/rootfs/." "$BASE/rootfs/"
mkdir -p "$BASE/rootfs/"{dev,proc,sys,tmp,run,root,mnt,dev/pts}
find "$BASE/rootfs/etc" -type f -exec chmod 644 {} +
chmod 755 "$BASE/rootfs/init"
chmod 1777 "$BASE/rootfs/tmp"
chmod 700 "$BASE/rootfs/root"
cd "$BASE/rootfs"
find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --owner=0:0 --reproducible > "$OUT/initrd.cpio"
# Append a device archive without requiring root or host mknod privileges.
printf 'nod /dev/console 0600 0 0 c 5 1\\n' > "$BASE/devices.list"
"$HOME/riscv-mini-linux/obj-minimal/usr/gen_init_cpio" "$BASE/devices.list" >> "$OUT/initrd.cpio"
riscv64-elf-gcc --version > "$OUT/toolchain.txt"
'''
    (out/'build.sh').write_text(script, encoding='utf-8', newline='\n')
    if not args.record:
        print(out/'build.sh')
        return
    entry_count = validate_archive((out/'initrd.cpio').read_bytes(), (out/'busybox').read_bytes())
    with (out/'initrd.cpio.gz').open('wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
            compressed.write((out/'initrd.cpio').read_bytes())
    metadata = dict(busybox_version='1.37.0', musl_version='1.2.5', abi='ilp32',
                    isa='RV32IM BusyBox + RV32IMA musl; no C/FPU', static=True, board_verified=False, archive_entries=entry_count,
                    sources={n:d for n,(_,d) in SOURCES.items()},
                    source_hash_format=SOURCE_HASH_FORMAT,
                    inputs={str(f.relative_to(ROOT)).replace('\\','/'):source_file_hash(f)
                            for f in list((ROOT/'firmware/linux/busybox').rglob('*'))+[Path(__file__),ROOT/'scripts/mini.py'] if f.is_file()},
                    artifacts={n:dict(bytes=(out/n).stat().st_size,sha256=hashlib.sha256((out/n).read_bytes()).hexdigest())
                               for n in ('busybox','initrd.cpio','initrd.cpio.gz','.config')})
    if args.board_evidence:
        evidence = json.loads(args.board_evidence.read_text())
        inputs = next((s for s in evidence['steps'] if s['name']=='linux_inputs'), {})
        acceptance = next((s for s in evidence['steps'] if s['name']=='linux_busybox'), {})
        if evidence.get('passed') is not True or acceptance.get('passed') is not True or inputs.get('initrd_sha256') != metadata['artifacts']['initrd.cpio.gz']['sha256']:
            raise ValueError('Board receipt does not qualify this initrd')
        metadata.update(board_verified=True, board_evidence=str(args.board_evidence.resolve()),
                        board_evidence_sha256=hashlib.sha256(args.board_evidence.read_bytes()).hexdigest(),
                        kernel_sha256=inputs['kernel_sha256'], os_image_sha256=inputs['os_image_sha256'],
                        selected_build=evidence['selected_build'])
        lcd = next((s for s in evidence['steps'] if s['name']=='linux_lcd_shell'), {})
        metadata['board_lcd_shell_verified'] = lcd.get('passed') is True
        metadata['board_pty_verified'] = lcd.get('pty') is True and lcd.get('passed') is True
    (out/'validation.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata['artifacts'],indent=2))
