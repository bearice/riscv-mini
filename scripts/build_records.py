"""Build identities and a filesystem catalog. Existing artifacts stay in place."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIRS = ('gateware', 'firmware', 'scripts', 'tests', 'sim')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def source_identity(root=ROOT):
    """Hash build inputs, including dirty/untracked code, independently of docs."""
    names = set(git('ls-files', '-z', root=root).decode().split('\0'))
    names.update(git('ls-files', '--others', '--exclude-standard', '-z', root=root).decode().split('\0'))
    digest = hashlib.sha256()
    for name in sorted(names):
        if not (name.startswith(tuple(p+'/' for p in INPUT_DIRS)) or name in ('requirements.lock', 'requirements.txt', 'VERSIONS.yaml')):
            continue
        path = root / name
        digest.update(name.encode() + b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'<deleted>')
    return dict(commit=git('rev-parse', 'HEAD', root=root).decode().strip(),
                dirty=bool(git('status', '--porcelain', root=root)),
                inputs_sha256=digest.hexdigest())


def slug(text):
    return re.sub(r'[^a-z0-9-]+', '-', text.lower()).strip('-')[:100] or 'build'


def configuration_name(profile, isa, rom, l2):
    return f'{profile}-{isa.split("_")[0]}-rom{rom // 1024}k-l2{l2 // 1024}k'


def run_path(profile, isa, rom, l2, purpose, root=ROOT):
    source = source_identity(root)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    version = source['commit'][:7] + ('-dirty' if source['dirty'] else '')
    name = f'{timestamp}-{version}-{configuration_name(profile, isa, rom, l2)}-{slug(purpose)}'
    return root / 'build/runs' / name, source


def artifact_identity(output):
    output = Path(output).resolve()
    v = json.loads((output / 'validation.json').read_text(encoding='utf-8'))
    image = output / 'firmware/app.img'
    if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != v['boot_image']['sha256']:
        raise ValueError('Application image differs from validation.json')
    if v.get('synthesis_requested'):
        fs = output / 'gateware/riscv_mini.fs'
        if not fs.is_file() or hashlib.sha256(fs.read_bytes()).hexdigest() != v.get('bitstream_sha256'):
            raise ValueError('Bitstream differs from validation.json')
    return v


def pnr_passed(v):
    return (v.get('synthesis_requested') is True
            and v.get('timing_violated_endpoints') == {'setup': 0, 'hold': 0})


def register(output, purpose, source=None, root=ROOT, label=None):
    output = Path(output).resolve()
    v = artifact_identity(output)
    config = configuration_name(v['profile'], v['isa'], v['rom_size_bytes'], v['l2_size_bytes'])
    identity = v.get('bitstream_sha256') or v['boot_image']['sha256']
    version = 'v'+source['version'] if source and source.get('version') else (source or {}).get('commit', 'unknown')[:7]
    record_id = f'{version}-{config}-{slug(purpose)}-{identity[:8]}'
    if (source or {}).get('kind') == 'captured':
        record_id += '-' + hashlib.sha256(str(output).encode()).hexdigest()[:8]
    path = root / 'build/catalog' / (record_id + '.json')
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    record = dict(id=record_id, label=label or purpose, path=str(output),
                  purpose=purpose, registered_at=old.get('registered_at', now()),
                  source=source or {'kind': 'unknown'}, configuration={k: v.get(k) for k in
                  ('profile', 'isa', 'rom_size_bytes', 'l2_size_bytes', 'memory_system',
                   'clock_hz', 'ddr_clock_hz', 'place_option', 'route_option', 'features')},
                  image_sha256=v['boot_image']['sha256'], bitstream_sha256=v.get('bitstream_sha256'),
                  abi=f"{v['boot_image']['abi_tag']:08x}",
                  component_versions=v.get('component_versions'),
                  status='pnr-pass' if pnr_passed(v) else 'pnr-fail' if v.get('synthesis_requested') else 'generated',
                  evidence=old.get('evidence', []))
    if old.get('status') == 'board-qualified':
        record['status'] = 'board-qualified'
    write_json(path, record)
    refresh(root)
    return record


def records(root=ROOT):
    return sorted((json.loads(p.read_text(encoding='utf-8')) for p in (root / 'build/catalog').glob('*.json')),
                  key=lambda r: r['registered_at'])


def get_record(name, root=ROOT):
    ref = root / 'build/refs' / (name + '.json')
    if ref.exists():
        name = json.loads(ref.read_text(encoding='utf-8'))['id']
    if name.startswith('latest-'):
        eligible = [r for r in records(root) if r['source'].get('kind') == 'captured' and r['status'] not in ('cleaned','cleanup-failed')
                    and (name == 'latest-generated' or r['status'] in ('pnr-pass', 'board-qualified'))]
        if name not in ('latest-generated', 'latest-pnr') or not eligible:
            raise ValueError(f'No {name}; use list or an explicit reference')
        record = eligible[-1]
    else:
        matches = [r for r in records(root) if r['id'] == name]
        if len(matches) != 1:
            raise ValueError(f'Unknown build/reference: {name}')
        record = matches[0]
    v = artifact_identity(record['path'])
    if v.get('bitstream_sha256') != record['bitstream_sha256'] or v['boot_image']['sha256'] != record['image_sha256']:
        raise ValueError('Catalog record is stale: artifacts were replaced')
    return record


def pin(name, record_id, evidence=None, root=ROOT):
    if name not in ('current', 'baseline'):
        raise ValueError('Reference must be current or baseline')
    record = get_record(record_id, root)
    v = artifact_identity(record['path'])
    if not pnr_passed(v):
        raise ValueError('References require PnR setup/hold 0/0')
    if evidence:
        evidence = Path(evidence).resolve()
        e = json.loads(evidence.read_text(encoding='utf-8'))
        if (e.get('passed') is not True or e.get('bitstream_sha256') != record['bitstream_sha256']
                or e.get('image_sha256') != record['image_sha256']):
            raise ValueError('Board evidence must pass and match both bitstream and application hashes')
        record['evidence'].append(dict(path=str(evidence), sha256=hashlib.sha256(evidence.read_bytes()).hexdigest()))
        record['status'] = 'board-qualified'
        write_json(root / 'build/catalog' / (record['id'] + '.json'), record)
    if record['status'] != 'board-qualified':
        raise ValueError('Pin requires matching board acceptance evidence')
    write_json(root / 'build/refs' / (name + '.json'), dict(id=record['id'], path=record['path'], selected_at=now()))
    refresh(root)
    return record


def refresh(root=ROOT):
    build = root / 'build'
    build.mkdir(exist_ok=True)
    lines = ['# 构建导航', '', '此索引由 scripts/builds.py refresh 生成。目录更新时间不表示实板验收。', '',
             '## 固定版本', '']
    for name in ('current', 'baseline'):
        ref = build / 'refs' / (name + '.json')
        if ref.exists():
            value = json.loads(ref.read_text(encoding='utf-8'))
            lines.append(f"- **{name}**：`{value['id']}` → `{value['path']}`")
    lines += ['', '## 已登记构建', '', '| ID / 用途 | 状态 | 源码身份 | 原路径 |', '| --- | --- | --- | --- |']
    for r in reversed(records(root)):
        source = r['source']
        version = source.get('commit', 'unknown')[:7] + ' / ' + source.get('kind', 'unknown')
        lines.append(f"| {r['id']} / {r['label']} | {r['status']} | {version} | {r['path']} |")
    lines += ['', '## 目录约定', '', '- runs/：新构建；UTC 时间、Git 版本/dirty、配置和用途组成目录名。',
              '- catalog/：逐份构建的身份、路径、状态和验收证据；索引的事实来源。',
              '- refs/：current 为明确选择的实板版本，baseline 为明确选择的性能基线。',
             '- reports/：提交检查和性能测量；性能不是构建成功的同义词。',
             '- releases/v<版本>/<配置>/：正式代码发布；版本、Git tag、二进制、报告和参数一一对应。',
             '- archives/v<版本>-<Git版本>/<配置>/：代码提交快照，保留最终产物但不占用发布版本、不进入 changelog。',
             '- recipes/：独立保留复现参数、CPU RTL、Git patch 和未跟踪源码；清理 runs 不清理它。',
              '- 旧实验、CPU RTL 和工具链目录保留原路径；详细分类见 legacy-index.md。',
              '- latest-generated/latest-pnr 仅选择新流程捕获源码身份的构建；历史登记时间不冒充构建时间。', '']
    (build / 'README.md').write_text('\n'.join(lines), encoding='utf-8')


def legacy_index(root=ROOT):
    """Inventory legacy trees without invalidating absolute paths in FPGA projects."""
    groups = {'CPU / 工具链': {'cpu-features','cpu-fence','cpu-qualification','opensbi-cpu-source','opensbi-jdk','vendor'},
              '历史性能实验': {'benchmark','cache-bench','crossbar','memory-opt','libc-sd','l2-combine','fence-combine'},
              '已登记版本所在系列': {'c-fit','cache-boot'},
              '流程目录': {'runs','catalog','refs','reports','releases','archives','recipes','replays'}}
    lines = ['# 历史构建目录分类', '', '旧目录保留绝对路径依赖；当前/基线通过 refs 和 README 查询，不按目录名字猜测。', '']
    for path in sorted((root / 'build').iterdir()):
        if not path.is_dir() or path.name.startswith('.'):
            continue
        category = next((label for label, names in groups.items() if path.name in names), '其他历史实验 / 未登记')
        lines.append(f'- `{path.name}/`：{category}')
    (root / 'build/legacy-index.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
