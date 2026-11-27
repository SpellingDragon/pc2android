# game-pc2and

**Turn PC games into one Android APK.**

RPG Maker MV/MZ · NW.js · Electron · visual novels (KiriKiri/Siglus/RealLive/QLIE) · automatic setup · virtual keyboard · verified builds

[![CI](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml/badge.svg)](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SpellingDragon/game-pc2and?label=release)](https://github.com/SpellingDragon/game-pc2and/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) · [Latest release](https://github.com/SpellingDragon/game-pc2and/releases/latest) · [Watch the 21-second demo](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4) · [CrySirror QLIE patch PR](https://github.com/xmoezzz/CrySirror/pull/21)

![game-pc2and: turn RPG Maker, Electron, KiriKiri, Siglus, RealLive and QLIE games into one Android APK](docs/images/hero.png)

## Start in 30 seconds

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

The engine is detected automatically and one APK is produced:

- RPG Maker MV/MZ, NW.js and Electron → `<game-name>-WebView.apk`
- KiriKiri, Siglus, RealLive, QLIE and other VN engines → `<game-name>-CrySirror.apk`

The first run installs missing JDK, Android SDK and Gradle components automatically. The CrySirror path also prepares Rust 1.95, the Android NDK and cargo-ndk. No Android Studio and no root required.

## What it packages

| Game | Engine/layout | Backend | Status |
|---|---|---|---|
| RPG Maker MZ | `js/rmmz_core.js`, `www/` | WebView | verified |
| RPG Maker MV | `js/rpg_core.js`, `www/` | WebView | verified |
| NW.js web game | `package.json` + `index.html` | WebView | verified |
| Electron (asar) | `app.asar` / `base.asar` | WebView | verified |
| Visual novel / ADV / galgame | KiriKiri, Siglus, RealLive, AVG32, BGI, SoftPAL, RFVP, NScripter, Circus, CatSystem2, QLIE, Majiro, AliceSoft System 4, System-NNN, SugarPot, codeX RScript, N-System, N2System, Malie, WillPlus/AdvHD, CMVS/CVNS, ExHibit, minori, CM3D2/COM3D2, AmanatsuLocation | CrySirror native backend | verified per engine |
| Other Windows-native engines | unlisted Win32 engines | not applicable | use Winlator |

CrySirror engines are detected from file signatures. If detection misses a layout, pass `--engine crysirror` explicitly.

## Features

- **One APK**: game, runtime, virtual keyboard/host and license notices in one installable package.
- **Automatic toolchain**: JDK 17, Android platform/build-tools, Gradle, Chrome for Testing and Python dependencies are installed and verified.
- **CrySirror native backend**: VN engines run on a Rust native runtime instead of WebView; maintained as a peer backend.
- **Virtual keyboard**: input-rich games such as MZ3D titles get a translucent keyboard with hide, opacity and multi-touch support.
- **Optional cheats**: negate RPG Maker item prices and prepend 1 to nonzero enemy exp; recursively patch `price` in custom Electron `window.__GAME_DATA__`.
- **Build verification**: signing, alignment, CRC and packaged-resource checks; the WebView path adds a Chromium preflight.
- **Agent Skill**: copy `skills/pc2android` and mount it as an Agent Skill.

## Demo

![RPG Maker/Electron game packaged as Android APK](docs/images/demo-preview.gif)

[Full demo video (build logs, virtual keyboard, real gameplay, verification report)](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4)

With a USB-debug-enabled phone connected, record a device clip:

```bash
bash docs/device-demo.sh /path/to/Game-WebView.apk
```

The script installs and launches the APK, records the screen and pulls back `device-demo.mp4`.

## As an Agent Skill

```bash
git clone https://github.com/SpellingDragon/game-pc2and.git
cd game-pc2and
```

Copy `skills/pc2android` into your agent's user-level or project-level skills directory, reload skills, then:

```text
/pc2android /path/to/game
```

Skill directory locations vary by agent; mount the same directory according to your agent's documentation.

## CrySirror native backend

The command stays the same:

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

It auto-detects the CrySirror-supported engines: KiriKiri, Siglus, RealLive, AVG32, BGI, SoftPAL, RFVP, NScripter, Circus, CatSystem2, QLIE, Majiro, AliceSoft System 4, System-NNN, SugarPot, codeX RScript, N-System, N2System, Malie, WillPlus/AdvHD, CMVS/CVNS, ExHibit, minori, CM3D2/COM3D2 and AmanatsuLocation.

- Engine/host separation: the app consumes only `libcrysirror_mobile.so`; the Android host is owned by game-pc2and (landscape fullscreen, Surface, input, lifecycle) and upstream `platform/android` is not modified.
- The first run prepares the CrySirror source, Rust 1.95, Android NDK and Gradle 9.1.
- The APK includes the MPL-2.0 license, source commit/modification notice, dependency license inventory and available license texts.
- The native runtime does not use the Chromium preflight; compatibility is verified per engine/game on a device.

## Touch controls and cheats

- Menu/ADV/turn-based games keep touch input; input-rich games such as MZ3D titles get a translucent virtual keyboard with hide and opacity controls.
- Hold = keydown, release = keyup, multi-touch supported.

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game --cheat
```

- RPG Maker: positive `price` becomes negative, `price == 0` becomes `-0`; nonzero enemy `exp` gets a leading `1`.
- Custom Electron engines exposing `window.__GAME_DATA__`: recursively patch `price` values.
- Only staged data is changed; source game files are untouched. Counts are written to `report.json` under `cheat`.

## Workflow and verification

```text
Detect engine and entry
  → WebView: HTML5 / RPG Maker / NW.js / Electron
  → CrySirror: native VN engine runtime
  → single APK
  → signing / alignment / CRC / resource integrity
  → WebView preflight / input profile
```

The APK is emitted only on exit code 0. On failure, inspect `failures`, `suggestions`, `cheat.candidates` and `remediation` in `report.json`.

<details>
<summary>Advanced options and troubleshooting</summary>

```bash
python3 skills/pc2android/scripts/pc2android.py \
  --source /path/to/game \
  --name "Game Name" \
  --application-id com.example.game \
  --out /path/to/output \
  --preflight
```

- `--engine auto|rmmz|rmmv|nwjs|electron|crysirror`
- `--dry-run` — detect and stage only
- omit `--preflight` — build and verify without the browser run
- `--browser <path>` — explicit Chromium path
- `--keep-work` — keep the generated Android project

For download errors, check `HTTPS_PROXY` and curl, then rerun.

</details>

## FAQ

**Can RPG Maker MV/MZ games run on Android?** Yes. Package `www/` or `data_Win/` directly; MZ3D, MessageSkip and other input compatibility is handled automatically.
**Can Electron or NW.js games become an APK?** Yes. Both `app.asar` / `base.asar` and `package.json` + `index.html` layouts are supported.
**Can KiriKiri, Siglus, RealLive or QLIE games run on Android?** They use the CrySirror native backend, verified per engine/game; not every VN is guaranteed to be fully playable.
**Do I need Android Studio?** No, SDK components are installed automatically.
**Do I need root?** No.
**Does it support every PC game?** No. HTML5/RPG Maker/NW.js/Electron use WebView; supported VN engines use the CrySirror native backend; other Windows-native engines should use Winlator.
**Are game assets included?** No. Package only games you own or are authorized to distribute.
**Is the APK release-signed?** The current build is debug-signed; configure a release signature for public distribution.
**Does the virtual keyboard play for me?** No, it only translates touch into game key events.

## License

MIT, see [LICENSE](LICENSE). CrySirror components are MPL-2.0; the source commit, modification notice, dependency license inventory and available license texts are included in the APK under `assets/licenses/`. Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

RPG Maker is a trademark of Gotcha Gotcha Games Corporation. Android is a trademark of Google LLC. This project is not affiliated with or endorsed by them.
