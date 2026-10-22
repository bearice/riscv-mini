"""Shared text input identity, canonical snapshots, and byte-exact archives."""
import codecs
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from scripts.modular.compile import source_files
from scripts.modular.storage import (Store, digest, file_hash, normalize_source_bytes,
                                     source_bytes, source_file_hash, text_file_hash)
from scripts.tools.build_records import source_identity


class SourceHashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_any_text_filename_has_the_same_hash_for_lf_crlf_and_cr(self):
        text = 'source 配置\nnext\n'.encode()
        expected = hashlib.sha256(text).hexdigest()
        for name in ('main.c', 'config.json', 'link.ld', 'unknown.extension', 'LICENSE', 'no-suffix'):
            path = self.root / name
            for eol in (b'\n', b'\r\n', b'\r'):
                with self.subTest(name=name, eol=eol):
                    path.write_bytes(text.replace(b'\n', eol))
                    self.assertEqual(source_file_hash(path), expected)
                    self.assertEqual(source_bytes(path), text)
                    self.assertEqual(source_files(self.root, [name])[name], expected)

    def test_bom_text_normalizes_eol_without_changing_encoding(self):
        for bom, encoding in ((codecs.BOM_UTF8, 'utf-8'),
                              (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be'),
                              (codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be')):
            with self.subTest(encoding=encoding):
                data = bom + '配置\r\nnext\r'.encode(encoding)
                expected = bom + '配置\nnext\n'.encode(encoding)
                self.assertEqual(normalize_source_bytes(data), expected)
                path = self.root / 'text'
                path.write_bytes(data)
                self.assertEqual(text_file_hash(path), hashlib.sha256(expected).hexdigest())

    def test_binary_inputs_and_archive_checksums_remain_byte_exact(self):
        for data in (b'\x00binary\r\n', b'\xff\xfeinvalid\r\n\xff', b'\x7fELF\x01\r\n'):
            self.assertEqual(normalize_source_bytes(data), data)
            path = self.root / 'data.bin'
            path.write_bytes(data)
            self.assertEqual(source_file_hash(path), hashlib.sha256(data).hexdigest())
        path.write_bytes(b'text\n')
        before = file_hash(path)
        path.write_bytes(b'text\r\n')
        self.assertNotEqual(file_hash(path), before)
        store = Store(self.root)
        ref = store.publish('test', digest('archive'), {'output.txt': path})
        (store.path(ref) / 'output.txt').write_bytes(b'text\n')
        with self.assertRaisesRegex(ValueError, 'Artifact file differs'):
            store.get(ref)

    def test_actual_content_whitespace_and_final_newlines_still_matter(self):
        path = self.root / 'input'
        path.write_bytes(b'one\ntwo\n')
        expected = source_file_hash(path)
        for data in (b'One\ntwo\n', b'one \ntwo\n', b'one\ntwo', b'one\ntwo\n\n'):
            path.write_bytes(data)
            self.assertNotEqual(source_file_hash(path), expected)

    def test_canonical_input_snapshot_republishes_the_same_artifact(self):
        path = self.root / 'source'
        store = Store(self.root)
        refs = []
        for data in (b'input\n', b'input\r\n', b'input\r'):
            path.write_bytes(data)
            refs.append(store.publish('test', source_file_hash(path), {'inputs/source': source_bytes(path)}))
        self.assertEqual(refs, [refs[0]] * 3)
        store.get(refs[0])

    def test_aggregate_source_identity_ignores_eol(self):
        path = self.root / 'firmware/main.c'
        path.parent.mkdir()
        def git(*args, **kwargs):
            if args[0] == 'ls-files':
                return b'' if '--others' in args else b'firmware/main.c\0'
            return b'commit' if args[0] == 'rev-parse' else b''
        with mock.patch('scripts.tools.build_records.git', side_effect=git):
            path.write_bytes(b'int main(void) {}\n')
            before = source_identity(self.root)['inputs_sha256']
            path.write_bytes(b'int main(void) {}\r\n')
            self.assertEqual(source_identity(self.root)['inputs_sha256'], before)
            path.write_bytes(b'int main(void) { return 1; }\n')
            self.assertNotEqual(source_identity(self.root)['inputs_sha256'], before)


if __name__ == '__main__':
    unittest.main()
