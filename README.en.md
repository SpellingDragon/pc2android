# game-pc2and

**Turn PC games into one Android APK.**

RPG Maker MV/MZ · NW.js · Electron · KiriKiri · Siglus · RealLive · QLIE

[![CI](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml/badge.svg)](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SpellingDragon/game-pc2and?label=release)](https://github.com/SpellingDragon/game-pc2and/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) · [Latest release](https://github.com/SpellingDragon/game-pc2and/releases/latest) · [Demo](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4) · [CrySirror project](https://github.com/xmoezzz/CrySirror)

![game-pc2and: turn RPG Maker, Electron, KiriKiri, Siglus, RealLive and QLIE games into one Android APK](docs/images/hero.png)

## Quick start

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

The engine is detected automatically and one APK is produced. The first run installs missing JDK, Android SDK and Gradle components; the CrySirror path also prepares Rust 1.95, the NDK and cargo-ndk. No Android Studio, no root.

## What it supports

| Backend | For | Status |
|---|---|---|
| WebView | RPG Maker MZ/MV, NW.js, Electron | verified |
| CrySirror | KiriKiri, Siglus, RealLive, QLIE and other VN engines | per engine |
| — | other Win32-native engines | use Winlator |

<details>
<summary>Full CrySirror engine list</summary>

KiriKiri, Siglus, RealLive, AVG32, BGI, SoftPAL, RFVP, NScripter, Circus, CatSystem2, QLIE, Majiro, AliceSoft System 4, System-NNN, SugarPot, codeX RScript, N-System, N2System, Malie, WillPlus/AdvHD, CMVS/CVNS, ExHibit, minori, CM3D2/COM3D2, AmanatsuLocation.

</details>

If detection misses a layout, pass `--engine crysirror`.

## Features

- **One APK**: game, runtime, virtual keyboard/host and license notices.
- **Automatic setup**: JDK, Android SDK, Gradle, Chrome and Python dependencies.
- **Two backends**: WebView (RPG Maker/NW.js/Electron) and CrySirror (VN).
- **Virtual keyboard**: MZ3D and other continuous-input games; hide, opacity, multi-touch.
- **Optional cheats**: RPG Maker item prices and enemy exp; Electron `__GAME_DATA__`.
- **Verification**: signing, alignment, CRC, resource integrity; WebView preflight.

## Demo

![RPG Maker/Electron game packaged as Android APK](docs/images/demo-preview.gif)

[Full demo video (build, virtual keyboard, gameplay, verification)](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4)

<details>
<summary>Device recording</summary>

```bash
bash docs/device-demo.sh /path/to/Game-WebView.apk
```

</details>

## Usage

Run directly:

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

As an Agent Skill: copy `skills/pc2android` into your agent skills directory, then:

```text
/pc2android /path/to/game
```

<details>
<summary>Advanced options</summary>

```bash
python3 skills/pc2android/scripts/pc2android.py \
  --source /path/to/game \
  --name "Game Name" \
  --application-id com.example.game \
  --out /path/to/output \
  --preflight
```

`--engine auto|rmmz|rmmv|nwjs|electron|crysirror`, `--dry-run`, `--keep-work`, `--browser`.

</details>

## CrySirror backend

- Auto-detects CrySirror-supported VN engines; maintained as a peer backend.
- Consumes only `libcrysirror_mobile.so`; the Android host is owned by game-pc2and, with upstream `platform/android` untouched.
- The APK includes MPL-2.0, source commit/modification notice, dependency license inventory and available license texts.
- No Chromium preflight; verify on a device.

## Touch and cheats

- Menu/ADV/turn-based games keep touch input; MZ3D gets a virtual keyboard.
- `--cheat`: negate positive RPG Maker item prices, turn zero into `-0`, prepend `1` to nonzero enemy exp; recursively patch Electron `window.__GAME_DATA__`.

## FAQ

**Can RPG Maker MV/MZ run on Android?** Yes; package `www/` or `data_Win/`.

**Can Electron/NW.js become an APK?** Yes; `app.asar`/`base.asar` and `package.json` + `index.html` are supported.

**Can KiriKiri/Siglus/RealLive/QLIE run?** Through CrySirror, verified per engine/game.

**Do I need Android Studio/root?** No.

**Does it support every PC game?** No; unlisted Win32 engines should use Winlator.

**Is the APK release-signed?** Currently debug-signed; configure a release signature for distribution.

**Are game assets included?** No; package only games you own or are authorized to distribute.

## License

MIT. CrySirror is MPL-2.0; the source commit, modification notice, dependency license inventory and available license texts are included in the APK under `assets/licenses/`.
