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
RX_ENDPOINTS=('ctl','data0','data1','data2','data3')
ROUNDING_GUARD_NS=.002


def _endpoint(name):
    m=re.search(r'ethernet_management_tx_(clk|ctl|data_([0-3]))_obuf(?:/O)?$',name)
    if not m:return None
    return 'data'+m[2] if m[2] is not None else m[1]


def _rx_endpoint(name):
    m=re.search(r'ethernet_management_rx_(ctl|data_([0-3]))_ibuf(?:/I)?$',name)
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
    # Table 60 transmitter duty cycle (45%..55%) applies to the physical output
    # waveform at the pins. The STA routed pulse widths already capture duty distortion.
    # Only floating-point rounding guard is subtracted.
    duty_guard=ROUNDING_GUARD_NS
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


def qualify_rx(text, rx_delay_ps=None):
    clock=re.search(r'^\s*\d+\s+eth_rx_clk\s+\w+\s+([\d.]+)\s+([\d.]+)MHz',text,re.M)
    if not clock or abs(float(clock[1])-8)>.001 or abs(float(clock[2])-125)>.001:
        raise ValueError('Missing or invalid 125MHz RGMII RX clock')
    records={}
    for mode,block in _blocks(text):
        if mode not in ('setup','hold'):continue
        source=re.search(r'^From\s*:\s*(\S+)',block,re.M)
        target=re.search(r'^To\s*:\s*(\S+)',block,re.M)
        if not source or not target:continue
        endpoint=_rx_endpoint(source[1])
        if endpoint is None or not target[1].startswith('IDDR'):continue
        launch=re.search(r'^Launch Clk\s*:\s*eth_rx_clk:\s*\[([RF])\]',block,re.M)
        latch=re.search(r'^Latch Clk\s*:\s*eth_rx_clk:\s*\[([RF])\]',block,re.M)
        slack=re.search(r'^Slack\s*:\s*([-+\d.]+)',block,re.M)
        arrival=re.search(r'^Data Arrival Time\s*:\s*([-+\d.]+)',block,re.M)
        required=re.search(r'^Data Required Time\s*:\s*([-+\d.]+)',block,re.M)
        rel_name='Setup' if mode=='setup' else 'Hold'
        relation=re.search(r'^'+rel_name+r' Relationship\s*:\s*([-+\d.]+)',block,re.M)
        if not (launch and latch and slack and arrival and required and relation):
            raise ValueError('Incomplete routed RX timing block')
        rel_val=float(relation[1])
        expected_rel=4.0 if mode=='setup' else 0.0
        if abs(rel_val-expected_rel)>.005:
            continue
        if (mode=='setup' and launch[1]==latch[1]) or (mode=='hold' and launch[1]!=latch[1]):
            continue
        if 'Data Arrival Path:' not in block or 'Data Required Path:' not in block:
            raise ValueError('Missing routed RX path details')
        arr_section=block.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        req_section=block.split('Data Required Path:',1)[1].split('Path Statistics:',1)[0]
        tin=ibuf=iodelay=None
        for line in arr_section.splitlines():
            fields=line.split()
            if len(fields)>=3 and fields[2]=='tIn':tin=float(fields[1])
            elif len(fields)>=3 and fields[2]=='tINS' and fields[-1].endswith('_ibuf/O'):ibuf=float(fields[1])
            elif len(fields)>=3 and fields[2]=='tINS' and 'IODELAY' in fields[-1]:iodelay=float(fields[1])
        clk_ibuf=clk_net=req_gate=None
        for line in req_section.splitlines():
            fields=line.split()
            if len(fields)>=3 and fields[2]=='tINS' and 'rx_clk_ibuf/O' in fields[-1]:clk_ibuf=float(fields[1])
            elif len(fields)>=3 and fields[2]=='tNET' and fields[-1].endswith('/CLK'):clk_net=float(fields[1])
            elif len(fields)>=3 and fields[2] in ('tSu','tHld'):req_gate=float(fields[1])
        if None in (tin,ibuf,iodelay,clk_ibuf,clk_net,req_gate):
            raise ValueError('Incomplete RX delay breakdown for '+str((mode,endpoint,latch[1])))
        expected_tin=3.0 if mode=='setup' else 1.0
        if abs(tin-expected_tin)>.005:
            raise ValueError('Unexpected RX external input delay: '+str((mode,endpoint,tin)))
        key=(mode,endpoint,latch[1])
        entry=dict(slack_ns=round(float(slack[1]),6),arrival_ns=round(float(arrival[1]),6),
                   required_ns=round(float(required[1]),6),launch_edge=launch[1],latch_edge=latch[1],
                   relationship_ns=round(rel_val,6),input_delay_ns=round(tin,6),
                   ibuf_delay_ns=round(ibuf,6),iodelay_ns=round(iodelay,6),
                   data_insertion_ns=round(ibuf+iodelay,6),clock_ibuf_ns=round(clk_ibuf,6),
                   clock_net_ns=round(clk_net,6),clock_insertion_ns=round(clk_ibuf+clk_net,6),
                   iddr_Constraint_ns=round(req_gate,6))
        if key not in records or entry['slack_ns']<records[key]['slack_ns']:
            records[key]=entry
    corners={};worst_setup=1e9;worst_hold=1e9
    for mode in ('setup','hold'):
        lanes={};clk_ins={'R':[],'F':[]};iodlys=[]
        for endpoint in RX_ENDPOINTS:
            for edge in ('R','F'):
                key=(mode,endpoint,edge)
                if key not in records:
                    raise ValueError('Missing routed RX dual-edge coverage: '+str(key))
                item=records[key]
                lanes[endpoint+'_'+edge]=item
                clk_ins[edge].append(item['clock_insertion_ns'])
                iodlys.append(item['iodelay_ns'])
                if mode=='setup':worst_setup=min(worst_setup,item['slack_ns'])
                else:worst_hold=min(worst_hold,item['slack_ns'])
                if item['slack_ns']<0:
                    raise ValueError('RGMII RX %s timing violation on %s_%s: %s ns'%(mode,endpoint,edge,item['slack_ns']))
        corners[mode]=dict(
            worst_slack_ns=round(min(v['slack_ns'] for v in lanes.values()),6),
            clock_insertion_ns={e:[round(min(clk_ins[e]),6),round(max(clk_ins[e]),6)] for e in ('R','F')},
            clock_skew_span_ns={e:round(max(clk_ins[e])-min(clk_ins[e]),6) for e in ('R','F')},
            iodelay_ns=[round(min(iodlys),6),round(max(iodlys),6)],
            lanes=lanes)
    return dict(
        model='routed-ddr-input-v1',
        rx_delay_ps=rx_delay_ps,
        external_input_delay_model=dict(setup_max_ns=3.0,hold_min_ns=1.0,
                                        setup_relationship_ns=4.0,hold_relationship_ns=0.0),
        worst_setup_slack_ns=round(worst_setup,6),
        worst_hold_slack_ns=round(worst_hold,6),
        corners=corners)
