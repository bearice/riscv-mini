"""Measure the linked boot ROM: LTO-inlined functions belong to their caller."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def analyze(elf):
    toolbin = Path(json.loads((ROOT/'.tools.local.json').read_text())['gcc']).parent
    symbols = subprocess.check_output([str(toolbin/'riscv-none-elf-nm.exe'), '-S', '--size-sort', str(elf)], text=True)
    sections = subprocess.check_output([str(toolbin/'riscv-none-elf-size.exe'), '-A', str(elf)], text=True)
    functions = []
    for row in symbols.splitlines():
        fields = row.split()
        if len(fields) == 4 and fields[2].lower() == 't':
            address, size, _, name = fields
            functions.append({'name': name, 'address': int(address, 16), 'bytes': int(size, 16)})
    sizes = {}
    for row in sections.splitlines():
        fields = row.split()
        if len(fields) == 3 and fields[0].startswith('.'):
            sizes[fields[0]] = int(fields[1])
    # Older start.S has no .size directive. Recover its text extent from the
    # first sized function, but label that inference rather than omitting it.
    if not any(f['name'] == '_start' for f in functions):
        functions.append({'name': '_start', 'address': 0,
                          'bytes': min(f['address'] for f in functions), 'inferred_extent': True})
    functions.sort(key=lambda f: f['bytes'], reverse=True)
    headers=subprocess.check_output([str(toolbin/'riscv-none-elf-objdump.exe'),'-h',str(elf)],text=True)
    ranges=[]
    for line in headers.splitlines():
        fields=line.split()
        if len(fields)>=5 and fields[1] in ('.text','.rodata','.data'):
            length=int(fields[2],16)
            if length:ranges.append((int(fields[4],16),length))
    section_bytes=sum(sizes.get(name,0) for name in ('.text','.rodata','.data'))
    rom_bytes=max(start+length for start,length in ranges)-min(start for start,_ in ranges)
    return {'elf': str(elf.resolve()), 'sections': sizes, 'functions': functions,
            'rom_bytes':rom_bytes,'rom_section_bytes':section_bytes,'rom_alignment_bytes':rom_bytes-section_bytes,
            'text_padding_bytes': sizes['.text']-sum(f['bytes'] for f in functions),
            'note': 'LTO-inlined callees are included in caller sizes; rodata contains merged strings.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('elf', type=Path)
    p.add_argument('--json', type=Path)
    a = p.parse_args()
    report = analyze(a.elf)
    if a.json: a.json.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(f"ROM {report['rom_bytes']} bytes; sections: {report['sections']}")
    for function in report['functions']: print(f"{function['bytes']:5d}  {function['name']}")


if __name__ == '__main__': main()
