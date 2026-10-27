"""AE350 file-only FatFs writes and interactive native BIOS/GW-BASIC tests."""
from modular.storage import Store, file_hash
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate, hardware_abi
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
from mini_ops.mega138k_ae350.ae350_upload import installed_provider, wait_provider
from gateware.machines.mega138k_ae350 import address_map as amap


def judge_fs(w, trap):
    if len(w)!=96:return ['incomplete FS result']
    errors=[]
    if w[0]!=0x5037ffff or any(w[i] for i in (1,2,4,7,8,9)):errors.append('completion/file/cleanup error')
    if w[3]!=25000000 or w[5]!=1 or w[10]!=1 or w[11]!=1 or w[12]!=1:errors.append('SD25 or BIOS ABI/audio/file checks')
    # Independently generated on the host with Python zlib, not card repeats.
    reference=(0x3d68bdcc,0x6ed0a415,0xc25f7ec4)
    for i,size in enumerate((1<<20,16<<20,64<<20)):
        off=16+i*8
        if w[off]!=reference[i] or w[off+1]!=reference[i] or w[off+4]!=size or w[off+5]!=1:errors.append('file CRC/size/check '+str(i))
    if trap and trap[0]:errors.append('CPU trap')
    return errors


def test_bios(session, hardware, op, directory):
    import re
    def command(line, marker=b'\r\n> ', timeout=60):
        session.port.write(line.encode()+b'\r');session.port.flush()
        response=session.until(marker,timeout)
        op.record('bios-command',command=line,response=response.decode(errors='replace'))
        return response
    session.until(b'AE350 BIOS READY\r\n',60);session.until(b'> ',30)
    if b'SD=25000000 Hz' not in command('status'):raise RuntimeError('BIOS SD25 status mismatch')
    if b'LS OK' not in command('ls'):raise RuntimeError('BIOS directory listing failed')
    listing=command('ls '+directory)
    if b'LS OK' not in listing or any(name not in listing for name in
            (b'TEST0.BIN',b'TEST1.BIN',b'TEST2.BIN',b'CONSOLE.TXT',b'PROGRAM.BAS')):
        raise RuntimeError('BIOS dedicated-directory names differ')
    if b'SIZE 1048576' not in command('size '+directory+'/TEST0.BIN'):raise RuntimeError('BIOS file size failed')
    if b'AE350 P7 filesystem\r\n' not in command('cat '+directory+'/CONSOLE.TXT'):raise RuntimeError('BIOS file read failed')
    for line in ('mkdir '+directory+'/BIOS',
                 'mv '+directory+'/CONSOLE.TXT '+directory+'/MOVED.TXT',
                 'mv '+directory+'/MOVED.TXT '+directory+'/CONSOLE.TXT',
                 'rm '+directory+'/BIOS'):
        if b'FILE OK' not in command(line):raise RuntimeError('BIOS metadata command failed: '+line)
    # Exercise the actual editor's Ctrl-U and middle-of-line overwrite.
    if b'SD=25000000 Hz' not in command('discard\x15stxtus\x01\x1b[C\x1b[Ca\x05'):
        raise RuntimeError('BIOS editor failed')
    command('basic',b'Ok\r\n')
    result=command('TESTS',b'Ok\r\n',180)
    if b'ALL TESTS PASSED (' not in result or b'FAIL' in result:raise RuntimeError('GW-BASIC self tests failed')
    command('NEW',b'Ok\r\n')
    command('10 S=0:FOR I=1 TO 5',b'Ok\r\n')
    command('20 S=S+I',b'Ok\r\n')
    command('30 NEXT I',b'Ok\r\n')
    command('40 PRINT "AE350 BASIC SUM=";S',b'Ok\r\n')
    result=command('RUN',b'Ok\r\n')
    if not re.search(rb'AE350 BASIC SUM=\s*15(?:\s|$)',result):raise RuntimeError('GW-BASIC program output mismatch')
    command('LOAD "'+directory+'/PROGRAM.BAS"',b'Ok\r\n')
    if b'AE350 FILE BASIC' not in command('RUN',b'Ok\r\n'):raise RuntimeError('GW-BASIC SD program load failed')
    command('SCREEN 1',b'Ok\r\n')
    command('PSET (300,180),4',b'Ok\r\n')
    result=command('PRINT "AE350 POINT=";POINT(300,180)',b'Ok\r\n')
    if not re.search(rb'AE350 POINT=\s*4(?:\s|$)',result):raise RuntimeError('GW-BASIC RGB565 graphics failed')
    cpu=hardware.halt()
    try:
        def word(address):return int.from_bytes(cpu.read_memory(address,4),'little')
        base=word(amap.VIDEO_BASE+amap.VIDEO_REGS['ACTIVE_BASE'])
        pixel=word(base+180*1280+300*2)&65535
        errors=word(amap.VIDEO_BASE+amap.VIDEO_REGS['ERRORS'])
        op.record('basic-scanout-readback',base=hex(base),pixel=hex(pixel),errors=errors)
        if base not in (0x08000000,0x08100000) or pixel!=0xa000 or errors:
            raise RuntimeError('GW-BASIC scanout contents/ownership failed')
    finally:hardware.resume()
    command('SCREEN 0',b'Ok\r\n')
    command('CLS',b'Ok\r\n')
    command('SYSTEM',b'> ')
    if b'UNMOUNT OK' not in command('unmount'):raise RuntimeError('BIOS unmount failed')
    if b'LS OK' not in command('ls '+directory):raise RuntimeError('BIOS remount failed')
    if b'SD=25000000 Hz' not in command('status'):raise RuntimeError('BIOS final status failed')


