"""Independent audio demo acceptance: PIO, DMA/pause, line-in and 5m LCD/SD."""
import argparse
import json
import re
import time
from pathlib import Path
import serial
from boot_image import unpack_image
from boot_upload import BootSession,verified_output,program
from audio_capture import record,analyze
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/m7-base')
    parser.add_argument('--image',type=Path,default=ROOT/'build/m7-demo/firmware/app.img')
    parser.add_argument('--port',default='COM4');parser.add_argument('--location',default='107569')
    parser.add_argument('--reset',action='store_true');parser.add_argument('--program',action='store_true')
    parser.add_argument('--skip-capture',action='store_true');parser.add_argument('--device',default='Line In')
    parser.add_argument('--soak-seconds',type=float,default=300)
    args=parser.parse_args();output=args.output_dir.resolve()
    if args.soak_seconds<0:parser.error('Negative duration')
    validation,_=verified_output(output);image=args.image.read_bytes()
    unpack_image(image,validation['boot_image']['abi_tag'])
    report={'passed':False,'checks':{},'bitstream_sha256':validation['bitstream_sha256']}
    try:
        with (output/'audio-verification-uart.log').open('wb') as log,serial.Serial(args.port,115200,timeout=.05) as port:
            port.reset_input_buffer()
            if args.program:program(output,args.location)
            session=BootSession(port,log);session.menu(args.reset)
            startup=session.upload(image)
            if b'AUDIO DEMO:' not in startup:raise RuntimeError('Load audio_demo.c')
            report['checks']['startup']=startup.decode(errors='replace')
            def command(letter):
                port.write(letter.encode());port.flush();result=session.until(b'> ',30)
                if b' FAIL' in result:raise RuntimeError(result.decode(errors='replace'))
                return result
            def status(running=False):
                text=command('s')
                line=re.search(rb'AUDIO hz=([^\r]+)',text)
                if not line:raise RuntimeError('Missing audio status')
                fields={'hz':int(line[1].split()[0],16)}
                fields.update({k.decode():int(v,16) for k,v in re.findall(rb'(\w+)=([0-9a-f]{8})',line[1])})
                if fields['hz']!=validation.get('audio_sample_rate',46875) or fields['errors'] or fields['overruns'] or fields['amp']:
                    raise RuntimeError('Audio status: '+text.decode(errors='replace'))
                if running and (fields['control']!=7 or fields['underruns']):raise RuntimeError('DMA stream underrun or not running/muted')
                if b'underflows=00000000' not in text or b'SD ready=00000001' not in text or b'errors=00000000 drops=00000000 unhandled=00000000' not in text:
                    raise RuntimeError('SD/LCD/IRQ status: '+text.decode(errors='replace'))
                counters=re.search(rb'frames=([0-9a-f]{8}) completed=([0-9a-f]{8}) underflows=',text)
                if not counters:raise RuntimeError('Missing LCD counters')
                return fields,tuple(int(counters[i],16) for i in (1,2)),text.decode(errors='replace')
            try:
                initial,_,_=status()
                if initial['control']!=4 or initial['level'] or initial['frames']:raise RuntimeError('Audio not idle after startup')
                pio=command('t')
                if b'AUDIO PIO/UNDERRUN PASS played=00000100' not in pio:raise RuntimeError('PIO result missing')
                report['checks']['pio']=pio.decode(errors='replace')
                if b'AUDIO DMA START PASS' not in command('d'):raise RuntimeError('DMA start missing')
                time.sleep(.25);a,_,_=status(True)
                command('p');paused,_,_=status();time.sleep(.25);later,_,_=status()
                if paused['frames']!=later['frames'] or later['control']!=6:raise RuntimeError('Pause did not stop consumption')
                command('c');time.sleep(.25);resumed,_,_=status(True)
                if resumed['played']<=later['played']:raise RuntimeError('Resume did not progress')
                report['checks']['pause_resume']={'paused':paused,'resumed':resumed}
                if not args.skip_capture:
                    wav=output/'audio-line-in.wav'
                    device=record(wav,device_name=args.device,on_start=lambda:command('u'))
                    capture=analyze(wav,validation.get('audio_sample_rate',46875));capture['device']=device;report['capture']=capture
                    print('Line-in: '+json.dumps(capture),flush=True)
                    for channel in capture['channels']:
                        if abs(channel['peak_hz']-channel['expected_hz'])>3 or channel['tone_amplitude']<.0001 or channel['separation_db']<12 or channel['clipped_samples']:
                            raise RuntimeError('Line-in tone/channel check failed')
                        # Amp transitions create DC/settling in AC-coupled line-in.
                        # Assert suppression of the actual tone, keep RMS/DC as evidence.
                        if channel['mute_tone_attenuation_db']<40:raise RuntimeError('Mute did not suppress analog test tone')
                    status(True)
                started=time.monotonic();rounds=0;first=None;last=None
                while time.monotonic()-started<args.soak_seconds:
                    if b'FRAME PASS' not in command('f'):raise RuntimeError('Frame switch missing')
                    read=command('r')
                    if b'SD READ PASS bytes=00001000 crc=08040e1e' not in read:raise RuntimeError('SD file CRC mismatch')
                    audio,lcd,text=status(True)
                    now=(audio['played'],audio['fetched'],audio['wraps'],*lcd)
                    if last is not None and any(((a-b)&0xffffffff)==0 for a,b in zip(now,last)):
                        raise RuntimeError('Audio/LCD counters stopped')
                    if first is None:first=now
                    last=now;rounds+=1
                    if rounds%10==0:print(f'{rounds} rounds, {time.monotonic()-started:.1f}s, audio DMA/SD CRC/LCD/IRQ OK',flush=True)
                    remaining=args.soak_seconds-(time.monotonic()-started)
                    if remaining>0:time.sleep(min(2,remaining))
                report['soak']={'seconds':time.monotonic()-started,'rounds':rounds,'first':first,'last':last}
                report['checks']['final_status']=status(True)[2]
                # Repeated stop/start must clear queued old PCM and DMA ownership.
                for _ in range(3):
                    command('x');stopped,_,_=status()
                    if stopped['busy'] or stopped['level'] or stopped['amp']:raise RuntimeError('Stop not idle')
                    command('d');time.sleep(.1);status(True)
                report['checks']['stop_restart']='3 cycles passed'
                report['passed']=True
            finally:
                command('x')  # leave DAC/amp disabled even if acceptance fails
    finally:
        (output/'audio-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Audio verification passed.',flush=True)

if __name__=='__main__':main()
