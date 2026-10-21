"""Separate LiteX's actual hierarchical modules and register every source with Gowin."""
import json
import re
import copy
from fnmatch import fnmatchcase
from pathlib import Path

from migen.fhdl.structure import If, Case, _Assign
from migen.fhdl.visit import NodeVisitor
from migen.fhdl.tools import list_signals, list_targets, list_special_ios


def normalize_clock_domains(top, fragment):
    """Recover effective child domains and statement priority from the full design.

    Migen renames the merged fragment's sync keys, but leaves nested modules'
    original keys untouched. LiteX's hierarchical converter reads those child
    fragments again, so a renamed parent's timer/FSM can otherwise use sys.
    Statement identity survives renaming and reset/CE wrappers; follow nested
    statements too, without changing any expressions or control conditions.
    """
    domains = {}
    order = {}

    class StatementDomains(NodeVisitor):
        def visit(self, node):
            if isinstance(node, (_Assign, If, Case)):
                previous = domains.setdefault(id(node), self.domain)
                order[id(node)] = self.position
                if previous != self.domain:
                    raise ValueError('Synchronous statement occurs in multiple clock domains')
            super().visit(node)

    visitor = StatementDomains()
    for domain, statements in fragment.sync.items():
        visitor.domain = domain
        for position,statement in enumerate(statements):
            visitor.position=position
            visitor.visit(statement)

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
    return order


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


def split_verilog(platform, *, deep=False):
    # Keep source modules separate, while permitting cross-module synthesis
    # optimization. Preserving the synthesis netlist hierarchy congests the
    # full MMU/FPU design; this setting has passed complete Gowin PnR.
    platform.toolchain.options['netlist_hierarchy'] = 0
    get_verilog = platform.get_verilog

    def convert(fragment, **kwargs):
        from litex.gen import LiteXContext
        if kwargs.get('hierarchical', False):
            order=normalize_clock_domains(LiteXContext.top, fragment)
            # Flat conversion initializes otherwise undriven internal Signals
            # to their reset values. Hierarchy ports can turn those same
            # Signals into uninitialized interconnect wires (e.g. ROM ERR).
            ios=set(kwargs.get('ios') or platform.constraint_manager.get_io_signals())
            signals=list_signals(fragment) | list_special_ios(fragment, ins=True, outs=True, inouts=True)
            driven=list_targets(fragment) | list_special_ios(fragment, ins=False, outs=True, inouts=True)
            clocks={signal for cd in fragment.clock_domains for signal in (cd.clk,cd.rst) if signal is not None}
            defaults=signals-driven-ios-clocks if kwargs.get('regs_init',True) else set()
            # The hierarchical converter gathers a parent's own statements
            # before those of inlined children. ResetInserter's final reset
            # block must retain its priority over the children's updates.
            from litex.gen.fhdl import verilog
            original_prepare=verilog._prepare_fragment
            original_tree=verilog._build_module_tree
            def module_tree(top):
                tree=remove_shared_aliases(original_tree(top))
                if not deep:
                    # Public SoC blocks stay separate; their implementation
                    # FSMs/FIFOs/CSR fields are optimized within each block.
                    for child in tree.children:child.children=[]
                return tree
            def prepare(source,*args,**options):
                ordered=copy.copy(source)
                ordered.sync={domain:sorted(statements,key=lambda s:order.get(id(s),float('inf')))
                              for domain,statements in source.sync.items()}
                return original_prepare(ordered,*args,**options)
            verilog._prepare_fragment=prepare
            verilog._build_module_tree=module_tree
            try:result=get_verilog(fragment, **kwargs)
            finally:
                verilog._prepare_fragment=original_prepare
                verilog._build_module_tree=original_tree
            result.set_main_source(preserve_signal_defaults(result.main_source,result.ns,defaults))
            return result
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


def remove_shared_aliases(node):
    """Serialize each actual module once without filtering its body via aliases."""
    # Aliases carry their owner's statement IDs but no body. Filtering an
    # alias can erase an inlined owner's logic (e.g. LiteEth SharedIRQ).
    node.children=[child for child in node.children if not getattr(child,'shared_alias',False)]
    for child in node.children:remove_shared_aliases(child)
    return node


def preserve_signal_defaults(source, namespace, signals):
    """Drive undriven cross-module wires with the flat converter's defaults.

    A shared namespace gives each Signal one name across all module ports.
    Its wire declaration belongs to its connecting ancestor; descendants
    receive it through input ports. Leave initialized local regs unchanged.
    """
    from litex.gen.fhdl.verilog import _generate_expression
    values={namespace.get_name(signal):_generate_expression(namespace,signal.reset)[0]
            for signal in signals}
    def update(match):
        module=match[0]
        assignments=[]
        for name,value in sorted(values.items()):
            declaration=re.search(r'^\s*wire\s+(?:signed\s+)?(?:\[[^\]]+\]\s*)?'+re.escape(name)+r'\s*;',module,re.M)
            if declaration:
                assignments.append(f'assign {name} = {value};')
        if assignments:
            module=module.rsplit('endmodule',1)[0]+'// Preserve flat Signal defaults across hierarchy ports.\n'+'\n'.join(assignments)+'\nendmodule'
        return module
    return re.sub(r'^module\b.*?^endmodule\b',update,source,flags=re.M|re.S)
