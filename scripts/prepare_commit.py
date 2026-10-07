"""Record reviewed changes and validation; keep Git scope checks local. Never commits."""
import argparse
import hashlib
import json
from pathlib import Path
from build_records import ROOT, get_record, git, now, source_identity, write_json

BOOKKEEPING = ('CHANGELOG.md', 'reports/changes/')


def review_receipt(output):
    key=hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:8]
    return ROOT/'build/reports/commit-checks'/(output.stem+'-'+key+'.json')


def scope_identity(value):
    return {name:value[name] for name in ('base','target','patch_sha256')}


def scope(revision=None, root=ROOT):
    def read(*args):return git(*args,root=root)
    if revision:
        base = read('rev-parse', revision+'^').decode().strip()
        target = read('rev-parse', revision).decode().strip()
        arguments = [base, target]
    else:
        base = read('rev-parse', 'HEAD').decode().strip()
        target = 'staged'
        arguments = ['--cached', base]
    exclusions = [':(exclude)'+p+'**' if p.endswith('/') else ':(exclude)'+p for p in BOOKKEEPING]
    selectors = ['--', '.', *exclusions]
    patch = read('diff', '--binary', '--no-ext-diff', *arguments, *selectors)
    names = read('diff', '--name-status', '--no-renames', *arguments, *selectors).decode().splitlines()
    if not names:
        raise ValueError('Empty scope; stage intended implementation/docs before preparing a commit')
    return dict(base=base, target=target, patch_sha256=hashlib.sha256(patch).hexdigest(),
                files=[dict(status=n.split('\t')[0], path=n.split('\t')[1]) for n in names])


def changelog_entry(title, changes, record, performance, checks):
    identity = 'v'+record['release_version']
    lines = [f"## {identity} — {title}", '']
    lines += ['- '+text for text in changes]
    if performance:
        lines += ['', f"性能报告：[{performance.as_posix()}]({performance.as_posix()})。基线必须显式选择，详见报告身份和边界。"]
    if checks:
        lines += ['', f"提交检查：{checks.get('tests_run', 'recorded')} tests，PASS（源码输入指纹匹配）。"]
    lines += ['', '发布二进制、Git hash、构建参数和逐文件 SHA256 见对应版本的 release.json；完整改动以 Git 记录为准，已知失败和未验收项保留在性能/验收报告中。', '']
    return '\n'.join(lines)


def prepare(title, changes, revision=None, build=None, performance=None, checks=None):
    selected = scope(revision)
    # Only RTL (gateware) changes re-qualify the hardware: they affect timing
    # and resources, so they need a captured board-qualified build and a
    # performance baseline. With RTL unchanged the prior hardware qualification
    # still holds, so software-only changes (bootloader/BIOS/drivers/HAL under
    # firmware/) skip every hardware-related test and are checked on the host.
    rtl = any(f['path'].startswith('gateware/') for f in selected['files'])
    if not revision:
        if not checks or checks.get('passed') is not True or checks.get('source_inputs_sha256') != source_identity()['inputs_sha256']:
            raise ValueError('Matching successful check record required; rerun checks after code edits')
        if rtl:
            if not build or build['status'] != 'board-qualified' or build['source'].get('kind') != 'captured':
                raise ValueError('RTL/gateware changes require a new captured and board-qualified build')
            if build['source']['inputs_sha256'] != source_identity()['inputs_sha256']:
                raise ValueError('Build inputs no longer match the worktree')
            if not performance:
                raise ValueError('RTL/gateware changes require a performance report')
    if performance:
        data = json.loads(performance.with_suffix('.json').read_text(encoding='utf-8'))
        if build and (data['candidate']['bitstream_sha256'] != build['bitstream_sha256'] or
                      data['candidate']['image_sha256'] != build['image_sha256']):
            raise ValueError('Performance report candidate does not match selected build')
    return dict(created_at=now(), title=title, changes=changes, scope=selected,
                build=build, performance_report=str(performance) if performance else None,
                checks=checks, mode='historical commit documentation' if revision else 'staged review')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--title', required=True)
    p.add_argument('--change', action='append', required=True, help='Reviewed behavior change; not inferred from filenames')
    p.add_argument('--revision', help='Document an existing commit instead of the staged scope')
    p.add_argument('--build', help='Catalog identity/reference associated with this scope')
    p.add_argument('--performance-report', type=Path)
    p.add_argument('--checks', type=Path)
    p.add_argument('--output', type=Path, required=True, help='Tracked change-record Markdown; JSON beside it')
    p.add_argument('--write-changelog', action='store_true')
    p.add_argument('--release-version',help='Only code releases enter CHANGELOG; documentation/workflow commits omit this and --write-changelog')
    p.add_argument('--verify', action='store_true', help='Compare current scope with existing change record')
    a = p.parse_args()
    if a.write_changelog and not a.release_version:p.error('--write-changelog requires an explicit --release-version; other commits use scope records only')
    if a.verify:
        saved = json.loads(review_receipt(a.output).read_text(encoding='utf-8'))
        if saved != scope_identity(scope(a.revision)):
            p.exit(1, 'Scope changed; regenerate change record and changelog before committing\n')
        print('Commit scope unchanged'); return
    record = prepare(a.title, a.change, a.revision, get_record(a.build) if a.build else None,
                     a.performance_report, json.loads(a.checks.read_text(encoding='utf-8')) if a.checks else None)
    if a.release_version:
        from release import release_version
        record['release_version']=release_version(a.release_version)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    identity=scope_identity(record.pop('scope'))
    write_json(review_receipt(a.output),identity)
    record['base_revision']=identity['base']
    record['source_revision']=identity['target'] if identity['target']!='staged' else None
    lines = ['# '+a.title, '', f"生成：{record['created_at']}；模式：{record['mode']}。", '',
             '## 改动', '', *['- '+text for text in a.change]]
    if record['checks']:
        lines += ['', '## 验证', '', f"对应源码的检查：{record['checks'].get('tests_run','recorded')} 项，PASS；命令和输入身份见同名 JSON。"]
    if a.performance_report:lines += ['', f'性能依据：`{a.performance_report.as_posix()}`。']
    lines += ['', '完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。', '']
    a.output.write_text('\n'.join(lines), encoding='utf-8')
    write_json(a.output.with_suffix('.json'), record)
    if a.write_changelog:
        path = ROOT / 'CHANGELOG.md'
        old = path.read_text(encoding='utf-8') if path.exists() else '# Changelog\n\n'
        heading = '## v'+record['release_version']+' — '
        # Replace only the entry for this target; keep all other version records.
        start = old.find(heading)
        if start >= 0:
            end = old.find('\n## ', start+len(heading))
            old = old[:start] + (old[end+1:] if end >= 0 else '')
        header, body = old.split('\n', 1)
        entry = changelog_entry(a.title, a.change, record, a.performance_report, record['checks'])
        path.write_text((header+'\n\n'+entry+'\n'+body.lstrip()).rstrip()+'\n', encoding='utf-8')
    print(a.output)


if __name__ == '__main__':
    main()
