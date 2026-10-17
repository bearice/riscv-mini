"""One target registry and execution convention for all project builds."""
import importlib
from pathlib import Path
from types import SimpleNamespace
from . import arguments, configuration
from .storage import canonical, digest, read, write
from . import machines

CORE = ('system', 'bios', 'bootloader', 'hal-sdk', 'cpu', 'soc-description', 'gateware')
BACKENDS = {'cpu-gcb':'cpu_gcb', 'cpu-qualification':'cpu_qualification', 'matrix':'matrix',
            'linux':'linux', 'busybox':'busybox', 'uboot':'uboot', 'opensbi':'opensbi',
            'payload':'payload', 'doom':'doom', 'gwbasic':'gwbasic', 'nyancat':'nyancat'}


def add_targets(parser):
    sub = parser.add_subparsers(dest='target', required=True)
    for name in (*CORE, *BACKENDS, 'machine-description'):
        target = sub.add_parser(name)
        target.add_argument('--machine', choices=machines.MACHINES,
                            help='Machine selection; omitted means the original 20K')
        target.add_argument('--hardware', default='current', help='Exact system/ref or archived hardware inputs')
        target.add_argument('--output-dir', type=Path, help='Optional output view; default output is managed under build/')
        target.add_argument('--dry-run', action='store_true', help='Resolve a plan without executing build tools')
        target.add_argument('--purpose', default='candidate', help='Purpose recorded with this build request')
        if name in CORE or name == 'machine-description':
            target.add_argument('--reference-root', type=Path, help='AE350 vendor input directory; archived by content, never an unidentified dependency')
        if name == 'machine-description':
            continue
        if name in CORE:
            target.add_argument('--locked', type=Path)
            if name in ('system', 'gateware', 'soc-description'):
                configuration.add_arguments(target)
                target.add_argument('--refresh-hardware', action='store_true')
            else:
                target.set_defaults(feature=[], refresh_hardware=False, **{key:None for key in configuration.HARDWARE_OPTIONS})
            if name in ('system', 'gateware'):
                target.add_argument('--synthesize', action='store_true', help='Run Gowin PnR for changed hardware')
            else: target.set_defaults(synthesize=False)
            if name in ('system', 'bios'):
                target.add_argument('--source', type=Path, help='Application source; default resident BIOS')
            else: target.set_defaults(source=None)
            if name == 'cpu':
                target.add_argument('--generate', action='store_true', help='Generate CPU RTL; otherwise verify/reuse selected CPU')
                # CPU source is the generator source tree, not application source.
                arguments.cpu_generator(target)
        else:
            getattr(arguments, BACKENDS[name])(target)


def execute(store, run, args):
    dry = args.command == 'plan' or args.dry_run
    machine = getattr(args, 'machine', None) or machines.LEGACY
    if machine == machines.AE350:
        return machines.execute(store, run, args, dry)
    if getattr(args, 'reference_root', None):
        raise ValueError('--reference-root requires --machine mega138k_ae350')
    if args.target == 'machine-description':
        raise ValueError('P0 machine-description requires --machine mega138k_ae350; 20K uses its existing soc-description')
    if args.target=='cpu' and not args.generate:
        generator_options=('vexriscv_source','java','sbt_launch','locked_config','relaxed_pc_calculation',
                           'compressed','pipelined_fetch','arithmetic_matrix')
        if any(getattr(args,key,None) for key in generator_options):
            raise ValueError('CPU generator options require --generate')
    if getattr(args,'locked',None) and (configuration.changed(args) or args.source or args.synthesize or args.refresh_hardware):
        raise ValueError('--locked cannot be combined with source/configuration overrides')
    if args.target in BACKENDS or (args.target=='cpu' and args.generate):
        return backend(store, run, args, dry)
    from .system import import_legacy, bundle, git_commit, build_firmware_system
    if args.locked:
        from .replay import locked_build
        ref=locked_build(store,args.locked,run,plan=dry);run.finish(system=ref)
        return ref
    hardware_build = args.target in ('soc-description','gateware') or args.refresh_hardware or configuration.changed(args) or args.synthesize
    if not hardware_build:
        if args.target=='cpu':
            ref=import_legacy(store,args.hardware,run);parts=read(store.path(ref)/'bundle.lock.json')['components']
            run.step('cpu','REUSE',parts['cpu']);run.finish(system=ref,artifact=parts['cpu']);return parts['cpu']
        return build_firmware_system(store,args.hardware,args.target,run,plan=dry,application=args.source)
    base = None
    if not args.profile:
        ref = import_legacy(store,args.hardware,run)
        base = read(store.path(ref)/'bundle.lock.json')['components']
    v, cpu = configuration.resolve(store,run,args,base,plan=dry)
    if dry and not cpu:
        for name in ('soc-description','bootloader','gateware','hal-sdk','bios'):
            run.step(name,'BUILD',reason='resolved hardware request; exact cache check occurs when upstream outputs exist')
            if args.target==name: break
        run.finish(plan=True);return None
    from .hardware import generate_soc, build_gateware
    from .firmware import build_component
    soc = generate_soc(store,run,None,cpu,validation=v,plan=dry)
    if not soc:
        run.step('downstream','BUILD',reason='SoC description output required');run.finish(plan=True);return None
    if args.target=='soc-description':run.finish(artifact=soc);return soc
    boot = build_component(store,run,'bootloader',soc,plan=dry)
    if not boot:
        run.step('downstream','BUILD',reason='Bootloader output required');run.finish(plan=True);return None
    gate = build_gateware(store,run,soc,boot,synthesize=args.synthesize,plan=dry)
    if not gate:
        run.step('downstream','BUILD',reason='Gateware output required');run.finish(plan=True);return None
    if args.target=='gateware':run.finish(artifact=gate);return gate
    sdk = build_component(store,run,'hal-sdk',soc,plan=dry)
    if not sdk:
        run.step('bios','BUILD',reason='SDK output required');run.finish(plan=True);return None
    bios = build_component(store,run,'bios',soc,boot=boot,sdk=sdk,application=args.source,plan=dry)
    if dry:run.finish(plan=True);return None
    parts={'cpu':cpu,'soc-description':soc,'bootloader':boot,'gateware':gate,'hal-sdk':sdk,'bios':bios}
    ref=bundle(store,parts,v,dict(kind='current-source',commit=git_commit(store.root)),run)
    run.finish(system=ref);return ref


