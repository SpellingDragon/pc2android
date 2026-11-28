#!/usr/bin/env python3
"""Godot 4 recovery and Android export backend for pc2android.

The backend is intentionally independent from the WebView and CrySirror paths. It
uses pinned gore/GDRE releases, recovers GDScript projects, applies the Android
compatibility fixes to the recovered copy, and exports a signed arm64 APK with a
matching Godot editor and export templates.
"""
import hashlib
import json
import mmap
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    from . import pc2android
except ImportError:
    pc2android = sys.modules.get('pc2android') or sys.modules.get('__main__')
    if pc2android is None or not hasattr(pc2android, 'run'):
        import pc2android


GORE_VERSION = '0.5.0'
GDRE_VERSION = '2.7.4'
GODOT_MAJOR = 4
ANDROID_ABI = 'arm64-v8a'
BUILD_TOOLS = '36.0.0'
ANDROID_PLATFORM = '36'
DEBUG_KEYSTORE_ALIAS = 'androiddebugkey'
DEBUG_KEYSTORE_PASSWORD = 'android'

GORE_SHA256 = {
    'macos-arm64': 'db56ca1161f31bcb02e7bb648761e9eafce408fa8b295a7f22b32cfeb53eb2d1',
    'macos-x86_64': '511fa747b2e68fed067429f16ecad2a0a2326c18af3f649340aaa1228f307424',
    'linux-aarch64': '7ae2f9dd2b2991f38905df6359c1fffa55034670771b19630e467886396f6be2',
    'linux-x86_64': 'cf38a4e94a9dee8ee92c69253b168a10e539df416ecf40b06b10f517c4f298ca',
}
GDRE_SHA256 = {
    'macos': 'a148b9509c2826fcc238e81c21005bf3128bdcee1a87c3f79ad943a205aa2170',
    'linux': '3dc4f1244dda62e41e0c952ac00a69e60e07f3b0d5a211164806bf9d41a54758',
}
VERSION_RE = re.compile(
    r'(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?(?:-([A-Za-z][A-Za-z0-9.]*))?'
)
PROJECT_CONFIG_NAMES = ('project.binary', 'project.godot', 'engine.cfg')
FAILED_REPORT_KEYS = (
    'failed',
    'failed_scripts',
    'failed_plugin_cfg_create',
    'failed_gdnative_copy',
)


class GodotBackendError(RuntimeError):
    """A source, recovery, export, or verification contract failed."""


@dataclass(frozen=True)
class PackInfo:
    pack_version: int
    engine_version: str
    offset: int


@dataclass(frozen=True)
class GodotSource:
    source: Path
    inputs: tuple
    kind: str
    base_hint: Optional[Path] = None

    @property
    def display_name(self):
        return self.source.stem if self.source.is_file() else self.source.name

    def as_dict(self):
        return {
            'source': str(self.source),
            'inputs': [str(path) for path in self.inputs],
            'kind': self.kind,
            'base_hint': str(self.base_hint) if self.base_hint else None,
        }


def _default_run_command(command, env=None, cwd=None, capture=False):
    printable = [str(part) for part in command]
    print('+ ' + ' '.join(printable), flush=True)
    if not capture:
        return pc2android.run(printable, env=env, cwd=cwd)
    result = subprocess.run(
        printable,
        check=True,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end='' if result.stdout.endswith('\n') else '\n', flush=True)
    return result.stdout


def _default_fetch_text(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'pc2android-godot'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode('utf-8')


@dataclass
class GodotDependencies:
    run_command: Optional[Callable] = None
    download_file: Optional[Callable] = None
    extract_archive: Optional[Callable] = None
    fetch_text: Optional[Callable] = None
    sha256: Optional[Callable] = None
    verify_apk: Optional[Callable] = None
    record_remediation: Optional[Callable] = None
    ensure_toolchain: Optional[Callable] = None

    def __post_init__(self):
        self.run_command = self.run_command or _default_run_command
        self.download_file = self.download_file or pc2android.download_file
        self.extract_archive = self.extract_archive or pc2android.extract_archive
        self.fetch_text = self.fetch_text or _default_fetch_text
        self.sha256 = self.sha256 or pc2android.sha256
        self.verify_apk = self.verify_apk or pc2android.verify
        self.record_remediation = self.record_remediation or pc2android.record_remediation
        self.ensure_toolchain = self.ensure_toolchain or pc2android.ensure_toolchain


def _parse_pack_header(data):
    if len(data) < 20:
        return None
    magic, pack_version, major, minor, patch = struct.unpack_from('<4sIIII', data)
    if magic != b'GDPC' or pack_version > 4 or not 2 <= major <= 4:
        return None
    if minor > 99 or patch > 999:
        return None
    return pack_version, f'{major}.{minor}.{patch}'


def _standalone_pack_info(path):
    try:
        with path.open('rb') as source:
            parsed = _parse_pack_header(source.read(20))
    except OSError:
        return None
    if not parsed:
        return None
    return PackInfo(parsed[0], parsed[1], 0)


def _embedded_pack_info(path):
    try:
        if path.stat().st_size < 20:
            return None
        with path.open('rb') as source:
            with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
                found = None
                offset = 0
                while True:
                    offset = data.find(b'GDPC', offset)
                    if offset < 0:
                        break
                    parsed = _parse_pack_header(data[offset:offset + 20])
                    if parsed:
                        found = PackInfo(parsed[0], parsed[1], offset)
                    offset += 4
                return found
    except (OSError, ValueError):
        return None


def _walk_candidates(root):
    pcks = []
    executables = []
    for current, directories, files in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if not name.startswith('.') and name not in ('__MACOSX', 'node_modules')
        )
        for name in sorted(files):
            path = Path(current) / name
            lower = name.lower()
            if lower.endswith('.pck') and _standalone_pack_info(path):
                pcks.append(path)
            elif lower.endswith(('.exe', '.x86_64', '.x64')) and _embedded_pack_info(path):
                executables.append(path)
    return pcks, executables


