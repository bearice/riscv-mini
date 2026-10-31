"""RTL8211 PHY discovery; link and packet acceptance are separate results."""
import os
import time
from pathlib import Path
from modular.storage import Store, file_hash
from mini_ops.common import Operation, ROOT
from mini_ops.mega138k_ae350.ae350 import candidate, hardware_abi
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
from mini_ops.mega138k_ae350.ae350_upload import installed_provider, wait_provider
from gateware.machines.mega138k_ae350 import address_map as amap


def wait_peer_link(interface, timeout=15):
    """PHY link-up can precede the host driver's carrier notification."""
    root=Path('/sys/class/net')/interface
    start=time.monotonic();stable=None;states=[]
    while time.monotonic()-start<timeout:
        try:
            status={name:(root/name).read_text().strip() for name in ('carrier','speed','duplex')}
        except OSError as error:
            status={'error':str(error)}
        if not states or states[-1]['status']!=status:
            states.append(dict(elapsed_ms=round((time.monotonic()-start)*1000),status=status))
        if status==dict(carrier='1',speed='1000',duplex='full'):
            if stable is None:stable=time.monotonic()
            if time.monotonic()-stable>=1:
                return dict(passed=True,interface=interface,states=states)
        else:stable=None
        time.sleep(.1)
    return dict(passed=False,interface=interface,states=states)


def judge_mdio(words):
    failures=[]
    if len(words)!=160:return dict(failures=['incomplete PHY result'])
    if words[0]!=0x45544801 or words[159]!=0x455448ff:failures.append('missing completion')
    if words[1]!=25000000:failures.append('PHY reference frequency')
    found=[p for p in range(32) if words[16+p] not in (0,0xffffffff) and words[48+p]==0]
    if not found or words[2]!=len(found):failures.append('PHY discovery/ACK mismatch')
    return dict(failures=failures,phys=[dict(address=p,identity=hex(words[16+p]),
        bmsr=hex(words[80+p]),link_up=bool(words[80+p]&4),
        autoneg_complete=bool(words[80+p]&32)) for p in found],packets_verified=False)


def judge_packets(words,wire):
    failures=[]
    if len(words)!=160:return ['incomplete packet result']
    if not wire.get('passed'):failures.append('raw-wire checks failed: '+wire.get('error','not run'))
    if wire.get('retries',0):failures.append('wire checks needed retransmission')
    if words[128]&4:failures.append('DMA descriptor error IRQ')
    if words[123]!=0x444d4132:failures.append('not a DMA ring payload')
    if words[124]!=words[125] or words[125]<words[7]:failures.append('DMA TX ring completion')
    if (words[128]&3)!=3 or words[129] or words[130]:failures.append('DMA ring IRQ/drain/fault')
    if words[5] or (words[15]&3)!=3:failures.append('PHY/MAC initialization')
    if not 120000000<=words[113]<=130000000:failures.append('RX clock is not gigabit')
    if any(words[i] for i in (12,13,14,114,115,135,136,137)):failures.append('packet loss/error/guard/DMA/RX_ER/FCS error')
    if words[140]&2:failures.append('diagnostic first-bad-frame buffer locked during clean qualification')
    if words[11]!=wire.get('bad_checksum_cases',2):failures.append('invalid checksum packets were not observed/rejected exactly')
    if words[6]<wire.get('checks',49):failures.append('RX packet evidence incomplete')
    if words[8]<1 or words[9]<wire.get('icmp_cases',1) or words[10]<wire.get('udp_cases',1):
        failures.append('application packet counters incomplete')
    if words[118]<words[7] or words[119]<words[7]:failures.append('MAC/PHY TX completion counters')
    return failures


