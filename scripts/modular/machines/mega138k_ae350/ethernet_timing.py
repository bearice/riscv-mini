"""Fail-closed RGMII clock/data skew qualification from routed I/O paths.

Gowin does not include the forwarding output path in an external generated
clock's required path. Absolute input-clock phase is therefore not a valid TX
setup reference. Report both clock and data as routed output paths, then check
RGMII Table60's edge-aligned transmitter skew and clock duty at each PVT corner.
No board image is qualified unless every lane, edge and polarity is present.
"""
import math
import re

ENDPOINTS=('clk','ctl','data0','data1','data2','data3')
ROUNDING_GUARD_NS=.002


def _endpoint(name):
    m=re.search(r'ethernet_management_tx_(clk|ctl|data_([0-3]))_obuf(?:/O)?$',name)
    if not m:return None
    return 'data'+m[2] if m[2] is not None else m[1]


def _blocks(text):
    mode=None;current=[];current_mode=None
    for line in text.splitlines():
        new=None
        if 'Setup Analysis Report' in line or re.search(r'Report Command.*report_(?:timing|exceptions)\s+-setup\b',line):new='setup'
        if 'Hold Analysis Report' in line or re.search(r'Report Command.*report_(?:timing|exceptions)\s+-hold\b',line):new='hold'
        if 'Recovery Analysis Report' in line or 'Removal Analysis Report' in line:new='other'
        if new is not None:
            if current:yield current_mode,'\n'.join(current);current=[]
            mode=new
        if line.strip()=='Path Summary:':
            if current:yield current_mode,'\n'.join(current)
            current=[];current_mode=mode
        if current or line.strip()=='Path Summary:':current.append(line)
    if current:yield current_mode,'\n'.join(current)


def qualify_tx(text):
    clock=re.search(r'^\s*\d+\s+eth_tx_clk\s+\w+\s+([\d.]+)\s+([\d.]+)MHz',text,re.M)
    if not clock or abs(float(clock[1])-8)>.001 or abs(float(clock[2])-125)>.001:
        raise ValueError('Missing or invalid 125MHz RGMII TX clock')
    if not re.search(r'report_(?:timing|exceptions)\s+-setup[^\n]*-max_common_paths\s+16\b',text):
        raise ValueError('Missing exhaustive routed TX setup report')
    if not re.search(r'report_(?:timing|exceptions)\s+-hold[^\n]*-max_common_paths\s+16\b',text):
        raise ValueError('Missing exhaustive routed TX hold report')
    samples={};uncertainty=.035
    for mode,block in _blocks(text):
        if mode not in ('setup','hold'):continue
        target=re.search(r'^To\s*:\s*(\S+)',block,re.M)
        if not target:continue
        endpoint=_endpoint(target[1])
        if endpoint is None:continue
        launch=re.search(r'^Launch Clk\s*:\s*eth_tx_clk:\s*\[([RF])\]',block,re.M)
        arrival=re.search(r'^Data Arrival Time\s*:\s*([-+\d.]+)',block,re.M)
        if not launch or not arrival:raise ValueError('Incomplete routed TX summary')
        if 'Data Arrival Path:' not in block or 'Data Required Path:' not in block:
            raise ValueError('Missing routed TX path details')
        path=block.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        active=None;polarity=None;tco=[]
        for line in path.splitlines():
            fields=line.split()
            if 'active clock edge time' in line and fields:active=float(fields[0])
            if len(fields)>=4 and fields[2]=='tC2Q':tco.append(float(fields[1]))
            if fields and fields[-1]==target[1].removesuffix('/O')+'/O':
                if len(fields)<4 or not re.fullmatch('[RF]{2}',fields[3]):
                    raise ValueError('Missing TX pad transition polarity')
                polarity=fields[3][-1]
        if active is None or polarity is None or 'ae350_eth_pll/CLKOUT0' not in path:
            raise ValueError('TX report is not an actual PLL-to-pad routed path')
        # Some DDR models encode the second serialized half in C2Q rather
        # than as a falling launch edge. Never fold routing delay modulo4ns.
        phase=0
        if tco:
            if len(tco)!=1:raise ValueError('Ambiguous DDR output timing arc')
            phase=math.floor(tco[0]/4)
            intrinsic=tco[0]-phase*4
            if phase not in (0,1) or not 0<=intrinsic<2:
                raise ValueError('Unsupported DDR C2Q/serialization model')
        elif endpoint!='clk':raise ValueError('Missing data output-register timing arc')
        edge=launch[1]
        if phase:edge='F' if edge=='R' else 'R'
        # The clock ODDR is tied D0=1,D1=0: only R->R and F->F
        # transitions are physically possible, unlike arbitrary data lanes.
        if endpoint=='clk' and polarity!=edge:continue
        delay=float(arrival[1])-active-phase*4
        if not 0<delay<=16:raise ValueError('Invalid routed TX propagation delay')
        samples.setdefault((mode,endpoint,edge,polarity),[]).append(delay)
        for value in re.findall(r'[-+\d.]+\s+([-+\d.]+)\s+tUnc\b',block):
            uncertainty=max(uncertainty,abs(float(value)))
    corners={};worst_skew=0.;worst_margin=1e9
    skew_guard=ROUNDING_GUARD_NS+uncertainty
    duty_guard=ROUNDING_GUARD_NS+2*uncertainty
    def bounds(mode,endpoint,edge,polarities):
        values=[]
        for polarity in polarities:
            key=(mode,endpoint,edge,polarity)
            if key not in samples:raise ValueError('Missing routed TX coverage: '+str(key))
            values+=samples[key]
        return min(values),max(values)
    for mode in ('setup','hold'):
        clocks={e:bounds(mode,'clk',e,(e,)) for e in ('R','F')}
        lanes={}
        for endpoint in ENDPOINTS[1:]:
            for edge in ('R','F'):
                low,high=bounds(mode,endpoint,edge,('R','F'))
                cmin,cmax=clocks[edge]
                minimum,maximum=low-cmax,high-cmin
                absolute=max(abs(minimum),abs(maximum))
                margin=.5-absolute-skew_guard
                lanes[endpoint+'_'+edge]=dict(min_skew_ns=round(minimum,6),max_skew_ns=round(maximum,6),margin_ns=round(margin,6))
                worst_skew=max(worst_skew,absolute);worst_margin=min(worst_margin,margin)
                if margin<0:raise ValueError('RGMII TX skew exceeds 0.5ns: '+str((mode,endpoint,edge,minimum,maximum,margin)))
        rmin,rmax=clocks['R'];fmin,fmax=clocks['F']
        widths=(4+fmin-rmax,4+fmax-rmin,4+rmin-fmax,4+rmax-fmin)
        duty_margin=min(widths[0]-3.6,4.4-widths[1],widths[2]-3.6,4.4-widths[3])-duty_guard
        if duty_margin<0:raise ValueError('RGMII TX duty outside 45..55%: '+str((mode,widths,duty_margin)))
        corners[mode]=dict(clock_delays_ns={e:list(clocks[e]) for e in clocks},lanes=lanes,
                           high_min_ns=round(widths[0],6),high_max_ns=round(widths[1],6),
                           low_min_ns=round(widths[2],6),low_max_ns=round(widths[3],6),duty_margin_ns=round(duty_margin,6))
    return dict(model='routed-ddr-skew-v1',limit_ns=.5,clock_uncertainty_ns=uncertainty,
                worst_abs_skew_ns=round(worst_skew,6),worst_skew_margin_ns=round(worst_margin,6),corners=corners)
