"""File evidence and explicit installed-bootloader compatibility gates."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'scripts'),str(Path(__file__).resolve().parents[2])]
from mini_ops.mega138k_ae350.ae350_p7_suite import judge_fs
from mini_ops.mega138k_ae350.ae350_upload import installed_provider,wait_provider
from mini_ops.mega138k_ae350.ae350_audio_readback import capture_prefix

class Contracts(unittest.TestCase):
    def test_completed_capture_prefix_is_independent_of_tail_cleanup(self):
        good=dict(passed=False,error='video capture cancelled',pattern_errors=0,captured_frames=108466,
            usb_timestamps=dict(window_seconds=1807.675,nonmonotonic=0,estimated_missing_frames=0))
        self.assertTrue(capture_prefix(good,1800))
        self.assertFalse(capture_prefix(good,1830))
        self.assertFalse(capture_prefix(dict(good,pattern_errors=1),1800))
        self.assertFalse(capture_prefix(dict(good,captured_frames=90000),1800))
        for field in ('nonmonotonic','estimated_missing_frames'):
            bad=dict(good,usb_timestamps=dict(good['usb_timestamps'],**{field:1}))
            self.assertFalse(capture_prefix(bad,1800))
    def test_file_checks_require_independent_crc_and_completion(self):
        w=[0]*96;w[0]=0x5037ffff;w[3]=25000000;w[5]=w[10]=w[11]=w[12]=1
        for i,(size,crc) in enumerate(zip((1<<20,16<<20,64<<20),(0x3d68bdcc,0x6ed0a415,0xc25f7ec4))):
            off=16+i*8;w[off:off+6]=[crc,crc,100,100,size,1]
        self.assertEqual(judge_fs(w,[0]*5),[])
        for index in (0,1,2,3,4,5,7,8,9,10,11,12,16,17,20,21,24,25,28,29,32,33,36,37):
            bad=w.copy();bad[index]^=1
            self.assertTrue(judge_fs(bad,[0]*5),index)
        bad=w.copy();bad[16]=bad[17]=123
        self.assertTrue(judge_fs(bad,[0]*5),'same-card repetition is not host evidence')
        self.assertTrue(judge_fs(w,[1,0,0,0,0]))
    def test_installed_firmware_is_explicit_and_contract_checked(self):
        selected={'component':'bootloader','digest':'new'}
        old={'component':'bootloader','digest':'installed'}
        config=dict(machine='mega138k_ae350',boot_contract_version=1,payload_base=0x3000)
        store=Mock()
        def get(ref,**kw):
            if ref=='gate':return {'dependencies':{'bootloader':selected}}
            return {'config':dict(config)}
        store.get.side_effect=get
        self.assertEqual(installed_provider(SimpleNamespace(),store,'gate'),selected)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'receipt.json'
            receipt=dict(passed=True,installed=True,operation='ae350-bootrom',bootloader=old)
            path.write_text(json.dumps(receipt))
            self.assertEqual(installed_provider(SimpleNamespace(bootrom_install=path),store,'gate'),old)
            def changed(ref,**kw):
                result=get(ref,**kw)
                if ref==old:result['config']['payload_base']=0x4000
                return result
            store.get.side_effect=changed
            with self.assertRaisesRegex(ValueError,'payload_base'):
                installed_provider(SimpleNamespace(bootrom_install=path),store,'gate')
            receipt['passed']=False;path.write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):installed_provider(SimpleNamespace(bootrom_install=path),store,'gate')
    def test_receipt_does_not_replace_live_full_rom_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'outputs').mkdir();(root/'outputs/boot.bin').write_bytes(b'boot bytes')
            store=Mock();store.get.return_value={'config':dict(build_id='abcd',rom_base=0x80000000)};store.path.return_value=root
            session=Mock();session.fresh_banner.return_value=b'riscv-mini AE350 ROM abcd\r\nPOST 600d0000\r\nDDR INIT OK\r\nBL> '
            hardware=Mock();hardware.halt.return_value.read_memory.return_value=b'boot bytes'
            wait_provider(session,hardware,store,{},1)
            hardware.halt.return_value.read_memory.assert_called_with(0x80000000,10)
            hardware.halt.return_value.read_memory.return_value=b'wrong'
            with self.assertRaisesRegex(RuntimeError,'readback mismatch'):wait_provider(session,hardware,store,{},1)
            self.assertEqual(hardware.resume.call_count,2)
            hardware.close_cpu.assert_called_once()

if __name__=='__main__':unittest.main()
