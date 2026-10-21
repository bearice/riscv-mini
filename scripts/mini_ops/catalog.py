"""List, register and select immutable FPGA build identities; never programs hardware."""
import argparse
import json
import shutil
import os
import stat
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.tools.build_records import ROOT, get_record, legacy_index, pin, records, refresh, register
sys.path.insert(0, str(ROOT))
from scripts.tools.build_recipe import reproduce, verify_recipe


def clean_work(execute=False, root=ROOT):
    """Drop rebuildable workspace caches; never touch immutable stores or receipts."""
    from scripts.tools.build_records import write_json
    root = Path(root).resolve()
    work = (root / 'build/work').resolve()
    if not work.is_relative_to(root / 'build'):
        raise ValueError('Work cache escapes build directory')
    protected = []
    for record in records(root):
        path = Path(record['path']).resolve()
        if path.is_relative_to(work):
            protected.append(path)
    if (root / '.git').exists():
        import subprocess
        result = subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=root,
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if line.startswith('worktree '):
                protected.append(Path(line[9:]).resolve())
    # Git-managed checkouts and acquisition/lock roots are not scratch output.
    reserved = {'worktrees', 'replays', 'hardware', '.session-lock', '.store-lock', '.publish',
                '.maintenance-lock', '.implementation-index-lock'}
    targets, kept = [], []
    if work.exists():
        for target in sorted(work.iterdir()):
            resolved = target.resolve()
            if (not resolved.is_relative_to(work) or target.is_symlink() or
                    target.name in reserved or (target / '.git').exists() or
                    any(p == resolved or p.is_relative_to(resolved) for p in protected)):
                kept.append(str(target)); continue
            targets.append(str(resolved))
    plan = dict(kind='rebuildable-work-cache', targets=targets, preserved=kept,
                protected=['build/store', 'build/runs', 'build/archives', 'build/releases',
                           'build/recipes', 'build/refs', 'build/vendor', 'build/reports'])
    path = root / 'build/reports/cleanup/work-plan.json'
    write_json(path, plan)
    print(('Cleaning' if execute else 'Would clean'), len(targets), 'work caches; plan:', path)
    if execute:
        from scripts.modular.storage import Store
        from scripts.modular.workspace import clean_workspaces
        deleted, errors = [], []
        for name in targets:
            target = Path(name)
            if not target.resolve().is_relative_to(work) or target.resolve() == work:
                raise ValueError('Unsafe work cleanup target')
            try:
                clean_workspaces(Store(root), [target])
                deleted.append(name)
            except (OSError, ValueError) as error:
                errors.append(dict(target=name, error=str(error)))
                # Preserve access-denied roots; do not stop other independent
                # cache cleanup or rewrite ACLs/attributes to force deletion.
        write_json(path.with_name('work-result.json'),
                   dict(plan, passed=not errors, deleted=deleted, retained_errors=errors))
        if errors:
            raise RuntimeError('Cleaned %d cache roots; retained %d inaccessible roots; see work-result.json' %
                               (len(deleted), len(errors)))
    return plan


def clean_build(record_id, execute=False, root=ROOT):
    if record_id == 'work':
        return clean_work(execute, root)
    if record_id == 'obsolete':
        from scripts.tools.build_cleanup import clean
        return clean(root, execute)
    matches=[r for r in records(root) if r['id']==record_id]
    if len(matches)!=1:raise ValueError('Unknown catalog ID')
    record=matches[0] if matches[0]['status']=='cleanup-failed' else get_record(record_id, root)
    output = Path(record['path']).resolve()
    runs = (root/'build/runs').resolve()
    if not output.is_relative_to(runs) or output == runs:
        raise ValueError('Only registered intermediate directories inside build/runs may be cleaned')
    for ref in (root/'build/refs').glob('*.json'):
        pinned=json.loads(ref.read_text(encoding='utf-8'))
        if pinned.get('id') == record_id or pinned.get('path') and Path(pinned['path']).resolve()==output:
            raise ValueError('Pinned build cannot be cleaned')
    recipe = record['source'].get('recipe')
    if not recipe:raise ValueError('No preserved replay recipe')
    verify_recipe(recipe)
    print(('Cleaning ' if execute else 'Would clean ')+str(output)+'; recipe preserved at '+recipe)
    if execute:
        def remove_readonly(function,path,error):
            if not isinstance(error[1],PermissionError) or not Path(path).resolve().is_relative_to(output):raise error[1]
            os.chmod(path,os.stat(path).st_mode | stat.S_IWRITE)
            function(path)
        from scripts.tools.build_records import write_json
        try:shutil.rmtree(output,onerror=remove_readonly)
        except OSError:
            for alias in records(root):
                if Path(alias['path']).resolve()==output:
                    write_json(root/'build/catalog'/(alias['id']+'.json'),dict(alias,status='cleanup-failed'))
            raise
        for alias in records(root):
            if Path(alias['path']).resolve()==output:
                alias['status']='cleaned'
                write_json(root/'build/catalog'/(alias['id']+'.json'),alias)
        refresh(root)


def add_arguments(parser):
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('list')
    sub.add_parser('refresh')
    lookup = sub.add_parser('path')
    lookup.add_argument('name', help='current, baseline, latest-generated, latest-pnr or exact ID')
    show = sub.add_parser('show')
    show.add_argument('name')
    add = sub.add_parser('register')
    add.add_argument('--path', type=Path, required=True)
    add.add_argument('--purpose', required=True)
    add.add_argument('--commit', help='Historical association, not proof of a clean source snapshot')
    add.add_argument('--label')
    select = sub.add_parser('pin')
    select.add_argument('name', choices=('current', 'baseline'))
    select.add_argument('id')
    select.add_argument('--evidence', type=Path, required=True)
    cleanup = sub.add_parser('clean')
    cleanup.add_argument('id', help='Registered intermediate build ID, work for rebuildable caches, or obsolete for unreferenced outputs')
    cleanup.add_argument('--execute', action='store_true', help='Default is a dry run; releases and pinned builds are protected')
    replay = sub.add_parser('reproduce')
    replay.add_argument('id')
    replay.add_argument('--output-dir', type=Path, required=True)


def perform(args, parser):
    parser=parser
    try:
        if args.operation == 'list':
            for r in reversed(records()):
                print(f"{r['status']:16} {r['id']}\n  {r['path']}")
        elif args.operation == 'refresh':
            refresh(); legacy_index(); print(ROOT / 'build/README.md')
        elif args.operation in ('path', 'show'):
            r = get_record(args.name)
            print(r['path'] if args.operation == 'path' else json.dumps(r, ensure_ascii=False, indent=2))
        elif args.operation == 'register':
            source = dict(kind='historical-association', commit=args.commit) if args.commit else None
            print(register(args.path, args.purpose, source, label=args.label)['id'])
        elif args.operation == 'pin':
            print(pin(args.name, args.id, args.evidence)['path'])
        elif args.operation == 'clean':
            clean_build(args.id, args.execute)
        else:
            matches = [r for r in records() if r['id']==args.id]
            if len(matches)!=1:raise ValueError('Unknown catalog ID')
            print(reproduce(matches[0]['source']['recipe'],sys.executable,args.output_dir))
            info=json.loads((args.output_dir/'build-info.json').read_text(encoding='utf-8'))
            print(register(args.output_dir,'reproduced',info['source'])['id'])
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, str(error)+'\n')