def detect_godot_source(source):
    """Return a GodotSource for a PCK/EXE or a directory containing them.

    Detection is offline and conservative. Sibling PCKs are included so patch
    packs can be merged later. A same-stem PCK is omitted when a self-contained
    executable already contains that base pack.
    """
    source = Path(source).expanduser().resolve()
    base_hint = None
    if source.is_file():
        if source.suffix.lower() == '.pck':
            if not _standalone_pack_info(source):
                return None
            kind = 'pck'
        else:
            if not _embedded_pack_info(source):
                return None
            kind = 'embedded_pck'
        base_hint = source
        inputs = [source]
        for sibling in sorted(source.parent.glob('*.pck'), key=lambda path: str(path).casefold()):
            if sibling == source or not _standalone_pack_info(sibling):
                continue
            if kind == 'embedded_pck' and sibling.stem.casefold() == source.stem.casefold():
                continue
            inputs.append(sibling.resolve())
    elif source.is_dir():
        pcks, executables = _walk_candidates(source)
        if not pcks and not executables:
            return None
        executable_stems = {path.stem.casefold() for path in executables}
        pcks = [path for path in pcks if path.stem.casefold() not in executable_stems]
        inputs = pcks + executables
        kind = 'directory'
        preferred = [path for path in executables if path.stem.casefold() == source.name.casefold()]
        if len(preferred) == 1:
            base_hint = preferred[0]
        elif len(executables) == 1:
            base_hint = executables[0]
        elif len(inputs) == 1:
            base_hint = inputs[0]
    else:
        return None
    inputs = sorted(set(path.resolve() for path in inputs), key=lambda path: str(path).casefold())
    return GodotSource(source, tuple(inputs), kind, base_hint)


def parse_engine_version(value):
    match = VERSION_RE.search(str(value))
    if not match:
        raise GodotBackendError(f'Cannot determine Godot engine version from: {value!r}')
    major, minor, patch, prerelease = match.groups()
    version = f'{major}.{minor}'
    if patch is not None and patch != '0':
        version += f'.{patch}'
    if prerelease:
        version += f'-{prerelease}'
    return version


def parse_gore_info_version(output):
    match = re.search(r'engine\s+version\s+(\d+\.\d+(?:\.\d+)?(?:-[A-Za-z][A-Za-z0-9.]*)?)', str(output), re.I)
    if not match:
        raise GodotBackendError(f'Cannot find Godot engine version in gore info output: {output!r}')
    return parse_engine_version(match.group(1))


def godot_release_tag(version):
    version = parse_engine_version(version)
    return version if '-' in version else version + '-stable'


def template_dir_name(version):
    version = parse_engine_version(version)
    if '-' in version:
        number, status = version.split('-', 1)
    else:
        number, status = version, 'stable'
    return f'{number}.{status}'


def _host_platform():
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'darwin':
        return system, ('arm64' if machine in ('arm64', 'aarch64') else 'x86_64')
    if system == 'linux':
        return system, ('aarch64' if machine in ('arm64', 'aarch64') else 'x86_64')
    raise GodotBackendError(
        f'Automatic Godot export is not supported on {platform.system()}. Use macOS or Linux.'
    )


def editor_asset_name(version):
    tag = godot_release_tag(version)
    system, arch = _host_platform()
    if system == 'darwin':
        suffix = 'macos.universal'
    else:
        suffix = f'linux.{arch}'
    return f'Godot_v{tag}_{suffix}.zip'


def managed_template_dir(home, version):
    return Path(home) / 'godot' / 'export_templates' / template_dir_name(version)


def _file_digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_digest(path, algorithm, expected, label):
    actual = _file_digest(path, algorithm)
    if actual.lower() != expected.lower():
        raise GodotBackendError(f'{label} checksum mismatch: expected {expected}, got {actual}')


