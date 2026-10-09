"""Board workflows: preflight, execution, exact verification and durable receipts."""
import hashlib
import os
import json
import re
import subprocess
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

from boot_upload import BootSession, programmer_succeeded
from uart_state import State
from boot_image import CHUNK, packet
from build_records import ROOT, write_json
from mini_ops.artifacts import Build, sha256
from mini_ops.probe import compile_probe


class Operation:
    def __init__(self, command, build=None, root=ROOT):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.path = root/'build/operations'/f'{stamp}-{command}'
        self.path.mkdir(parents=True)
        self.report = dict(operation=command, passed=False, steps=[],
                           selected_build=build.describe() if build else None,
                           device_identity='unconfirmed; selected files are not device readback')
        self.save()

    def save(self):
        write_json(self.path/'result.json', self.report)

    def record(self, name, **values):
        self.report['steps'].append(dict(name=name, **values))
        self.save()


class Board:
    def __init__(self, build, operation, port='COM4', location='107569',
                 runner=subprocess.run, serial_factory=None, tools=None):
        self.build, self.op = build, operation
        self.port, self.location = port, location
        self.runner, self.serial_factory = runner, serial_factory
        self.tools = tools or json.loads((ROOT/'.tools.local.json').read_text(encoding='utf-8'))
        programmer = Path(self.tools['programmer'])
        if not programmer.is_file():
            raise ValueError('Configured Gowin programmer not found')

    def connect(self):
        if self.serial_factory is None:
            import serial
            return serial.Serial(self.port,115200,timeout=.1)
        return self.serial_factory(self.port,115200,timeout=.1)

    def program(self, name, index, path=None, offset=None, raw=False):
        print('Programmer step:',name,flush=True)
        command = [self.tools['programmer'],'--device','GW2A-18C',
                   '--cable-index','4','--location',str(self.location),
                   '--frequency','2MHz','--operation_index',str(index)]
        if path is not None:
            command += ['--mcuFile' if raw else '--fsFile',str(Path(path).resolve())]
        if offset is not None:
            command += ['--spiaddr',f'0x{offset:06X}']
        log_path = self.op.path/(name+'.log')
        temporary=self.op.path/'tmp';temporary.mkdir(exist_ok=True)
        environment=dict(os.environ,TEMP=str(temporary),TMP=str(temporary),TMPDIR=str(temporary))
        with log_path.open('w',encoding='utf-8') as stream:
            result = self.runner(command,stdout=stream,stderr=subprocess.STDOUT,timeout=120,env=environment)
        output = log_path.read_text(encoding='utf-8',errors='replace')
        completed = subprocess.CompletedProcess(command,result.returncode,output,'')
        # Only the specifically documented zero-exit Verify warning may proceed
        # to independent readback. Other errors and malformed extents stop now.
        warning = (raw and result.returncode == 0 and 'Error: SPI Verify failed!' in output
                   and programmer_succeeded(subprocess.CompletedProcess(
                       command,0,output.replace('Error: SPI Verify failed!',''),'')))
        if raw:
            starts = re.findall(r'SPI start of address:\s*(0x[0-9a-fA-F]+)',output)
            ends = re.findall(r'SPI end of address:\s*(0x[0-9a-fA-F]+)',output)
            # The programmer rejects a stream whose padded size exceeds the
            # Flash; that failure prints no address extent, so treat a missing
            # extent as invalid rather than silently skipping the check.
            extent_valid = (len(starts)==len(ends)==1 and int(starts[0],16)==offset
                            and offset <= int(ends[0],16) < offset+Path(path).stat().st_size)
            if not extent_valid:
                warning = False
                success = False
            else:
                success = programmer_succeeded(completed)
        else:
            success = programmer_succeeded(completed)
        self.op.record(name, command=command, exit_code=result.returncode,
                       log=str(log_path), programmer_passed=success,
                       requires_independent_readback=bool(warning),
                       detected_flash_ids=re.findall(r'SPI flash detected:\s*(0x[0-9a-fA-F]+)',output))
        if not success and not warning:
            raise RuntimeError(f'{name} failed; inspect {log_path}. Flash may be partially updated.')

    def menu(self, session, reset=False):
        startup = session.menu(reset,fresh=True)
        if b'ERR ' in startup or b'DDR READY' not in startup:
            raise RuntimeError('Loader/DDR startup failed: '+startup.decode(errors='replace'))
        self.op.record('loader', output=startup.decode(errors='replace'))
        return startup

    def enter_loader(self, session):
        """Get the bootloader menu from any board state. A board already at
        the menu or in the application is asked to reboot ('!'); a silent or
        failed board escalates through the recovery ladder (query, reset,
        SRAM reload) until the menu answers. Replaces the old fresh-banner
        requirement, which failed whenever the boot banner had already
        drained from the UART FIFO."""
        from uart_state import BoardFSM
        fsm=BoardFSM(session.port,self.op.path/'uart.log',
                     self.build.validation['boot_image']['abi_tag'])
        fsm.observe()
        if fsm.state is State.APP_READY:
            session.port.write(b'!');session.port.flush()
            fsm.observe(timeout=8.0)
            if fsm.state is not State.LOADER:
                # The running application did not hand the console back; the
                # only reliable boundary is reloading the complete design.
                fsm.state=State.UNKNOWN
        if fsm.state is State.BOOTING:
            try:
                session.until(b'BOOT SELECT:',10);session.port.write(b'b');session.port.flush()
                session.until(b'BL> ',10)
                fsm.state=State.LOADER
            except (TimeoutError,RuntimeError):
                fsm.state=State.UNKNOWN
        if fsm.state is not State.LOADER:
            fsm.recover(program=lambda:self.program('recover-sram',2,self.build.fs),
                        allowed=(State.LOADER,))
        self.op.record('board_state',state=fsm.state.value,detail=fsm.detail,ladder=fsm.history)
        if fsm.state is not State.LOADER:
            raise RuntimeError(f'cannot reach loader menu (state {fsm.state.value}); ladder: '+
                               repr(fsm.history))
        self.op.record('loader',output=fsm.data.decode(errors='replace'))
        return fsm.data

    def run(self):
        with self.connect() as port, (self.op.path/'uart.log').open('wb') as log:
            port.reset_input_buffer()
            self.program('sram',2,self.build.fs)
            session = BootSession(port,log)
            self.enter_loader(session)
            startup = session.upload(self.build.image)
            self.op.record('uart_run',output=startup.decode(errors='replace'))

    def recover(self):
        with self.connect() as port, (self.op.path/'uart.log').open('wb') as log:
            port.reset_input_buffer()
            self.program('sram',2,self.build.fs)
            session = BootSession(port,log)
            self.enter_loader(session)
            info = session.command('i')
            self.op.record('flash_header',output=info.decode(errors='replace'),
                           checks_payload=False)

    def repair_xip(self,plan,probe_image):
        if self.build.mode!='xip':raise ValueError('repair-xip requires an XIP build')
        write=next(w for w in plan['writes'] if w['name']=='xip')
        path=self.op.path/'xip.bin';path.write_bytes(Path(write['path']).read_bytes())
        if sha256(path)!=write['sha256']:raise ValueError('XIP artifact changed')
        with self.connect() as port,(self.op.path/'uart.log').open('wb') as log:
            self.program('repair-xip-programmer',32,path,write['offset'],True)
            session=BootSession(port,log)
            self.restore_loader(session)
            try:
                self.readback(session,probe_image)
            except RuntimeError:
                if not self.op.report.get('readback_complete') or not self.op.report['flash_regions']['xip']['matched']:
                    raise
            finally:
                self.restore_loader(session)
            self.op.record('xip_repaired',application_may_differ=True)

    def readback(self, session, probe_image):
        startup = session.upload(probe_image)
        if b'FLASH READBACK' not in startup:
            raise RuntimeError('Readback utility did not start')
        session.port.write(b'r');session.port.flush()
        session.until(b'READBACK BEGIN\r\n')
        targets = [('application',self.build.image)]
        if self.build.mode == 'xip':
            targets.append(('xip',(self.build.path/'firmware/xip.bin').read_bytes()))
        mismatches=[]
        for name, expected in targets:
            data = bytearray()
            deadline = time.monotonic()+max(30,len(expected)/5000+15)
            while len(data)<len(expected) and time.monotonic()<deadline:
                data.extend(session.port.read(min(4096,len(expected)-len(data))))
            path = self.op.path/(name+'-readback.bin')
            path.write_bytes(data)
            matched = bytes(data)==expected
            self.op.record(name+'_readback', bytes=len(data), expected_bytes=len(expected),
                           sha256=sha256(path), expected_sha256=hashlib.sha256(expected).hexdigest(),
                           matched=matched)
            if len(data)!=len(expected):
                raise RuntimeError(name+' readback truncated; subsequent region framing cannot be trusted')
            if not matched:
                mismatches.append(name)
            print('Exact Flash readback:',name,len(data),'bytes', 'MISMATCH' if not matched else 'PASS',flush=True)
        session.until(b'READBACK END\r\n> ')
        self.op.report['readback_complete']=True
        self.op.report['flash_regions']={name:dict(matched=name not in mismatches) for name,_ in targets}
        self.op.report['device_identity']='Flash regions independently compared; gateware selected by programming, not read back'
        self.op.save()
        if mismatches:
            raise RuntimeError('Flash readback differs from selected artifact: '+', '.join(mismatches))

    def checked_readback(self, session, probe_image):
        try:
            self.readback(session,probe_image)
        except RuntimeError:
            if self.op.report.get('readback_complete'):
                try:
                    self.restore_loader(session)
                    self.op.record('readback_failure_recovery',restored_loader=True)
                except Exception as error:
                    self.op.record('readback_failure_recovery',restored_loader=False,error=str(error))
            raise

    def restore_loader(self,session):
        # Reload the complete SRAM design, including peripherals. A CPU-only
        # reset after a DDR utility is not a reliable SPI/DDR recovery boundary.
        session.port.reset_input_buffer()
        self.program('restore-sram',2,self.build.fs)
        self.enter_loader(session)

    def quiesce_xip(self, session, probe_image):
        startup=session.upload(probe_image)
        if b'FLASH READBACK' not in startup:
            raise RuntimeError('DDR utility did not start; external Flash write prohibited')
        session.port.write(b'q');session.port.flush()
        response=session.until(b'> ',15)
        marker=b'XIP QUIESCED enable=0 busy=0 spi_cs=idle'
        passed=marker in response and b'ERR ' not in response
        self.op.record('xip_quiesced',passed=passed,output=response.decode(errors='replace'))
        if not passed:
            raise RuntimeError('SPI quiesce was not acknowledged; external Flash write prohibited')

    def write_spi(self,session,write,path):
        data=Path(path).read_bytes()
        print('Software SPI write:',write['name'],len(data),'bytes',flush=True)
        self.op.record(write['name']+'_software_spi_started',offset=write['offset'],
                       bytes=len(data),sha256=sha256(path))
        session.port.write(b'w'+struct.pack('<II',write['offset'],len(data)));session.port.flush()
        while True:
            response=session.until(b'\r\n',120)
            if b'ERR ' in response:raise RuntimeError('Software SPI preflight/erase failed: '+repr(response))
            if response.endswith(b'READY WRITE\r\n'):break
        for sequence,start in enumerate(range(0,len(data),CHUNK)):
            session.port.write(packet(sequence,data[start:start+CHUNK]));session.port.flush()
            ack=bytearray();deadline=time.monotonic()+10
            while len(ack)<5 and time.monotonic()<deadline:
                ack.extend(session.port.read(5-len(ack)))
            if bytes(ack)!=b'K'+struct.pack('<I',sequence):
                session.note(ack)
                if ack.startswith(b'ERR '):ack.extend(session.until(b'\r\n',10))
                raise RuntimeError('Software SPI write/verify failed at packet '+str(sequence)+': '+repr(bytes(ack)))
        response=session.until(b'> ',15)
        if b'WRITE VERIFIED' not in response or b'ERR ' in response:
            raise RuntimeError('Software SPI completion missing')
        self.op.record(write['name']+'_software_spi',offset=write['offset'],bytes=len(data),
                       sha256=sha256(path),page_fragments_verified=True,jedec_checks=3)

    def verify_spi(self, probe_image):
        """Rehearse the update's SPI handoff without any external Flash write."""
        with self.connect() as port, (self.op.path/'uart.log').open('wb') as log:
            port.reset_input_buffer()
            self.program('sram',2,self.build.fs)
            session=BootSession(port,log)
            self.enter_loader(session)
            try:
                self.quiesce_xip(session,probe_image)
            finally:
                self.restore_loader(session)
            info=session.command('i')
            self.op.record('spi_handoff_restored',output=info.decode(errors='replace'),
                           flash_written=False)

    def flash_boot(self, session):
        session.port.write(b'f');session.port.flush()
        startup = session.until(b'> ')
        if b'BOOT FLASH' not in startup or b'SYSTEM READY' not in startup or b'ERR ' in startup:
            raise RuntimeError('Flash boot failed: '+startup.decode(errors='replace'))
        self.op.record('flash_boot',output=startup.decode(errors='replace'))

    def verify_flash(self, probe_image):
        with self.connect() as port, (self.op.path/'uart.log').open('wb') as log:
            port.reset_input_buffer()
            self.program('sram',2,self.build.fs)
            session = BootSession(port,log)
            self.enter_loader(session)
            self.checked_readback(session,probe_image)
            self.restore_loader(session)
            self.flash_boot(session)

    def update_flash(self, plan):
        """Full Flash write through the Gowin programmer only; needs no running
        loader, so it works when the board cannot boot and software SPI is
        unavailable. The FPGA configuration must be programmed before the Flash
        regions it addresses, so the XIP boot code is never fetched by a design
        that does not provide the XIP window.

        The programmer's raw Flash operation (index 32) sizes its stream from
        the file itself, so each region is staged as a file padded to the
        region's end; the extent check in program() still proves the written
        range starts at the requested offset."""
        staged=[]
        fs=self.op.path/'configuration.fs'
        fs.write_bytes(self.build.fs.read_bytes())
        if sha256(fs)!=self.build.validation['bitstream_sha256']:
            raise ValueError('Configuration changed during preflight')
        for write in plan['writes']:
            source=Path(write['path'])
            if sha256(source)!=write['sha256']:
                raise ValueError('Artifact changed during preflight: '+write['name'])
            data=source.read_bytes()
            staged_path=self.op.path/(write['name']+'.bin')
            staged_path.write_bytes(data)
            staged.append((write,staged_path))
        self.program('configuration',8,fs,0)
        for write,path in staged:
            self.program('flash-'+write['name'],32,path,write['offset'],True)
        self.program('reload',1)
        with self.connect() as port,(self.op.path/'uart.log').open('wb') as log:
            session=BootSession(port,log)
            # Canonical state wait: the loader auto-boot takes 2 s, so wait for
            # the application to reach APP_READY; a FAILED classification
            # (any ERR line) raises immediately instead of burning the timeout.
            startup,detail=session.wait_state(State.APP_READY,180,
                                              self.build.validation['boot_image']['abi_tag'])
            if b'BOOT FLASH' not in startup:
                raise RuntimeError('Programmer-written Flash boot did not boot from Flash: '+startup[-400:].decode(errors='replace'))
            self.op.record('flash_boot',output=startup.decode(errors='replace'),state=detail)

    def ensure_state(self,session,allowed=(State.LOADER,State.APP_READY)):
        """Drive the board to a known state before any operation that needs a
        live console. UNKNOWN/FAILED escalate: query, CPU reset, SRAM reload.
        Returns the FSM; raise with the walked ladder when it is exhausted."""
        from uart_state import BoardFSM
        fsm=BoardFSM(session.port,self.op.path/'uart.log',
                     self.build.validation['boot_image']['abi_tag'])
        fsm.observe()
        if not fsm.known(allowed):
            fsm.recover(program=lambda:self.program('recover-sram',2,self.build.fs),
                        allowed=allowed)
        self.op.record('board_state',state=fsm.state.value,
                       detail=fsm.detail,ladder=fsm.history)
        if not fsm.known(allowed):
            raise RuntimeError(f'board state {fsm.state.value}; recovery ladder exhausted: '+
                               repr(fsm.history))
        return fsm

    def update(self, plan, probe_image):
        # Stage raw inputs with absolute .bin paths: do not give Gowin relative
        # paths or an image extension that its file reader may misinterpret.
        staged_writes=[]
        fs=self.op.path/'configuration.fs'
        fs.write_bytes(self.build.fs.read_bytes())
        if sha256(fs)!=self.build.validation['bitstream_sha256']:
            raise ValueError('Configuration changed during preflight')
        if self.build.mode == 'xip':
            for write in plan['writes']:
                staged = self.op.path/(write['name']+'.bin')
                staged.write_bytes(Path(write['path']).read_bytes())
                if sha256(staged)!=write['sha256']:
                    raise ValueError('Artifact changed during preflight: '+write['name'])
                staged_writes.append((write,staged))
        with self.connect() as port, (self.op.path/'uart.log').open('wb') as log:
            # Acquire UART before any persistent write, so a busy/missing COM
            # port cannot leave half an update behind.
            session = BootSession(port,log)
            if staged_writes:
                port.reset_input_buffer()
                self.program('quiesce-sram',2,fs)
                self.enter_loader(session)
                self.quiesce_xip(session,probe_image)
            for write,staged in staged_writes:
                self.write_spi(session,write,staged)
            port.reset_input_buffer()
            self.program('configuration',8,fs,0)
            self.program('reload',1)
            self.enter_loader(session)
            if self.build.mode == 'rom':
                installed = session.upload(self.build.image,install=True)
                self.op.record('uart_install',output=installed.decode(errors='replace'))
            self.checked_readback(session,probe_image)
            self.restore_loader(session)
            self.flash_boot(session)


