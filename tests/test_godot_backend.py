import contextlib
import hashlib
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/pc2android/scripts'
sys.path.insert(0, str(SCRIPTS))
import godot_backend as godot
import pc2android


def pck_header(version='4.7.2'):
    major, minor, patch = (int(part) for part in version.split('.'))
    return struct.pack('<4sIIII', b'GDPC', 2, major, minor, patch) + b'\0' * 80


def recovery_report(**overrides):
    report = {
        'report_version': 1,
        'gdre_version': godot.GDRE_VERSION,
        'ver': '4.7.2',
        'had_encryption_error': False,
        'mono_detected': False,
        'custom_version_detected': False,
        'uses_double_precision': False,
        'decompiled_scripts': ['main.gd'],
        'success': [
            {'loss_type': 0, 'saved_path': 'res://texture.png'},
            {'loss_type': 1, 'saved_path': 'res://audio.ogg'},
        ],
        'failed': [],
        'failed_scripts': [],
        'failed_plugin_cfg_create': [],
        'failed_gdnative_copy': [],
        'not_converted': [],
        'unsupported_types': [],
    }
    report.update(overrides)
    return report


class FakeCommands:
    def __init__(self, *, report=None, export_apk=True, omit_native_library=False, checksum_warning=False):
        self.report = report or recovery_report()
        self.export_apk = export_apk
        self.omit_native_library = omit_native_library
        self.checksum_warning = checksum_warning
        self.commands = []
        self.verified = False

    def __call__(self, command, env=None, cwd=None, capture=False):
        parts = [str(part) for part in command]
        self.commands.append(parts)
        name = Path(parts[0]).name
        if name == 'gore' and '--version' in parts:
            return 'gore 0.5.0\n'
        if (name.startswith('gdre_tools') or name == 'Godot RE Tools') and '--gdre-version' in parts:
            return 'Godot RE Tools 2.7.4\n'
        if name == 'Godot' and '--version' in parts:
            return '4.7.2.stable.official.test\n'
        if name == 'keytool' and '-list' in parts:
            return 'androiddebugkey, Nov 28, 2026, PrivateKeyEntry\n'
        if name == 'gore' and 'info' in parts:
            return 'file game.pck\nengine version 4.7.2\npack version v2\n'
        if name == 'gore' and 'extract' in parts:
            source = Path(parts[parts.index('extract') + 1])
            output = Path(parts[parts.index('-o') + 1])
            output.mkdir(parents=True, exist_ok=True)
            if source.name == 'base.pck':
                (output / 'project.binary').write_bytes(b'base-project')
                (output / 'shared.txt').write_text('base', encoding='utf-8')
            else:
                (output / 'shared.txt').write_text('patch', encoding='utf-8')
                (output / 'patch_added.txt').write_text('patch-data', encoding='utf-8')
            return '* warning: 1 files failed the MD5 check\n' if self.checksum_warning else 'extracted\n'
        if (name.startswith('gdre_tools') or name == 'Godot RE Tools') and any(part.startswith('--recover=') for part in parts):
            output_arg = next(part for part in parts if part.startswith('--output='))
            project = Path(output_arg.split('=', 1)[1])
            (project / 'scripts').mkdir(parents=True, exist_ok=True)
            (project / 'project.godot').write_text(
                '[application]\nconfig/name="Recovered"\nrun/main_scene="res://main.tscn"\n',
                encoding='utf-8',
            )
            (project / 'scripts/main.gd').write_text('extends Node\n', encoding='utf-8')
            (project / 'scripts/patch_loader.gd').write_text(
                'extends Node\n\nfunc _init() -> void:\n'
                '\tvar dir = OS.get_executable_path().get_base_dir()\n'
                '\tProjectSettings.load_resource_pack(dir.path_join("game.pck"))\n',
                encoding='utf-8',
            )
            (project / 'scripts/save_dir.gd').write_text(
                'const OLD_DIR := "user://"\nstatic var base = OLD_DIR\n\n'
                'static func setup(release: bool) -> String:\n'
                '\tvar dir = OS.get_executable_path().get_base_dir().path_join("save")\n'
                '\tbase = dir\n\treturn base\n',
                encoding='utf-8',
            )
            (project / 'gdre_export.json').write_text(json.dumps(self.report), encoding='utf-8')
            (project / 'gdre_export.log').write_text('Lossy: 1\n', encoding='utf-8')
            return 'Recovery finished\n'
        if name == 'Godot' and '--script' in parts:
            return 'editor settings saved\n'
        if name == 'Godot' and '--import' in parts:
            return 'import complete\n'
        if name == 'Godot' and '--export-release' in parts:
            if self.export_apk:
                output = Path(parts[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output, 'w') as archive:
                    if not self.omit_native_library:
                        archive.writestr('lib/arm64-v8a/libgodot_android.so', b'godot')
                        archive.writestr('lib/arm64-v8a/libc++_shared.so', b'c++')
                    archive.writestr('assets/.godot/exported/1/main.scn', b'scene')
                    archive.writestr('assets/scripts/main.gdc', b'script')
                    for notice in ('GODOT-SOURCE.txt', 'GORE-MIT.txt', 'GDRE-MIT.txt', 'GODOT-MIT.txt', 'GODOT-COPYRIGHT.txt'):
                        archive.writestr(f'assets/licenses/{notice}', b'license')
            return 'export complete\n'
        if name == 'zipalign' and '-f' in parts:
            shutil.copy2(parts[-2], parts[-1])
            return 'align complete\n'
        if name == 'apksigner' and 'sign' in parts:
            output = Path(parts[parts.index('--out') + 1])
            shutil.copy2(parts[-1], output)
            return 'signed\n'
        raise AssertionError(f'unexpected fake command: {parts}')


class GodotBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def make_source(self):
        source = self.root / 'game'
        source.mkdir()
        (source / 'base.pck').write_bytes(pck_header())
        (source / 'patch.pck').write_bytes(pck_header())
        return source

    def make_toolchain(self):
        home = self.root / 'toolchain'
        gore = home / 'gore-v0.5.0/gore'
        gore.parent.mkdir(parents=True)
        gore.write_text('fake', encoding='utf-8')
        gore.chmod(0o755)
        system, arch = godot._host_platform()
        gdre_name = godot.GodotBackend._gdre_binary_names(system, arch)[0]
        gdre = home / 'gdre-v2.7.4' / gdre_name
        gdre.parent.mkdir(parents=True)
        gdre.write_text('fake', encoding='utf-8')
        gdre.chmod(0o755)
        editor = home / 'godot/editors/4.7.2/Godot'
        editor.parent.mkdir(parents=True)
        editor.write_text('fake', encoding='utf-8')
        editor.chmod(0o755)
        templates = godot.managed_template_dir(home, '4.7.2')
        templates.mkdir(parents=True)
        (templates / 'version.txt').write_text('4.7.2.stable', encoding='utf-8')
        for name in ('android_release.apk', 'android_debug.apk', 'android_source.zip'):
            (templates / name).write_bytes(b'fake')
        sdk = self.root / 'sdk'
        tools = sdk / 'build-tools/36.0.0'
        tools.mkdir(parents=True)
        for name in ('apksigner', 'zipalign', 'aapt2'):
            (tools / name).write_text('fake', encoding='utf-8')
            (tools / name).chmod(0o755)
        platform_dir = sdk / 'platforms/android-36'
        platform_dir.mkdir(parents=True)
        (platform_dir / 'android.jar').write_bytes(b'fake')
        adb = sdk / 'platform-tools/adb'
        adb.parent.mkdir(parents=True)
        adb.write_text('fake', encoding='utf-8')
        java = self.root / 'jdk'
        (java / 'bin').mkdir(parents=True)
        for name in ('java', 'keytool'):
            (java / 'bin' / name).write_text('fake', encoding='utf-8')
            (java / 'bin' / name).chmod(0o755)
        keystore = home / 'godot/debug.keystore'
        keystore.write_bytes(b'fake-keystore')
        return home, sdk, java

    def make_backend(self, commands):
        home, sdk, java = self.make_toolchain()
        remediations = []

        def verify(args, apk):
            commands.verified = True
            with zipfile.ZipFile(apk) as archive:
                self.assertIsNone(archive.testzip())

        def unexpected_network(*args, **kwargs):
            raise AssertionError('offline pipeline attempted network access')

        dependencies = godot.GodotDependencies(
            run_command=commands,
            download_file=unexpected_network,
            extract_archive=unexpected_network,
            fetch_text=unexpected_network,
            sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            verify_apk=verify,
            record_remediation=remediations.append,
            ensure_toolchain=unexpected_network,
        )
        return godot.GodotBackend(dependencies, toolchain_home=home), sdk, java

    def test_detect_standalone_embedded_and_directory(self):
        standalone = self.root / 'game.pck'
        standalone.write_bytes(pck_header())
        detected = godot.detect_godot_source(standalone)
        self.assertEqual(detected.kind, 'pck')
        self.assertEqual(detected.inputs, (standalone,))

        executable = self.root / 'game.exe'
        executable.write_bytes(b'MZ' + b'\0' * 126 + pck_header())
        (self.root / 'game.pck').unlink()
        detected = godot.detect_godot_source(executable)
        self.assertEqual(detected.kind, 'embedded_pck')
        self.assertEqual(detected.inputs, (executable,))

        (self.root / 'game.pck').write_bytes(pck_header())
        (self.root / 'patch.pck').write_bytes(pck_header())
        detected = godot.detect_godot_source(self.root)
        self.assertEqual(detected.kind, 'directory')
        self.assertNotIn(self.root / 'game.pck', detected.inputs)
        self.assertIn(self.root / 'patch.pck', detected.inputs)

    def test_main_auto_detection_and_dry_run_accept_godot_files(self):
        source = self.root / 'game.pck'
        source.write_bytes(pck_header())
        self.assertEqual(pc2android.detect_engine(source), 'godot')
        args = types.SimpleNamespace(source=source, engine='auto', dry_run=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pc2android.run_pipeline(args)
        self.assertIn('"engine": "godot"', output.getvalue())
        self.assertIn(str(source), output.getvalue())

    def test_base_hint_wins_when_patch_also_contains_project_config(self):
        base = self.root / 'base.pck'
        patch = self.root / 'patch.pck'
        base_dir = self.root / 'base'
        patch_dir = self.root / 'patch'
        base_dir.mkdir()
        patch_dir.mkdir()
        (base_dir / 'project.binary').write_text('base', encoding='utf-8')
        (patch_dir / 'project.binary').write_text('patch', encoding='utf-8')
        (patch_dir / 'extra.txt').write_text('extra', encoding='utf-8')
        merged = self.root / 'merged'
        selected = godot.GodotBackend._merge_packs(
            [(patch, patch_dir), (base, base_dir)], merged, base_hint=base
        )
        self.assertEqual(selected, base)
        self.assertEqual((merged / 'project.binary').read_text(encoding='utf-8'), 'base')
        self.assertTrue((merged / 'extra.txt').is_file())
        with self.assertRaisesRegex(godot.GodotBackendError, 'unambiguously'):
            godot.GodotBackend._merge_packs(
                [(patch, patch_dir), (base, base_dir)], self.root / 'ambiguous'
            )

    def test_detect_rejects_non_godot(self):
        path = self.root / 'game.exe'
        path.write_bytes(b'MZ not a godot pack')
        self.assertIsNone(godot.detect_godot_source(path))
        self.assertIsNone(godot.detect_godot_source(self.root / 'missing'))

    def test_version_and_template_paths(self):
        self.assertEqual(godot.parse_engine_version('4.7.2.stable.official'), '4.7.2')
        self.assertEqual(godot.parse_engine_version('4.7.0.stable.official'), '4.7')
        self.assertEqual(godot.godot_release_tag('4.7.0'), '4.7-stable')
        self.assertEqual(godot.template_dir_name('4.7.0'), '4.7.stable')
        self.assertEqual(
            godot.parse_gore_info_version('file game_v1.05.exe\nengine version 4.7.2\n'),
            '4.7.2',
        )
        self.assertEqual(godot.parse_engine_version('4.6-beta2'), '4.6-beta2')
        self.assertEqual(godot.godot_release_tag('4.7.2'), '4.7.2-stable')
        self.assertEqual(godot.template_dir_name('4.7.2'), '4.7.2.stable')
        self.assertEqual(godot.template_dir_name('4.6-beta2'), '4.6.beta2')
        self.assertEqual(
            godot.managed_template_dir('/tmp/tools', '4.7.2'),
            Path('/tmp/tools/godot/export_templates/4.7.2.stable'),
        )
        with mock.patch.object(godot.platform, 'system', return_value='Darwin'):
            self.assertIn('macos.universal', godot.editor_asset_name('4.7.2'))
        with mock.patch.object(godot.platform, 'system', return_value='Linux'), \
                mock.patch.object(godot.platform, 'machine', return_value='x86_64'):
            self.assertEqual(
                godot.editor_asset_name('4.7.2'),
                'Godot_v4.7.2-stable_linux.x86_64.zip',
            )

    def test_engine_version_reconciliation_prefers_matching_pack_and_project(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'project.godot').write_text(
            '[application]\nconfig/features=PackedStringArray("4.7", "GL Compatibility")\n',
            encoding='utf-8',
        )
        version, notes = godot.reconcile_engine_version('4.7.2', '4.5.0', project)
        self.assertEqual(version, '4.7.2')
        self.assertTrue(notes)
        (project / 'project.godot').write_text(
            '[application]\nconfig/features=PackedStringArray("4.6", "GL Compatibility")\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(godot.GodotBackendError, 'version mismatch'):
            godot.reconcile_engine_version('4.7.2', '4.5.0', project)

    def test_recovery_report_contract_and_lossy_warning_data(self):
        summary = godot.validate_recovery_report(recovery_report(gdre_version='v2.7.4'))
        self.assertEqual(summary['failed'], 0)
        self.assertEqual(summary['lossy_imports'], 1)
        self.assertEqual(summary['scripts'], 1)
        with self.assertRaisesRegex(godot.GodotBackendError, 'failed item'):
            godot.validate_recovery_report(recovery_report(failed=[{'message': 'broken'}]))
        with self.assertRaisesRegex(godot.GodotBackendError, 'C#'):
            godot.validate_recovery_report(recovery_report(mono_detected=True))
        with self.assertRaisesRegex(godot.GodotBackendError, 'Godot 4'):
            godot.validate_recovery_report(recovery_report(ver='3.5.2'))
        with self.assertRaisesRegex(godot.GodotBackendError, 'unconverted'):
            godot.validate_recovery_report(recovery_report(not_converted=[{'message': 'missing'}]))
        with self.assertRaisesRegex(godot.GodotBackendError, 'unsupported'):
            godot.validate_recovery_report(recovery_report(unsupported_types=[{'type': 'mesh'}]))

    def test_android_patches_are_applied_once_and_preserve_project(self):
        project = self.root / 'project'
        (project / 'scripts').mkdir(parents=True)
        project_file = project / 'project.godot'
        project_file.write_text(
            '[application]\nconfig/name="Keep me"\nconfig/features=PackedStringArray("4.7", "Forward Plus")\n',
            encoding='utf-8',
        )
        loader = project / 'scripts/patch_loader.gd'
        loader.write_text(
            'extends Node\n\nfunc _init() -> void:\n'
            '\tvar dir = OS.get_executable_path().get_base_dir()\n'
            '\tProjectSettings.load_resource_pack(dir.path_join("patch.pck"))\n',
            encoding='utf-8',
        )
        save = project / 'scripts/save_dir.gd'
        save.write_text(
            'const OLD_DIR := "user://"\nstatic var base = OLD_DIR\n\n'
            'static func setup(release: bool) -> String:\n'
            '\tvar dir = OS.get_executable_path().get_base_dir().path_join("save")\n'
            '\tbase = dir\n\treturn base\n',
            encoding='utf-8',
        )
        patches = godot.apply_android_compatibility(project, '4.7.2')
        self.assertIn('project.godot', patches)
        self.assertIn('scripts/patch_loader.gd', patches)
        self.assertIn('scripts/save_dir.gd', patches)
        text = project_file.read_text(encoding='utf-8')
        self.assertIn('config/name="Keep me"', text)
        self.assertIn('config/quit_on_go_back=false', text)
        self.assertIn('import_etc2_astc=true', text)
        self.assertIn('rendering_method.mobile="gl_compatibility"', text)
        self.assertIn('PackedStringArray("4.7", "GL Compatibility")', text)
        loader_text = loader.read_text(encoding='utf-8')
        save_text = save.read_text(encoding='utf-8')
        self.assertIn('OS.has_feature("android")', loader_text)
        self.assertIn('OS.has_feature("android")', save_text)
        self.assertIn('base = "user://"', save_text)
        self.assertEqual(godot.apply_android_compatibility(project, '4.7.2'), [])
        self.assertEqual(loader.read_text(encoding='utf-8').count('OS.has_feature("android")'), 1)
        self.assertEqual(save.read_text(encoding='utf-8').count('OS.has_feature("android")'), 1)

    def test_tpz_export_templates_are_extracted_as_zip(self):
        archive = self.root / 'templates.tpz'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('templates/version.txt', '4.7.2.stable')
        destination = self.root / 'templates'
        pc2android.extract_archive(archive, destination)
        self.assertEqual(
            (destination / 'templates/version.txt').read_text(encoding='utf-8'),
            '4.7.2.stable',
        )

    def test_export_preset_rendering(self):
        template = SCRIPTS.parent / 'templates/godot/export_presets.cfg'
        rendered = godot.render_export_preset(
            template,
            export_path='/tmp/a.apk',
            application_id='com.example.game',
            app_name='A "quoted" game',
            release_template='/tmp/release.apk',
            debug_template='/tmp/debug.apk',
            icon_resource='res://icon.png',
        )
        self.assertIn('package/unique_name="com.example.game"', rendered)
        self.assertIn('package/signed=false', rendered)
        self.assertIn('include_filter="licenses/*"', rendered)
        self.assertIn('architectures/arm64-v8a=true', rendered)
        self.assertIn('custom_template/release="/tmp/release.apk"', rendered)
        self.assertIn('package/name="A \\"quoted\\" game"', rendered)
        self.assertNotIn('__APPLICATION_ID__', rendered)

    def test_pinned_gore_install_uses_download_checksum_and_version(self):
        system, arch = godot._host_platform()
        key = f'macos-{arch}' if system == 'darwin' else f'linux-{arch}'
        payload = b'fake-gore-archive'
        downloads = []
        remediations = []

        def download(url, destination):
            downloads.append(url)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(payload)

        def extract(archive, destination):
            binary = Path(destination) / 'gore'
            binary.write_text('fake', encoding='utf-8')

        def commands(command, env=None, cwd=None, capture=False):
            self.assertIn('--version', command)
            return 'gore 0.5.0\n'

        dependencies = godot.GodotDependencies(
            run_command=commands,
            download_file=download,
            extract_archive=extract,
            record_remediation=remediations.append,
        )
        backend = godot.GodotBackend(dependencies, toolchain_home=self.root / 'tools')
        with mock.patch.dict(godot.GORE_SHA256, {key: hashlib.sha256(payload).hexdigest()}):
            binary = backend._install_pinned_tool('gore')
        self.assertTrue(binary.is_file())
        self.assertIn('/releases/download/v0.5.0/', downloads[0])
        self.assertTrue(any('installed gore 0.5.0' in item for item in remediations))

    def test_gdre_version_accepts_nonzero_exit_with_matching_output(self):
        home = self.root / 'tools'
        system, arch = godot._host_platform()
        name = godot.GodotBackend._gdre_binary_names(system, arch)[0]
        binary = home / f'gdre-v{godot.GDRE_VERSION}' / name
        binary.parent.mkdir(parents=True)
        binary.write_text('fake', encoding='utf-8')

        def commands(command, **kwargs):
            raise subprocess.CalledProcessError(
                1, command, output='Godot RE Tools v2.7.4\n'
            )

        backend = godot.GodotBackend(
            godot.GodotDependencies(run_command=commands), toolchain_home=home
        )
        self.assertEqual(backend._install_pinned_tool('gdre'), binary)

    def test_license_assets_are_written_with_versions(self):
        project = self.root / 'project'
        project.mkdir()
        destination = godot.GodotBackend._write_license_assets(project, '4.7.2')
        notice = (destination / 'GODOT-SOURCE.txt').read_text(encoding='utf-8')
        self.assertIn('Godot Engine: 4.7.2 (MIT)', notice)
        self.assertIn('gore: 0.5.0 (MIT)', notice)
        self.assertIn('GDRE Tools: 2.7.4 (MIT)', notice)
        self.assertTrue((destination / 'GODOT-MIT.txt').is_file())
        self.assertTrue((destination / 'GODOT-COPYRIGHT.txt').is_file())

    def test_editor_setup_script_uses_bound_settings_api(self):
        backend = godot.GodotBackend(toolchain_home=self.root / 'tools')
        project = self.root / 'project'
        project.mkdir()
        script = backend._write_editor_setup(project, {
            'sdk': self.root / 'sdk',
            'java_home': self.root / 'jdk',
            'keystore': self.root / 'debug.keystore',
        })
        text = script.read_text(encoding='utf-8')
        self.assertIn('EditorSettings.set_setting("export/android/android_sdk_path"', text)
        self.assertIn('EditorSettings.set_setting("export/android/java_sdk_path"', text)
        self.assertIn('EditorSettings.set_setting("export/android/debug_keystore"', text)
        self.assertNotIn('EditorSettings.save()', text)
        self.assertIn('quit()', text)

    def test_pipeline_success_contract_offline(self):
        source = self.make_source()
        commands = FakeCommands()
        backend, sdk, java = self.make_backend(commands)
        out = self.root / 'out'
        args = types.SimpleNamespace(
            source=source,
            name='Game',
            application_id='com.example.game',
            out=out,
            sdk=sdk,
            java_home=java,
            keep_work=True,
        )
        result = godot.run_godot_pipeline(args, backend=backend)
        final = out / 'Game-Godot.apk'
        self.assertTrue(final.is_file())
        self.assertEqual(result['apk'], str(final))
        self.assertEqual(result['recovery']['failed'], 0)
        self.assertEqual(result['recovery']['lossy_imports'], 1)
        self.assertFalse(result['lossless_recovery'])
        self.assertTrue(commands.verified)
        self.assertIn('scripts/patch_loader.gd', result['android_patches'])
        self.assertIn('scripts/save_dir.gd', result['android_patches'])
        self.assertTrue((out / 'Game-Godot-recovery/gdre_export.json').is_file())
        self.assertTrue((out / 'Game-Godot-recovery/pipeline-report.json').is_file())

        workdir = Path(result['workdir'])
        self.assertTrue(workdir.is_dir())
        merged = workdir / 'merged'
        self.assertEqual((merged / 'shared.txt').read_text(encoding='utf-8'), 'base')
        self.assertTrue((merged / 'patch_added.txt').is_file())
        preset = (workdir / 'project/export_presets.cfg').read_text(encoding='utf-8')
        self.assertIn('package/signed=false', preset)
        self.assertIn('custom_template/release=', preset)

        recover = next(command for command in commands.commands if any(
            part.startswith('--recover=') for part in command
        ))
        self.assertIn('--setting=write_json_report=true', recover)
        align_index = next(index for index, command in enumerate(commands.commands)
                           if Path(command[0]).name == 'zipalign' and '-f' in command)
        sign_index = next(index for index, command in enumerate(commands.commands)
                          if Path(command[0]).name == 'apksigner' and 'sign' in command)
        self.assertLess(align_index, sign_index)

    def test_pipeline_fails_on_gore_checksum_warning(self):
        source = self.make_source()
        commands = FakeCommands(checksum_warning=True)
        backend, _, _ = self.make_backend(commands)
        detected = godot.detect_godot_source(source)
        with self.assertRaisesRegex(godot.GodotBackendError, 'checksum failures'):
            backend._extract_packs(detected, self.root / 'gore', self.root / 'work')

    def test_pipeline_fails_when_export_produces_no_apk(self):
        source = self.make_source()
        commands = FakeCommands(export_apk=False)
        backend, sdk, java = self.make_backend(commands)
        out = self.root / 'out'
        args = types.SimpleNamespace(
            source=source,
            name='Game',
            application_id='com.example.game',
            out=out,
            sdk=sdk,
            java_home=java,
            keep_work=False,
        )
        with self.assertRaisesRegex(godot.GodotBackendError, 'did not produce an APK'):
            godot.run_godot_pipeline(args, backend=backend)
        self.assertFalse((out / 'Game-Godot.apk').exists())

    def test_apk_content_contract_rejects_missing_native_library(self):
        apk = self.root / 'bad.apk'
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('lib/arm64-v8a/libc++_shared.so', b'c++')
            archive.writestr('assets/.godot/exported/1/main.scn', b'scene')
        with self.assertRaisesRegex(godot.GodotBackendError, 'libgodot_android.so'):
            godot.GodotBackend._verify_apk_contents(apk)

    def test_csharp_and_gdextension_are_rejected(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'main.gd').write_text('extends Node\n', encoding='utf-8')
        (project / 'main.cs').write_text('class Main {}', encoding='utf-8')
        with self.assertRaisesRegex(godot.GodotBackendError, 'C#'):
            godot._validate_project_support(project, {})
        (project / 'main.cs').unlink()
        (project / 'addon.gdextension').write_text('[configuration]', encoding='utf-8')
        with self.assertRaisesRegex(godot.GodotBackendError, 'GDExtension'):
            godot._validate_project_support(project, {})


if __name__ == '__main__':
    unittest.main()
