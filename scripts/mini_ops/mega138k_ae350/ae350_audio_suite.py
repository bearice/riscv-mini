"""Run real-time audio without halting the CPU until the final UART marker."""
from mini_ops.mega138k_ae350.ae350 import hardware_abi

import time
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from modular.storage import Store, file_hash
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
from mini_ops.mega138k_ae350.ae350_audio_contract import NAMES, judge, judge_routes, judge_video
from gateware.machines.mega138k_ae350 import address_map as amap


def perform(args):
    op=Operation('ae350-audio-suite'); hardware=None
    video_future=video_pool=None
    video_cancel=threading.Event()
    video_device=getattr(args,'video_capture_device',None)
    print('Operation logs:',op.path,flush=True)
    try:
        if not 5<=args.soak_seconds<=3600 or not args.soak_seconds+10<=args.timeout<=4000:
            raise ValueError('audio budget needs 5..3600 seconds and timeout >= soak+10, <=4000')
        if args.qualify_routes and args.capture_device: raise ValueError('route qualification has no analog playback')
        if args.qualify_routes and video_device: raise ValueError('route qualification has no video stress payload')
        root,ref,build_id=candidate(args.build)
        store=Store();manifest=store.get(ref)
        boot=store.get(manifest['dependencies']['bootloader'])
        abi=hardware_abi(store, ref)
        pref,proot=resolve_payload(store,args.payload,expected_abi=abi);payload=payload_file(proot).read_bytes()
        op.report.update(artifact=ref,fs_sha256=file_hash(root/'gateware/impl/pnr/project.fs'),
                         rom_build_id=build_id,payload_artifact=pref,payload_sha256=file_hash(payload_file(proot)),
                         flash_written=False,soak_seconds=args.soak_seconds,
                         realtime_debugger_halts=0,acoustic_stimulus='not-verified',listening='not-verified')
        if args.dry_run:
            op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        from mini_ops.mega138k_ae350 import ae350_image
        from tools.boot_session import BootSession
        hardware=HardwareSession(op,port=args.port,cpu_jtag_location=args.cpu_jtag_location,location=args.location)
        s=BootSession(hardware.open_serial(),hardware.open_log('uart.log'))
        hardware.program(root/'gateware/impl/pnr/project.fs')
        op.record('banner',text=wait_loader(s,build_id,30))
        image=ae350_image.build(payload, abi);(op.path/'image.bin').write_bytes(image)
        s.upload(image,load=ae350_image.LOAD,abi=abi)
        if video_device:
            from mini_ops.mega138k_ae350.ae350_video_suite import capture
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise ValueError('video capture requires ffmpeg in PATH')
            video_op=Operation('ae350-audio-video-capture')
            video_op.report.update(artifact=ref,payload_artifact=pref,parent_operation=str(op.path),
                capture_device=video_device,flash_written=False,
                scope='every delivered USB frame: RGB/CMY panels; static frame repetition is not detected')
            op.report['video_capture_operation']=str(video_op.path)
            op.save()
            def record_video():
                try:
                    capture(video_op,video_device,args.soak_seconds+30,ffmpeg,stop_event=video_cancel,
                            input_format='mjpeg',fps=60)
                    video_op.report['passed']=True
                except Exception as error:
                    video_op.report.update(passed=False,error=str(error));raise
                finally:video_op.save()
            video_pool=ThreadPoolExecutor(max_workers=1)
            video_future=video_pool.submit(record_video)
        started=time.monotonic()
        s.until(b'AE350 AUDIO ',30)
        marker=s.until(b'\r\n',5).strip()
        analog_errors=[]
        if marker==b'STREAM':
            stream_started=time.monotonic()
            op.record('stream-start',monotonic=stream_started)
            print('Audio/mic streaming; CPU remains running',flush=True)
            if args.capture_device:
                from tools.audio_capture import record,analyze
                try:
                    device=record(op.path/'line-in.wav',device_name=args.capture_device)
                    analog=analyze(op.path/'line-in.wav',source_rate=48000)
                    for i,ch in enumerate(analog['channels']):
                        if abs(ch['peak_hz']-ch['expected_hz'])>ch['expected_hz']*.001: analog_errors.append('channel%d frequency'%i)
                        if ch['tone_amplitude']<.0001: analog_errors.append('channel%d no usable tone'%i)
                        if ch['clipped_samples']: analog_errors.append('channel%d clipped'%i)
                        if ch['separation_db']<20: analog_errors.append('channel%d separation'%i)
                        if ch['mute_tone_attenuation_db']<40: analog_errors.append('channel%d mute'%i)
                    (op.path/'line-in.json').write_text(json.dumps(analog,indent=2)+'\n')
                    op.record('analog',device=device,analysis=analog,failures=analog_errors,passed=not analog_errors)
                except Exception as error:
                    analog_errors.append(str(error));op.record('analog',passed=False,error=str(error))
            else: op.record('analog',performed=False)
            chunk=bytearray()
            def progress(data):
                chunk.extend(data)
                if data==b'\n':
                    print(chunk.decode(errors='replace').strip(),flush=True);chunk.clear()
                    if video_future is not None and video_future.done():video_future.result()
            s.until(b'AE350 AUDIO DONE\r\n',max(1,args.timeout-(time.monotonic()-started)),on_chunk=progress)
            stream_seconds=time.monotonic()-stream_started
        elif marker!=b'DONE': raise RuntimeError('unexpected audio UART marker '+repr(marker))
        elapsed=time.monotonic()-started
        # A single halt only after the payload has stopped both devices.
        cpu=hardware.halt()
        raw=cpu.read_memory(amap.RESULTS_BASE,len(NAMES)*4)
        words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        loopback=[]
        if words[1]==34:
            extra=cpu.read_memory(amap.RESULTS_BASE+32*4,16)
            loopback=[int.from_bytes(extra[i:i+4],'little') for i in range(0,16,4)]
            op.record('route-qualification',
                      audio_on=hex(words[27]),audio_off=hex(words[28]),
                      mic_on=hex(words[29]),mic_off=hex(words[30]),
                      loopback=[hex(v) for v in loopback])
        elif words[1]:
            debug=cpu.read_memory(amap.RESULTS_BASE+32*4,32)
            op.record('failure-diagnostics',values=dict(zip(
                ('tx_ctrl','tx_remaining','rx_ctrl','rx_remaining','dma_status','tx_level','rx_level','dma_result'),
                [hex(int.from_bytes(debug[i:i+4],'little')) for i in range(0,32,4)])))
        errors=(judge_routes(words,loopback) if args.qualify_routes else judge(words,args.soak_seconds))+analog_errors
        if not args.qualify_routes:
            raw_pcm=cpu.read_memory(amap.RESULTS_BASE+64*4,64)
            pcm=[int.from_bytes(raw_pcm[i:i+4],'little') for i in range(0,64,4)]
            op.record('pcm-observations',schema=hex(pcm[0]),observed=pcm[1],nonzero=pcm[2],
                      out_of_range=pcm[3],last=hex(pcm[4]),source_words=[hex(v) for v in pcm[5:7]],
                      examples=[hex(v) for v in pcm[8:16]])
            if pcm[0]!=0x50434d01 or pcm[1]<8 or pcm[2]<4 or pcm[3]: errors.append('PCM output observations invalid')
        trap=cpu.read_memory(amap.RESULTS_BASE+amap.TRAP_RECORD*4,amap.TRAP_RECORD_WORDS*4)
        if any(trap): errors.append('trap record is nonzero')
        pc=cpu.register(0x7b1)
        if not amap.DDR_CODE<=pc<amap.DDR_CODE+len(payload): errors.append('PC outside audio payload')
        if cpu.register(0x342): errors.append('unexpected mcause')
        if video_device:
            raw_video=cpu.read_memory(amap.RESULTS_BASE+96*4,32*4)
            v=[int.from_bytes(raw_video[i:i+4],'little') for i in range(0,128,4)]
            op.record('video-stress',words=v)
            errors.extend(judge_video(v,args.soak_seconds))
            if marker==b'STREAM' and v[19]:
                delta=(v[4]-v[19])&0xffffffff
                op.record('video-source-rate',frames=delta,uart_window_seconds=stream_seconds,
                    observed_frame_rate_hz=delta/stream_seconds,
                    method='FPGA raster counter delta / host monotonic UART STREAM-to-DONE; includes stop/report overhead')
        op.record('results',values=dict(zip(NAMES,words)),elapsed_seconds=elapsed,pc=hex(pc),
                  failures=errors,passed=not errors)
        if marker==b'STREAM' and args.soak_seconds>=30:
            op.record('serializer-rate',frames=words[12],uart_window_seconds=stream_seconds,
                      observed_frame_rate_hz=words[12]/stream_seconds,
                      method='serializer frame counter / host monotonic UART STREAM-to-DONE; includes stop/report overhead')
        hardware.resume()
        if video_future is not None:
            if errors:video_cancel.set()
            try:
                video_future.result(timeout=max(10,args.soak_seconds+60-(time.monotonic()-started)))
            except Exception:
                if not errors:raise
        hardware.close();hardware=None
        op.report.update(passed=not errors,phase='dma-route-qualification' if args.qualify_routes else 'audio-digital-and-requested-capture')
        op.save()
        if errors: raise RuntimeError('Audio suite: '+'; '.join(errors))
        print('PASS audio digital suite'+(' and host line input' if args.capture_device else ''),flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        if video_future is not None and not video_future.done():video_cancel.set()
        if video_pool is not None:video_pool.shutdown(wait=True,cancel_futures=True)
        if hardware is not None: hardware.close()
