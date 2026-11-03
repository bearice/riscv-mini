"""Upstream caches must be reconstructible and reject unrecorded edits."""
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'firmware')]
from scripts.modular.storage import Store,Run,file_hash
from scripts.modular.machines.mega138k_ae350.sourceprep import prepare,tree_hash
import vendor_install

# A port installer reduced to the manifest contract: tree argument, --machine, --inputs.
INSTALLER='''import argparse
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path,nargs='?')
parser.add_argument('--machine',required=True)
parser.add_argument('--inputs',action='store_true')
args=parser.parse_args()
if args.inputs:print(Path(__file__).resolve())
else:(args.source/'sample').write_text('after\\n')
'''


def upstream_archive(store):
    archive=store.build/'cache/downloads/source.tar.gz'
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive,'w:gz') as tar:
        data=b'before\n';member=tarfile.TarInfo('upstream/sample');member.size=len(data)
        tar.addfile(member,io.BytesIO(data))
    return archive


class SourceReplayTests(unittest.TestCase):
    def test_reconstruct_verify_and_reject_unsaved_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            expected=store.root/'expected'
            expected.mkdir()
            (expected/'sample').write_text('after\n')
            archive=upstream_archive(store)
            port=store.root/'firmware/opensbi'
            (port/'ae350').mkdir(parents=True)
            (port/'install.py').write_text(INSTALLER)
            spec=dict(archive=archive.name,url='unused',archive_sha256=file_hash(archive),patches=[],
                      installer='firmware/opensbi/install.py --machine mega138k_ae350',tree_sha256=tree_hash(expected))
            (port/'ae350/source.json').write_text(json.dumps(spec))
            source=store.build/'vendor/opensbi'
            run=Run('test','source-replay',root=store.root)
            prepare(store,run,'opensbi',source)
            self.assertEqual((source/'sample').read_text(),'after\n')
            self.assertEqual(list(run.result['upstream_source']['installer_inputs']),['firmware/opensbi/install.py'])
            prepare(store,run,'opensbi',source)
            (source/'sample').write_text('not saved in the port\n')
            with self.assertRaisesRegex(ValueError,'Unrecorded upstream edits'):
                prepare(store,run,'opensbi',source)

    def test_manifest_patches_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp);port=store.root/'firmware/opensbi/ae350';port.mkdir(parents=True)
            (port/'source.json').write_text(json.dumps(dict(archive='a',url='unused',archive_sha256='0'*64,
                patches=['firmware/opensbi/ae350/x.patch'],installer='firmware/opensbi/install.py',tree_sha256='0'*64)))
            with self.assertRaisesRegex(ValueError,'applied by the manifest installer'):
                prepare(store,Run('test','manifest-patches',root=store.root),'opensbi',store.build/'vendor/opensbi')

    def test_corrupt_cached_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp);port=store.root/'firmware/opensbi';(port/'ae350').mkdir(parents=True)
            (port/'install.py').write_text(INSTALLER)
            archive=store.build/'cache/downloads/source.tar.gz';archive.parent.mkdir(parents=True)
            archive.write_bytes(b'corrupt')
            (port/'ae350/source.json').write_text(json.dumps(dict(archive=archive.name,url='unused',
                archive_sha256='0'*64,patches=[],installer='firmware/opensbi/install.py --machine m',tree_sha256='0'*64)))
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                prepare(store,Run('test','bad-archive',root=store.root),'opensbi',store.build/'vendor/opensbi')


class VendorPatchTests(unittest.TestCase):
    def patch(self,folder,name,old,new):
        (folder/name).write_text('--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-%s\n+%s\n'%(old,new))

    def test_patches_build_on_each_other_and_conflicts_roll_back(self):
        with tempfile.TemporaryDirectory() as temp:
            tree=Path(temp)/'tree';tree.mkdir();patches=Path(temp)/'patches';patches.mkdir()
            (tree/'sample').write_text('one\n')
            self.patch(patches,'10-two.patch','one','two')
            self.patch(patches,'20-three.patch','two','three')
            vendor_install.apply_patches(tree,patches)
            vendor_install.apply_patches(tree,patches)
            self.assertEqual((tree/'sample').read_text(),'three\n')
            (tree/'sample').write_text('one\n')
            self.patch(patches,'30-conflict.patch','other','four')
            with self.assertRaisesRegex(RuntimeError,'30-conflict.patch conflicts'):
                vendor_install.apply_patches(tree,patches)
            self.assertEqual((tree/'sample').read_text(),'one\n')

    @unittest.skipUnless(subprocess.run(['which','patch'],capture_output=True).returncode==0,'patch not in PATH')
    def test_port_installers_list_their_inputs(self):
        for component,count in (('opensbi',2),('uboot',5),('linux',3)):
            listing=subprocess.run([sys.executable,str(ROOT/'firmware'/component/'install.py'),'--inputs',
                                    '--machine','mega138k_ae350'],check=True,capture_output=True,text=True).stdout.split()
            self.assertIn(str(ROOT/'firmware/vendor_install.py'),listing)
            self.assertIn(str(ROOT/'firmware'/component/'install.py'),listing)
            self.assertGreaterEqual(len(listing),count)


if __name__=='__main__':unittest.main()
