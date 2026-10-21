"""Regression tests for cross-run identity, immutable store and directional contracts."""
import copy
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from scripts.modular.storage import Store, Run, canonical, digest, read, write, workspace_lock
from scripts.modular.machines.primer20k_vexriscv.contracts import hardware, requirements, check
from scripts.modular.machines.primer20k_vexriscv.cpu import locked_cpu
from scripts.modular.machines.primer20k_vexriscv.system import export_bundle, verify_export, import_export, bundle
from scripts.modular.environment import linux_runner, check_locked, process_environment


class ModularTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'build')
        self.root = Path(self.temp.name)
        self.store = Store(self.root)

    def tearDown(self): self.temp.cleanup()

    def artifact(self, name='cpu', payload=b'RTL', dependencies=None, key=None):
        return self.store.publish(name,key or digest(payload.hex()),{'outputs/file':payload},dependencies=dependencies)

    def test_same_artifact_reused_across_runs(self):
        a = self.artifact(); b = self.artifact()
        self.assertEqual(a,b)
        self.assertEqual(self.store.lookup('cpu',digest(b'RTL'.hex())),a)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows sharing semantics')
    def test_publish_retries_transient_windows_rename_denial(self):
        original = Path.rename
        calls = []
        def briefly_locked(source, destination):
            calls.append(source)
            if len(calls) == 1:
                error = PermissionError('temporary sharing denial')
                error.winerror = 5
                raise error
            return original(source, destination)
        with mock.patch.object(Path, 'rename', briefly_locked):
            ref = self.artifact(payload=b'retry')
        self.store.get(ref)
        self.assertGreaterEqual(len(calls), 2)

    def test_publish_rename_failure_never_creates_index(self):
        with mock.patch.object(Path, 'rename', side_effect=PermissionError('persistent denial')):
            with self.assertRaises(PermissionError):
                self.artifact(payload=b'not-published')
        self.assertFalse((self.store.build/'index/build-keys/cpu').exists())

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows sharing semantics')
    def test_windows_rename_retries_are_bounded(self):
        error = PermissionError('persistent Windows denial')
        error.winerror = 5
        with mock.patch.object(Path, 'rename', side_effect=error) as rename, \
             mock.patch('scripts.modular.storage.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                self.artifact(payload=b'bounded')
        self.assertEqual(rename.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertFalse((self.store.build/'index/build-keys/cpu').exists())

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows sharing semantics')
    def test_json_atomic_replace_retries_transient_denial(self):
        import os
        original = os.replace
        calls = []
        def briefly_locked(source, destination):
            calls.append(source)
            if len(calls) == 1:
                error = PermissionError('temporary JSON sharing denial')
                error.winerror = 5
                raise error
            return original(source, destination)
        target = self.root/'result.json'
        write(target, {'old': True})
        with mock.patch('scripts.modular.storage.os.replace', briefly_locked):
            write(target, {'new': True})
        self.assertEqual(read(target), {'new': True})
        self.assertEqual(len(calls), 2)

    def test_failed_json_replace_preserves_previous_and_removes_temporary(self):
        target = self.root/'result.json'
        write(target, {'old': True})
        with mock.patch('scripts.modular.storage.os.replace', side_effect=PermissionError('persistent JSON denial')):
            with self.assertRaises(PermissionError):
                write(target, {'new': True})
        self.assertEqual(read(target), {'old': True})
        self.assertFalse(list(self.root.glob('result.json.*.tmp')))

    def test_detect_tampered_file(self):
        ref=self.artifact();(self.store.path(ref)/'outputs/file').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'file differs'):self.store.get(ref)

    def test_manifest_tampering_rejected(self):
        ref=self.artifact();p=self.store.path(ref)/'manifest.json';d=read(p);d['version']='fake';write(p,d)
        with self.assertRaisesRegex(ValueError,'manifest differs'):self.store.get(ref)

    def test_path_escape_rejected(self):
        with self.assertRaisesRegex(ValueError,'escapes'):self.store.publish('cpu','key',{'../oops':b'x'})

    def test_extra_file_rejected(self):
        ref=self.artifact();(self.store.path(ref)/'extra').write_text('x')
        with self.assertRaisesRegex(ValueError,'inventory'):self.store.get(ref)

    def test_same_key_different_output_preserved_conflict(self):
        key=digest('key');old=self.artifact(payload=b'a',key=key)
        with self.assertRaisesRegex(ValueError,'different artifacts'):self.artifact(payload=b'b',key=key)
        self.store.get(old)
        self.assertEqual(len(list((self.store.build/'store/cpu').iterdir())),2)
        (self.store.build/'index/build-keys/cpu'/(key+'.json')).unlink()
        with self.assertRaisesRegex(ValueError,'Ambiguous build key'):self.store.lookup('cpu',key)

    def test_rebuild_index_from_store(self):
        ref=self.artifact();p=self.store.build/'index/build-keys/cpu'/ (digest(b'RTL'.hex())+'.json');p.unlink()
        self.assertEqual(self.store.lookup('cpu',digest(b'RTL'.hex())),ref)

    def test_failed_run_is_not_artifact(self):
        run=Run('build','failed',root=self.root)
        self.assertFalse(read(run.path/'result.json')['passed'])
        self.assertFalse((self.store.build/'store').exists())

    def test_tests_are_typed_runs(self):
        run=Run('test','host',root=self.root);run.finish()
        self.assertEqual(read(run.path/'result.json')['kind'],'test')
        self.assertFalse((self.store.build/'evidence').exists())

    def test_lock_blocks_concurrent_workspace(self):
        path=self.store.build/'work/a'
        with workspace_lock(path):
            with self.assertRaisesRegex(ValueError,'already in use'):
                with workspace_lock(path):pass

    def system(self):
        cpu=self.artifact();gate=self.artifact('gateware',b'FS',{'cpu':cpu})
        run=Run('build','system',root=self.root)
        return bundle(self.store,{'cpu':cpu,'gateware':gate},{'flags':{'x':False}}, {},run)

    def test_export_import_on_different_root(self):
        system=self.system();out=self.root/'release';export_bundle(self.store,system,out)
        other=Store(self.root/'other');restored=import_export(other,out)
        self.assertEqual(restored,system);other.get(restored,recursive=True)

    def test_export_checks_dependency_tampering(self):
        system=self.system();out=self.root/'release';export_bundle(self.store,system,out)
        file=next((out/'artifacts/cpu').glob('*/outputs/file'));file.write_bytes(b'tamper')
        with self.assertRaises(ValueError):verify_export(out)

    def test_export_cannot_claim_unrelated_tests(self):
        system=self.system();run=Run('test','host',root=self.root);run.finish(system={'component':'system','digest':'f'*64})
        with self.assertRaisesRegex(ValueError,'exact system'):export_bundle(self.store,system,self.root/'release',[run.id])

    def test_bundle_independent_of_invocation_provenance(self):
        a=self.system();run=Run('build','second',root=self.root)
        lock=read(self.store.path(a)/'bundle.lock.json')
        b=bundle(self.store,lock['components'],lock['configuration'],{'commit':'different'},run)
        self.assertEqual(a,b)

    def test_new_bios_keeps_hardware_references(self):
        a=self.system();lock=read(self.store.path(a)/'bundle.lock.json');parts=lock['components']
        bios1=self.artifact('bios',b'bios1');bios2=self.artifact('bios',b'bios2')
        run=Run('build','first',root=self.root);x=bundle(self.store,dict(parts,bios=bios1),{}, {},run)
        run=Run('build','next',root=self.root);y=bundle(self.store,dict(parts,bios=bios2),{}, {},run)
        lx=read(self.store.path(x)/'bundle.lock.json');ly=read(self.store.path(y)/'bundle.lock.json')
        self.assertEqual(lx['components']['gateware'],ly['components']['gateware'])
        self.assertNotEqual(x,y)

    def contract(self):
        csr={'csr_registers':{'control':{'addr':100,'size':1,'type':'rw'}},'memories':{'ram':{'base':0,'size':128}},'constants':{}}
        v={'memory_system':'shared-writeback','l2_policy':'write-back','boot_mode':'rom','boot_ram_address':10,'boot_ram_size_bytes':4,'clock_hz':60,'isa':'rv32ima','abi':'ilp32','features':{'video':True}}
        return hardware(csr,v)

    def test_appended_csr_is_compatible(self):
        old=self.contract();new=copy.deepcopy(old);new['registers']['extra']={'addr':104,'size':1,'type':'rw'}
        self.assertTrue(check(new,requirements(old))['compatible'])

    def test_moved_csr_rejected(self):
        old=self.contract();new=copy.deepcopy(old);new['registers']['control']['addr']=104
        self.assertFalse(check(new,requirements(old))['compatible'])

    def test_behavior_change_rejected(self):
        old=self.contract();new=copy.deepcopy(old);new['behavior']['l2_policy']='other'
        self.assertFalse(check(new,requirements(old))['compatible'])

    def test_required_capability_rejected_optional_reported(self):
        p=self.contract()
        self.assertFalse(check(p,requirements(p,required=['filesystem']))['compatible'])
        r=check(p,requirements(p,required=[],optional=['filesystem']))
        self.assertTrue(r['compatible']);self.assertEqual(r['unavailable_optional'],['filesystem'])

    def test_unknown_mandatory_flags_rejected(self):
        p=self.contract();r=requirements(p);r['mandatory_flags']=128
        self.assertFalse(check(p,r)['compatible'])

    def test_isa_provider_superset(self):
        old=self.contract();new=copy.deepcopy(old)
        new['isa']='rv32imafdc_zicsr_zifencei'
        self.assertTrue(check(new,requirements(old))['compatible'])
        self.assertFalse(check(old,requirements(new))['compatible'])

    def test_backend_semantics_change_rejected(self):
        old=self.contract();new=copy.deepcopy(old)
        new['behavior']['usb_backend']='ohci'
        old['behavior']['usb_backend']='ultra'
        self.assertFalse(check(new,requirements(old))['compatible'])

    def test_bundle_rejects_swapped_static_library(self):
        sdk=self.artifact('hal-sdk',b'library1');other=self.artifact('hal-sdk',b'library2')
        bios=self.artifact('bios',b'app',{'hal-sdk':sdk})
        run=Run('build','invalid-composition',root=self.root)
        with self.assertRaisesRegex(ValueError,'bios.*hal-sdk'):
            bundle(self.store,{'bios':bios,'hal-sdk':other},{},{},run)

    def test_unknown_capability_bits_rejected_optional_bits_tolerated(self):
        p=self.contract();r=requirements(p)
        r['required_capability_flags']=1<<31
        self.assertFalse(check(p,r)['compatible'])
        r['required_capability_flags']=0;r['optional_capability_flags']=1<<31
        result=check(p,r)
        self.assertTrue(result['compatible']);self.assertEqual(result['unavailable_optional_flags'],1<<31)

    def image_parts(self, image_tag=42):
        from scripts.tools.boot_image import pack_image
        config=dict(contract=self.contract(),legacy_abi_tag=42)
        soc=self.store.publish('soc-description',digest(config),{'csr.json':b'{}'},config=config)
        boot=self.store.publish('bootloader',digest('boot'),{'outputs/boot.bin':b'boot'},dependencies={'soc-description':soc})
        bios=self.store.publish('bios',digest(image_tag),{'outputs/app.img':pack_image(b'1234',image_tag)},
                                config=dict(requires=requirements(config['contract'])),dependencies={'soc-description':soc,'bootloader':boot})
        return {'soc-description':soc,'bootloader':boot,'bios':bios}

    def test_bundle_cannot_bypass_v1_loader_tag(self):
        run=Run('build','invalid-wire-tag',root=self.root)
        with self.assertRaisesRegex(ValueError,'Incompatible ABI'):
            bundle(self.store,self.image_parts(43),{},{},run)

    def test_assembly_accepts_directional_hardware_extension(self):
        parts=self.image_parts()
        provider=copy.deepcopy(self.contract());provider['registers']['extra']={'addr':104,'size':1,'type':'rw'}
        cfg=dict(contract=provider,legacy_abi_tag=42)
        parts['soc-description']=self.store.publish('soc-description',digest(cfg),{'csr.json':b'{}'},config=cfg)
        # The old bootloader may run on this extended hardware; its own v1 tag
        # remains 42. Executable consumer requirements justify that replacement.
        old_boot=parts['bootloader'];old=self.store.get(old_boot)
        parts['bootloader']=self.store.publish('bootloader',digest('boot-contract'),{'outputs/boot.bin':b'boot'},
            config=dict(requires=requirements(self.contract())),dependencies=old['dependencies'])
        # BIOS identity includes its selected bootloader, so rebuild that edge.
        bios=self.store.get(parts['bios'])
        parts['bios']=self.store.publish('bios',digest('app-contract'),
            {'outputs/app.img':(self.store.path(parts['bios'])/'outputs/app.img')},
            config=bios['config'],dependencies=dict(bios['dependencies'],bootloader=parts['bootloader']))
        run=Run('build','extended-hardware',root=self.root)
        ref=bundle(self.store,parts,{},{},run)
        self.assertTrue(ref)

    def test_interface_semantics_revision_rejected(self):
        old=self.contract();new=copy.deepcopy(old)
        new['interface_revisions']['shared-l2-coherence']=2
        self.assertFalse(check(new,requirements(old))['compatible'])

    def test_cpu_missing_configuration_rejected(self):
        rtl=self.root/'CPU.v';rtl.write_text('module CPU; endmodule')
        write(self.root/'generator.json',{'cpu_rtls':{'CPU':{}}})
        with self.assertRaisesRegex(ValueError,'Incomplete CPU'):locked_cpu(rtl)

    def test_native_linux_environment_is_automatic(self):
        def detected(argv):
            if argv[-2:] == ['-lc','command -v gcc']:return '/usr/bin/gcc'
            if argv[-1]=='--version':return 'gcc 15.2'
            if 'sha256sum' in argv:return 'a'*64+'  /usr/bin/gcc'
            raise AssertionError(argv)
        with mock.patch('scripts.modular.environment.platform.system',return_value='Linux'),mock.patch('scripts.modular.environment.output',side_effect=detected):
            environment=linux_runner(['cc'],root=self.root)
        self.assertEqual(environment['kind'],'native')
        self.assertEqual(environment['tools']['cc']['path'],'/usr/bin/gcc')

    def test_locked_tool_substitution_rejected(self):
        with self.assertRaisesRegex(ValueError,'differ from lock'):
            check_locked({'platform':{},'tools':{'cc':'old'}},{'platform':{},'tools':{'cc':'new'},'bindings':{}})

    def test_ambient_compiler_overrides_are_filtered(self):
        with mock.patch.dict('os.environ',{'GCC_EXEC_PREFIX':'wrong','CFLAGS':'-O0','CPATH':'wrong'}):
            env=process_environment()
        self.assertNotIn('GCC_EXEC_PREFIX',env);self.assertNotIn('CPATH',env);self.assertNotIn('CFLAGS',env)

    def test_release_inventory_rejects_unlisted_file(self):
        ref=self.system();out=self.root/'release';export_bundle(self.store,ref,out)
        (out/'extra').write_text('unexpected')
        with self.assertRaisesRegex(ValueError,'inventory'):verify_export(out)


if __name__=='__main__':unittest.main()
