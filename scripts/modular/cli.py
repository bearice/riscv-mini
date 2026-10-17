"""Task-oriented CLI for the modular build and portable artifact workflow."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from .storage import ROOT, Store, Run, read, write
from .system import import_legacy, build_firmware_system, export_bundle, verify_export, import_export, resolve_system, materialize
from .environment import discover, identity, linux_runner


def add_commands(sub):
    configure = sub.add_parser('configure', help='Automatically discover tools used by a target')
    configure.add_argument('--target', choices=('bios', 'gateware', 'cpu', 'linux'), default='bios')
    from .targets import add_targets
    for command in ('build', 'plan'):
        op = sub.add_parser(command, help='Build a target' if command=='build' else 'Resolve a target without execution')
        add_targets(op)
    release=sub.add_parser('release',help='Export, verify or create a release')
    releases=release.add_subparsers(dest='release_operation',required=True)
    create=releases.add_parser('create',help='Rebuild a clean committed source and archive a formal release')
    from scripts.mini_ops.release import add_arguments as release_arguments
    release_arguments(create)
    cat=sub.add_parser('builds',help='Discover or manage historical catalog artifacts')
    from scripts.mini_ops.catalog import add_arguments as catalog_arguments
    catalog_arguments(cat)
    worker=sub.add_parser('_worker',help=argparse.SUPPRESS)
    worker.add_argument('argv',nargs=argparse.REMAINDER)
    sub._choices_actions[:]=[item for item in sub._choices_actions if item.dest!='_worker']
    imp = sub.add_parser('import', help='Import a legacy build or portable artifact bundle without changing current')
    imp.add_argument('source')
    show = sub.add_parser('show')
    show.add_argument('system')
    view = sub.add_parser('materialize', help='Create a legacy board-tool view of an exact bundle')
    view.add_argument('system')
    view.add_argument('--output-dir', type=Path, required=True)
    export = releases.add_parser('export', help='Export a portable component/lock closure; no publication')
    export.add_argument('system')
    export.add_argument('--output-dir', type=Path, required=True)
    export.add_argument('--test-run', action='append', default=[])
    verify = releases.add_parser('verify')
    verify.add_argument('directory', type=Path)
    rep = sub.add_parser('reproduce', help='Verify and import exact release artifacts; --rebuild verifies source and tools before executing')
    rep.add_argument('directory', type=Path)
    rep.add_argument('--rebuild', action='store_true')
    test = sub.add_parser('test', help='Run an explicit host test command and record its exact system and result')
    test.add_argument('--system', required=True)
    test.add_argument('argv', nargs=argparse.REMAINDER)
    pin = sub.add_parser('pin', help='Create an explicit named modular reference')
    pin.add_argument('name')
    pin.add_argument('system')
    for command in (show, view, export, pin):
        from .machines import MACHINES
        command.add_argument('--machine', choices=MACHINES, help='Scope current/baseline; omitted means original 20K')
    compat = sub.add_parser('compat', help='Check directional hardware/software contract compatibility')
    compat.add_argument('--hardware', required=True)
    compat.add_argument('--software', required=True, type=Path, help='Software manifest.json or requirement JSON')
    clean = sub.add_parser('clean', help='Plan or delete expendable work/cache directories only')
    clean.add_argument('--execute', action='store_true')


def perform(args, p):
    if args.command=='_worker':
        from .worker import perform as worker
        return worker(args.argv)
    if args.command=='builds':
        from scripts.mini_ops.catalog import perform as catalog
        catalog(args,p);return 0
    if args.command=='release':
        if args.release_operation=='create':
            from scripts.mini_ops.release import perform as create
            create(args,p);return 0
        args.command=args.release_operation
    store = Store()
    operation=args.command+'-'+args.purpose if args.command in ('build','plan') else args.command
    run = Run('test' if args.command == 'test' else 'build', operation, vars_serializable(args))
    print('Run:', run.path, flush=True)
    session = None
    session_acquired = False
    try:
        from .storage import workspace_lock
        if not (args.command in ('build','plan') and args.target=='matrix'):
            session = workspace_lock(store.build/'work/.session-lock')
            session.__enter__()
            session_acquired = True
        if args.command == 'configure':
            if args.target == 'linux':
                environment = linux_runner(['cc', 'make', 'clang', 'lld', 'dtc'])
            else:
                from .firmware import ROLES
                roles = {'bios': ROLES,
                         'gateware': ['python','gowin'], 'cpu': ['python','java','sbt-launch']}[args.target]
                environment = discover(roles)
            write(run.path / 'environment.json', environment)
            write(store.build / 'refs/configured.json', dict(target=args.target, run=run.id))
            run.finish(environment=environment)
            print(__import__('json').dumps(environment, indent=2))
        elif args.command == 'import':
            source = Path(args.source)
            ref = import_export(store, source) if (source / 'release.lock.json').is_file() else import_legacy(store, args.source, run)
            run.finish(system=ref)
        elif args.command in ('build', 'plan'):
            from .targets import execute, output_view
            ref=execute(store,run,args)
            if args.command=='build' and not args.dry_run:
                output_view(store,run,args,ref)
        elif args.command in ('show', 'materialize', 'export', 'pin'):
            from .machines import select_hardware, check_system, reference_path, LEGACY
            selection = select_hardware(store, args.system, args.machine)
            ref = resolve_system(store, selection)
            if ref is None:
                if args.machine and args.machine != LEGACY:
                    raise ValueError('No AE350 system bundle exists; P0 artifacts are descriptions only')
                ref = import_legacy(store, selection, run)
            check_system(store, ref, args.machine)
            if args.command == 'show':
                print(__import__('json').dumps(read(store.path(ref) / 'bundle.lock.json'), indent=2))
            elif args.command == 'materialize':
                materialize(store, ref, args.output_dir)
            elif args.command == 'export':
                export_bundle(store, ref, args.output_dir, args.test_run)
            elif args.command == 'pin':
                if args.name not in ('current', 'baseline'):
                    raise ValueError('Only current/baseline references can be pinned')
                old = reference_path(store, args.name, args.machine)
                if old.is_file(): write(run.path / 'previous-ref.json', read(old))
                # Preserve old catalog references until the user explicitly pins.
                write(old, dict(system=ref, selected_by_run=run.id))
            run.finish(system=ref)
        elif args.command == 'verify':
            if (args.directory/'release.lock.json').is_file():
                run.finish(release=verify_export(args.directory))
            else:
                from scripts.mini_ops.release import verify_bundle
                run.finish(release=verify_bundle(args.directory))
        elif args.command == 'reproduce':
            ref = import_export(store, args.directory)
            if args.rebuild:
                from .replay import rebuild_preserved
                ref = rebuild_preserved(store, ref, run)
            run.finish(system=ref, mode='rebuild' if args.rebuild else 'verified-artifact-restore')
        elif args.command == 'test':
            ref = resolve_system(store, args.system)
            if ref is None: raise ValueError('Import the system before testing')
            command = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
            if not command: raise ValueError('A test command is required after --')
            run.result.update(system=ref, command=command)
            with (run.path / 'test.log').open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            run.result.update(passed=result.returncode == 0, returncode=result.returncode)
            run.save()
            if result.returncode: raise RuntimeError('Test failed; see ' + str(run.path / 'test.log'))
        elif args.command == 'compat':
            from .contracts import check
            ref = resolve_system(store, args.hardware)
            if ref is None: ref=import_legacy(store,args.hardware,run)
            parts=read(store.path(ref)/'bundle.lock.json')['components']
            provider=store.get(parts['soc-description'])['config']['contract']
            data=read(args.software)
            consumer=data.get('config',{}).get('requires',data)
            result=check(provider,consumer)
            run.result.update(compatibility=result);run.save()
            if not result['compatible']:raise ValueError('; '.join(result['problems']))
            run.finish(system=ref)
        elif args.command == 'clean':
            import shutil
            candidates=[]
            for directory in ('work','cache'):
                base=(store.build/directory).resolve()
                if base.is_dir():
                    candidates.extend(p for p in base.iterdir() if p.is_dir() and not p.name.startswith('.'))
            run.result['cleanup_plan']=[str(p) for p in candidates];run.save()
            for path in candidates:
                resolved=path.resolve()
                if not any(resolved.is_relative_to((store.build/n).resolve()) for n in ('work','cache')):
                    raise ValueError('Cleanup path escapes disposable workspace')
                print(('Cleaning ' if args.execute else 'Would clean ')+str(path))
                if args.execute:
                    # Session lock excludes other modular builds during cleanup.
                    shutil.rmtree(path)
            run.finish(executed=args.execute)
        print('Result:', run.path / 'result.json', flush=True)
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        run.result.update(passed=False,error=str(error)); run.save()
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if session_acquired:
            session.__exit__(None,None,None)


def vars_serializable(args):
    return {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
