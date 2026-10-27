"""Recover completed evidence through a fresh CPU TAP; never reprogram/reset."""
import hashlib
import re
from pathlib import Path
from modular.storage import Store,read
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate,hardware_abi
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload,payload_file
from mini_ops.mega138k_ae350.ae350_audio_contract import NAMES,judge,judge_video
from gateware.machines.mega138k_ae350 import address_map as amap


def capture_prefix(capture,seconds):
    timestamps=capture.get('usb_timestamps',{})
    return (capture.get('pattern_errors')==0 and capture.get('captured_frames',0)>=seconds*54 and
            timestamps.get('window_seconds',0)>=seconds and
            timestamps.get('nonmonotonic')==0 and timestamps.get('estimated_missing_frames')==0)


def perform(args):
    op=Operation('ae350-audio-readback');hardware=None
    print('Operation logs:',op.path,flush=True)
    try:
        _,ref,_=candidate(args.build);store=Store();abi=hardware_abi(store,ref)
        pref,proot=resolve_payload(store,args.payload,expected_abi=abi)
        binary=payload_file(proot).read_bytes();parent=read(args.readback)
        if parent.get('operation')!='ae350-audio-suite' or parent.get('artifact')!=ref or parent.get('payload_artifact')!=pref:
            raise ValueError('Readback requires the same original hardware and payload')
        if parent.get('payload_sha256')!=hashlib.sha256(binary).hexdigest():raise ValueError('Parent payload hash mismatch')
        uart=(args.readback.parent/'uart.log').read_bytes()
        if not uart.endswith(b'AE350 AUDIO DONE\r\n'):raise ValueError('Original uninterrupted run has no completion marker')
        progress=[int(v,16) for v in re.findall(rb' sd=([0-9a-f]{8})',uart)]
        if args.sd_stress and (len(progress)<args.soak_seconds//10 or any(b<=a for a,b in zip(progress,progress[1:]))):
            raise ValueError('Original SD progress incomplete')
        cap=read(Path(parent['video_capture_operation'])/'result.json') if parent.get('video_capture_operation') else None
        if cap and (cap.get('artifact')!=ref or cap.get('payload_artifact')!=pref or not capture_prefix(cap,args.soak_seconds)):
            raise ValueError('Captured continuous prefix does not cover the required window')
        op.report.update(artifact=ref,payload_artifact=pref,parent_operation=str(args.readback),
            flash_written=False,media_written=False,programmed=False,uploaded=False,
            realtime_debugger_halts=0,capture_operation=parent.get('video_capture_operation'))
        if args.dry_run:op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        hardware=HardwareSession(op,port=args.port,cpu_jtag_location=args.cpu_jtag_location,location=args.location)
        cpu=hardware.halt()
        def memory(address,length):return cpu.read_memory(address,length)
        def word(address):return int.from_bytes(memory(address,4),'little')
        if word(amap.BOOT_IMAGE_ABI_ADDR)!=abi:raise RuntimeError('Live hardware ABI mismatch')
        symbols=(proot/'outputs/payload.map').read_text()
        def symbol(name):return int(re.search(r'0x([0-9a-fA-F]+)\s+'+name+r'\s*=',symbols).group(1),16)
        text_bytes=symbol('__text_end')-amap.DDR_CODE
        ilm_bytes=symbol('__ilm_end')-amap.ILM_BASE;ilm_offset=symbol('__ilm_load')-amap.DDR_CODE
        if not 0<text_bytes<=len(binary) or not 0<=ilm_offset<=len(binary)-ilm_bytes:raise ValueError('Invalid immutable ELF layout')
        print('Verifying resident code and ILM before reading results',flush=True)
        if memory(amap.DDR_CODE,text_bytes)!=binary[:text_bytes]:raise RuntimeError('Resident code differs from original payload')
        if memory(amap.ILM_BASE,ilm_bytes)!=binary[ilm_offset:ilm_offset+ilm_bytes]:raise RuntimeError('Resident ILM differs from original payload')
        op.record('resident-image',text_bytes=text_bytes,ilm_bytes=ilm_bytes,matched=True)
        raw=memory(amap.RESULTS_BASE,144*4);w=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        errors=judge(w[:32],args.soak_seconds)+judge_video(w[96:128],args.soak_seconds)
        if args.sd_stress and (w[128]!=0x53445301 or w[129]<args.soak_seconds*50 or not w[130] or
            w[131]!=0x3d68bdcc or w[132]!=25000000 or any(w[133:136])):errors.append('SD CRC/cleanup/count')
        trap=memory(amap.RESULTS_BASE+amap.TRAP_RECORD*4,amap.TRAP_RECORD_WORDS*4)
        if any(trap):errors.append('CPU trap')
        pc=cpu.register(0x7b1)
        if not amap.DDR_CODE<=pc<amap.DDR_CODE+len(binary) or cpu.register(0x342):errors.append('CPU execution state')
        pcm=w[64:80]
        if pcm[0]!=0x50434d01 or pcm[1]<8 or pcm[2]<4 or pcm[3]:errors.append('PCM observations')
        op.record('results',values=dict(zip(NAMES,w[:32])),video=w[96:128],sd=w[128:144],
            pcm=pcm,pc=hex(pc),trap=trap.hex(),failures=errors)
        if cap:op.record('capture-prefix',passed=True,captured_frames=cap['captured_frames'],**cap['usb_timestamps'])
        if errors:raise RuntimeError('Recovered result checks: '+'; '.join(errors))
        hardware.close();hardware=None
        op.report.update(passed=True,qualification='completed-uninterrupted-run-and-resident-results-recovered')
        op.save();print('PASS completed mixed run recovered without reset/programming',flush=True);return op
    except Exception as error:
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        if hardware is not None:hardware.close()
