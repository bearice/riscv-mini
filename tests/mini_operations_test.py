"""Exercise user workflows with a simulated programmer and UART loader."""
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zlib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from boot_image import HEADER, abi_tag, pack_image
from boot_upload import require_install_supported
from mini_ops.artifacts import Build, configuration_bytes
from mini_ops.board import Board, Operation
from mini import main
from mini import parser
from mini_ops.build import invocation


def digest(data):return hashlib.sha256(data).hexdigest()


class Port:
    def __init__(self,build,corrupt=False):
        self.build=build;self.data=bytearray();self.state=None;self.received=bytearray()
        self.commands=[];self.corrupt=corrupt;self.flush=lambda:None;self.quiesce_failure=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def reset_input_buffer(self):self.data.clear()
    def startup(self):self.data.extend(b'\r\nriscv-mini XIP test-version\r\nDDR INIT 1\r\nDDR READY\r\nBOOT SELECT:')
    def read(self,n):
        if not self.data:raise AssertionError('Workflow waited past simulated response')
        result=bytes(self.data[:n]);del self.data[:n];return result
    def write(self,data):
        if self.state=='spi-payload':
            seq,count=struct.unpack_from('<IH',data)
            assert zlib.crc32(data[:-4])==struct.unpack_from('<I',data,len(data)-4)[0]
            self.received.extend(data[6:6+count]);self.data.extend(b'K'+struct.pack('<I',seq))
            if len(self.received)==self.expected:
                self.state=None;self.data.extend(b'WRITE VERIFIED\r\n> ')
            return
        if self.state=='header':
            self.expected=HEADER.unpack(data)[4];self.state='payload'
            self.data.extend(b'READY DATA\r\n');return
        if self.state=='payload':
            seq,count=struct.unpack_from('<IH',data)
            self.received.extend(data[6:6+count]);self.data.extend(b'K'+struct.pack('<I',seq))
            if len(self.received)==self.expected:
                self.state=None
                self.data.extend(b'FLASH INSTALLED\r\nBL> ' if self.install else
                    b'SYSTEM READY - FLASH READBACK\r\n> ')
            return
        self.commands.append(data)
        if data[:1]==b'w':
            if getattr(self,'spi_reject',False):
                self.data.extend(b'ERR WRITE JEDEC\r\n> ');return
            self.offset,self.expected=struct.unpack('<II',data[1:])
            self.received.clear();self.state='spi-payload'
            self.data.extend(b'ERASING\r\nREADY WRITE\r\n');return
        if data in (b'u',b'p'):
            self.state='header';self.install=data==b'p';self.received.clear()
            self.data.extend(b'READY HEADER\r\n')
        elif data==b'b':self.data.extend(b' BL> ')
        elif data==b'!':self.startup()
        elif data==b'i':self.data.extend(b'FLASH HEADER VALID\r\nBL> ')
        elif data==b'f':self.data.extend(b'BOOT FLASH\r\nSYSTEM READY\r\n> ')
        elif data==b'q':self.data.extend(b'ERR XIP QUIESCE\r\n> ' if self.quiesce_failure else
                                       b'XIP QUIESCED enable=0 busy=0 spi_cs=idle\r\n> ')
        elif data==b'r':
            image=bytearray(self.build.image)
            if self.corrupt:image[-1]^=1
            self.data.extend(b'READBACK BEGIN\r\n'+image)
            if self.build.mode=='xip':self.data.extend((self.build.path/'firmware/xip.bin').read_bytes())
            self.data.extend(b'READBACK END\r\n> ')
        else:raise AssertionError(data)


class MiniOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'artifact';(self.path/'firmware').mkdir(parents=True)
        (self.path/'gateware').mkdir()
        self.fs=b'// Gowin FS\n'+b'0'*8192+b'\n'
        self.image=pack_image(b'BIOS payload',42);self.xip=b'boot code'*4
        (self.path/'gateware/riscv_mini.fs').write_bytes(self.fs)
        (self.path/'firmware/app.img').write_bytes(self.image)
        (self.path/'firmware/xip.bin').write_bytes(self.xip)
        self.v=dict(synthesis_requested=True,timing_violated_endpoints={'setup':0,'hold':0},
                    bitstream_sha256=digest(self.fs),boot_image=dict(abi_tag=42,sha256=digest(self.image)),
                    firmware_bytes=len(self.xip),boot_mode='xip',features={'flash':True},
                    xip=dict(base=0xf3000000,flash_offset=0x100000,reset_address=0xf3100000,
                             reserved_bytes=0x100000,sha256=digest(self.xip)))
        self.save()
    def tearDown(self):self.temp.cleanup()
    def save(self):(self.path/'validation.json').write_text(json.dumps(self.v))
    def build(self):return Build(str(self.path))
    def backend(self,corrupt=False,output_error=None):
        build=self.build();op=Operation('update',build,self.root);port=Port(build,corrupt)
        calls=[]
        def runner(command,stdout,**kwargs):
            calls.append(command);index=command[command.index('--operation_index')+1]
            if index=='32':
                offset=int(command[command.index('--spiaddr')+1],16)
                file=Path(command[command.index('--mcuFile')+1]);self.assertTrue(file.is_absolute())
                self.assertEqual(file.suffix,'.bin')
                stdout.write(output_error or f'SPI start of address: {offset:#x}\nSPI end of address: {offset:#x}\nFinished.\n')
            else:
                stdout.write('Finished.\n')
                if index in ('1','2'):port.startup()
            return subprocess.CompletedProcess(command,0)
        board=Board(build,op,runner=runner,serial_factory=lambda *a,**k:port,
                    tools={'programmer':str(Path(sys.executable))})
        return build,op,board,port,calls
    def test_xip_update_writes_three_artifacts_and_verifies_before_boot(self):
        build,op,board,port,calls=self.backend()
        board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['2','8','1','2'])
        self.assertNotIn(b'p',port.commands)
        self.assertEqual(port.commands[-2:],[b'b',b'f'])
        self.assertTrue(all(s['matched'] for s in op.report['steps'] if 'matched' in s))
    def test_rom_uses_uart_install_then_exact_readback(self):
        self.v['boot_mode']='rom';self.save()
        build,op,board,port,calls=self.backend()
        board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertIn(b'p',port.commands)
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['8','1','2'])
    def test_corrupt_readback_stops_before_flash_boot(self):
        build,op,board,port,_=self.backend(corrupt=True)
        with self.assertRaisesRegex(RuntimeError,'readback differs'):
            board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertNotIn(b'f',port.commands)
        application=next(s for s in op.report['steps'] if s['name']=='application_readback')
        self.assertEqual(application['matched'],False)
        self.assertTrue((op.path/'application-readback.bin').is_file())
    def test_application_mismatch_still_collects_xip(self):
        build,op,board,port,_=self.backend(corrupt=True)
        with self.assertRaisesRegex(RuntimeError,'readback differs'):
            board.verify_flash(pack_image(b'probe',42))
        self.assertTrue((op.path/'xip-readback.bin').is_file())
        self.assertEqual((op.path/'xip-readback.bin').read_bytes(),self.xip)
        self.assertNotIn(b'f',port.commands)
    def test_stale_binary_errors_do_not_override_fresh_startup(self):
        build,op,board,port,_=self.backend()
        port.data.extend(b'ERR DDR INIT; reset\r\nBOOT SELECT:\x00\r\nriscv-mini XIP stale\r\n\x00DDR INIT \x00ERR DDR INIT; reset\r\n')
        port.startup()
        from boot_upload import BootSession
        startup=board.menu(BootSession(port))
        self.assertNotIn(b'ERR ',startup)
        self.assertIn(b'DDR READY',startup)
    def test_real_ddr_failure_after_banner_is_rejected(self):
        build,op,board,port,_=self.backend()
        port.data.extend(b'\r\nriscv-mini XIP test-version\r\nDDR INIT 1\r\nERR DDR INIT; reset\r\n')
        from boot_upload import BootSession
        with self.assertRaisesRegex(RuntimeError,'DDR initialization failed'):
            board.menu(BootSession(port))
    def test_spi_diagnostic_only_programs_sram_and_restores_loader(self):
        build,op,board,port,calls=self.backend()
        board.verify_spi(pack_image(b'probe',42))
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['2','2'])
        self.assertEqual(port.commands[-2:],[b'b',b'i'])
        self.assertFalse(op.report['steps'][-1]['flash_written'])
    def test_xip_update_quiesces_before_software_spi_writes(self):
        build,op,board,port,calls=self.backend()
        board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertIn(b'q',port.commands)
        self.assertLess([s['name'] for s in op.report['steps']].index('xip_quiesced'),
                        [s['name'] for s in op.report['steps']].index('xip_software_spi'))
    def test_handoff_end_before_start_stops_before_configuration(self):
        build,op,board,_,calls=self.backend(output_error='SPI start of address: 0x100000\nSPI end of address: 0xfff00\nFinished.\n')
        with self.assertRaises(RuntimeError):board.program('xip',32,build.path/'firmware/xip.bin',0x100000,True)
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['32'])
    def test_quiesce_failure_blocks_all_persistent_writes(self):
        build,op,board,port,calls=self.backend()
        port.quiesce_failure=True
        with self.assertRaisesRegex(RuntimeError,'quiesce was not acknowledged'):
            board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['2'])
    def test_software_spi_jedec_failure_blocks_configuration(self):
        build,op,board,port,calls=self.backend();port.spi_reject=True
        with self.assertRaisesRegex(RuntimeError,'WRITE JEDEC'):
            board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['2'])
    def test_repair_xip_accepts_old_application_only_after_xip_readback(self):
        build,op,board,port,calls=self.backend(corrupt=True)
        board.repair_xip(build.update_plan(),pack_image(b'probe',42))
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls],['32','2','2'])
        self.assertTrue(op.report['flash_regions']['xip']['matched'])
        self.assertEqual(op.report['steps'][-1]['name'],'xip_repaired')
    def test_real_capacity_failure_is_not_success_despite_finished(self):
        build,op,board,port,calls=self.backend()
        log=(ROOT/'tests/fixtures/gowin-capacity-rejection.log').read_text()
        def runner(command,stdout,**kw):
            stdout.write(log);return subprocess.CompletedProcess(command,0)
        board.runner=runner
        with self.assertRaises(RuntimeError):
            board.program('application',32,build.path/'firmware/app.img',0x200000,True)
        self.assertEqual(op.report['steps'][-1]['detected_flash_ids'],['0x0B4013'])
    def test_specific_verify_warning_requires_independent_readback(self):
        build,op,board,port,_=self.backend(output_error='SPI start of address: 0x100000\nSPI end of address: 0x100000\nError: SPI Verify failed!\nFinished.\n')
        board.program('xip',32,build.path/'firmware/xip.bin',0x100000,True)
        self.assertTrue(op.report['steps'][-1]['requires_independent_readback'])
    def test_other_programmer_error_is_never_accepted(self):
        build,_,board,_,_=self.backend(output_error='SPI start of address: 0x100000\nSPI end of address: 0x100000\nError: Error found!\nFinished.\n')
        with self.assertRaises(RuntimeError):board.program('xip',32,build.path/'firmware/xip.bin',0x100000,True)
    def test_changed_xip_rejected_by_preflight(self):
        (self.path/'firmware/xip.bin').write_bytes(b'other')
        with self.assertRaisesRegex(ValueError,'XIP binary'):self.build().update_plan()
    def test_partition_overlap_rejected(self):
        self.v['xip']['flash_offset']=4096;self.v['xip']['reset_address']=0xf3001000;self.save()
        with self.assertRaisesRegex(ValueError,'does not fit'):self.build().update_plan()
    def test_timing_metadata_must_be_complete(self):
        self.v['timing_violated_endpoints']={};self.save()
        with self.assertRaisesRegex(ValueError,'PnR'):self.build()
    def test_incompatible_override_rejected(self):
        other=self.root/'other.img';other.write_bytes(pack_image(b'other',43))
        with self.assertRaisesRegex(ValueError,'ABI'):Build(str(self.path),other)
    def test_legacy_install_guard_before_serial_or_programmer(self):
        with self.assertRaisesRegex(ValueError,'XIP'):require_install_supported(self.v)
        with patch('boot_upload.serial.Serial') as serial,patch('boot_upload.program') as programmer:
            from boot_upload import main as upload
            with patch.object(sys,'argv',['boot_upload','--output-dir',str(self.path),'--configure-flash','--mode','install']):
                with self.assertRaises(ValueError):upload()
            serial.assert_not_called();programmer.assert_not_called()
    def test_probe_metadata_must_match_build_abi(self):
        csr=dict(csr_registers={},memories={})
        (self.path/'csr.json').write_text(json.dumps(csr))
        with self.assertRaisesRegex(ValueError,'CSR metadata ABI'):self.build().csr()
    def test_missing_probe_metadata_prevents_flash_mutation(self):
        with self.assertRaisesRegex(ValueError,'Missing csr'):self.build().csr()
    def test_logs_never_overwrite_previous_operation(self):
        a=Operation('run',self.build(),self.root);b=Operation('run',self.build(),self.root)
        a.record('failure',error='preserved')
        self.assertNotEqual(a.path,b.path)
        self.assertEqual(json.loads((a.path/'result.json').read_text())['steps'][0]['error'],'preserved')
    def test_fs_format_must_be_known(self):
        self.assertEqual(configuration_bytes(self.path/'gateware/riscv_mini.fs'),4096)
        (self.path/'gateware/riscv_mini.fs').write_text('opaque data')
        with self.assertRaisesRegex(ValueError,'Unsupported FS'):configuration_bytes(self.path/'gateware/riscv_mini.fs')
    def test_cli_rejects_image_override_ignored_by_suite(self):
        with self.assertRaises(SystemExit):main(['board','verify','--suite','cold','--image','other.img'])
    def test_busy_serial_port_stops_before_any_persistent_write(self):
        build,op,board,_,calls=self.backend()
        board.serial_factory=lambda *a,**k: (_ for _ in ()).throw(OSError('port busy'))
        with self.assertRaisesRegex(OSError,'port busy'):
            board.update(build.update_plan(),pack_image(b'probe',42))
        self.assertEqual(calls,[])
    def test_changed_application_rejected_before_update(self):
        build=self.build()
        (self.path/'firmware/app.img').write_bytes(pack_image(b'replacement',42))
        with self.assertRaisesRegex(ValueError,'changed during preflight'):build.update_plan()
    def test_minimal_build_needs_no_existing_cpu_or_board(self):
        args=parser().parse_args(['build','--minimal'])
        with patch('mini_ops.build.Build') as build:
            command=invocation(args)
        build.assert_not_called()
        self.assertEqual(command[-4:],['--profile','minimal','--purpose','development'])
    def test_build_template_preserves_xip_and_feature_configuration(self):
        self.v.update(profile='full',application_source=str(ROOT/'firmware/bios/main.c'),
                      cpu_verilog=str(self.path/'cpu.v'),cpu_variant='linux',rom_size_bytes=0,
                      l2_size_bytes=4096,sd_profile='lite',usb_backend='ultra',audio_clock='dds',
                      place_option=3,route_option=2)
        (self.path/'cpu.v').write_text('// cpu')
        self.save()
        command=invocation(parser().parse_args(['build','--from',str(self.path),'--synthesize']))
        self.assertEqual(command[command.index('--boot-mode')+1],'xip')
        self.assertEqual(command[command.index('--place-option')+1],'3')
        self.assertIn('--with-flash',command)
        self.assertIn('--synthesize',command)
    def test_release_csr_is_self_contained_after_original_run_disappears(self):
        import release
        csr=dict(csr_registers={},memories={})
        tag=abi_tag(csr)
        image=pack_image(b'BIOS',tag)
        (self.path/'firmware/app.img').write_bytes(image)
        self.v['boot_image']=dict(abi_tag=tag,sha256=digest(image));self.save()
        (self.path/'csr.json').write_text(json.dumps(csr))
        (self.path/'build-info.json').write_text('{}')
        recipe=self.root/'recipe';recipe.mkdir();(recipe/'recipe.json').write_text('{"arguments":[]}')
        (self.root/'.tools.local.json').write_text('{}')
        destination=self.root/'release';destination.mkdir()
        with patch.object(release,'ROOT',self.root):
            release.package(self.path,destination,dict(recipe=str(recipe),commit='abcdef',version='0.8.0',inputs_sha256='abc'))
        import shutil
        shutil.rmtree(self.path)
        relocated=Build(str(destination))
        self.assertEqual(relocated.csr()[0],csr)
    def test_failed_preflight_has_receipt_without_board_access(self):
        from mini_ops.board import perform
        self.v['timing_violated_endpoints']={};self.save()
        args=parser().parse_args(['board','update','--build',str(self.path)])
        factory=lambda name,*a:Operation(name,root=self.root)
        with patch('mini_ops.board.Operation',side_effect=factory),patch('mini_ops.board.Board') as board:
            with self.assertRaisesRegex(ValueError,'PnR'):perform(args)
        board.assert_not_called()
        receipt=next((self.root/'build/operations').glob('*/result.json'))
        result=json.loads(receipt.read_text())
        self.assertFalse(result['passed']);self.assertIn('PnR',result['error'])
    def test_unsupported_suite_stops_before_sram_programming(self):
        from mini_ops.board import perform
        self.v['features']['filesystem']=False;self.save()
        args=parser().parse_args(['board','verify','--suite','cold','--build',str(self.path)])
        factory=lambda name,*a:Operation(name,root=self.root)
        with patch('mini_ops.board.Operation',side_effect=factory),patch('mini_ops.board.Board') as board:
            with self.assertRaisesRegex(ValueError,'full monitor'):perform(args)
        board.assert_not_called()


if __name__=='__main__':unittest.main()
