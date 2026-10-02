"""Separate LiteX's actual hierarchical modules and register every source with Gowin."""
import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

from migen.fhdl.structure import If, Case, _Assign
from migen.fhdl.visit import NodeVisitor


def normalize_clock_domains(top, fragment):
    """Give child fragments the effective domains of the finalized full design.

    Migen renames the merged fragment's sync keys, but leaves nested modules'
    original keys untouched. LiteX's hierarchical converter reads those child
    fragments again, so a renamed parent's timer/FSM can otherwise use sys.
    Statement identity survives renaming and reset/CE wrappers; follow nested
    statements too, without changing any expressions or control conditions.
    """
    domains = {}

    class StatementDomains(NodeVisitor):
        def visit(self, node):
            if isinstance(node, (_Assign, If, Case)):
                previous = domains.setdefault(id(node), self.domain)
                if previous != self.domain:
                    raise ValueError('Synchronous statement occurs in multiple clock domains')
            super().visit(node)

    visitor = StatementDomains()
    for domain, statements in fragment.sync.items():
        visitor.domain = domain
        visitor.visit(statements)

    seen = set()

    def normalize(module):
        if id(module) in seen:
            return
        seen.add(id(module))
        source = module._fragment
        sync = {}
        for original_domain, statements in source.sync.items():
            for statement in statements:
                domain = domains.get(id(statement), original_domain)
                sync.setdefault(domain, []).append(statement)
        source.sync = sync
        for _, child in module._submodules:
            normalize(child)

    normalize(top)


def qualify_sdc(modules,top,text):
    # Existing selective CDC/pad exceptions now refer to cells inside modules.
    # Keep the exception itself unchanged, qualify its owning instance path.
    owners={};net_owners={}
    for module in modules:
        prefix=module[1].removeprefix(top).replace('__','/').lstrip('/')
        prefix=prefix+'/' if prefix else ''
        for signal in re.findall(r'\b(\w+)\s*<=',module[0]):
            owners.setdefault(signal,set()).add(prefix)
            net_owners.setdefault(signal,set()).add(prefix)
        for signal in re.findall(r'\bassign\s+(\w+)\s*=',module[0]):
            net_owners.setdefault(signal,set()).add(prefix)
        for cell in re.findall(r'^\w+\s+(\w+)\s*\(',module[0],re.M):
            owners.setdefault(cell,set()).add(prefix)
    def qualify(match):
        tokens=[]
        for token in match[1].split():
            stem=token.split('/')[0]
            inferred_base=re.sub(r'_(?:\d+_)?s\d+$','',stem)
            # Gowin infers scalar/vector FF names with _sN suffixes.
            paths=set()
            for name,prefixes in owners.items():
                if name==inferred_base or any(fnmatchcase(candidate,stem) for candidate in
                       (name,name+'_s0',name+'_s1',name+'_0_s1')):
                    paths.update(prefixes)
            if not paths:tokens.append(token)
            else:tokens.extend(prefix+token for prefix in sorted(paths))
        return '[get_pins {'+' '.join(dict.fromkeys(tokens))+'}]'
    text=re.sub(r'\[get_pins\s+\{([^}]+)\}\]',qualify,text)
    def qualify_net(match):
        tokens=[]
        for token in match[1].split():
            prefixes=net_owners.get(token,{''})
            tokens.extend(prefix+token for prefix in sorted(prefixes))
        return '[get_nets {'+' '.join(tokens)+'}]'
    return re.sub(r'\[get_nets\s+\{([^}]+)\}\]',qualify_net,text)


def split_verilog(platform):
    get_verilog = platform.get_verilog

    def convert(fragment, **kwargs):
        from litex.gen import LiteXContext
        if kwargs.get('hierarchical', False):
            normalize_clock_domains(LiteXContext.top, fragment)
        return get_verilog(fragment, **kwargs)

    platform.get_verilog = convert
    original=platform.toolchain.build_timing_constraints
    def build(namespace):
        # The timing adapter inspects the combined hierarchical RTL first.
        result=original(namespace)
        top=platform.toolchain._build_name
        path=Path(top+'.v');source=path.read_text()
        modules=list(re.finditer(r'^module\s+(\w+)\b.*?^endmodule\b[^\n]*',source,re.M|re.S))
        if len(modules)<2 or not any(m[1]==top for m in modules):
            raise ValueError('Expected a top and instantiated hierarchical modules')
        timing=Path(result[0])
        timing.write_text(qualify_sdc(modules,top,timing.read_text()))
        directory=Path('modules');directory.mkdir(exist_ok=True)
        # Clear only stale files previously owned by this generator.
        manifest=Path('rtl-manifest.json')
        if manifest.exists():
            for entry in json.loads(manifest.read_text())['modules']:
                old=Path(entry['file'])
                if old.parent==directory and old.suffix=='.v':old.unlink(missing_ok=True)
        entries=[]
        for match in modules:
            name=match[1];file=path if name==top else directory/(name+'.v')
            file.write_text('`timescale 1ns/1ps\n'+match[0]+'\n',encoding='utf-8')
            if name!=top:platform.add_source(str(file.resolve()))
            entries.append({'name':name,'file':file.as_posix()})
        manifest.write_text(json.dumps({'top':top,'modules':entries},indent=2)+'\n')
        external=[str(Path(e[0]).resolve()) for e in platform.sources
                  if Path(e[0]).resolve().parent!=directory.resolve()]
        Path('sources.f').write_text('\n'.join(dict.fromkeys(external+[e['file'] for e in entries]))+'\n')
        return result
    platform.toolchain.build_timing_constraints=build