def backend(store, run, args, dry):
    name='cpu_generator' if args.target=='cpu' else BACKENDS[args.target]
    output = (args.output_dir or store.build/'work'/args.target/run.id).resolve()
    request={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    run.result.update(target=args.target, output_dir=str(output), resolved_request=request)
    run.save()
    if dry:
        run.step(args.target,'BUILD',reason='resolved backend request; no tools or source downloads executed')
        run.finish(plan=True);return None
    module=importlib.import_module('scripts.builders.'+name)
    actual=SimpleNamespace(**vars(args));actual.output_dir=output
    actual.prepare_only=False;actual.record=False;actual.pack_only=False
    if args.target=='uboot':
        actual.without_video=False
        for flag in args.feature:
            if flag not in ('video=on','video=off'):raise ValueError('U-Boot supports --feature video=on|off')
            actual.without_video=flag=='video=off'
    if args.target in ('opensbi','uboot'):
        from .system import import_legacy,materialize
        ref=import_legacy(store,args.hardware,run)
        view=store.build/'work/hardware-view'/ref['digest']
        actual.soc_dir=materialize(store,ref,view)
        run.result['system']=ref
    output.mkdir(parents=True,exist_ok=True)
    write(output/'build.lock.json',dict(schema=1,target=args.target,request=request))
    if args.target=='opensbi':module.COMMANDS.clear()
    module.build(actual,run=run)
    if args.target in ('linux','busybox','opensbi'):
        from .executor import execute
        roles={'linux':('cc','make','clang','lld','dtc'),
               'busybox':('cc','make','riscv64-elf-gcc'),
               'opensbi':('cc','make','clang','lld','dtc')}
        execute(output/'build.sh', ['--package-only'] if getattr(args,'rootfs_only',False) else (),
                required=roles[args.target],run=run)
        actual.record=True;actual.pack_only=True
        if args.target=='opensbi':module.pack_output(output,actual.osb_name)
        else:module.build(actual,run=run)
    run.finish(output_dir=str(output));return None


def output_view(store,run,args,ref):
    if not args.output_dir or not ref:return
    if ref['component']!='system':
        import shutil
        shutil.copytree(store.path(ref),args.output_dir.resolve(),dirs_exist_ok=True)
        run.result['output_dir']=str(args.output_dir.resolve());run.save();return
    from .system import materialize
    from scripts.build_records import source_identity,register,now,write_json
    from scripts.build_recipe import create_recipe
    output=materialize(store,ref,args.output_dir.resolve())
    source=source_identity(store.root);source.update(kind='captured',created_at=now())
    # New recipes capture mini's canonical invocation; old recipes remain tied
    # to their historical checkout and are never rewritten to today's flags.
    v=read(output/'validation.json')
    cpu=Path(v['cpu_verilog']) if v.get('cpu_verilog') else None
    argv=configuration.configuration_arguments(v,args.source or store.root/'firmware/bios/main.c',cpu,'reproduced',
                                               synthesize=v.get('synthesis_requested',False))
    source['recipe']=str(create_recipe(output,source,argv,cpu,root=store.root,entrypoint='scripts/mini.py'))
    record=register(output,'build',source,root=store.root)
    write_json(output/'build-info.json',dict(id=record['id'],status=record['status'],source=source))
    run.result['output_dir']=str(output);run.result['build_id']=record['id'];run.save()
