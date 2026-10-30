"""UART-based full-speed USB enumeration evidence; physical input is separate."""
import re
from modular.storage import Store, file_hash
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate, hardware_abi
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
from mini_ops.mega138k_ae350.ae350_upload import installed_provider, wait_provider


def judge_usb(text):
    devices=re.findall(r'USB DEVICE ([0-9a-f]{8})',text)
    status=re.findall(r'VIDPID ([0-9a-f]{8}) HID ([0-9a-f]{8}) REPORTS ([0-9a-f]{8})',text)
    hid=len(re.findall(r'USB HID\r?\n',text))
    reports=len(re.findall(r'USB REPORT ',text))
    if status:
        vid,hcount,rcount=status[-1]
        if vid!='00000000' and vid not in devices:devices.append(vid)
        hid=max(hid,int(hcount,16));reports=max(reports,int(rcount,16))
    raw=re.findall(r'RAW ([0-9a-f]{8})',text)
    raw=int(raw[-1],16) if raw else None
    control=re.findall(r'CTRL ([0-9a-f]{8})',text)
    control=int(control[-1],16) if control else None
    failures=[]
    if 'USB BEGIN' not in text:failures.append('missing payload start')
    if 'USB ENUM PASS' not in text:failures.append('enumeration did not complete successfully')
    if not devices or not hid:failures.append('missing VID/PID or HID interface')
    return dict(devices=devices,hid_interfaces=hid,reports_received=reports,
                raw_status=raw,raw_lines=None if raw is None else raw&3,
                pll_locked=None if raw is None else bool(raw&8),
                tx_enabled=None if raw is None else bool(raw&4),controller_control=control,
                detach_events=text.count('USB DETACH'),failures=failures,
                physical_keypress_verified=False)


def perform(args):
    op=Operation('ae350-usb-suite');hardware=None
    print('Operation logs:',op.path,flush=True)
    try:
        root,ref,_=candidate(args.build);store=Store();abi=hardware_abi(store,ref)
        provider=installed_provider(args,store,ref)
        pref,proot=resolve_payload(store,args.payload,expected_abi=abi)
        binary=payload_file(proot)
        op.report.update(artifact=ref,payload_artifact=pref,payload_sha256=file_hash(binary),
                         flash_written=False,scope='USB FS enumeration and HID report observation')
        if args.dry_run:op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350 import ae350_image
        from tools.boot_session import BootSession
        hardware=HardwareSession(op,port=args.port,location=args.location,cpu_jtag_location=args.cpu_jtag_location)
        session=BootSession(hardware.open_serial(),hardware.open_log('uart.log'))
        hardware.program(root/'gateware/impl/pnr/project.fs', cable_serial=args.cable_serial)
        op.record('banner',text=wait_provider(session,hardware,store,provider,30))
        image=ae350_image.build(binary.read_bytes(),abi);(op.path/'image.bin').write_bytes(image)
        session.upload(image,load=ae350_image.LOAD,abi=abi)
        print('USB payload running; collecting enumeration and HID reports',flush=True)
        line=bytearray()
        def progress(chunk):
            line.extend(chunk)
            if chunk==b'\n':
                print(line.decode(errors='replace').strip(),flush=True);line.clear()
        text=session.until(b'USB ENUM ',args.timeout,on_chunk=progress)
        text+=session.until(b'\r\n',5)
        result=judge_usb(text.decode(errors='replace'));op.record('usb',**result)
        if result['failures']:raise RuntimeError('USB suite failed: '+repr(result['failures']))
        op.report.update(passed=True);op.save();return op
    except Exception as error:
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        if hardware is not None:hardware.close()