def status(operation, port='COM4', seconds=2):
    """Passive observation: opening serial sends no reset/menu/program command."""
    import serial
    from serial.tools import list_ports
    operation.report['ports'] = [dict(device=p.device,description=p.description,hwid=p.hwid)
                                 for p in list_ports.comports()]
    data = bytearray()
    with serial.Serial(port,115200,timeout=.1) as connection:
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            data.extend(connection.read(4096))
    (operation.path/'uart.log').write_bytes(data)
    state = ('loader' if b'BL> ' in data or b'BOOT SELECT:' in data else
             'application' if b'SYSTEM READY' in data or b'RISCV MINI BIOS' in data else 'unknown')
    operation.record('observation',state=state,output=data.decode(errors='replace'),
                     note='Silence does not prove a hang; no reset or query sent')
    print('UART',port+':',state,flush=True)
    if state=='unknown':print('No identifying output observed; this does not prove the board is hung.',flush=True)


def perform(args):
    build = None
    operation = Operation(args.operation)
    print('Operation logs:',operation.path,flush=True)
    try:
        if args.operation=='status':
            status(operation,args.port,args.seconds)
        else:
            build=Build(args.build,getattr(args,'image',None))
            operation.report['selected_build']=build.describe()
            operation.save()
            print('Selected build:',build.id,'boot='+build.mode,flush=True)
            if args.operation=='verify':
                features=build.validation.get('features',{})
                if args.suite in ('boot','cold') and not all(
                        features.get(n,True) for n in ('filesystem','video','spi_lcd','audio')):
                    raise ValueError('boot/cold suite requires the full monitor/BIOS peripheral profile; use firmware for minimal builds')
                if args.suite=='firmware' and args.soak_seconds and not all(
                        features.get(n,True) for n in ('filesystem','video','usb','audio')):
                    raise ValueError('Soak requires filesystem/video/USB/audio')
                if args.mic and not features.get('mic',True):
                    raise ValueError('Selected build has no microphone support')
                if args.suite=='spi' and build.mode!='xip':
                    raise ValueError('SPI handoff rehearsal requires an XIP build')
            plan = build.update_plan() if args.operation in ('update','repair-xip') or (
                args.operation=='verify' and args.suite in ('flash','spi')) else None
            if plan:
                operation.report['plan']=plan
            if getattr(args,'method','auto')=='programmer':
                operation.report['method']='gowin-programmer'
                operation.report['verification']='programmer SPI verify per region, then Flash boot; no DDR software SPI, no independent readback'
            if args.dry_run:
                if plan:
                    _,csr_path=build.csr()
                    operation.report['csr_metadata']=str(csr_path)
                operation.report['dry_run']=True
                operation.report['passed']=True
                operation.save()
                print(json.dumps(operation.report,indent=2,ensure_ascii=False))
                return operation
            board = Board(build,operation,args.port,args.location)
            probe = None
            if plan and not (args.operation=='update' and getattr(args,'method','auto')=='programmer'):
                probe,csr_path=compile_probe(build,operation.path/'probe',board.tools,
                                             writable=args.operation=='update')
                operation.record('probe_compiled',csr_metadata=csr_path)
            if args.operation=='run':
                board.run()
            elif args.operation=='recover':
                board.recover()
            elif args.operation=='update':
                if getattr(args,'method','auto')=='programmer':
                    board.update_flash(plan)
                else:
                    board.update(plan,probe)
            elif args.operation=='repair-xip':
                board.repair_xip(plan,probe)
            elif args.suite=='flash':
                board.verify_flash(probe)
            elif args.suite=='spi':
                board.verify_spi(probe)
            else:
                script={'firmware':'firmware_verify.py','boot':'boot_verify.py','cold':'cold_boot.py'}[args.suite]
                import sys
                command=[sys.executable,str(ROOT/'scripts'/script),'--output-dir',str(build.path),
                         '--log-dir',str(operation.path),'--port',args.port]
                if args.suite!='cold':
                    command+=['--program','--location',args.location]
                if args.suite=='firmware' and args.image:
                    command+=['--image',str(build.image_path)]
                if args.mic:command+=['--mic']
                if args.suite!='cold':
                    command+=['--soak-seconds',str(args.soak_seconds)]
                else:
                    command+=['--timeout',str(args.timeout)]
                board.runner(command,check=True,cwd=ROOT)
                filename={'firmware':'firmware-verification.json','boot':'boot-verification.json',
                          'cold':'cold-boot.json'}[args.suite]
                evidence=json.loads((operation.path/filename).read_text(encoding='utf-8'))
                if evidence.get('passed') is not True:
                    raise RuntimeError('Verification did not produce passing evidence')
                operation.record('verification',evidence=str(operation.path/filename))
        operation.report['passed']=True
        print('PASS:',operation.path/'result.json',flush=True)
    except Exception as error:
        operation.report['error']=str(error)
        operation.report['recovery_command']=['./mini.ps1','board','recover','--build',
            getattr(args,'build','current'),'--port',args.port,'--location',getattr(args,'location','107569')]
        operation.report['recovery_limit']='SRAM recovery needs intact XIP boot code in XIP builds; reprogram target XIP first if that region was damaged'
        raise
    finally:
        operation.save()
    return operation
