"""Generate the single CPU selected by the validated machine IP parameters."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pythondata_cpu_vexriscv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.modular.storage import text_file_hash, source_file_hash, SOURCE_HASH_FORMAT


def _copy_sources(source, destination):
    """Remove deleted source inputs while retaining sbt's target caches."""
    source, destination = Path(source), Path(destination)
    if destination.is_dir():
        for path in destination.rglob('*'):
            relative = path.relative_to(destination)
            if path.is_file() and not any(part in ('.git', 'target') for part in relative.parts):
                if not (source / relative).is_file():
                    path.unlink()
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', 'target'))


def build(args, run=None):
    base = Path(pythondata_cpu_vexriscv.data_location)
    args.vexriscv_source=args.vexriscv_source or Path(os.environ.get('VEXRISCV_SOURCE',base/'ext/VexRiscv'))
    from scripts.modular.environment import discover
    if args.java is None:args.java=Path(discover(['java'])['bindings']['java'][0])
    if args.sbt_launch is None:args.sbt_launch=Path(discover(['sbt-launch'])['bindings']['sbt-launch'][0])
    for role,path in [('java',args.java),('sbt-launch',args.sbt_launch),('VexRiscv source',args.vexriscv_source)]:
        if not path.exists():raise ValueError('Missing '+role+': '+str(path))
    from scripts.modular.storage import file_hash, digest
    source_files={p.relative_to(args.vexriscv_source).as_posix():source_file_hash(p)
                  for folder in ('src','project') for p in (args.vexriscv_source/folder).rglob('*')
                  if p.is_file() and 'target' not in p.parts}
    for name in ('build.sbt',):
        if (args.vexriscv_source/name).is_file():source_files[name]=source_file_hash(args.vexriscv_source/name)
    source_lock=dict(source_hash_format=SOURCE_HASH_FORMAT, generator_script_sha256=source_file_hash(__file__),upstream_files=source_files,
                     java_binary_sha256=file_hash(args.java),sbt_launcher_sha256=file_hash(args.sbt_launch),
                     java_version=subprocess.check_output([str(args.java),'-version'],stderr=subprocess.STDOUT,text=True).strip(),
                     template_sha256=source_file_hash(base/'src/main/scala/vexriscv/GenCoreDefault.scala'))
    source_lock['generator_files']={p.relative_to(base).as_posix():source_file_hash(p)
                                   for folder in ('src','project') for p in (base/folder).rglob('*')
                                   if p.is_file() and 'target' not in p.parts}
    source_lock['generator_build_sbt_sha256']=source_file_hash(base/'build.sbt')
    source_lock['requirements_sha256']=source_file_hash(Path(__file__).resolve().parents[2]/'requirements.lock')
    if '"1.8.' not in source_lock['java_version']:raise ValueError('CPU generation requires Java 8')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ('src', 'project'):
        _copy_sources(base/name, output/name)
    # Keep upstream checkout untouched. TM permission must exist even when
    # OpenSBI emulates time/timeh instead of exposing a hardware time port.
    vex_local=output/'ext/VexRiscv'
    _copy_sources(args.vexriscv_source, vex_local)
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
    selected = args.selected_parameters
    variants = [('linux' if selected['mmu'] else 'all', str(selected['fpu']).lower(),
                 args.module_name, selected['single_cycle_shift'], selected['single_cycle_muldiv'])]
    cache_i, cache_d = selected['icache_bytes'], selected['dcache_bytes']
    commands = [
        f'runMain vexriscv.GenCoreDefault --csrPluginConfig {csr} --iCacheSize {cache_i} '
        f'--dCacheSize {cache_d} --singleCycleMulDiv {str(mul).lower()} --singleCycleShift {str(shift).lower()} '
        f'--fpu {fpu} --compressedGen {str(args.compressed).lower()}{pipeline} --outputFile {name}'
        for csr, fpu, name, shift, mul in variants
    ]
    # sbt's Unix socket name must fit sockaddr_un even with a deep workspace.
    import tempfile
    java_tmp = Path(tempfile.gettempdir()) / ('mini-jvm-' + digest(str(output))[:12])
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
                       env=__import__('scripts.modular.environment',fromlist=['process_environment']).process_environment(
                           {'TMP':str(java_tmp),'TEMP':str(java_tmp),'TMPDIR':str(java_tmp),
                            'JAVA_HOME':str(args.java.resolve().parent.parent),'COURSIER_CACHE':str(output/'coursier')}))
    report = {'commands': commands, 'high_mmio_xip': True, 'pipeline': {
                  'relaxed_pc_calculation': args.relaxed_pc_calculation,
                  'extra_fetch_stage': args.relaxed_pc_calculation},
              'cpu_rtls': {},
              'vexriscv_source': str(args.vexriscv_source.resolve()),
              'upstream_generator_sha256': source_file_hash(base/'src/main/scala/vexriscv/GenCoreDefault.scala')}
    report['pipeline'].update(two_cycle_icache=args.pipelined_fetch or not args.compressed,
                              injector_register=args.pipelined_fetch)
    report['source_lock']=source_lock
    report['resolved_flags']={n:getattr(args,n) for n in ('relaxed_pc_calculation','compressed','pipelined_fetch')}
    report['fixed_parameters']=dict(icache_bytes=cache_i,dcache_bytes=cache_d,java_heap='3G',
                                    csr_permission_patch='time/TM',high_mmio_xip=True)
    for csr, fpu, name, shift, mul in variants:
        path = output/(name+'.v')
        report['cpu_rtls'][name] = {'path': str(path), 'sha256': text_file_hash(path),
                                  'hash_normalization': 'lf',
                                  'mmu':csr=='linux','fpu':fpu=='true','dcache':True,
                                  'compressed':args.compressed,'bitmanip':[],
                                  'single_cycle_shift':shift,'single_cycle_muldiv':mul}
    (output/'generator.json').write_text(json.dumps(report, indent=2)+'\n')
    (output/'generator.lock.json').write_text(json.dumps(dict(schema=1,flags=report['resolved_flags'],source_lock=source_lock,
                                                            fixed_parameters=report['fixed_parameters']),indent=2)+'\n')
    return report
