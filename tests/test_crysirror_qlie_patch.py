import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/pc2android/scripts/pc2android.py'
SPEC = importlib.util.spec_from_file_location('pc2android', SCRIPT)
pc2android = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc2android)

OLD_RENDER = '''    for id in bars {
        let bar = &vm.objects[&id];
        let now = vm.visual_time(bar.thread, bar.started, host_now);
        let child = |part: &str| {
            bar.properties
                .get(&format!("bar/{part}_id"))
                .and_then(|v| v.first())
                .map(Value::text)
        };
        let thumb_id = child("thumb").context("QLIE scrollbar has no thumb resource")?;
        let track_id = child("track").context("QLIE scrollbar has no track resource")?;
        let fill_id = child("fill");
        if !bar.visible {
            if bar.ui_registered {
                ui.push(json!({"op":"set_visible","id":bar.layer,"visible":false}));
            }
            continue;
        }
        let thumb = image_extent(
            vm.objects
                .get(&thumb_id)
                .context("QLIE scrollbar thumb object")?,
        );
            (Some(thumb_id), thumb_position, 10, false),'''


OLD_BAR = '''            "?p" => {
                for (i, value) in self.objects[id].position.into_iter().enumerate() {
                    m.result(i, Value::Int(value));
                }
            }'''


class CrySirrorPreparationTests(unittest.TestCase):
    def make_source(self, root):
        source = Path(root) / 'crysirror'
        (source / 'crates/crysirror-mobile').mkdir(parents=True)
        (source / 'Cargo.toml').write_text('[workspace]\n', encoding='utf-8')
        render = source / 'crates/crysirror-qlie/src/adapter/render.rs'
        render.parent.mkdir(parents=True)
        render.write_text(OLD_RENDER, encoding='utf-8')
        bar = source / 'crates/crysirror-qlie/src/adapter/objects/bar.rs'
        bar.parent.mkdir(parents=True)
        bar.write_text(OLD_BAR, encoding='utf-8')
        return source

    def test_fresh_clone_is_patched_after_each_cleanup(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            home = Path(temporary)
            target = home / 'crysirror'

            def fake_clone(command, env=None, cwd=None):
                self.assertIn('clone', command)
                self.assertIn('--depth', command)
                self.make_source(home)

            with mock.patch.object(pc2android, 'TOOLCHAIN_HOME', home), \
                    mock.patch.dict(os.environ, {'CRYSIRROR_SOURCE': ''}), \
                    mock.patch.object(pc2android.shutil, 'which', return_value='/usr/bin/git'), \
                    mock.patch.object(pc2android, 'run', side_effect=fake_clone), \
                    mock.patch.object(pc2android, 'patch_crysirror_audio'):
                for _ in range(2):
                    shutil.rmtree(target, ignore_errors=True)
                    source = pc2android.ensure_crysirror_source()
                    render = source / 'crates/crysirror-qlie/src/adapter/render.rs'
                    text = render.read_text(encoding='utf-8')
                    self.assertIn('let thumb_id = child("thumb");', text)
                    self.assertIn('None => [0., 0.],', text)
                    self.assertIn('(thumb_id, thumb_position, 10, false),', text)
                    self.assertLess(text.index('if !bar.visible'), text.index('let track_id'))
                    self.assertIn(pc2android.QLIE_CONFIG_MARKER, text)
                    self.assertNotIn('has no thumb resource', text)
                    bar = source / 'crates/crysirror-qlie/src/adapter/objects/bar.rs'
                    bar_text = bar.read_text(encoding='utf-8')
                    self.assertIn('EngineQuery::Layer(o.layer)', bar_text)
                    self.assertIn(pc2android.QLIE_CONFIG_POSITION_MARKER, bar_text)
                    render_modified = render.stat().st_mtime_ns
                    bar_modified = bar.stat().st_mtime_ns
                    pc2android.patch_crysirror_qlie_config(source)
                    self.assertEqual(text, render.read_text(encoding='utf-8'))
                    self.assertEqual(bar_text, bar.read_text(encoding='utf-8'))
                    self.assertEqual(render_modified, render.stat().st_mtime_ns)
                    self.assertEqual(bar_modified, bar.stat().st_mtime_ns)

    def test_patch_rejects_source_drift(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source = self.make_source(temporary)
            render = source / 'crates/crysirror-qlie/src/adapter/render.rs'
            render.write_text('unrelated source\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pc2android.patch_crysirror_qlie_config(source)
            self.assertEqual(render.read_text(encoding='utf-8'), 'unrelated source\n')

    def test_bar_drift_does_not_leave_partial_render_patch(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source = self.make_source(temporary)
            render = source / 'crates/crysirror-qlie/src/adapter/render.rs'
            original = render.read_text(encoding='utf-8')
            bar = source / 'crates/crysirror-qlie/src/adapter/objects/bar.rs'
            bar.write_text('unrelated source\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pc2android.patch_crysirror_qlie_config(source)
            self.assertEqual(render.read_text(encoding='utf-8'), original)

    def test_source_notice_describes_config_patches(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source = self.make_source(temporary)
            pc2android.patch_crysirror_qlie_config(source)
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
            self.assertIn('crates/crysirror-qlie/src/adapter/render.rs', notice)
            self.assertIn('QLIE scrollbar thumb is optional', notice)
            self.assertIn('crates/crysirror-qlie/src/adapter/objects/bar.rs', notice)
            self.assertIn('current animated layer position', notice)


if __name__ == '__main__':
    unittest.main()
