"""Generate four independently selected MMU/FPU CPUs with 2 KiB I/D caches.

Requires a VexRiscv source checkout compatible with the installed LiteX CPU
generator (this trial used b6118e5cc2a33323425df6455697139021d50c72),
Java 8 and sbt-launch 1.9.7. No installed packages are modified.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pythondata_cpu_vexriscv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vexriscv-source', required=True, type=Path)
    parser.add_argument('--java', required=True, type=Path)
    parser.add_argument('--sbt-launch', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--relaxed-pc-calculation', action='store_true',
                        help='Add VexRiscv fetch address calculation stage')
    parser.add_argument('--compressed', action='store_true',
                        help='Enable the RISC-V C compressed instruction extension')
    parser.add_argument('--pipelined-fetch', action='store_true',
                        help='Use a two-cycle I-cache and an instruction injector register')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = Path(pythondata_cpu_vexriscv.data_location)
    for name in ('src', 'project'):
        shutil.copytree(base/name, output/name, dirs_exist_ok=True)
    # Keep upstream checkout untouched. TM permission must exist even when
    # OpenSBI emulates time/timeh instead of exposing a hardware time port.
    vex_local=output/'ext/VexRiscv'
    shutil.copytree(args.vexriscv_source,vex_local,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git','target'))
    csr_source=vex_local/'src/main/scala/vexriscv/plugin/CsrPlugin.scala'
    csr_text=csr_source.read_text()
    tm_anchor='if(utimeAccess != CsrAccess.NONE)    rw(csrId, 1 -> TM)'
    if csr_text.count(tm_anchor)!=1:raise ValueError('Unsupported counter permission mapping')
    csr_source.write_text(csr_text.replace(tm_anchor,'rw(csrId, 1 -> TM) // Also governs firmware-emulated time'))
    build = (base/'build.sbt').read_text()
    build = build.replace('file("ext/VexRiscv")',
                          'file('+json.dumps(vex_local.as_posix())+')')
    (output/'build.sbt').write_text(build)
    target = output/'src/main/scala/vexriscv/GenCoreDefault.scala'
    source = target.read_text()
    replacements = {
        'debug : Boolean = false,': 'fpu : Boolean = false,\n  debug : Boolean = false,',
        'val parser = new scopt.OptionParser[ArgConfig]("VexRiscvGen") {':
            'val parser = new scopt.OptionParser[ArgConfig]("VexRiscvGen") {\n'
            '      opt[Boolean]("fpu") action { (v,c) => c.copy(fpu=v) }',
        'CsrPluginConfig.linuxFull(mtVecInit = argConfig.machineTrapVector).copy(ebreakGen = false)':
            'CsrPluginConfig.linuxFull(mtVecInit = argConfig.machineTrapVector).copy(ebreakGen = false, '
            'misaExtensionsInit = 0x141101 | (if(argConfig.fpu) 0x20 else 0) | '
            '(if(argConfig.compressedGen) 0x4 else 0))',
        '// CPU configuration':
            'if(argConfig.fpu) plugins += new FpuPlugin(externalFpu=false,\n'
            '        p=vexriscv.ip.fpu.FpuParameter(withDouble=false,\n'
            '          withDivSqrt=false, withDiv=true, withSqrt=true))\n\n'
            '      // CPU configuration',
    }
    for anchor, replacement in replacements.items():
        if source.count(anchor) != 1:
            raise ValueError('Unsupported upstream generator: '+anchor)
        source = source.replace(anchor, replacement)
    if args.pipelined_fetch:
        for anchor, replacement in (
            ('twoCycleCache = !argConfig.compressedGen', 'twoCycleCache = true'),
            ('relaxedPcCalculation = argConfig.relaxedPcCalculation,',
             'relaxedPcCalculation = argConfig.relaxedPcCalculation,\n            injectorStage = true,'),
        ):
            if source.count(anchor) != 1:
                raise ValueError('Unsupported upstream fetch pipeline: '+anchor)
            source = source.replace(anchor, replacement)
    source=source.replace('ioRange = _.msb',
        'ioRange = a => (a(31 downto 28) === U(15, 4 bits)) && (a(31 downto 22) =/= U(0x3cc, 10 bits))')
    source=source.replace('ioRange      = _.msb',
        'ioRange = a => (a(31 downto 28) === U(15, 4 bits)) && (a(31 downto 22) =/= U(0x3cc, 10 bits))')
    target.write_text(source)
    repositories = output/'repositories'
    repositories.write_text('[repositories]\nlocal\nmaven-central: https://repo.maven.apache.org/maven2/\n')
    pipeline = ' --relaxedPcCalculation true' if args.relaxed_pc_calculation else ''
    variants = [('all','false','VexRiscv_Base'), ('all','true','VexRiscv_Fpu'),
                ('linux','false','VexRiscv_Mmu'), ('linux','true','VexRiscv_MmuFpu')]
    commands = [
        f'runMain vexriscv.GenCoreDefault --csrPluginConfig {csr} --iCacheSize 2048 '
        '--dCacheSize 2048 --singleCycleMulDiv false --singleCycleShift false '
        f'--fpu {fpu} --compressedGen {str(args.compressed).lower()}{pipeline} --outputFile {name}'
        for csr, fpu, name in variants
    ]
    java_tmp = output/'java-tmp'
    java_tmp.mkdir(exist_ok=True)
    with (output/'generate.log').open('w') as log:
        subprocess.run([str(args.java.resolve()), '-Xmx3G',
                        '-Djna.tmpdir='+str(java_tmp), '-Djava.io.tmpdir='+str(java_tmp),
                        '-Dsbt.global.base='+str(output/'sbt-global'),
                        '-Dsbt.io.jdktimestamps=true', '-Dsbt.ivy.home='+str(output/'ivy'),
                        '-Dsbt.override.build.repos=true',
                        '-Dsbt.repository.config='+str(repositories),
                        '-Dsbt.boot.directory='+str(output/'sbt-boot'),
                        '-jar', str(args.sbt_launch.resolve()), *commands],
                       cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True,
                       env={**os.environ, 'TMP':str(java_tmp), 'TEMP':str(java_tmp)})
    report = {'commands': commands, 'high_mmio_xip': True, 'pipeline': {
                  'relaxed_pc_calculation': args.relaxed_pc_calculation,
                  'extra_fetch_stage': args.relaxed_pc_calculation},
              'cpu_rtls': {},
              'vexriscv_source': str(args.vexriscv_source.resolve()),
              'upstream_generator_sha256': hashlib.sha256(
                  (base/'src/main/scala/vexriscv/GenCoreDefault.scala').read_bytes()).hexdigest()}
    report['pipeline'].update(two_cycle_icache=args.pipelined_fetch or not args.compressed,
                              injector_register=args.pipelined_fetch)
    for csr, fpu, name in variants:
        path = output/(name+'.v')
        report['cpu_rtls'][name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'mmu':csr=='linux','fpu':fpu=='true','dcache':True,
                                  'compressed':args.compressed,'bitmanip':[]}
    (output/'generator.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