def perform(args):
    op=Operation('ae350-p7-suite');hardware=None
    print('Operation logs:',op.path,flush=True)
    try:
        root,ref,_=candidate(args.build);store=Store();abi=hardware_abi(store,ref)
        provider=installed_provider(args,store,ref)
        directory=None
        if args.bios:
            import re
            from modular.storage import read
            if not args.filesystem_result:raise ValueError('--bios needs --filesystem-result from the dedicated file tests')
            files=read(args.filesystem_result);directory=files.get('test_directory','')
            if not files.get('passed') or not re.fullmatch(r'/P7T[0-9]{3}',directory):
                raise ValueError('Invalid filesystem acceptance receipt')
            op.report['filesystem_result']=str(args.filesystem_result)
        payload_ref,payload_root=resolve_payload(store,args.payload,expected_abi=abi)
        binary=payload_file(payload_root);data=binary.read_bytes()
        op.report.update(artifact=ref,payload_artifact=payload_ref,payload_sha256=file_hash(binary),
                         flash_written=False,media_written=True,
                         scope='native BIOS/GW-BASIC and dedicated test-directory metadata' if args.bios else 'new dedicated FatFs files; no formatting or raw-sector writes')
        if args.dry_run:op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350 import ae350_image
        from tools.boot_session import BootSession
        hardware=HardwareSession(op,port=args.port,cpu_jtag_location=args.cpu_jtag_location,location=args.location)
        session=BootSession(hardware.open_serial(),hardware.open_log('uart.log'))
        hardware.program(root/'gateware/impl/pnr/project.fs')
        op.record('banner',text=wait_provider(session,hardware,store,provider,30))
        image=ae350_image.build(data,abi);(op.path/'image.bin').write_bytes(image)
        session.upload(image,load=ae350_image.LOAD,abi=abi)
        if args.bios:
            test_bios(session,hardware,op,directory)
        else:
            def progress(chunk):
                if chunk==b'\n':print('FS UART progress; see uart.log',flush=True)
            session.until(b'AE350 FS DONE\r\n',args.timeout,on_chunk=progress)
        cpu=hardware.halt()
        raw=cpu.read_memory(amap.RESULTS_BASE,96*4)
        words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        raw=cpu.read_memory(amap.RESULTS_BASE+amap.TRAP_RECORD*4,amap.TRAP_RECORD_WORDS*4)
        trap=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        failures=(['CPU trap'] if trap[0] else []) if args.bios else judge_fs(words,trap)
        op.record('results',words=words,trap=trap,pc=hex(cpu.register(0x7b1)),failures=failures)
        if failures:raise RuntimeError('P7 suite failed: '+repr(failures))
        if not args.bios:op.report['test_directory']='/P7T%03d'%words[6]
        op.report.update(passed=True);op.save();return op
    except Exception as error:
        if hardware is not None:
            try:
                cpu=hardware.halt();raw=cpu.read_memory(amap.RESULTS_BASE,192*4)
                op.record('failure-diagnostics',words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)],pc=hex(cpu.register(0x7b1)))
            except Exception as diagnostic:op.record('failure-diagnostics-error',error=str(diagnostic))
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        if hardware is not None:hardware.close()
