"""Read-only SPI Flash / TWI mux qualification through the public board path."""
from modular.storage import Store,file_hash
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload,payload_file
from mini_ops.mega138k_ae350.ae350_bus_contract import WORDS,judge,observe
from gateware.machines.mega138k_ae350 import address_map as amap


def perform(args):
    op=Operation('ae350-bus-suite');hardware=None
    print('Operation logs:',op.path,flush=True)
    try:
        root,ref,build_id=candidate(args.build)
        store=Store();manifest=store.get(ref)
        boot=store.get(manifest['dependencies']['bootloader']);abi=boot['config']['image_abi']
        payload_ref,payload_root=resolve_payload(store,args.payload,expected_abi=abi)
        payload=payload_file(payload_root).read_bytes()
        fs=root/'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref,fs_sha256=file_hash(fs),rom_build_id=build_id,
                         payload_artifact=payload_ref,payload_sha256=file_hash(payload_file(payload_root)),
                         flash_written=False,eeprom_written=False,
                         scope='user U7 Flash read identification and TWI mux EEPROM read; no XIP or storage writes')
        if args.dry_run:
            op.report.update(passed=True,dry_run=True);op.save();return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        from mini_ops.mega138k_ae350 import ae350_image
        from tools.boot_session import BootSession
        hardware=HardwareSession(op,port=args.port,cpu_jtag_location=args.cpu_jtag_location,location=args.location)
        session=BootSession(hardware.open_serial(),hardware.open_log('uart.log'))
        hardware.program(fs)
        op.record('banner',text=wait_loader(session,build_id,30))
        image=ae350_image.build(payload,abi);(op.path/'image.bin').write_bytes(image)
        session.upload(image,load=ae350_image.LOAD,abi=abi)
        # Never halt while SPI/I2C can still be receiving bytes.
        session.until(b'AE350 BUS DONE\r\n',args.timeout)
        cpu=hardware.halt()
        raw=cpu.read_memory(amap.RESULTS_BASE,WORDS*4)
        words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        trap_raw=cpu.read_memory(amap.RESULTS_BASE+amap.TRAP_RECORD*4,amap.TRAP_RECORD_WORDS*4)
        trap=[int.from_bytes(trap_raw[i:i+4],'little') for i in range(0,len(trap_raw),4)]
        pc=cpu.register(0x7b1)
        failures=judge(words,pc,amap.DDR_CODE,len(payload),trap)
        op.record('results',words=words,trap=trap,pc=hex(pc),**observe(words))
        op.record('assertions',failures=failures,passed=not failures)
        if failures:raise RuntimeError('Board bus suite failed: '+'; '.join(failures))
        hardware.close();hardware=None
        op.report.update(passed=True);op.save()
        print('PASS read-only Flash identification and TWI mux EEPROM connectivity',flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False,error=str(error));op.save();raise
    finally:
        if hardware is not None:hardware.close()
