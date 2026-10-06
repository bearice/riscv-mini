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


def clean_build(record_id, execute=False, root=ROOT):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
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
    cleanup.add_argument('id')
    cleanup.add_argument('--execute', action='store_true', help='Default is a dry run; releases and pinned builds are protected')
    replay = sub.add_parser('reproduce')
    replay.add_argument('id')
    replay.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'list':
            for r in reversed(records()):
                print(f"{r['status']:16} {r['id']}\n  {r['path']}")
        elif args.command == 'refresh':
            refresh(); legacy_index(); print(ROOT / 'build/README.md')
        elif args.command in ('path', 'show'):
            r = get_record(args.name)
            print(r['path'] if args.command == 'path' else json.dumps(r, ensure_ascii=False, indent=2))
        elif args.command == 'register':
            source = dict(kind='historical-association', commit=args.commit) if args.commit else None
            print(register(args.path, args.purpose, source, label=args.label)['id'])
        elif args.command == 'pin':
            print(pin(args.name, args.id, args.evidence)['path'])
        elif args.command == 'clean':
            clean_build(args.id, args.execute)
        else:
            matches = [r for r in records() if r['id']==args.id]
            if len(matches)!=1:raise ValueError('Unknown catalog ID')
            print(reproduce(matches[0]['source']['recipe'],sys.executable,args.output_dir))
            info=json.loads((args.output_dir/'build-info.json').read_text(encoding='utf-8'))
            print(register(args.output_dir,'reproduced',info['source'])['id'])
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
