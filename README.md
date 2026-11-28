# game-pc2and: PC Game to Android APK Converter

Build one installable Android APK from RPG Maker MV/MZ, NW.js, Electron, Godot, or CrySirror-supported visual novels. The CLI detects the engine, prepares backend-specific toolchains, adapts touch input, and verifies the package without Android Studio or root access.

[![CI](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml/badge.svg)](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SpellingDragon/game-pc2and?label=release)](https://github.com/SpellingDragon/game-pc2and/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[简体中文](README.zh-CN.md) · [Latest release](https://github.com/SpellingDragon/game-pc2and/releases/latest) · [Demo video](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4) · [Full CLI reference](skills/pc2android/SKILL.md)

![PC game to Android APK converter with automatic setup, touch controls, and build verification](docs/images/hero.png)

## Quick start

Run on macOS or Linux with Python 3, Git, curl, and network access for first-run downloads. CrySirror builds also require rustup and Rust 1.95+:

```bash
git clone https://github.com/SpellingDragon/game-pc2and.git
cd game-pc2and
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

Pass the game root, `www/`, `data_Win/`, a Godot executable or PCK, or a directory containing them. Downloaded tools are cached under `~/.pc2android/` (override with `PC2ANDROID_HOME`); a successful run prints `APK=` and `SHA256=`, and a failed gate returns a nonzero exit code instead of shipping an unchecked package.

## Supported engines and output

| PC game source | Backend | Output |
|---|---|---|
| RPG Maker MV/MZ, NW.js, Electron | WebView | `<name>-WebView.apk` |
| KiriKiri, Siglus, RealLive, QLIE, and other supported VN engines | CrySirror | `<name>-CrySirror.apk` |
| Godot 4 GDScript with GL Compatibility | Godot recovery | `<name>-Godot.apk` plus a recovery report |
| Unlisted Win32-native engines | None | Use a Windows compatibility layer such as Winlator; this CLI does not emulate arbitrary Windows binaries |

<details>
<summary>Supported CrySirror engines</summary>

KiriKiri, Siglus, RealLive, AVG32, BGI, SoftPAL, RFVP, NScripter, Circus, CatSystem2, QLIE, Majiro, AliceSoft System 4, System-NNN, SugarPot, codeX RScript, N-System, N2System, Malie, WillPlus/AdvHD, CMVS/CVNS, ExHibit, minori, CM3D2/COM3D2, and AmanatsuLocation. Use `--engine crysirror` only when automatic detection misses a supported layout.

</details>

## Demo

The recording covers APK construction, automatic key remapping, multi-touch gameplay, and the resulting preflight report.

[![Build and gameplay demonstration](docs/images/demo-preview.gif)](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4)

## Backend behavior and limits

### WebView: RPG Maker, NW.js, and Electron

The WebView route packages game assets and a local runtime, then launches the staged copy in Chromium. Missing packaged resources are located in the source tree and rebuilt into the APK when possible, keeping the original game directory unchanged.

### CrySirror: native visual-novel runtime

The Android host is maintained here and consumes `libcrysirror_mobile.so`; upstream `platform/android` is not copied or patched. The APK carries the source revision, local modification notice, dependency inventory, and available license texts.

### Godot: GDScript project recovery

gore locates the game packs, GDRE Tools recovers scripts and imported resources, and the matching Godot editor exports with Android templates. C#, GDExtension, Mono, custom builds, and double-precision builds are rejected; unsuccessful, unconverted, or unsupported recovery entries also fail the build. Lossy imports are reported separately, point-and-click interfaces use native touch, and action games need a virtual-control profile.

## Touch input and optional cheats

Input detection keeps direct touch for menu, ADV, and turn-based games; continuous-input games receive a translucent virtual keyboard with multi-touch, press/release key events, adjustable opacity, and a hide control. Add `--cheat` to negate positive RPG Maker item prices, preserve zero as `-0`, and prepend `1` to nonzero enemy experience; Electron games exposing `window.__GAME_DATA__` receive recursive price updates in the staged copy only.

## What a verified build means

Every backend requires APK construction, signing, zip alignment, CRC validation, and route-specific content checks. One-command WebView builds also run scene, asset, JavaScript, font, WebGL, and save preflight (advanced CLI: `--preflight`); CrySirror checks native libraries and assets but still needs device gameplay testing, while Godot validates recovery, export, native libraries, assets, and license files. Current APKs are debug-signed; configure release signing before public distribution.

![Verified Android build with input profile, automatic remediation, and zero JavaScript exceptions](docs/images/preflight.png)

## Advanced use

```bash
python3 skills/pc2android/scripts/pc2android.py \
  --source /path/to/game \
  --name "Game Name" \
  --application-id com.example.game \
  --out /path/to/output \
  --preflight
```

Use `--engine auto|rmmz|rmmv|nwjs|electron|crysirror|godot` to select a backend, `--dry-run` to inspect detection, `--keep-work` to retain the generated project, and `--browser` to choose Chromium. The [CLI reference](skills/pc2android/SKILL.md) documents dependency installation, remediation order, reports, cheating rules, and troubleshooting.

To expose the same workflow as an Agent Skill, copy `skills/pc2android` into a compatible agent's skills directory and invoke:

```text
/pc2android /path/to/game
```

For a device recording of an existing WebView APK:

```bash
bash docs/device-demo.sh /path/to/Game-WebView.apk
```

## License and distribution

The CLI is MIT-licensed; CrySirror is MPL-2.0, while gore, GDRE Tools, and Godot are MIT. Each APK includes notices under `assets/licenses/` for CrySirror and Godot, or `assets/THIRD_PARTY_NOTICES.txt` for WebView. Package only games you own or are authorized to distribute; game assets, APKs, and saves do not belong in this open-source repository.
