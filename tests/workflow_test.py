"""Workflow gates: identity mismatch, bad performance, scope drift and replay/cleanup."""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import stat
from unittest.mock import patch
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from scripts.build_records import get_record, pin, register, source_identity, write_json
from scripts.build_recipe import create_recipe, reproduce, verify_recipe
from builds import clean_build
from performance_report import compare, load_measurements
from prepare_commit import scope
from release import release_version, verify_bundle
from versions import COMPONENTS, bundle_versions, read_versions


def sha(data):return hashlib.sha256(data).hexdigest()


def artifact(path, failed=False):
    (path/'firmware').mkdir(parents=True)
    (path/'gateware').mkdir()
    image=b'image';bitstream=b'bitstream'
    (path/'firmware/app.img').write_bytes(image)
    (path/'gateware/riscv_mini.fs').write_bytes(bitstream)
    v=dict(profile='full',isa='rv32imafc_zicsr_zifencei',rom_size_bytes=4096,l2_size_bytes=4096,
           synthesis_requested=True,timing_violated_endpoints={'setup':1 if failed else 0,'hold':0},
           clock_hz=60000000,ddr_clock_hz=120000000,features={'mmu':True,'fpu':True},
           boot_image=dict(sha256=sha(image),abi_tag=42),bitstream_sha256=sha(bitstream))
    write_json(path/'validation.json',v)
    return v


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()
    def entry(self, failed=False, folder='build/runs/test'):
        output=self.root/folder
        v=artifact(output,failed)
        recipe=self.root/'build/recipes/test';recipe.mkdir(parents=True,exist_ok=True)
        write_json(recipe/'recipe.json',dict(files={},source={'commit':'abc'},arguments=[]))
        r=register(output,'test',dict(kind='captured',commit='abcdef',recipe=str(recipe)),root=self.root)
        return r,v
    def evidence(self,v,**overrides):
        path=self.root/'board.json'
        write_json(path,dict(passed=True,bitstream_sha256=v['bitstream_sha256'],image_sha256=v['boot_image']['sha256'],**overrides))
        return path
    def test_pin_rejects_fail_and_mismatched_image(self):
        r,v=self.entry()
        e=self.evidence(v)
        data=json.loads(e.read_text());data['image_sha256']='wrong';write_json(e,data)
        with self.assertRaises(ValueError):pin('current',r['id'],e,self.root)
        pin('current',r['id'],self.evidence(v),self.root)
        self.assertEqual(get_record('current',self.root)['status'],'board-qualified')
        (Path(r['path'])/'firmware/app.img').write_bytes(b'changed')
        with self.assertRaises(ValueError):get_record('current',self.root)
    def test_failed_pnr_cannot_be_pinned(self):
        r,v=self.entry(failed=True)
        with self.assertRaises(ValueError):pin('current',r['id'],self.evidence(v),self.root)
    def test_historical_registration_is_not_latest(self):
        path=self.root/'build/history';artifact(path)
        register(path,'historical',dict(kind='historical-association',commit='abc'),root=self.root)
        with self.assertRaises(ValueError):get_record('latest-pnr',self.root)
    def measurement(self,v,**changes):
        directory=self.root/'measurement';directory.mkdir(exist_ok=True)
        row=dict(name='cpu.xorshift',size=6,count=100,ticks=1000,round=1,passed=True)
        row.update(changes)
        write_json(directory/'results.json',dict(clock_hz=60000000,build_identity=dict(bitstream_sha256=v['bitstream_sha256'],image_sha256=v['boot_image']['sha256']),rows=[row]))
        return directory
    def test_failed_zero_and_missing_identity_are_rejected(self):
        _,v=self.entry()
        for changes in ({'passed':False},{'ticks':0}):
            with self.assertRaises(ValueError):load_measurements([self.measurement(v,**changes)],v)
        d=self.measurement(v);raw=json.loads((d/'results.json').read_text());raw.pop('build_identity');write_json(d/'results.json',raw)
        with self.assertRaises(ValueError):load_measurements([d],v)
        load_measurements([d],v,allow_legacy=True)
    def test_measurement_workload_and_build_mismatch(self):
        r,v=self.entry();d=self.measurement(v)
        old=self.root/'old';old.mkdir()
        raw=json.loads((d/'results.json').read_text());raw['rows'][0]['count']=99;write_json(old/'results.json',raw)
        with self.assertRaises(ValueError):compare(r,r,[d],[old])
        raw['build_identity']['bitstream_sha256']='wrong';write_json(old/'results.json',raw)
        with self.assertRaises(ValueError):load_measurements([old],v)
    def test_cleanup_protects_pins_and_releases(self):
        r,v=self.entry();pin('current',r['id'],self.evidence(v),self.root)
        with self.assertRaises(ValueError):clean_build(r['id'],True,self.root)
        alias=register(r['path'],'alias',r['source'],self.root)
        with self.assertRaises(ValueError):clean_build(alias['id'],True,self.root)
        r2,_=self.entry(folder='build/releases/final')
        with self.assertRaises(ValueError):clean_build(r2['id'],True,self.root)
    def test_cleanup_retains_recipe(self):
        r,_=self.entry();clean_build(r['id'],True,self.root)
        self.assertFalse(Path(r['path']).exists());verify_recipe(r['source']['recipe'])
        with self.assertRaises(ValueError):get_record('latest-generated',self.root)
    def test_cleanup_handles_readonly_gowin_outputs(self):
        r,_=self.entry()
        binary=Path(r['path'])/'gateware/project.bin';binary.write_bytes(b'Gowin output');binary.chmod(stat.S_IREAD)
        clean_build(r['id'],True,self.root)
        self.assertFalse(Path(r['path']).exists())
    def test_interrupted_cleanup_can_be_retried(self):
        r,_=self.entry()
        with patch('builds.shutil.rmtree',side_effect=PermissionError('in use')):
            with self.assertRaises(PermissionError):clean_build(r['id'],True,self.root)
        (Path(r['path'])/'validation.json').unlink()
        clean_build(r['id'],True,self.root)
        self.assertFalse(Path(r['path']).exists())
    def test_final_bundle_tampering(self):
        p=self.root/'final';artifact(p)
        files={str(f.relative_to(p)).replace('\\','/'):sha(f.read_bytes()) for f in p.rglob('*') if f.is_file()}
        write_json(p/'release.json',dict(state='complete',files=files))
        verify_bundle(p);(p/'firmware/app.img').write_bytes(b'changed')
        with self.assertRaises(ValueError):verify_bundle(p)
    def init_git(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        (self.root/'scripts').mkdir()
        (self.root/'scripts/build.py').write_text("from pathlib import Path\nimport sys\np=Path(sys.argv[-1]);p.mkdir(parents=True);(p/'marker').write_text('original')\n")
        (self.root/'.tools.local.json').write_text('{}')
        subprocess.run(['git','add','scripts'],cwd=self.root,check=True)
        subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],cwd=self.root,check=True)
    def test_scope_tracks_index_and_excludes_self_reference(self):
        self.init_git()
        code=self.root/'scripts/build.py';code.write_text(code.read_text()+'# change\n')
        subprocess.run(['git','add','scripts'],cwd=self.root,check=True)
        a=scope(root=self.root)
        (self.root/'CHANGELOG.md').write_text('generated log')
        subprocess.run(['git','add','CHANGELOG.md'],cwd=self.root,check=True)
        self.assertEqual(a,scope(root=self.root))
        code.write_text(code.read_text()+'# second change\n')
        subprocess.run(['git','add','scripts'],cwd=self.root,check=True)
        self.assertNotEqual(a['patch_sha256'],scope(root=self.root)['patch_sha256'])
    def test_dirty_recipe_replays_commit_patch_and_untracked_inputs(self):
        self.init_git()
        code=self.root/'scripts/build.py';code.write_text(code.read_text().replace('original','dirty'))
        (self.root/'scripts/new.py').write_text('untracked input')
        recipe=create_recipe(self.root/'build/runs/example',source_identity(self.root),[],root=self.root)
        replay=reproduce(recipe,sys.executable,self.root/'result',root=self.root)
        self.assertEqual((self.root/'result/marker').read_text(),'dirty')
        self.assertEqual((replay/'scripts/new.py').read_text(),'untracked input')

    def test_release_version_validation(self):
        self.assertEqual(release_version('v0.7.0'), '0.7.0')
        self.assertEqual(release_version('0.7.0-rc.1'), '0.7.0-rc.1')
        for value in ('../0.7.0','01.7.0','0.7','0.7.0-..','0.7.0-rc.01'):
            with self.assertRaises(ValueError):release_version(value)

    def test_version_input_is_preserved_when_replaying(self):
        self.init_git()
        before=source_identity(self.root)['inputs_sha256']
        (self.root/'VERSIONS.yaml').write_text('rtl: 0.7.0\nbootloader: 0.7.0\nhal: 0.7.0\nbios: 0.7.0\nopensbi: 0.7.0\nuboot: 0.1.0\n')
        self.assertNotEqual(before,source_identity(self.root)['inputs_sha256'])
        recipe=create_recipe(self.root/'build/runs/version',source_identity(self.root),[],root=self.root)
        replay=reproduce(recipe,sys.executable,self.root/'result',root=self.root)
        self.assertEqual((replay/'VERSIONS.yaml').read_text(),(self.root/'VERSIONS.yaml').read_text())

    def test_other_commits_cannot_implicitly_write_changelog(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_commit.py'),'--title','fixture',
            '--change','fixture','--write-changelog','--output',str(self.root/'scope.md')],capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertIn(b'requires --component and an explicit --release-version',result.stderr)

    def test_changelog_component_version_must_match_manifest(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_commit.py'),'--title','fixture',
            '--change','fixture','--write-changelog','--component','bios','--release-version','9.9.9',
            '--output',str(self.root/'scope.md')],capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertIn(b'does not match VERSIONS.yaml',result.stderr)

    def test_component_versions_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'VERSIONS.yaml'
            path.write_text('rtl: 0.7.1\nbootloader: 0.7.1\nhal: 0.7.1\nbios: 0.7.2\nopensbi: 0.7.1\nuboot: 0.1.0\n')
            self.assertEqual(read_versions(path)['bios'],'0.7.2')
            path.write_text('rtl: 0.7\nbios: 0.7.0\n')
            with self.assertRaises(ValueError):read_versions(path)
            rel=Path(tmp)/'RELEASES.yaml'
            rel.write_text('0.7.2:\n  '+'\n  '.join(f'{c}: 0.7.1' for c in COMPONENTS)+'\n')
            self.assertEqual(set(bundle_versions('0.7.2',rel)),set(COMPONENTS))
            with self.assertRaises(ValueError):bundle_versions('9.9.9',rel)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path);a=p.parse_args()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkflowTests))
    if a.report:write_json(a.report,dict(passed=result.wasSuccessful(),tests_run=result.testsRun,
                         command=[sys.executable,*sys.argv],source_inputs_sha256=source_identity()['inputs_sha256']))
    raise SystemExit(0 if result.wasSuccessful() else 1)