def _loss_type(entry):
    value = entry.get('loss_type', 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GodotBackendError(f'Invalid GDRE loss_type: {value!r}')


def validate_recovery_report(report, expected_gdre_version=GDRE_VERSION):
    if not isinstance(report, dict):
        raise GodotBackendError('GDRE recovery report is not a JSON object')
    gdre_version = str(report.get('gdre_version', '')).lstrip('v')
    if gdre_version != expected_gdre_version:
        raise GodotBackendError(
            f'Unexpected GDRE report version {gdre_version!r}; expected {expected_gdre_version}'
        )
    version = parse_engine_version(report.get('ver', ''))
    if int(version.split('.')[0]) != GODOT_MAJOR:
        raise GodotBackendError(f'Only Godot {GODOT_MAJOR}.x is supported; recovered {version}')
    for key in ('success', 'decompiled_scripts', *FAILED_REPORT_KEYS):
        if not isinstance(report.get(key), list):
            raise GodotBackendError(f'GDRE recovery report is missing list field: {key}')
    for key in ('not_converted', 'unsupported_types'):
        if not isinstance(report.get(key, []), list):
            raise GodotBackendError(f'GDRE recovery report has invalid list field: {key}')
    for key in FAILED_REPORT_KEYS:
        if report[key]:
            raise GodotBackendError(f'GDRE recovery reported {len(report[key])} {key} item(s)')
    if report.get('had_encryption_error'):
        raise GodotBackendError('GDRE recovery reported an encryption error')
    if report.get('mono_detected'):
        raise GodotBackendError('C# / Godot Mono projects are not supported by this backend')
    if report.get('custom_version_detected'):
        raise GodotBackendError('Custom Godot engine builds are not supported by this backend')
    if report.get('uses_double_precision'):
        raise GodotBackendError('Double-precision Godot builds are not supported by this backend')
    if report.get('not_converted', []):
        raise GodotBackendError(f'GDRE recovery left {len(report["not_converted"])} resource(s) unconverted')
    if report.get('unsupported_types', []):
        raise GodotBackendError(f'GDRE recovery reported {len(report["unsupported_types"])} unsupported resource type(s)')
    lossy = [entry for entry in report['success'] if _loss_type(entry) != 0]
    return {
        'engine_version': version,
        'scripts': len(report['decompiled_scripts']),
        'resources': len(report['success']),
        'failed': sum(len(report[key]) for key in FAILED_REPORT_KEYS),
        'lossy_imports': len(lossy),
        'not_converted': len(report.get('not_converted', [])),
        'unsupported_types': len(report.get('unsupported_types', [])),
    }


def reconcile_engine_version(pack_version, gdre_version, project):
    pack_version = parse_engine_version(pack_version)
    gdre_version = parse_engine_version(gdre_version)
    if pack_version == gdre_version:
        return pack_version, []
    project_file = Path(project) / 'project.godot'
    text = project_file.read_text(encoding='utf-8-sig') if project_file.is_file() else ''
    match = re.search(r'config/features\s*=\s*PackedStringArray\(([^)]*)\)', text)
    feature_version = None
    if match:
        feature_match = re.search(r'"(\d+\.\d+(?:\.\d+)?)"', match.group(1))
        if feature_match:
            feature_version = parse_engine_version(feature_match.group(1))
    pack_line = '.'.join(pack_version.split('.')[:2])
    gdre_line = '.'.join(gdre_version.split('.')[:2])
    feature_line = '.'.join(feature_version.split('.')[:2]) if feature_version else None
    if feature_line == pack_line:
        return pack_version, [
            f'GDRE detected {gdre_version}, but pack/project metadata indicates {pack_version}; using {pack_version}'
        ]
    if feature_line == gdre_line:
        return gdre_version, [
            f'GDRE detected {gdre_version}, but pack metadata indicates {pack_version}; using project-compatible {gdre_version}'
        ]
    raise GodotBackendError(
        f'Godot version mismatch: pack={pack_version}, GDRE={gdre_version}, project={feature_version or "unknown"}'
    )


def _atomic_write_text(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + '.pc2android-tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def set_project_settings(path, values):
    """Set top-level Godot ConfigFile values while preserving unrelated lines."""
    path = Path(path)
    lines = path.read_text(encoding='utf-8-sig').splitlines(keepends=True)
    newline = '\r\n' if any(line.endswith('\r\n') for line in lines) else '\n'
    sections = {}
    current = None
    for index, line in enumerate(lines):
        match = re.match(r'^\s*\[([^\]]+)\]\s*(?:\r?\n)?$', line)
        if match:
            current = match.group(1)
            sections.setdefault(current, []).append(index)
    changed = False
    for section, entries in values.items():
        indexes = sections.get(section, [])
        if not indexes:
            if lines and lines[-1].strip():
                lines.append(newline)
            lines.append(f'[{section}]{newline}')
            start = len(lines)
            sections[section] = [start - 1]
            indexes = [start - 1]
            current = section
        else:
            current = section
        start = indexes[0] + 1
        end = len(lines)
        if len(indexes) > 1:
            end = indexes[1]
        else:
            for index in range(start, len(lines)):
                if re.match(r'^\s*\[[^\]]+\]\s*(?:\r?\n)?$', lines[index]):
                    end = index
                    break
        for key, value in entries.items():
            pattern = re.compile(rf'^(\s*){re.escape(key)}\s*=')
            replacement = f'{key}={value}{newline}'
            matched = False
            for index in range(start, end):
                if pattern.match(lines[index]):
                    indent = pattern.match(lines[index]).group(1)
                    desired = indent + replacement
                    if lines[index] != desired:
                        lines[index] = desired
                        changed = True
                    matched = True
                    break
            if not matched:
                insert_at = end
                while insert_at > start and not lines[insert_at - 1].strip():
                    insert_at -= 1
                lines.insert(insert_at, replacement)
                changed = True
                end += 1
                for name in sections:
                    sections[name] = [index + (index >= insert_at) for index in sections[name]]
                start = indexes[0] + 1
    if changed:
        _atomic_write_text(path, ''.join(lines))
    return changed


def _indent_width(line):
    prefix = line[:len(line) - len(line.lstrip(' \t'))]
    return len(prefix.expandtabs(4))


def _function_spans(lines):
    spans = []
    signature_re = re.compile(
        r'^(\s*)(?:static\s+)?func\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:->[^:]+)?:\s*(?:#.*)?(?:\r?\n)?$'
    )
    for index, line in enumerate(lines):
        match = signature_re.match(line)
        if not match:
            continue
        indent = len(match.group(1).expandtabs(4))
        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            stripped = lines[candidate].strip()
            if not stripped or stripped.startswith('#'):
                continue
            if _indent_width(lines[candidate]) <= indent:
                end = candidate
                break
        spans.append((index, end, match.group(2), indent))
    return spans


def _insert_android_guard(path, mode):
    path = Path(path)
    original = path.read_text(encoding='utf-8-sig')
    lines = original.splitlines(keepends=True)
    newline = '\r\n' if any(line.endswith('\r\n') for line in lines) else '\n'
    text = ''.join(lines)
    old_dir_match = re.search(r'const\s+OLD_DIR\s*(?::[^=]+)?=\s*"([^"]+)"', text)
    old_dir = old_dir_match.group(1) if old_dir_match else 'user://'
    for start, end, name, indent in _function_spans(lines):
        body = ''.join(lines[start + 1:end])
        if (re.search(r'OS\.has_feature\(\s*["\']android["\']\s*\)', body)
                or re.search(r'OS\.get_name\(\s*\)\s*==\s*["\']Android["\']', body)):
            continue
        if mode == 'loader':
            if 'OS.get_executable_path' not in body:
                continue
            if 'ProjectSettings.load_resource_pack' not in body and '.pck' not in body:
                continue
            guard = ['if OS.has_feature("android") or OS.get_name() == "Android":', 'return']
        else:
            if 'OS.get_executable_path' not in body:
                continue
            if 'base =' not in body and 'save' not in path.name.lower():
                continue
            signature = lines[start]
            returns_value = '->' in signature and not re.search(r'->\s*void\b', signature)
            guard = [
                'if OS.has_feature("android") or OS.get_name() == "Android":',
                f'base = "{old_dir}"',
                'return base' if returns_value else 'return',
            ]
        prefix = lines[start][:len(lines[start]) - len(lines[start].lstrip(' \t'))]
        unit = '\t' if '\t' in prefix else '    '
        inserted = [prefix + unit + line + newline for line in guard]
        lines[start + 1:start + 1] = inserted
        _atomic_write_text(path, ''.join(lines))
        return name
    return None


def apply_android_compatibility(project, engine_version):
    project = Path(project)
    project_file = project / 'project.godot'
    if not project_file.is_file():
        raise GodotBackendError(f'Recovered project is missing {project_file}')
    version = parse_engine_version(engine_version)
    feature_version = '.'.join(version.split('.')[:2])
    changed = set_project_settings(project_file, {
        'application': {
            'config/quit_on_go_back': 'false',
            'config/features': f'PackedStringArray("{feature_version}", "GL Compatibility")',
        },
        'rendering': {
            'renderer/rendering_method': '"gl_compatibility"',
            'renderer/rendering_method.mobile': '"gl_compatibility"',
            'textures/vram_compression/import_etc2_astc': 'true',
        },
    })
    patches = ['project.godot'] if changed else []
    for path in sorted(project.rglob('*.gd'), key=lambda item: str(item).casefold()):
        name = path.name.lower()
        if name == 'patch_loader.gd':
            function = _insert_android_guard(path, 'loader')
        elif name == 'save_dir.gd':
            function = _insert_android_guard(path, 'save')
        else:
            continue
        if function:
            patches.append(path.relative_to(project).as_posix())
    return patches


def _project_icon(project):
    project_file = Path(project) / 'project.godot'
    text = project_file.read_text(encoding='utf-8-sig')
    match = re.search(r'^config/icon\s*=\s*"res://([^"]+)"', text, re.MULTILINE)
    candidates = []
    if match:
        candidates.append((Path(project) / match.group(1), 'res://' + match.group(1)))
    candidates.append((Path(project) / 'icon.png', 'res://icon.png'))
    for path, resource_path in candidates:
        if path.is_file():
            return resource_path
    return ''


def _config_escape(value):
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def render_export_preset(template_path, *, export_path, application_id, app_name,
                         release_template, debug_template, icon_resource=''):
    text = Path(template_path).read_text(encoding='utf-8')
    values = {
        '__EXPORT_PATH__': export_path,
        '__APPLICATION_ID__': application_id,
        '__APP_NAME__': app_name,
        '__RELEASE_TEMPLATE__': release_template,
        '__DEBUG_TEMPLATE__': debug_template,
        '__ICON_RESOURCE__': icon_resource,
    }
    for key, value in values.items():
        text = text.replace(key, _config_escape(value))
    return text


def _validate_project_support(project, report):
    project = Path(project)
    if report.get('mono_detected') or any(project.rglob('*.cs')) or any(project.rglob('*.csproj')):
        raise GodotBackendError('C# / Godot Mono projects are not supported by this backend')
    native = list(project.rglob('*.gdextension')) + list(project.rglob('*.gdnlib'))
    if native:
        names = ', '.join(path.relative_to(project).as_posix() for path in native[:5])
        raise GodotBackendError(f'GDExtension/GDNative projects are not supported: {names}')
    if not any(project.rglob('*.gd')):
        raise GodotBackendError('Recovered project contains no GDScript sources')


def _copy_tree(source, destination):
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        copy_function=shutil.copy2,
        symlinks=True,
    )


class GodotBackend:
    def __init__(self, dependencies=None, toolchain_home=None):
        self.dependencies = dependencies or GodotDependencies()
        self.home = Path(toolchain_home or pc2android.TOOLCHAIN_HOME).expanduser().resolve()
        self.log_dir = None
        self._metadata_cache = {}

    def _command(self, stage, command, *, env=None, cwd=None, capture=False, allow_nonzero=False):
        try:
            output = self.dependencies.run_command(
                command, env=env, cwd=cwd, capture=capture
            )
        except subprocess.CalledProcessError as error:
            if self.log_dir and error.output:
                self._write_log(stage, error.output if isinstance(error.output, str) else str(error.output))
            if allow_nonzero and capture and error.output:
                return error.output if isinstance(error.output, str) else str(error.output)
            raise GodotBackendError(f'{stage} failed with exit code {error.returncode}') from error
        except OSError as error:
            raise GodotBackendError(f'{stage} could not start: {error}') from error
        if capture and self.log_dir:
            self._write_log(stage, output or '')
        return output or ''

    def _write_log(self, stage, text):
        if not self.log_dir:
            return
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '-', stage)
        (self.log_dir / f'{safe}.log').write_text(text, encoding='utf-8')

    def _install_pinned_tool(self, kind):
        system, arch = _host_platform()
        if kind == 'gore':
            version = GORE_VERSION
            key = f'macos-{arch}' if system == 'darwin' else f'linux-{arch}'
            archive_name = f'{key}.tar.gz'
            url = f'https://github.com/figurophobia/gore/releases/download/v{version}/{archive_name}'
            expected = GORE_SHA256[key]
            target = self.home / f'gore-v{version}'
            version_args = ['--version']
            version_pattern = re.compile(rf'(?<!\d){re.escape(version)}(?!\d)')
        else:
            version = GDRE_VERSION
            key = 'macos' if system == 'darwin' else 'linux'
            archive_name = f'GDRE_tools-v{version}-{key}.zip'
            url = f'https://github.com/GDRETools/gdsdecomp/releases/download/v{version}/{archive_name}'
            expected = GDRE_SHA256[key]
            target = self.home / f'gdre-v{version}'
            version_args = ['--gdre-version']
            version_pattern = re.compile(rf'(?<!\d){re.escape(version)}(?!\d)')

        def locate(root):
            names = ['gore'] if kind == 'gore' else self._gdre_binary_names(system, arch)
            for name in names:
                matches = sorted(root.rglob(name), key=lambda path: str(path).casefold())
                for match in matches:
                    if match.is_file():
                        return match
            return None

        binary = locate(target) if target.is_dir() else None
        if binary:
            output = self._command(f'{kind}-version', [binary, *version_args], capture=True, allow_nonzero=True)
            if not version_pattern.search(output):
                raise GodotBackendError(f'{kind} tool version mismatch: {output.strip()!r}')
            return binary

        self.home.mkdir(parents=True, exist_ok=True)
        downloads = self.home / 'downloads'
        archive = downloads / archive_name
        self.dependencies.download_file(url, archive)
        _verify_digest(archive, 'sha256', expected, f'{kind} {version}')
        temporary = Path(tempfile.mkdtemp(prefix=f'extract-{kind}-', dir=downloads))
        try:
            self.dependencies.extract_archive(archive, temporary)
            binary = locate(temporary)
            if not binary:
                raise GodotBackendError(f'{kind} archive does not contain the expected binary')
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            _copy_tree(temporary, target)
            binary = locate(target)
            if not binary:
                raise GodotBackendError(f'{kind} installation did not produce a usable binary')
            binary.chmod(0o755)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        output = self._command(f'{kind}-version', [binary, *version_args], capture=True, allow_nonzero=True)
        if not version_pattern.search(output):
            raise GodotBackendError(f'{kind} tool version mismatch: {output.strip()!r}')
        self.dependencies.record_remediation(f'installed {kind} {version} at {binary}')
        return binary

    @staticmethod
    def _gdre_binary_names(system, arch):
        if system == 'darwin':
            preferred = 'gdre_tools.arm64' if arch == 'arm64' else 'gdre_tools.x86_64'
            return ['Godot RE Tools', preferred, 'gdre_tools']
        preferred = 'gdre_tools.aarch64' if arch == 'aarch64' else 'gdre_tools.x86_64'
        return [preferred, 'gdre_tools']

    def _godot_metadata(self, version):
        tag = godot_release_tag(version)
        if tag not in self._metadata_cache:
            url = (
                'https://raw.githubusercontent.com/godotengine/godot-builds/'
                f'{tag}/releases/godot-{tag}.json'
            )
            try:
                metadata = json.loads(self.dependencies.fetch_text(url))
            except (OSError, ValueError) as error:
                raise GodotBackendError(f'Cannot fetch Godot {version} release metadata: {error}') from error
            self._metadata_cache[tag] = metadata
        return self._metadata_cache[tag]

    @staticmethod
    def _metadata_checksum(metadata, filename):
        for entry in metadata.get('files', []):
            if entry.get('filename') == filename:
                checksum = str(entry.get('checksum', ''))
                if re.fullmatch(r'[0-9a-fA-F]{128}', checksum):
                    return checksum
        raise GodotBackendError(f'Godot release metadata has no SHA512 for {filename}')

    @staticmethod
    def _find_editor(root):
        root = Path(root)
        mac = root / 'Godot.app/Contents/MacOS/Godot'
        if mac.is_file():
            return mac
        for path in sorted(root.rglob('Godot*'), key=lambda item: str(item).casefold()):
            if path.is_file() and not path.suffix.lower() in ('.zip', '.tpz'):
                return path
        return None

    def _checked_editor(self, binary, version):
        output = self._command('godot-version', [binary, '--version'], capture=True)
        reported = parse_engine_version(output)
        if reported != parse_engine_version(version) or '.mono' in output.lower():
            raise GodotBackendError(
                f'Godot editor version mismatch: expected {version}, got {output.strip()!r}'
            )
        return binary

    def ensure_godot_editor(self, version):
        version = parse_engine_version(version)
        target = self.home / 'godot' / 'editors' / version
        binary = self._find_editor(target) if target.is_dir() else None
        if binary:
            return self._checked_editor(binary, version)
        filename = editor_asset_name(version)
        metadata = self._godot_metadata(version)
        expected = self._metadata_checksum(metadata, filename)
        url = f'https://github.com/godotengine/godot-builds/releases/download/{godot_release_tag(version)}/{filename}'
        downloads = self.home / 'downloads'
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / filename
        self.dependencies.download_file(url, archive)
        _verify_digest(archive, 'sha512', expected, f'Godot {version} editor')
        temporary = Path(tempfile.mkdtemp(prefix='extract-godot-editor-', dir=downloads))
        try:
            self.dependencies.extract_archive(archive, temporary)
            binary = self._find_editor(temporary)
            if not binary:
                raise GodotBackendError('Godot editor archive does not contain an editor binary')
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            _copy_tree(temporary, target)
            binary = self._find_editor(target)
            if not binary:
                raise GodotBackendError('Godot editor installation is incomplete')
            binary.chmod(0o755)
            (binary.parent / '._sc_').write_text('', encoding='utf-8')
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        binary = self._checked_editor(binary, version)
        self.dependencies.record_remediation(f'installed Godot {version} editor at {binary}')
        return binary

    @staticmethod
    def _valid_templates(directory, version):
        directory = Path(directory)
        required = ('version.txt', 'android_release.apk', 'android_debug.apk', 'android_source.zip')
        if not all((directory / name).is_file() for name in required):
            return False
        try:
            reported = (directory / 'version.txt').read_text(encoding='utf-8').strip()
        except OSError:
            return False
        return reported == template_dir_name(version)

    def ensure_godot_templates(self, version):
        version = parse_engine_version(version)
        target = managed_template_dir(self.home, version)
        if self._valid_templates(target, version):
            return target
        filename = f'Godot_v{godot_release_tag(version)}_export_templates.tpz'
        metadata = self._godot_metadata(version)
        expected = self._metadata_checksum(metadata, filename)
        url = f'https://github.com/godotengine/godot-builds/releases/download/{godot_release_tag(version)}/{filename}'
        downloads = self.home / 'downloads'
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / filename
        self.dependencies.download_file(url, archive)
        _verify_digest(archive, 'sha512', expected, f'Godot {version} export templates')
        temporary = Path(tempfile.mkdtemp(prefix='extract-godot-templates-', dir=downloads))
        try:
            self.dependencies.extract_archive(archive, temporary)
            inner = temporary / 'templates'
            source = inner if inner.is_dir() else temporary
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_tree(source, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        if not self._valid_templates(target, version):
            raise GodotBackendError(f'Godot {version} export templates are incomplete or mismatched')
        self.dependencies.record_remediation(f'installed Godot {version} export templates at {target}')
        return target

    def _install_platform_tools(self, sdk):
        sdk = Path(sdk)
        base = 'https://dl.google.com/android/repository/'
        try:
            root = ElementTree.fromstring(self.dependencies.fetch_text(base + 'repository2-3.xml'))
        except (OSError, ElementTree.ParseError) as error:
            raise GodotBackendError(f'Cannot fetch Android repository metadata: {error}') from error
        host_os = 'macosx' if platform.system() == 'Darwin' else 'linux'
        metadata = None
        for package in root.iter():
            if not package.tag.endswith('remotePackage') or package.get('path') != 'platform-tools':
                continue
            for archive in package.iter():
                if not archive.tag.endswith('archive'):
                    continue
                host = next((child.text.strip() for child in archive if child.tag.endswith('host-os')), '')
                if host and host != host_os:
                    continue
                complete = next((child for child in archive if child.tag.endswith('complete')), None)
                if complete is not None:
                    metadata = {child.tag.split('}')[-1]: (child.text or '').strip() for child in complete}
                    break
            if metadata:
                break
        if not metadata:
            raise GodotBackendError('Android repository does not provide platform-tools for this host')
        downloads = self.home / 'downloads'
        archive = downloads / Path(metadata['url']).name
        self.dependencies.download_file(base + metadata['url'], archive)
        if archive.stat().st_size != int(metadata['size']):
            raise GodotBackendError('Android platform-tools download size mismatch')
        if _file_digest(archive, 'sha1') != metadata['checksum'].lower():
            raise GodotBackendError('Android platform-tools download checksum mismatch')
        temporary = Path(tempfile.mkdtemp(prefix='extract-platform-tools-', dir=downloads))
        try:
            self.dependencies.extract_archive(archive, temporary)
            source = temporary / 'platform-tools'
            if not source.is_dir():
                source = temporary
            target = sdk / 'platform-tools'
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_tree(source, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        adb = target / ('adb.exe' if os.name == 'nt' else 'adb')
        if not adb.is_file():
            raise GodotBackendError('Android platform-tools installation did not produce adb')
        self.dependencies.record_remediation(f'installed Android platform-tools at {target}')
        return target

    def ensure_android_toolchain(self, args):
        sdk = Path(args.sdk).expanduser() if getattr(args, 'sdk', None) else None
        java_home = Path(args.java_home).expanduser() if getattr(args, 'java_home', None) else None

        def valid_sdk(path):
            return bool(path and (path / f'build-tools/{BUILD_TOOLS}/apksigner').is_file()
                        and (path / f'build-tools/{BUILD_TOOLS}/zipalign').is_file()
                        and (path / f'build-tools/{BUILD_TOOLS}/aapt2').is_file()
                        and (path / f'platforms/android-{ANDROID_PLATFORM}/android.jar').is_file())

        def valid_java(path):
            return bool(path and (path / 'bin/java').is_file() and (path / 'bin/keytool').is_file())

        if not valid_sdk(sdk) or not valid_java(java_home):
            toolchain = self.dependencies.ensure_toolchain(need_browser=False, need_gradle=False)
            sdk = sdk if valid_sdk(sdk) else Path(toolchain['sdk'])
            java_home = java_home if valid_java(java_home) else Path(toolchain['java_home'])
        if not valid_sdk(sdk) or not valid_java(java_home):
            raise GodotBackendError('Android SDK build-tools 36.0.0/platform 36 and JDK 17+ are required')
        adb = sdk / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb')
        if not adb.is_file():
            self._install_platform_tools(sdk)
        args.sdk = sdk.resolve()
        args.java_home = java_home.resolve()
        return {'sdk': args.sdk, 'java_home': args.java_home}

    def ensure_debug_keystore(self, java_home):
        keytool = Path(java_home) / 'bin/keytool'
        keystore = self.home / 'godot' / 'debug.keystore'
        if keystore.is_file():
            output = self._command(
                'keystore-check',
                [keytool, '-list', '-keystore', keystore, '-storepass', DEBUG_KEYSTORE_PASSWORD,
                 '-alias', DEBUG_KEYSTORE_ALIAS],
                capture=True,
            )
            if DEBUG_KEYSTORE_ALIAS in output:
                return keystore
            keystore.unlink()
        keystore.parent.mkdir(parents=True, exist_ok=True)
        self._command(
            'keystore-create',
            [keytool, '-genkeypair', '-keystore', keystore, '-storepass', DEBUG_KEYSTORE_PASSWORD,
             '-keypass', DEBUG_KEYSTORE_PASSWORD, '-alias', DEBUG_KEYSTORE_ALIAS,
             '-dname', 'CN=Android Debug,O=Android,C=US', '-keyalg', 'RSA', '-keysize', '2048',
             '-validity', '10000', '-noprompt'],
            capture=True,
        )
        if not keystore.is_file():
            raise GodotBackendError('Debug keystore generation did not produce a keystore')
        self.dependencies.record_remediation(f'created Android debug keystore at {keystore}')
        return keystore

    def _extract_packs(self, source_info, gore, workdir):
        extracted = []
        versions = []
        for index, pack in enumerate(source_info.inputs):
            info = self._command('gore-info', [gore, 'info', pack], capture=True)
            self._write_log(f'gore-info-{index}', info)
            try:
                versions.append(parse_gore_info_version(info))
            except GodotBackendError as error:
                raise GodotBackendError(f'Cannot determine Godot version for {pack}: {error}') from error
            destination = workdir / 'packs' / f'{index:03d}'
            destination.parent.mkdir(parents=True, exist_ok=True)
            output = self._command('gore-extract', [gore, 'extract', pack, '-o', destination], capture=True)
            if re.search(r'files? failed the MD5 check', output, re.I):
                raise GodotBackendError(f'gore reported checksum failures while extracting {pack}')
            if not destination.is_dir() or not any(path.is_file() for path in destination.rglob('*')):
                raise GodotBackendError(f'gore did not extract any files from {pack}')
            extracted.append((pack, destination))
        unique_versions = sorted(set(versions), key=lambda value: tuple(int(part) for part in re.findall(r'\d+', value)))
        if len(unique_versions) != 1:
            raise GodotBackendError(f'Godot packs report different engine versions: {unique_versions}')
        return extracted, unique_versions[0]

    @staticmethod
    def _merge_packs(extracted, merged, base_hint=None):
        bases = []
        for pack, directory in extracted:
            if any((directory / name).is_file() for name in PROJECT_CONFIG_NAMES):
                bases.append((pack, directory))
        if base_hint:
            hinted = [item for item in bases if item[0] == base_hint]
            if len(hinted) != 1:
                raise GodotBackendError(f'Base Godot pack hint has no unique project config: {base_hint}')
            base_pack, base_directory = hinted[0]
        elif len(bases) == 1:
            base_pack, base_directory = bases[0]
        elif len(bases) == 1:
            base_pack, base_directory = bases[0]
        elif bases:
            names = ', '.join(str(item[0]) for item in bases)
            raise GodotBackendError(f'Cannot choose the base Godot pack unambiguously: {names}')
        else:
            raise GodotBackendError('No extracted Godot pack contains project.binary/project.godot/engine.cfg')
        merged.mkdir(parents=True)
        for pack, directory in extracted:
            if directory == base_directory:
                continue
            _copy_tree(directory, merged)
        _copy_tree(base_directory, merged)
        return base_pack

    def _recover_project(self, merged, project, gdre, workdir):
        env = os.environ.copy()
        isolated_home = workdir / 'home'
        isolated_home.mkdir(parents=True)
        env.update({
            'HOME': str(isolated_home),
            'XDG_DATA_HOME': str(isolated_home / '.local/share'),
            'XDG_CONFIG_HOME': str(isolated_home / '.config'),
            'APPDATA': str(isolated_home / 'AppData/Roaming'),
            'LOCALAPPDATA': str(isolated_home / 'AppData/Local'),
        })
        command = [
            gdre,
            '--headless',
            f'--recover={merged}',
            f'--output={project}',
            '--setting=write_json_report=true',
            '--setting=download_plugins=false',
            '--setting=Exporter/GDExtension/make_editor_copy=false',
            '--setting=Recovery/clear_output_dir_except_git_before_full_recovery=false',
        ]
        self._command('gdre-recover', command, env=env, capture=True)
        report_path = project / 'gdre_export.json'
        if not report_path.is_file():
            raise GodotBackendError('GDRE did not produce gdre_export.json')
        try:
            report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            raise GodotBackendError(f'Cannot parse GDRE recovery report: {error}') from error
        return report, report_path

    def _write_editor_setup(self, project, tools):
        script = Path(project) / 'pc2android_editor_setup.gd'
        values = {
            'export/android/android_sdk_path': str(tools['sdk']),
            'export/android/java_sdk_path': str(tools['java_home']),
            'export/android/debug_keystore': str(tools['keystore']),
            'export/android/debug_keystore_user': DEBUG_KEYSTORE_ALIAS,
            'export/android/debug_keystore_pass': DEBUG_KEYSTORE_PASSWORD,
        }
        lines = ['extends SceneTree', '', 'func _init():']
        for key, value in values.items():
            lines.append(f'\tEditorSettings.set_setting({json.dumps(key)}, {json.dumps(str(value))})')
        lines += ['\tquit()', '']
        script.write_text('\n'.join(lines), encoding='utf-8')
        return script

    @staticmethod
    def _write_license_assets(project, version):
        project = Path(project)
        source = Path(__file__).resolve().parents[1] / 'templates/godot/licenses'
        destination = project / 'licenses'
        destination.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob('*.txt')):
            shutil.copy2(path, destination / path.name)
        notice = '\n'.join([
            'game-pc2and Godot backend',
            f'Godot Engine: {version} (MIT)',
            f'gore: {GORE_VERSION} (MIT)',
            f'GDRE Tools: {GDRE_VERSION} (MIT)',
            'Sources:',
            'https://github.com/godotengine/godot',
            'https://github.com/figurophobia/gore',
            'https://github.com/GDRETools/gdsdecomp',
            'Recovered game resources remain subject to the game owner\'s authorization.',
            '',
        ])
        (destination / 'GODOT-SOURCE.txt').write_text(notice, encoding='utf-8')
        return destination

    def _export_unsigned_apk(self, args, project, workdir, tools, version):
        project = Path(project)
        templates = Path(tools['templates'])
        build = workdir / 'build'
        build.mkdir(parents=True)
        unsigned = build / 'godot-unsigned.apk'
        template_path = Path(__file__).resolve().parents[1] / 'templates/godot/export_presets.cfg'
        preset = render_export_preset(
            template_path,
            export_path=unsigned,
            application_id=args.application_id,
            app_name=args.name,
            release_template=templates / 'android_release.apk',
            debug_template=templates / 'android_debug.apk',
            icon_resource=_project_icon(project),
        )
        (project / 'export_presets.cfg').write_text(preset, encoding='utf-8')
        setup_script = self._write_editor_setup(project, tools)
        env = os.environ.copy()
        env.update({
            'JAVA_HOME': str(tools['java_home']),
            'ANDROID_HOME': str(tools['sdk']),
            'ANDROID_SDK_ROOT': str(tools['sdk']),
            'GODOT_SILENCE_ROOT_WARNING': '1',
        })
        try:
            self._command(
                'godot-editor-settings',
                [tools['editor'], '--headless', '--editor', '--path', project, '--script',
                 'res://pc2android_editor_setup.gd'],
                env=env,
                capture=True,
            )
        finally:
            setup_script.unlink(missing_ok=True)
        self._command(
            'godot-import',
            [tools['editor'], '--headless', '--path', project, '--import'],
            env=env,
            capture=True,
        )
        self._command(
            'godot-export-release',
            [tools['editor'], '--headless', '--path', project, '--export-release', 'Android', unsigned],
            env=env,
            capture=True,
        )
        if not unsigned.is_file() or unsigned.stat().st_size == 0:
            raise GodotBackendError('Godot export reported success but did not produce an APK')
        return unsigned

    @staticmethod
    def _verify_apk_contents(apk):
        try:
            with zipfile.ZipFile(apk) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise GodotBackendError(f'APK CRC verification failed: {bad}')
                names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile) as error:
            raise GodotBackendError(f'Invalid APK: {error}') from error
        for library in ('libgodot_android.so', 'libc++_shared.so'):
            required = f'lib/{ANDROID_ABI}/{library}'
            if required not in names:
                raise GodotBackendError(f'Godot APK is missing {required}')
        if not any(name.startswith('assets/.godot/exported/') for name in names):
            raise GodotBackendError('Godot APK is missing exported project resources')
        for notice in ('GODOT-SOURCE.txt', 'GORE-MIT.txt', 'GDRE-MIT.txt', 'GODOT-MIT.txt', 'GODOT-COPYRIGHT.txt'):
            required = f'assets/licenses/{notice}'
            if required not in names:
                raise GodotBackendError(f'Godot APK is missing {required}')
        assets = [name for name in names if name.startswith('assets/') and not name.startswith('assets/dexopt/')]
        if not assets:
            raise GodotBackendError('Godot APK is missing bundled assets')
        return len(assets)

    def _copy_recovery_artifacts(self, project, report_path, artifacts):
        artifacts.mkdir(parents=True, exist_ok=True)
        destination = artifacts / 'gdre_export.json'
        shutil.copy2(report_path, destination)
        log = Path(project) / 'gdre_export.log'
        if log.is_file():
            shutil.copy2(log, artifacts / 'gdre_export.log')
        return destination

    def run(self, args):
        source_info = detect_godot_source(args.source)
        if source_info is None:
            raise GodotBackendError(
                f'Unrecognized Godot source: {args.source}. Expected a .pck, an executable with an embedded Godot pack, or a directory containing them.'
            )
        if not args.name or Path(args.name).name != args.name or args.name in ('.', '..'):
            raise GodotBackendError('Godot app name must be a safe filename component')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+', args.application_id):
            raise GodotBackendError('Godot application-id must be a valid Java package name')
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f'.{args.name}-Godot-work-', dir=out))
        self.log_dir = workdir / 'logs'
        self.log_dir.mkdir()
        final = out / f'{args.name}-Godot.apk'
        artifacts = out / f'{args.name}-Godot-recovery'
        try:
            print(json.dumps({
                'source': str(args.source),
                'engine': 'godot',
                'name': args.name,
                'application_id': args.application_id,
                'inputs': [str(path) for path in source_info.inputs],
            }, ensure_ascii=False, indent=2), flush=True)
            gore = self._install_pinned_tool('gore')
            gdre = self._install_pinned_tool('gdre')
            extracted, pack_version = self._extract_packs(source_info, gore, workdir)
            merged = workdir / 'merged'
            base_pack = self._merge_packs(extracted, merged, source_info.base_hint)
            project = workdir / 'project'
            report, report_path = self._recover_project(merged, project, gdre, workdir)
            summary = validate_recovery_report(report)
            _validate_project_support(project, report)
            export_version, version_notes = reconcile_engine_version(
                pack_version, summary['engine_version'], project
            )
            summary['gdre_detected_version'] = summary['engine_version']
            summary['pack_engine_version'] = pack_version
            summary['engine_version'] = export_version
            patches = apply_android_compatibility(project, export_version)
            self._write_license_assets(project, export_version)
            saved_report = self._copy_recovery_artifacts(project, report_path, artifacts)
            report_path.unlink(missing_ok=True)
            (project / 'gdre_export.log').unlink(missing_ok=True)

            android = self.ensure_android_toolchain(args)
            editor = self.ensure_godot_editor(summary['engine_version'])
            templates = self.ensure_godot_templates(summary['engine_version'])
            tools = {
                **android,
                'editor': editor,
                'templates': templates,
                'keystore': self.ensure_debug_keystore(android['java_home']),
            }
            unsigned = self._export_unsigned_apk(args, project, workdir, tools, summary['engine_version'])
            aligned = workdir / 'build/godot-aligned.apk'
            signed = workdir / 'build/godot-signed.apk'
            build_tools = android['sdk'] / f'build-tools/{BUILD_TOOLS}'
            self._command('zipalign', [build_tools / 'zipalign', '-f', '4', unsigned, aligned])
            self._command('apksigner-sign', [
                build_tools / 'apksigner', 'sign',
                '--ks', tools['keystore'],
                '--ks-key-alias', DEBUG_KEYSTORE_ALIAS,
                '--ks-pass', f'pass:{DEBUG_KEYSTORE_PASSWORD}',
                '--key-pass', f'pass:{DEBUG_KEYSTORE_PASSWORD}',
                '--out', signed, aligned,
            ])
            self.dependencies.verify_apk(args, signed)
            asset_count = self._verify_apk_contents(signed)
            temporary = final.with_name(final.name + '.tmp')
            temporary.unlink(missing_ok=True)
            shutil.move(str(signed), temporary)
            os.replace(temporary, final)
            digest = self.dependencies.sha256(final)
            pipeline_report = {
                'source': str(args.source),
                'inputs': [str(path) for path in source_info.inputs],
                'base_pack': str(base_pack),
                'engine_version': summary['engine_version'],
                'engine_version_notes': version_notes,
                'gdre_version': GDRE_VERSION,
                'gore_version': GORE_VERSION,
                'recovery': summary,
                'lossless_recovery': summary['lossy_imports'] == 0,
                'android_patches': patches,
                'apk': str(final),
                'sha256': digest,
                'assets': asset_count,
                'workdir': str(workdir) if getattr(args, 'keep_work', False) else None,
                'verified': ['apksigner', 'zipalign', 'crc', 'native_libs', 'assets'],
                'scope': 'Godot 4 GDScript, GL Compatibility, arm64-v8a; device preflight not performed',
            }
            (artifacts / 'pipeline-report.json').write_text(
                json.dumps(pipeline_report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
            )
            print(f'APK={final}')
            print(f'SHA256={digest}')
            print(f'Godot={summary["engine_version"]}')
            print(f'Recovery report: {saved_report}')
            print(f'RecoveryLossy={summary["lossy_imports"]}')
            for note in version_notes:
                print(f'WARNING: {note}')
            if summary['lossy_imports']:
                print('WARNING: GDRE reported lossy imports; this recovery is not lossless.')
            print('Scope=Godot 4 GDScript GL Compatibility; build/signature/alignment/content verified; device preflight not performed')
            if not getattr(args, 'keep_work', False):
                shutil.rmtree(workdir)
                self.log_dir = None
            else:
                print(f'Workdir={workdir}')
            return pipeline_report
        except Exception:
            print(f'Godot workdir retained for diagnostics: {workdir}', file=sys.stderr)
            raise


def run_godot_pipeline(args, *, backend=None):
    """Run the one-command Godot backend using an args-style namespace."""
    return (backend or GodotBackend()).run(args)
