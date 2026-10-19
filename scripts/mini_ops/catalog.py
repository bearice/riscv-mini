"""List, register and select immutable FPGA build identities; never programs hardware."""
import argparse
import json
import shutil
import os
import stat
import sys
from pathlib import Path
from build_records import ROOT, get_record, legacy_index, pin, records, refresh, register
sys.path.insert(0, str(ROOT))
from scripts.build_recipe import reproduce, verify_recipe


def clean_work(execute=False, root=ROOT):
    """Drop rebuildable workspace caches; never touch immutable stores or receipts."""
    from build_records import write_json
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
    reserved = {'worktrees', 'replays', 'hardware', '.session-lock', '.store-lock', '.publish'}
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
        deleted = []
        for name in targets:
            target = Path(name)
            if not target.resolve().is_relative_to(work) or target.resolve() == work:
                raise ValueError('Unsafe work cleanup target')
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted.append(name)
            except OSError as error:
                write_json(path.with_name('work-result.json'),
                           dict(plan, passed=False, deleted=deleted, error=str(error), failed_target=name))
                raise
        write_json(path.with_name('work-result.json'), dict(plan, passed=True, deleted=deleted))
    return plan


def clean_build(record_id, execute=False, root=ROOT):
    if record_id == 'work':
        return clean_work(execute, root)
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
        from build_records import write_json
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
    cleanup.add_argument('id', help='Registered intermediate build ID, or work for rebuildable caches')
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
