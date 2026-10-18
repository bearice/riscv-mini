"""Check an exact generated P1 artifact against its embedded firmware and clocks."""
import argparse
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from scripts.modular.storage import Store, Run, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-run', type=Path, required=True)
    args = parser.parse_args()
    store = Store(ROOT)
    build = read(args.build_run/'result.json')
    ref = build['artifact']
    run = Run('test', 'ae350-p1-artifact', dict(artifact=ref))
    checks = []
    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))
        run.result['checks'] = checks; run.save()
        if not passed:
            raise ValueError('P1 artifact check failed: ' + name)
    try:
        artifact = store.get(ref, recursive=True)
        check('generated P1 profile with DDR disabled', artifact['config']['profile']=='P1-DDR-independent' and not artifact['config']['ddr'])
        folder = store.path(ref)
        boot = store.path(artifact['dependencies']['bootloader'])
        boot_config = store.get(artifact['dependencies']['bootloader'])['config']
        binary = (boot/'outputs/boot.bin').read_bytes()
        attributes = (boot/'outputs/elf-attributes.txt').read_text()
        check('ELF32 entry and integer ABI', 'ELF32' in attributes and '0x80000000' in attributes and 'rv32i' in attributes and bool(re.search(r'Flags:\s+0x0\b', attributes)))
        check('build identity embedded in binary', boot_config['build_id'].encode() in binary)
        words = (folder/'gateware/mini_ae350_memory.init').read_text().split()
        data = binary + bytes((-len(binary)) % 4)
        expected = [f'{int.from_bytes(data[n:n+4], "little"):08x}' for n in range(0,len(data),4)]
        check('ROM init exactly embeds selected firmware', [w.lower() for w in words[:len(expected)]]==expected and all(int(w,16)==0 for w in words[len(expected):]))
        memories = read(folder/'memory-map.json')['memories']
        check('ROM and SRAM map', memories['rom']['base']==0x80000000 and memories['rom']['size']==8192 and memories['sram']['base']==0 and memories['sram']['size']==8192)
        verilog = (folder/'gateware/mini_ae350.v').read_text()
        check('hard macro and UART2 connected without a fabric bus bridge', 'AE350_SOC ' in verilog and '.UART2_RXD' in verilog and '.UART2_TXD' in verilog and not re.search(r'\bDDR3_|VexRiscv|SharedL2|wishbone|ahb2wishbone|downconverter',verilog, re.I))
        check('UART2 directly drives TX without a diagnostic takeover',
              bool(re.search(r'\.UART2_TXD\s*\(serial_tx\)', verilog)) and 'diagnostic' not in verilog.lower())
        cst = (folder/'gateware/mini_ae350.cst').read_text()
        check('board UART reset J23 JTAG and PLL pins', all(s in cst for s in ('"serial_rx" N16','"serial_tx" P15','"reset_key" U4','"reset_key" IO_TYPE=LVCMOS15','"cpu_jtag_tck" V16','"cpu_jtag_tdi" V17','"cpu_jtag_tdo" Y21','"cpu_jtag_tms" U17','"ae350_pll" PLL_R[0]')))
        description = store.get(artifact['dependencies']['cpu'])['dependencies']['machine-description']
        camera = read(store.path(description)/'inputs/board.json')['resources']['camera']
        reserved = {p for pins in camera.values() if isinstance(pins,list) for p in pins}
        used = set(re.findall(r'IO_LOC\s+"[^"]+"\s+([A-Z]+\d+);', cst))
        check('generated pin constraints leave all camera resources unused', not (used & reserved) and 'cpu_jtag_trst' not in cst and bool(re.search(r"\.TRST_IN\s*\(1'd1\)", verilog)))
        sdc = (folder/'gateware/mini_ae350.sdc').read_text()
        check('800MHz core on CLKOUT1 and 100MHz sys on CLKOUT0', 'multiply_by 16 [get_pins {ae350_pll/CLKOUT1}]' in sdc and 'divide_by 8 -multiply_by 16 [get_pins {ae350_pll/CLKOUT0}]' in sdc)
        check('generated artifacts do not claim hardware qualification', not build['hardware_qualified'] and build['ready_for_execution']==bool(artifact['config']['synthesize']))
        run.finish(artifact=ref, checks=checks, tests_run=len(checks), qualification='host-artifact-only')
        print('PASS', len(checks), 'P1 artifact checks; receipt:',run.path/'result.json')
    except Exception as error:
        run.result.update(passed=False,error=str(error));run.save();raise


if __name__ == '__main__':
    main()
