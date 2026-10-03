"""Generate four independently selected MMU/FPU CPUs with 2 KiB I/D caches.

Requires a VexRiscv source checkout compatible with the installed LiteX CPU
generator (this trial used b6118e5cc2a33323425df6455697139021d50c72),
Java 8 and sbt-launch 1.9.7. No installed packages are modified.
"""
import argparse
import hashlib
import json
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
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = Path(pythondata_cpu_vexriscv.data_location)
    for name in ('src', 'project'):
        shutil.copytree(base/name, output/name, dirs_exist_ok=True)
    build = (base/'build.sbt').read_text()
    build = build.replace('file("ext/VexRiscv")',
                          'file('+json.dumps(args.vexriscv_source.resolve().as_posix())+')')
    (output/'build.sbt').write_text(build)
    target = output/'src/main/scala/vexriscv/GenCoreDefault.scala'
    source = target.read_text()
    replacements = {
        'debug : Boolean = false,': 'fpu : Boolean = false,\n  debug : Boolean = false,',
        'val parser = new scopt.OptionParser[ArgConfig]("VexRiscvGen") {':
            'val parser = new scopt.OptionParser[ArgConfig]("VexRiscvGen") {\n'
            '      opt[Boolean]("fpu") action { (v,c) => c.copy(fpu=v) }',
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
    target.write_text(source)
    repositories = output/'repositories'
    repositories.write_text('[repositories]\nlocal\nmaven-central: https://repo.maven.apache.org/maven2/\n')
    pipeline = ' --relaxedPcCalculation true' if args.relaxed_pc_calculation else ''
    variants = [('all','false','VexRiscv_Base'), ('all','true','VexRiscv_Fpu'),
                ('linux','false','VexRiscv_Mmu'), ('linux','true','VexRiscv_MmuFpu')]
    commands = [
        f'runMain vexriscv.GenCoreDefault --csrPluginConfig {csr} --iCacheSize 2048 '
        '--dCacheSize 2048 --singleCycleMulDiv false --singleCycleShift false '
        f'--fpu {fpu}{pipeline} --outputFile {name}'
        for csr, fpu, name in variants
    ]
    with (output/'generate.log').open('w') as log:
        subprocess.run([str(args.java.resolve()), '-Xmx3G',
                        '-Dsbt.override.build.repos=true',
                        '-Dsbt.repository.config='+str(repositories),
                        '-Dsbt.boot.directory='+str(output/'sbt-boot'),
                        '-jar', str(args.sbt_launch.resolve()), *commands],
                       cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = {'commands': commands, 'pipeline': {
                  'relaxed_pc_calculation': args.relaxed_pc_calculation,
                  'extra_fetch_stage': args.relaxed_pc_calculation},
              'cpu_rtls': {},
              'vexriscv_source': str(args.vexriscv_source.resolve()),
              'upstream_generator_sha256': hashlib.sha256(
                  (base/'src/main/scala/vexriscv/GenCoreDefault.scala').read_bytes()).hexdigest()}
    for csr, fpu, name in variants:
        path = output/(name+'.v')
        report['cpu_rtls'][name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'mmu':csr=='linux','fpu':fpu=='true','dcache':True}
    (output/'generator.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
