"""Keep replay inputs outside disposable build directories."""
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from scripts.build_records import ROOT, INPUT_DIRS, git, write_json


def create_recipe(output, source, arguments, cpu=None, root=ROOT):
    key = output.name + '-' + hashlib.sha256(str(output).encode()).hexdigest()[:8]
    recipe = root / 'build/recipes' / key
    if recipe.exists():
        recipe = recipe.with_name(key+'-'+uuid.uuid4().hex[:8])
    recipe.mkdir(parents=True)
    selectors = [*INPUT_DIRS, 'requirements.lock', 'requirements.txt', 'VERSION']
    (recipe/'source.patch').write_bytes(git('diff', '--binary', 'HEAD', '--', *selectors, root=root))
    untracked = []
    for name in git('ls-files', '--others', '--exclude-standard', '-z', root=root).decode().split('\0'):
        if not name or not (name.startswith(tuple(p+'/' for p in INPUT_DIRS)) or name in selectors[len(INPUT_DIRS):]):
            continue
        target = recipe/'untracked'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root/name, target)
        untracked.append(name)
    arguments = [str(a) for a in arguments]
    if cpu:
        cpu = Path(cpu).resolve()
        folder = recipe/'inputs/cpu'; folder.mkdir(parents=True)
        for p in (cpu, cpu.with_suffix('.yaml'), cpu.parent/'generator.json'):
            if p.is_file():shutil.copy2(p, folder/p.name)
        arguments[arguments.index('--cpu-verilog')+1] = '$RECIPE/inputs/cpu/'+cpu.name
    prefix = str(root).replace('\\', '/')+'/'
    arguments = [a.replace('\\', '/').replace(prefix, '$ROOT/') for a in arguments]
    files = {str(p.relative_to(recipe)).replace('\\', '/'):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in recipe.rglob('*') if p.is_file()}
    write_json(recipe/'recipe.json',dict(source=source, arguments=arguments, untracked=untracked, files=files))
    return recipe


def verify_recipe(recipe):
    recipe = Path(recipe).resolve()
    data = json.loads((recipe/'recipe.json').read_text(encoding='utf-8'))
    for name, digest in data['files'].items():
        p = (recipe/name).resolve()
        if not p.is_relative_to(recipe) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('Replay input changed: '+name)
    return data


def reproduce(recipe, python, output, root=ROOT):
    """Replay a commit plus captured dirty patch in a separate detached checkout."""
    recipe = Path(recipe).resolve(); data = verify_recipe(recipe)
    checkout = root/'build/replays'/uuid.uuid4().hex[:12]
    checkout.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git','worktree','add','--detach',str(checkout),data['source']['commit']],cwd=root,check=True)
    if (recipe/'source.patch').stat().st_size:
        subprocess.run(['git','apply','--binary',str(recipe/'source.patch')],cwd=checkout,check=True)
    for name in data['untracked']:
        target = (checkout/name).resolve()
        if not target.is_relative_to(checkout):raise ValueError('Untracked replay path escapes checkout')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(recipe/'untracked'/name, target)
    # Tool paths are machine-local; configure the same versions on another host.
    shutil.copy2(root/'.tools.local.json',checkout/'.tools.local.json')
    arguments = [a.replace('$ROOT',str(checkout)).replace('$RECIPE',str(recipe)) for a in data['arguments']]
    subprocess.run([str(python),str(checkout/'scripts/build.py'),*arguments,'--output-dir',str(Path(output).resolve())],cwd=checkout,check=True)
    return checkout
