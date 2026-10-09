"""Build pinned single-hart OpenSBI and its S-mode probe using WSL LLVM."""
import argparse,json,shlex,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build import generate_csr
from scripts.bios_image import pack
REV="6ad246a1494807ee91e36ffcf60e4c30bb309d45"
def linux(p):
    p=Path(p).resolve().as_posix();return '/mnt/'+p[0].lower()+p[2:]
COMMANDS=[]

def lcd_node(csr):
    """Describe only the programmable-base LCD ABI used by this driver."""
    base=csr['csr_bases'].get('rgb_lcd')
    if base is None:return ''
    names=('enable','select','base0','base1','state','address_error')
    offsets=[]
    for name in names:
        reg=csr['csr_registers'].get('rgb_lcd_'+name)
        if reg is None or reg['size']!=1:
            raise ValueError('U-Boot LCD requires programmable-base, packed-state CSR ABI')
        offsets.append(reg['addr']-base)
    size=max(offsets)+4
    return (f'lcd@{base:x} {{ compatible="riscv-mini,rgb-lcd"; '
            f'reg=<0x{base:x} 0x{size:x}>; bootph-all; '
            'riscv-mini,csr-offsets=<'+' '.join(hex(o) for o in offsets)+'>; }; ')
ETH_RING_REGISTERS = ('control', 'rx_base', 'tx_base', 'mask', 'rx_consumer',
    'tx_producer', 'rx_producer', 'tx_consumer', 'busy', 'error', 'rx_packets',
    'tx_packets', 'ev_pending', 'ev_enable')


def ethernet_node(csr):
    bases, constants = csr['csr_bases'], csr.get('constants', {})
    dma = bool(constants.get('mini_feature_eth_dma', 0))
    if dma != bool(constants.get('config_eth_ring_dma', 0)):
        raise ValueError('Ethernet DMA requires the exclusive ring ABI')
    if dma:
        base, phy = bases['eth_dma'], bases['ethphy']
        offsets = []
        for name in ETH_RING_REGISTERS:
            register = csr['csr_registers'].get('eth_dma_' + name)
            if register is None or register['size'] != 1:
                raise ValueError('Ethernet ring CSR missing or incompatible: ' + name)
            offsets.append(register['addr'] - base)
        if any(offset < 0 or offset & 3 for offset in offsets):
            raise ValueError('Invalid Ethernet ring CSR offset')
        irq = constants['eth_dma_interrupt']
        return (f'ethernet@{base:x} {{ compatible="riscv-mini,liteeth-ring"; '
            f'reg=<0x{base:x} 0x{max(offsets)+4:x}>,<0x{phy:x} 0x10>; '
            'reg-names="dma","phy"; riscv-mini,csr-offsets=<'
            + ' '.join(hex(offset) for offset in offsets) + '>; '
            f'interrupt-parent=<&irq>; interrupts=<{irq}>; '
            'local-mac-address=[20 12 03 14 05 06]; }; ')
    if 'ethmac' not in bases:
        return ''
    base, phy = bases['ethmac'], bases['ethphy']
    rx, tx = csr['memories']['ethmac_rx'], csr['memories']['ethmac_tx']
    return (f'ethernet@{base:x} {{ compatible="riscv-mini,liteeth"; '
        f'reg=<0x{base:x} 0x40>,<0x{phy:x} 0x10>,'
        f'<0x{rx["base"]:x} 0x{rx["size"]:x}>,<0x{tx["base"]:x} 0x{tx["size"]:x}>; '
        f'interrupt-parent=<&irq>; interrupts=<{constants["ethmac_interrupt"]}>; '
        'local-mac-address=[20 12 03 14 05 06]; }; ')

