"""The framework extractor must rebuild the same tree on any host.

Git archives carry symlink members; extractors differ by interpreter
privilege. extract() rewrites every link member from the archive metadata,
so the tree on disk equals the linked content everywhere, chains included."""
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from scripts.modular import sourceprep


def build_archive(path):
    with tarfile.open(path, 'w:gz') as tar:
        def info(name, type, mode=0o644, linkname=''):
            member = tarfile.TarInfo(name)
            member.type = type
            member.mode = mode
            member.linkname = linkname
            return member
        tar.addfile(info('root', tarfile.DIRTYPE, 0o755))
        tar.addfile(info('root/realdir', tarfile.DIRTYPE, 0o755))
        data = b'shared-content'
        member = info('root/realdir/a.txt', tarfile.REGTYPE)
        member.size = len(data)
        tar.addfile(member, __import__('io').BytesIO(data))
        tar.addfile(info('root/filelink.txt', tarfile.SYMTYPE, linkname='realdir/a.txt'))
        tar.addfile(info('root/dirlink', tarfile.SYMTYPE, 0o755, linkname='realdir'))
        tar.addfile(info('root/chain.txt', tarfile.SYMTYPE, linkname='filelink.txt'))
        tar.addfile(info('root/hard.txt', tarfile.LNKTYPE, linkname='root/realdir/a.txt'))


class UpstreamExtractTest(unittest.TestCase):
    def test_links_become_content(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive = temp/'a.tar.gz'
            build_archive(archive)
            staging = temp/'staging'
            staging.mkdir()
            sourceprep.extract(archive, staging)
            root = staging/'root'
            self.assertEqual((root/'filelink.txt').read_bytes(), b'shared-content')
            self.assertEqual((root/'dirlink'/'a.txt').read_bytes(), b'shared-content')
            self.assertEqual((root/'chain.txt').read_bytes(), b'shared-content')
            self.assertEqual((root/'hard.txt').read_bytes(), b'shared-content')
            for name in ('filelink.txt', 'dirlink', 'chain.txt', 'hard.txt'):
                self.assertFalse((root/name).is_symlink(), name+' stayed a link')
            first = sourceprep.tree_hash(root)
            staging2 = temp/'staging2'
            staging2.mkdir()
            sourceprep.extract(archive, staging2)
            self.assertEqual(first, sourceprep.tree_hash(staging2/'root'))

    def test_dangling_link_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive = temp/'a.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                member = tarfile.TarInfo('root')
                member.type = tarfile.DIRTYPE
                tar.addfile(member)
                member = tarfile.TarInfo('root/ghost')
                member.type = tarfile.SYMTYPE
                member.linkname = 'nowhere'
                tar.addfile(member)
            staging = temp/'staging'
            staging.mkdir()
            with self.assertRaises(ValueError):
                sourceprep.extract(archive, staging)


if __name__ == '__main__':
    unittest.main()
