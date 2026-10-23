from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENV = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}


class BuildRevisionRecord(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tmnf-payload-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        shutil.copytree(ROOT / 'src', self.project / 'src', ignore=ignore)
        shutil.copytree(ROOT / 'config', self.project / 'config')
        shutil.copy2(ROOT / 'VERSION', self.project / 'VERSION')

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=t', '-c', 'user.email=t@t',
                               *arguments], env=ENV, text=True, capture_output=True, check=True).stdout.strip()

    def stage(self, project=None, name='staged'):
        destination = self.root / name
        result = subprocess.run(
            ['/bin/bash', '-euc', '. "$1"; stage_launcher_payload "$2" "$3"', 'fixture',
             str(ROOT / 'src/shell/lib/apps/payload.sh'), str(project or self.project), str(destination)],
            env=ENV, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return destination / 'config/build-revision'

    def commit(self):
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def test_clean_and_dirty_trees_record_their_commit(self):
        revision = self.commit()
        self.assertRegex(revision, r'^[0-9a-f]{40}$')
        self.assertEqual(self.stage().read_text(), revision + '\nclean\n')
        (self.project / 'VERSION').write_text('9.9.9\n')
        self.assertEqual(self.stage(name='dirty').read_text(), revision + '\ndirty\n')

    def test_a_staged_payload_passes_its_record_on(self):
        self.commit()
        first = self.stage()
        again = self.stage(project=first.parents[1], name='again')
        self.assertEqual(again.read_text(), first.read_text())

    def test_a_parent_repository_is_never_consulted(self):
        parent = self.root / 'parent'
        parent.mkdir()
        subprocess.run(['git', '-C', str(parent), 'init', '-q'], env=ENV, check=True)
        subprocess.run(['git', '-C', str(parent), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit',
                        '-q', '--allow-empty', '-m', 'x'], env=ENV, check=True, capture_output=True)
        nested = parent / 'project'
        shutil.move(str(self.project), str(nested))
        self.assertFalse(self.stage(project=nested).exists())

    def test_a_stale_record_in_a_checkout_is_recomputed(self):
        revision = self.commit()
        (self.project / 'config/build-revision').write_text(revision + '\nclean\n')
        # The record itself is untracked, so the tree is dirty and says so.
        self.assertEqual(self.stage().read_text(), revision + '\ndirty\n')
        (self.project / 'config/build-revision').write_text('b' * 40 + '\nclean\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'record')
        self.assertEqual(self.stage(name='again').read_text(), self.git('rev-parse', 'HEAD') + '\nclean\n')

    def test_a_tree_without_git_records_nothing(self):
        self.assertFalse(self.stage().exists())


if __name__ == '__main__':
    unittest.main()
