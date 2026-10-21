"""Generate four independently selected MMU/FPU CPUs with 2 KiB I/D caches.

Requires a VexRiscv source checkout compatible with the installed LiteX CPU
generator (this trial used b6118e5cc2a33323425df6455697139021d50c72),
Java 8 and sbt-launch 1.9.7. No installed packages are modified.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import platform
from pathlib import Path

import pythondata_cpu_vexriscv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def build(args, run=None):
    if args.locked_config:
        lock=json.loads(args.locked_config.read_text(encoding='utf-8'))
        required=('relaxed_pc_calculation','compressed','pipelined_fetch','arithmetic_matrix')
        if set(lock.get('flags',{})) != set(required) or any(type(lock['flags'][n]) is not bool for n in required):
            raise ValueError('CPU lock must explicitly specify all generator flags: '+', '.join(required))
        for name in required:setattr(args,name,lock['flags'][name])
    base = Path(pythondata_cpu_vexriscv.data_location)
    args.vexriscv_source=args.vexriscv_source or Path(os.environ.get('VEXRISCV_SOURCE',base/'ext/VexRiscv'))
    from scripts.modular.environment import discover
    if args.java is None:args.java=Path(discover(['java'])['bindings']['java'][0])
    if args.sbt_launch is None:args.sbt_launch=Path(discover(['sbt-launch'])['bindings']['sbt-launch'][0])
    for role,path in [('java',args.java),('sbt-launch',args.sbt_launch),('VexRiscv source',args.vexriscv_source)]:
        if not path.exists():raise ValueError('Missing '+role+': '+str(path))
    from scripts.modular.storage import file_hash, digest
    source_files={p.relative_to(args.vexriscv_source).as_posix():file_hash(p)
                  for folder in ('src','project') for p in (args.vexriscv_source/folder).rglob('*')
                  if p.is_file() and 'target' not in p.parts}
    for name in ('build.sbt',):
        if (args.vexriscv_source/name).is_file():source_files[name]=file_hash(args.vexriscv_source/name)
    source_lock=dict(generator_script_sha256=file_hash(__file__),upstream_files=source_files,
                     java_binary_sha256=file_hash(args.java),sbt_launcher_sha256=file_hash(args.sbt_launch),
                     java_version=subprocess.check_output([str(args.java),'-version'],stderr=subprocess.STDOUT,text=True).strip(),
                     template_sha256=file_hash(base/'src/main/scala/vexriscv/GenCoreDefault.scala'))
    source_lock['generator_files']={p.relative_to(base).as_posix():file_hash(p)
                                   for folder in ('src','project') for p in (base/folder).rglob('*')
                                   if p.is_file() and 'target' not in p.parts}
    source_lock['generator_build_sbt_sha256']=file_hash(base/'build.sbt')
    source_lock['requirements_sha256']=file_hash(Path(__file__).resolve().parents[2]/'requirements.lock')
    if '"1.8.' not in source_lock['java_version']:raise ValueError('CPU generation requires Java 8')
    if args.locked_config and lock.get('source_lock') != source_lock:
        raise ValueError('CPU source/tool identities differ from locked config')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
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
    variants = [('all','false','VexRiscv_Base',False,False), ('all','true','VexRiscv_Fpu',False,False),
                ('linux','false','VexRiscv_Mmu',False,False), ('linux','true','VexRiscv_MmuFpu',False,False)]
    if args.arithmetic_matrix:
        variants = [('linux','false',f'VexRiscv_Mmu_s{int(shift)}m{int(mul)}',shift,mul)
                    for shift,mul in ((False,False),(True,False),(False,True),(True,True))]
    if getattr(args, 'vendor_dir', None) is not None:
        # Only the selected production variant is committed as vendor IP.
        variants = [('linux','false','VexRiscv_Mmu',True,True)]
    commands = [
        f'runMain vexriscv.GenCoreDefault --csrPluginConfig {csr} --iCacheSize 2048 '
        f'--dCacheSize 2048 --singleCycleMulDiv {str(mul).lower()} --singleCycleShift {str(shift).lower()} '
        f'--fpu {fpu} --compressedGen {str(args.compressed).lower()}{pipeline} --outputFile {name}'
        for csr, fpu, name, shift, mul in variants
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
                       env=__import__('scripts.modular.environment',fromlist=['process_environment']).process_environment(
                           {'TMP':str(java_tmp),'TEMP':str(java_tmp),'TMPDIR':str(java_tmp),
                            'JAVA_HOME':str(args.java.resolve().parent.parent),'COURSIER_CACHE':str(output/'coursier')}))
    report = {'commands': commands, 'high_mmio_xip': True, 'pipeline': {
                  'relaxed_pc_calculation': args.relaxed_pc_calculation,
                  'extra_fetch_stage': args.relaxed_pc_calculation},
              'cpu_rtls': {},
              'vexriscv_source': str(args.vexriscv_source.resolve()),
              'upstream_generator_sha256': hashlib.sha256(
                  (base/'src/main/scala/vexriscv/GenCoreDefault.scala').read_bytes()).hexdigest()}
    report['pipeline'].update(two_cycle_icache=args.pipelined_fetch or not args.compressed,
                              injector_register=args.pipelined_fetch)
    report['source_lock']=source_lock
    report['resolved_flags']={n:getattr(args,n) for n in ('relaxed_pc_calculation','compressed','pipelined_fetch','arithmetic_matrix')}
    report['fixed_parameters']=dict(icache_bytes=2048,dcache_bytes=2048,java_heap='3G',
                                    csr_permission_patch='time/TM',high_mmio_xip=True)
    for csr, fpu, name, shift, mul in variants:
        path = output/(name+'.v')
        report['cpu_rtls'][name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'mmu':csr=='linux','fpu':fpu=='true','dcache':True,
                                  'compressed':args.compressed,'bitmanip':[],
                                  'single_cycle_shift':shift,'single_cycle_muldiv':mul}
    (output/'generator.json').write_text(json.dumps(report, indent=2)+'\n')
    (output/'generator.lock.json').write_text(json.dumps(dict(schema=1,flags=report['resolved_flags'],source_lock=source_lock,
                                                            fixed_parameters=report['fixed_parameters']),indent=2)+'\n')
    if getattr(args, 'vendor_dir', None) is not None:
        install_vendor(report, output, args.vendor_dir, args, force=getattr(args, 'force', False))
    print(json.dumps(report, indent=2))


VENDOR_CPU = 'VexRiscv_Mmu'


def _without_commit_comment(data):
    """VexRiscv stamps the generating commit into a header comment; ignore it."""
    return b'\n'.join(line for line in data.split(b'\n') if not line.startswith(b'// Git hash'))


def install_vendor(report, output, vendor_dir, args, force=False):
    """Install the selected variant as committed vendor IP with its full record."""
    vendor = Path(vendor_dir).resolve()
    vendor.mkdir(parents=True, exist_ok=True)
    produced = output/(VENDOR_CPU+'.v')
    target = vendor/(VENDOR_CPU+'.v')
    if target.is_file():
        current, regenerated = target.read_bytes(), produced.read_bytes()
        if current != regenerated and not force:
            if _without_commit_comment(current) == _without_commit_comment(regenerated):
                print('CPU RTL matches the committed file apart from the embedded git hash line')
            else:
                raise ValueError('Regenerated CPU RTL differs from the committed file %s (committed %s, regenerated %s); '
                                 'review the parameter change, then rerun with --force to accept it'
                                 % (target, hashlib.sha256(current).hexdigest(),
                                    hashlib.sha256(regenerated).hexdigest()))
    shutil.copyfile(produced, target)
    yaml = output/(VENDOR_CPU+'.yaml')
    if yaml.is_file(): shutil.copyfile(yaml, vendor/(VENDOR_CPU+'.yaml'))
    root = Path(__file__).resolve().parents[2]
    try: relative = target.relative_to(root).as_posix()
    except ValueError: relative = target.as_posix()
    try:
        source = subprocess.check_output(['git','-C',str(args.vexriscv_source),'rev-parse','HEAD'],
                                         text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        source = ''
    record = {'commands': report['commands'], 'high_mmio_xip': report['high_mmio_xip'],
              'pipeline': report['pipeline'],
              'cpu_rtls': {VENDOR_CPU: dict(report['cpu_rtls'][VENDOR_CPU], path=relative)},
              'vexriscv_source': ('tools/vexriscv (VexRiscv '+source+')') if source else str(args.vexriscv_source),
              'upstream_generator_sha256': report['upstream_generator_sha256'],
              'source_lock': report['source_lock'], 'resolved_flags': report['resolved_flags'],
              'fixed_parameters': report['fixed_parameters']}
    (vendor/'generator.json').write_text(json.dumps(record, indent=2)+'\n')
    provenance = vendor/'PROVENANCE.json'
    data = json.loads(provenance.read_text(encoding='utf-8')) if provenance.is_file() else {}
    data.setdefault('schema', 1)
    data.setdefault('vendor', 'Spinal HDL contributors (VexRiscv, MIT license)')
    data.setdefault('component', "VexRiscv RV32IMA 'linux' CPU RTL: MMU, 2 KiB I-cache / 2 KiB D-cache, "
                                 'no FPU, single-cycle shift and mul/div')
    data.setdefault('device', {'part': 'GW2A-18C (Tang Primer 20K Dock)', 'system_clock': '60 MHz'})
    data['source'] = dict(data.get('source', {}), generator='pythondata_cpu_vexriscv vexriscv.GenCoreDefault',
                          command=report['commands'][0],
                          toolchain='Java 8, sbt-launch 1.9.7, mini.py build cpu --generate',
                          note='Only the selected production variant is committed as '+VENDOR_CPU+'.v. '
                               'Regenerate with mini.py build cpu --generate --vendor-dir gateware/vendor/vexriscv; '
                               'the tool refuses to overwrite differing bytes unless --force is given.')
    files = data.setdefault('files', {})
    for path in (target, vendor/(VENDOR_CPU+'.yaml')):
        if path.is_file():
            files[path.name] = dict(files.get(path.name, {}), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    bytes=path.stat().st_size)
    files.setdefault(VENDOR_CPU+'.v', {}).update(module='VexRiscv', protected=False)
    provenance.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    print('Vendor CPU installed: '+relative)