def wsl(args,log=None):
    command=shlex.join([str(a) for a in args])
    if log:command+=' > '+shlex.quote(linux(log))+' 2>&1'
    COMMANDS.append(command)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--soc-dir',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'build/vendor/opensbi')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/opensbi/firmware')
    parser.add_argument('--pack-only',action='store_true')
    parser.add_argument('--payload-path',type=Path,default=None,help='FW_PAYLOAD image (defaults to the S-mode probe)')
    parser.add_argument('--osb-name',default='OPENSBI.OSB',help='Output OSB1 file name in --output-dir')
    a=parser.parse_args();src=a.source.resolve();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if not src.exists():
        subprocess.run(['git','clone','-c','core.autocrlf=false','https://github.com/riscv-software-src/opensbi',str(src)],check=True)
        subprocess.run(['git','-C',str(src),'checkout',REV],check=True)
    if subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()!=REV:
        raise ValueError('OpenSBI source revision differs from pinned version')
    if a.pack_only:
        pack_output(out,a.osb_name);return
    # Python entrypoint shebangs must retain LF when the checkout is on Windows.
    for script in (src/'scripts').rglob('*'):
        if script.suffix not in ('.py','.sh'):continue
        script.write_bytes(script.read_bytes().replace(b'\r\n',b'\n'))
    for config in src.rglob('*.carray'):
        config.write_bytes(config.read_bytes().replace(b'\r\n',b'\n'))
    patch=out/'no-mdt.patch'
    patch.write_bytes((ROOT/'firmware/opensbi/patches/no-mdt.patch').read_bytes().replace(b'\r\n',b'\n'))
    # Idempotent: refuse a changed upstream source rather than guessing a patch.
    if subprocess.run(['git','-C',str(src),'apply','--reverse','--check',str(patch)],capture_output=True).returncode:
        subprocess.run(['git','-C',str(src),'apply',str(patch)],check=True)
    csr=json.loads((a.soc_dir/'csr.json').read_text())
    if 'cpu_timer_time' not in csr['csr_registers']:raise ValueError('SoC needs CPU machine timer')
    generate_csr(csr,out/'include')
    header=(out/'include/generated/csr.h').read_text()
    for k,v in csr['constants'].items():
        if isinstance(v,int):header+='\n#define '+k.upper()+' '+str(v)+'u'
    plat=src/'platform/riscv_mini';shutil.copytree(ROOT/'firmware/opensbi/platform',plat,dirs_exist_ok=True)
    (plat/'mini_csr.h').write_text(header.replace('#include <stdint.h>', '#include <sbi/sbi_types.h>'))
    (out/'mini_csr.h').write_text(header)
    dts=out/'riscv_mini.dts';dtb=out/'riscv_mini.dtb'
    validation=json.loads((a.soc_dir/'validation.json').read_text())
    isa=validation['isa']
    uart=csr['csr_bases']['uart']
    sd=csr['csr_bases']['sdcard'];sd_control=csr['csr_bases']['sd_control']
    usb_control=csr['csr_bases']['usb_host'];phy_reset=csr['csr_bases']['phy_reset']
    dts.write_text('/dts-v1/;\n/ { #address-cells=<1>; #size-cells=<1>; compatible="riscv-mini,tangprimer20k"; model="riscv-mini"; '
        f'chosen {{ stdout-path="/soc/serial@{uart:x}"; }}; timer {{ compatible="riscv,timer"; }}; '
        'cpus { #address-cells=<1>; #size-cells=<0>; timebase-frequency=<60000000>; '
        f'cpu@0 {{ device_type="cpu"; reg=<0>; compatible="riscv"; riscv,isa="{isa}"; mmu-type="riscv,sv32"; cpu_intc: interrupt-controller {{ #interrupt-cells=<1>; interrupt-controller; compatible="riscv,cpu-intc"; }}; }}; }}; '
        'irq: interrupt-controller { compatible="riscv-mini,vexriscv-supervisor-irq"; #interrupt-cells=<1>; interrupt-controller; interrupts-extended=<&cpu_intc 9>; riscv-mini,mask-csr=<0x9c0>; riscv-mini,pending-csr=<0xdc0>; }; '
        'memory@0 { device_type="memory"; reg=<0x00000000 0x08000000>; }; '
        'reserved-memory { #address-cells=<1>; #size-cells=<1>; ranges; '
        'opensbi@1000000 { reg=<0x01000000 0x100000>; no-map; }; }; '
        'soc { #address-cells=<1>; #size-cells=<1>; compatible="simple-bus"; ranges; '
        f'serial@{uart:x} {{ compatible="riscv-mini,liteuart32"; reg=<0x{uart:x} 0x800>; interrupt-parent=<&irq>; interrupts=<0>; }}; '
        +ethernet_node(csr)+
        # LiteSDCard native SD host (drivers/mmc/litesd.c). reg order:
        # sdcard CSR block, sd_control reset block.
        f'mmc@{sd:x} {{ compatible="riscv-mini,litesd"; '
        f'reg=<0x{sd:x} 0x100>,<0x{sd_control:x} 0x4>; interrupt-parent=<&irq>; interrupts=<4>; }}; '
        # Custom Ultraembedded PIO full-speed USB host (drivers/usb/liteusb.c).
        # reg order: PIO transaction block, usb_host enable/reset/ready CSR block.
        'usb@f2000000 { compatible="riscv-mini,liteusb"; '
        f'reg=<0xf2000000 0x1000>,<0x{usb_control:x} 0x20>,<0x{phy_reset:x} 0x4>; interrupt-parent=<&irq>; interrupts=<{csr["constants"]["usb_host_interrupt"]}>; }}; '
        +lcd_node(csr)+'}; };\n')
    wsl(['dtc','-I','dts','-O','dtb','-o',linux(dtb),linux(dts)])
    probe=ROOT/'firmware/opensbi/probe';elf=out/'probe.elf';raw=out/'probe.bin'
    wsl(['clang','--target=riscv32-unknown-elf','-march=rv32ima_zicsr_zifencei','-mabi=ilp32','-Os','-Wall','-Wextra','-Werror',
         '-ffreestanding','-nostdlib','-msmall-data-limit=0','-fuse-ld=lld','-I'+linux(out),
         linux(probe/'start.S'),linux(probe/'probe.c'),linux(probe/'mmu.c'),'-Wl,-T,'+linux(probe/'probe.ld'),'-o',linux(elf)],out/'probe-build.log')
    wsl(['llvm-objcopy','-O','binary',linux(elf),linux(raw)])
    payload=a.payload_path.resolve() if a.payload_path else raw
    wsl(['make','-C',linux(src),'-j4','LLVM=1','PLATFORM=riscv_mini','O='+linux(out/'sbi-soft'),
         'FW_PAYLOAD_PATH='+linux(payload), 'FW_FDT_PATH='+linux(dtb)],out/'opensbi-build.log')
    (out/'build.sh').write_text('set -eu\n'+'\n'.join(COMMANDS)+'\n',newline='\n')
    print('Prepared',out/'build.sh')
def pack_output(out,name='OPENSBI.OSB'):
    binary=out/'sbi-soft/platform/riscv_mini/firmware/fw_payload.bin'
    # Keep the ordinary RPB checksum/layout; OSB1 selects one-way BIOS entry.
    image=pack(binary.read_bytes(),0x200000,os_image=True)
    (out/name).write_bytes(image)
    print('OpenSBI OS image:',len(image),'bytes')
if __name__=='__main__':main()