def perform(args):
    op=Operation('ae350-ethernet-suite');hardware=None
    print('Operation logs:',op.path,flush=True)
    uploaded=False
    packets=getattr(args,'packets',False)
    if packets and not args.interface:raise ValueError('--packets requires --interface for the direct peer')
    try:
        root,ref,_=candidate(args.build);store=Store();abi=hardware_abi(store,ref)
        provider=installed_provider(args,store,ref)
        pref,proot=resolve_payload(store,args.payload,expected_abi=abi);binary=payload_file(proot)
        op.report.update(artifact=ref,payload_artifact=pref,payload_sha256=file_hash(binary),
            flash_written=False,scope='ARP/ICMP/UDP and PHY/MAC counters' if packets else 'Clause22 PHY discovery and link observation; no packet qualification')
        if args.dry_run:op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350 import ae350_image
        from tools.boot_session import BootSession
        hardware=HardwareSession(op,port=args.port,location=args.location,cpu_jtag_location=args.cpu_jtag_location)
        session=BootSession(hardware.open_serial(),hardware.open_log('uart.log'))
        hardware.program(root/'gateware/impl/pnr/project.fs',cable_serial=args.cable_serial)
        op.record('banner',text=wait_provider(session,hardware,store,provider,30))
        image=ae350_image.build(binary.read_bytes(),abi);(op.path/'image.bin').write_bytes(image)
        session.upload(image,load=ae350_image.LOAD,abi=abi)
        uploaded=True
        line=bytearray()
        uart_words=[]
        in_words=False
        def handle_line(text_line):
            nonlocal in_words
            stripped=text_line.strip()
            if stripped=='ETH WORDS BEGIN':
                in_words=True;return
            if stripped=='ETH WORDS END':
                in_words=False;return
            if in_words:
                for token in stripped.split():
                    try:uart_words.append(int(token,16))
                    except ValueError:pass
        def progress(chunk):
            line.extend(chunk)
            if chunk==b'\n':
                decoded=line.decode(errors='replace')
                print(decoded.strip(),flush=True)
                handle_line(decoded)
                line.clear()
        wire={}
        if packets:
            deadline=time.monotonic()+args.timeout;ready=False;ended=False
            while time.monotonic()<deadline:
                text=session.until(b'\r\n',max(.1,deadline-time.monotonic()))
                print(text.decode(errors='replace').strip(),flush=True)
                if b'ETH PACKET READY' in text:ready=True;break
                if b'ETH DONE' in text:ended=True;break
            if ready:
                from mini_ops.mega138k_ae350.ae350_ethernet_packets import (
                    exercise, sweep_rx_delay, exercise_sustained_long_frames
                )
                try:
                    peer_ready=wait_peer_link(args.interface)
                    op.record('peer-link-ready',**peer_ready)
                    if not peer_ready['passed']:
                        raise RuntimeError('Direct peer did not reach stable gigabit/full carrier')
                    if getattr(args,'rx_delay_sweep',False):
                        sweep=sweep_rx_delay(args.interface,session,op.path,per_lane=getattr(args,'per_lane_sweep',False))
                        op.record('rx-delay-sweep',**sweep)
                        if not sweep.get('passed'):
                            raise RuntimeError('RX IODELAY sweep did not find a valid common window')
                    sustained_n=getattr(args,'sustained_frames',0) or 0
                    if sustained_n>0:
                        sustained=exercise_sustained_long_frames(args.interface,session,op.path,frame_count=sustained_n)
                        op.record('sustained-long-frames',**sustained)
                    wire=exercise(args.interface,op.path)
                except Exception as error:wire=dict(passed=False,error=str(error))
                if not ended:
                    session.port.write(b'Q\n');session.port.flush()
                    session.until(b'ETH DONE\r\n',args.timeout,on_chunk=progress)
            else:wire=dict(passed=False,error='packet initialization did not reach READY')
            op.record('wire',**wire)
        else:session.until(b'ETH DONE\r\n',args.timeout,on_chunk=progress)
        if hardware.cpu_location is not None:
            cpu=hardware.halt();raw=cpu.read_memory(amap.RESULTS_BASE,160*4)
            words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
            raw=cpu.read_memory(amap.RESULTS_BASE+amap.TRAP_RECORD*4,amap.TRAP_RECORD_WORDS*4)
            trap=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        elif len(uart_words)>=165:
            words=uart_words[:160]
            trap=uart_words[160:165]
        else:
            raise RuntimeError('Missing completion words: JTAG not configured and only %d words received over UART'%len(uart_words))
        result=judge_mdio(words)
        if packets:
            result['failures']+=judge_packets(words,wire)
            result['rx_diagnostics']=dict(
                phy_rx_er_frames=words[135],
                dma_rx_er_frames=words[136],
                dma_fcs_errors=words[137],
                mac_crc_errors=words[116],
                mac_preamble_errors=words[117],
                rx_delay_ctrl=hex(words[138]),
                rx_delay_data=hex(words[139]),
                diag_ctrl_status=hex(words[140]),
                diag_err_info=hex(words[141]),
                phy_reg10=hex(words[132]),
                phy_idle_errors=words[132]&0xff,
            )
            peer=Path('/sys/class/net')/args.interface
            try:
                status=dict(interface=args.interface,carrier=(peer/'carrier').read_text().strip(),speed=(peer/'speed').read_text().strip(),duplex=(peer/'duplex').read_text().strip())
                op.record('peer-link',**status)
                if status['carrier']!='1' or status['speed']!='1000' or status['duplex']!='full':result['failures'].append('peer link is not gigabit/full at completion')
            except OSError as error:result['failures'].append('peer link status: '+str(error))
        if trap[0]:result['failures'].append('CPU trap')
        result['packets_verified']=packets and not result['failures']
        op.record('phy',words=words,trap=trap,**result)
        if result['failures']:raise RuntimeError('Ethernet PHY suite failed: '+repr(result['failures']))
        op.report.update(passed=True);op.save();return op
    except Exception as error:
        if uploaded and hardware is not None and hardware.cpu_location is not None:
            try:
                cpu=hardware.halt();raw=cpu.read_memory(amap.RESULTS_BASE,165*4)
                op.record('failure-diagnostics',words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)],pc=hex(cpu.register(0x7b1)))
            except Exception as diagnostic:op.record('failure-diagnostics-error',error=str(diagnostic))
        elif uploaded and len(uart_words)>=165:
            op.record('failure-diagnostics',words=uart_words[:165],source='uart')
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        try:
            if hardware is not None:hardware.close()
        finally:
            if os.name=='posix' and os.geteuid()==0:
                owner=ROOT.stat()
                for path in op.path.rglob('*'):os.chown(path,owner.st_uid,owner.st_gid,follow_symlinks=False)
                os.chown(op.path,owner.st_uid,owner.st_gid)
