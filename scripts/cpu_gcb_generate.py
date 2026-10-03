"""Isolated C/B/D area experiment; generated RTL is not a qualified CPU release.

Use generated Zba/Zbb/Zbs plugins from rdolbeau/VexRiscvBPluginGenerator,
after auditing their patterns against the current RV32 encodings. The existing
MMU/FPU generator and installed dependency sources are left unchanged.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def bridge_double_cpu(path):
    """Retain the SoC's 32-bit Wishbone interface using LiteX's converter."""
    from migen import Module, ClockDomain, Instance, Signal, ClockSignal, ResetSignal
    from migen.fhdl import verilog
    from litex.soc.interconnect import wishbone
    top=Module();top.clock_domains.cd_sys=ClockDomain('sys')
    top.cd_sys.clk.name_override='clk';top.cd_sys.rst.name_override='reset'
    ios={top.cd_sys.clk,top.cd_sys.rst};params={}
    for name,width in [('externalResetVector',32),('timerInterrupt',1),
                       ('softwareInterrupt',1),('externalInterruptArray',32)]:
        sig=Signal(width,name_override=name);ios.add(sig);params['i_'+name]=sig
    ibus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    wide=wishbone.Interface(data_width=64,address_width=32,addressing='word')
    dbus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    top.submodules.converter=wishbone.DownConverter(wide,dbus)
    fields={'CYC':('cyc','o'),'STB':('stb','o'),'ACK':('ack','i'),
            'WE':('we','o'),'ADR':('adr','o'),'DAT_MISO':('dat_r','i'),
            'DAT_MOSI':('dat_w','o'),'SEL':('sel','o'),'ERR':('err','i'),
            'CTI':('cti','o'),'BTE':('bte','o')}
    for prefix,public,internal in [('iBusWishbone',ibus,ibus),('dBusWishbone',dbus,wide)]:
        for suffix,(field,direction) in fields.items():
            port=getattr(public,field);port.name_override=prefix+'_'+suffix;ios.add(port)
            params[direction+'_'+prefix+'_'+suffix]=getattr(internal,field)
    params.update(i_clk=ClockSignal(),i_reset=ResetSignal())
    top.specials+=Instance('VexRiscv64',**params)
    core=path.read_text().replace('module VexRiscv (','module VexRiscv64 (',1)
    wrapper=verilog.convert(top,ios=ios,name='VexRiscv').main_source
    path.write_text(wrapper+'\n'+core)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-generator',type=Path,required=True)
    p.add_argument('--plugins',type=Path,required=True)
    p.add_argument('--java',type=Path,required=True)
    p.add_argument('--sbt-launch',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--pipelined-fetch',action='store_true',help='Use two-cycle I-cache plus an instruction injector register')
    p.add_argument('--single-precision-only',action='store_true',help='Skip the double-precision resource experiment')
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    base=a.base_generator.resolve()
    for name in ('src','project'):shutil.copytree(base/name,out/name,dirs_exist_ok=True)
    shutil.copy2(base/'build.sbt',out/'build.sbt')
    target=out/'src/main/scala/vexriscv/GenCoreDefault.scala'
    source=target.read_text()
    replacements={
        'fpu : Boolean = false,':'fpu : Boolean = false,\n  doubleGen : Boolean = false,\n  bitmanip : Boolean = false,',
        'opt[Boolean]("fpu") action { (v,c) => c.copy(fpu=v) }':
            'opt[Boolean]("fpu") action { (v,c) => c.copy(fpu=v) }\n'
            '      opt[Boolean]("doubleGen") action { (v,c) => c.copy(doubleGen=v) }\n'
            '      opt[Boolean]("bitmanip") action { (v,c) => c.copy(bitmanip=v) }',
        'cpuDataWidth = 32,\n              memDataWidth = 32,\n              catchAccessError':
            'cpuDataWidth = if(argConfig.doubleGen) 64 else 32,\n              rfDataWidth = 32,\n'
            '              memDataWidth = if(argConfig.doubleGen) 64 else 32,\n              catchAccessError',
        'FpuParameter(withDouble=false,':'FpuParameter(withDouble=argConfig.doubleGen,',
        '// CPU configuration':'if(argConfig.bitmanip) plugins ++= List(\n'
            '        new BitManipZbaPlugin, new BitManipZbbPlugin, new BitManipZbsPlugin)\n\n'
            '      // CPU configuration',
    }
    for anchor,replacement in replacements.items():
        if source.count(anchor)!=1:raise ValueError('Unsupported generator anchor: '+anchor)
        source=source.replace(anchor,replacement)
    if a.pipelined_fetch:
        anchor='twoCycleCache = !argConfig.compressedGen'
        if source.count(anchor)!=1:raise ValueError('Unsupported I-cache pipeline anchor')
        source=source.replace(anchor,'twoCycleCache = true')
        anchor='relaxedPcCalculation = argConfig.relaxedPcCalculation,'
        if source.count(anchor)!=1:raise ValueError('Unsupported fetch injector anchor')
        source=source.replace(anchor,anchor+'\n            injectorStage = true,')
    target.write_text(source)
    plugin_hashes={}
    dest=out/'src/main/scala/vexriscv/plugin';dest.mkdir(parents=True,exist_ok=True)
    for name in ('BitManipZba.scala','BitManipZbb.scala','BitManipZbs.scala'):
        path=a.plugins/name;plugin=path.read_text()
        # Upstream emits unused third-source action lists even for subsets
        # containing only unary/binary instructions. No core RS3 patch needed.
        plugin,n=re.subn(r'\s*val (?:ternaryActions|immTernaryActions) = List\[.*?\n\s*\)',
                         '',plugin,flags=re.S)
        if n!=2 or re.search(r'\b(?:SRC3|RS3|ternaryActions|immTernaryActions)',plugin):
            raise ValueError('Unexpected third-source dependency: '+name)
        (dest/name).write_text(plugin)
        plugin_hashes[name]=hashlib.sha256((dest/name).read_bytes()).hexdigest()
    repo=out/'repositories';shutil.copy2(base/'repositories',repo)
    variants=[('VexRiscv_MmuFpuC',False,False),('VexRiscv_MmuFpuCB',False,True),
              ('VexRiscv_MmuGCB',True,True)]
    if a.single_precision_only:variants=variants[:2]
    commands=[f'runMain vexriscv.GenCoreDefault --csrPluginConfig linux --iCacheSize 2048 '
              '--dCacheSize 2048 --singleCycleMulDiv false --singleCycleShift false '
              f'--fpu true --compressedGen true --doubleGen {str(d).lower()} '
              f'--bitmanip {str(b).lower()} --outputFile {name}' for name,d,b in variants]
    with (out/'generate.log').open('w') as log:
        subprocess.run([str(a.java.resolve()),'-Xmx3G','-Dsbt.override.build.repos=true',
                        '-Dsbt.repository.config='+str(repo),'-jar',str(a.sbt_launch.resolve()),
                        *commands],cwd=out,stdout=log,stderr=subprocess.STDOUT,check=True)
    if not a.single_precision_only:bridge_double_cpu(out/'VexRiscv_MmuGCB.v')
    report={'commands':commands,'pipeline':{'relaxed_pc_calculation':False,
            'two_cycle_icache':a.pipelined_fetch,'injector_register':a.pipelined_fetch},
            'plugin_hashes':plugin_hashes,'cpu_rtls':{},'board_test':'not performed',
            'boundary':'Synthesis candidate; no ISA compliance or timing/hardware qualification'}
    for name,d,b in variants:
        path=out/(name+'.v')
        report['cpu_rtls'][name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'mmu':True,'fpu':True,'dcache':True,'double':d,'compressed':True,
            'bitmanip':['Zba','Zbb','Zbs'] if b else [],'load_store_bits':64 if d else 32,
            'wishbone_bits':32,'internal_data_bus_bits':64 if d else 32,
            'width_converter':bool(d)}
    (out/'generator.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
