import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/pc2android/scripts/pc2android.py'
SPEC = importlib.util.spec_from_file_location('pc2android', SCRIPT)
pc2android = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc2android)


OLD_OUTPUT = '''pub const OUTPUT_RATE: u32 = 48_000;
pub const OUTPUT_CAPACITY: usize = OUTPUT_RATE as usize / 4;
'''
OLD_SUBMIT = '''            if !tail.iter().flatten().any(|s| s.is_finite() && *s != 0.) {
                return Ok(());
            }
        }
        self.shared.push(frames);'''
OLD_EMBEDDED = 'let ms = now.duration_since(previous).as_millis().min(100) as u64;'


class CrySirrorAudioPatchTests(unittest.TestCase):
    def make_source(self, root):
        source = Path(root) / 'crysirror'
        output = source / 'crates/crysirror-audio/src/pcm_output.rs'
        output.parent.mkdir(parents=True)
        output.write_text(OLD_OUTPUT + OLD_SUBMIT, encoding='utf-8')
        embedded = source / 'crates/crysirror-desktop/src/app/embedded.rs'
        embedded.parent.mkdir(parents=True)
        embedded.write_text(OLD_EMBEDDED, encoding='utf-8')
        return source

    def test_audio_patch_is_applied_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_source(temporary)
            pc2android.patch_crysirror_audio(source)
            output = source / 'crates/crysirror-audio/src/pcm_output.rs'
            embedded = source / 'crates/crysirror-desktop/src/app/embedded.rs'
            text = output.read_text(encoding='utf-8')
            self.assertIn('OUTPUT_CAPACITY: usize = OUTPUT_RATE as usize * 2', text)
            self.assertIn('OUTPUT_START_SILENCE', text)
            self.assertIn('self.shared.push(&vec![[0.; 2]; OUTPUT_START_SILENCE]);', text)
            self.assertIn('.min(2000) as u64', embedded.read_text(encoding='utf-8'))
            modified = output.stat().st_mtime_ns
            pc2android.patch_crysirror_audio(source)
            self.assertEqual(text, output.read_text(encoding='utf-8'))
            self.assertEqual(modified, output.stat().st_mtime_ns)

    def test_audio_patch_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_source(temporary)
            output = source / 'crates/crysirror-audio/src/pcm_output.rs'
            output.write_text('unrelated source\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pc2android.patch_crysirror_audio(source)
            self.assertEqual(output.read_text(encoding='utf-8'), 'unrelated source\n')

    def test_embedded_drift_does_not_leave_partial_output_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_source(temporary)
            output = source / 'crates/crysirror-audio/src/pcm_output.rs'
            original = output.read_text(encoding='utf-8')
            embedded = source / 'crates/crysirror-desktop/src/app/embedded.rs'
            embedded.write_text('unrelated source\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pc2android.patch_crysirror_audio(source)
            self.assertEqual(output.read_text(encoding='utf-8'), original)

    def test_marker_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_source(temporary)
            pc2android.patch_crysirror_audio(source)
            embedded = source / 'crates/crysirror-desktop/src/app/embedded.rs'
            embedded.write_text(
                embedded.read_text(encoding='utf-8') + '\n' + OLD_EMBEDDED + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pc2android.patch_crysirror_audio(source)

    def test_source_notice_describes_audio_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_source(temporary)
            pc2android.patch_crysirror_audio(source)
            licenses = Path(temporary) / 'licenses'
            licenses.mkdir()

            def fake_git(command, **kwargs):
                args = tuple(command[3:])
                values = {
                    ('remote', 'get-url', 'fork'): '',
                    ('remote', 'get-url', 'origin'): 'https://github.com/xmoezzz/CrySirror.git',
                    ('rev-parse', 'HEAD'): 'a' * 40,
                    ('branch', '--show-current'): 'main',
                    ('log', '-1', '--format=%s'): 'upstream',
                    ('status', '--porcelain'): '',
                }
                return values[args]

            with mock.patch.object(pc2android.subprocess, 'check_output', side_effect=fake_git):
                pc2android.write_crysirror_source_notice(source, licenses)
            notice = (licenses / 'CRYIRROR-SOURCE.txt').read_text(encoding='utf-8')
            self.assertIn('larger mobile output queue', notice)
            self.assertIn('two seconds of real-time audio catch-up', notice)


if __name__ == '__main__':
    unittest.main()
