"""Compare explicit build identities and checksum-verified BIOS measurements."""
import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path
from build_records import artifact_identity, get_record, now, write_json


def load_measurements(directories, validation, allow_legacy=False):
    groups, sources, underflows = {}, [], {}
    for directory in directories:
        directory = Path(directory).resolve()
        path = directory / 'results.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        if raw['clock_hz'] != validation['clock_hz']:
            raise ValueError('Measurement clock differs from build clock')
        identity = raw.get('build_identity')
        if identity:
            if (identity['bitstream_sha256'] != validation['bitstream_sha256']
                    or identity['image_sha256'] != validation['boot_image']['sha256']):
                raise ValueError('Measurement belongs to a different build/image')
        elif not allow_legacy:
            raise ValueError('Legacy results have no build identity; use explicit --allow-legacy-results after checking upload logs')
        if not raw.get('rows'):
            raise ValueError('Empty measurements cannot produce a performance report')
        for row in raw['rows']:
            if row.get('passed') is not True or row['ticks'] <= 0 or row['count'] <= 0 or row['size'] <= 0:
                raise ValueError('Failed/zero measurement cannot be counted as performance')
            groups.setdefault((row['name'], row['size']), []).append(row)
        sources.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            identity='recorded' if identity else 'operator-associated legacy'))
        uart = directory / 'uart.log'
        if uart.exists():
            text = uart.read_text(errors='replace')
            if 'BENCH DONE FAIL' in text:
                raise ValueError('UART contains a failed suite')
            counts = [int(n, 16) for n in re.findall(r'underflows=([0-9a-f]+)', text)]
            underflows[directory.name] = counts
    return groups, sources, underflows


def compare(candidate, baseline, candidate_dirs, baseline_dirs, allow_legacy=False):
    cv, bv = artifact_identity(candidate['path']), artifact_identity(baseline['path'])
    for key in ('clock_hz', 'ddr_clock_hz'):
        if cv[key] != bv[key]:
            raise ValueError(f'Different {key}; this report requires matched clocks')
    cg, cs, cu = load_measurements(candidate_dirs, cv, allow_legacy)
    bg, bs, bu = load_measurements(baseline_dirs, bv, allow_legacy)
    shared = sorted(cg.keys() & bg.keys())
    if not shared:
        raise ValueError('No matching benchmark workloads')
    rows = []
    for name, size in shared:
        old, new = bg[name, size], cg[name, size]
        if len({r['count'] for r in old + new}) != 1:
            raise ValueError(f'Workload count changed: {name}/{size}')
        metric = ('cycles/access' if name.startswith('cache.') else 'cycles/iteration' if name.startswith('cpu.')
                  else 'ns/load' if name == 'mem.chase32' else 'MiB/s')
        entry = dict(name=name, size=size, metric=metric)
        for label, group in (('baseline', old), ('candidate', new)):
            if len({r['round'] for r in group}) != len(group):
                raise ValueError('Duplicate rounds/workloads; supply each suite once')
            values = [r['ticks']/r['count'] if metric.startswith('cycles') else
                      r['ticks']/cv['clock_hz']*1e9/r['count'] if metric == 'ns/load' else
                      r['size']*r['count']*cv['clock_hz']/r['ticks']/1048576 for r in group]
            entry[label] = dict(median=statistics.median(values), minimum=min(values), maximum=max(values), samples=len(values))
        entry['change_percent'] = (entry['candidate']['median']/entry['baseline']['median']-1)*100
        rows.append(entry)
    config_keys = ('isa', 'rom_size_bytes', 'l2_size_bytes', 'memory_system', 'dma_backend',
                   'ddr_initialization', 'cpu_capabilities', 'features', 'place_option', 'route_option')
    differences = {k: dict(baseline=bv.get(k), candidate=cv.get(k)) for k in config_keys if bv.get(k) != cv.get(k)}
    return dict(created_at=now(), candidate=candidate, baseline=baseline, comparisons=rows,
                configuration_differences=differences, candidate_sources=cs, baseline_sources=bs,
                unmatched_candidate=[list(k) for k in sorted(cg.keys()-bg.keys())],
                unmatched_baseline=[list(k) for k in sorted(bg.keys()-cg.keys())],
                lcd_underflows=dict(baseline=bu, candidate=cu),
                method='Explicit build pairing; observed whole-version differences, not attribution to a single RTL change.')


def markdown(report, limitations):
    lines = ['# 性能测试报告', '', f"生成时间：{report['created_at']}。数值来自已保存测量，不表示本命令重新跑板。", '',
             '## 明确选择的版本', '', '| 项目 | 基线 | 候选 |', '| --- | --- | --- |']
    for key in ('id', 'path', 'abi', 'bitstream_sha256', 'image_sha256'):
        lines.append(f"| {key} | {report['baseline'].get(key)} | {report['candidate'].get(key)} |")
    lines += ['', '## 配置差异', '', '以下差异同时存在，变化百分比不能单独归因于 C 或 fence。', '']
    for key, value in report['configuration_differences'].items():
        lines.append(f"- {key}：`{value['baseline']}` → `{value['candidate']}`")
    lines += ['', '## 测量中位数', '', '| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |', '| --- | ---: | ---: | ---: | ---: |']
    for row in report['comparisons']:
        label = row['name'] if row['name'].startswith(('cpu.', 'cache.')) else f"{row['name']} ({row['size']} B)"
        lines.append(f"| {label} | {row['baseline']['median']:.3f} {row['metric']} | {row['candidate']['median']:.3f} {row['metric']} | {row['change_percent']:+.1f}% | {row['baseline']['samples']}/{row['candidate']['samples']} |")
    lines += ['', '周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。', '',
              '## 并发状态和边界', '']
    for version, counts in report['lcd_underflows'].items():
        lines.append(f'- LCD underflow {version}：`{counts}`（每个 UART 中的前后计数）。')
    lines += [f'- {text}' for text in limitations]
    lines += [f"- 未匹配候选项目：`{report['unmatched_candidate']}`；未匹配基线项目：`{report['unmatched_baseline']}`。", '',
              '## 原始数据', '']
    for version in ('baseline', 'candidate'):
        for source in report[version+'_sources']:
            lines.append(f"- {version}：`{source['path']}`，SHA256 `{source['sha256']}`，身份关联：{source['identity']}。")
    return '\n'.join(lines)+'\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', required=True, help='Catalog ID or current')
    p.add_argument('--baseline', required=True, help='Catalog ID or baseline; never inferred from dates')
    p.add_argument('--candidate-results', action='append', type=Path, required=True)
    p.add_argument('--baseline-results', action='append', type=Path, required=True)
    p.add_argument('--allow-legacy-results', action='store_true')
    p.add_argument('--limitation', action='append', default=[])
    p.add_argument('--output', type=Path, required=True, help='Markdown path; matching .json written beside it')
    a = p.parse_args()
    report = compare(get_record(a.candidate), get_record(a.baseline), a.candidate_results, a.baseline_results, a.allow_legacy_results)
    report['limitations'] = a.limitation
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(markdown(report, a.limitation), encoding='utf-8')
    write_json(a.output.with_suffix('.json'), report)
    print(a.output)


if __name__ == '__main__':
    main()
