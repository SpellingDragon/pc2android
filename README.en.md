# pc2android

Package PC HTML5 / RPG Maker MV/MZ (NW.js) / Electron games into a **single Android APK**.

One command, one argument. Missing JDK, Android SDK components, Gradle, Chrome for Testing and Python dependencies are downloaded, installed and verified automatically. Build, signature/alignment/CRC verification, local preflight and missing-asset repair run as one pipeline.

[中文](README.md)

## One command

```bash
python3 pc2android.py /path/to/game
```

It automatically:

- detects RPG Maker MZ/MV (NW.js) and Electron (asar) projects
- derives the app name and package id from `index.html` / `package.json`
- installs a missing toolchain into `~/.pc2android/` (`PC2ANDROID_HOME` overrides it) and verifies every component
- generates mipmaps, an Android 8+ adaptive icon and a round icon
- builds the APK, then verifies it with `apksigner`, `zipalign` and a full ZIP CRC test
- serves assets directly from the built APK and drives Chromium through CDP for the local preflight
- repairs missing assets that exist elsewhere in the source tree, rebuilds and re-runs the preflight
- **emits the APK only when everything passes**; any failure exits non-zero and never replaces an older APK

Output is written to the current directory:

```
<app-name>-WebView.apk
<app-name>-preflight/report.json
<app-name>-preflight/title.png
<app-name>-preflight/map.png
```

## Use as a Qoder Skill

The repository ships `skills/pc2android/` as a ready-to-mount Qoder skill:

```bash
git clone https://github.com/SpellingDragon/pc2android.git
cd pc2android
mkdir -p ~/.qoder/skills
cp -R skills/pc2android ~/.qoder/skills/
```

Run `/skills reload` in Qoder, then:

```text
/pc2android /path/to/game
```

For a project-level mount, copy `skills/pc2android` into `<project>/.qoder/skills/`.

## Success contract

Exit code 0 means all of the following hold. There is no partial or conditional success:

1. Build, signing, alignment and ZIP CRC verification pass.
2. The preflight reaches the title/map; all images are ready; there are no image, WebGL or JavaScript errors; fonts are ready.
3. Every request for a packaged asset is served; a packaged asset that returns 404 is a failure.
4. Save → load → remove round-trip succeeds.
5. Electron mode finishes startup, has the correct stage scale and passes a localStorage round-trip.

The only tolerated case: a requested file does not exist anywhere in the source game (the game's own optional probe, such as material maps or language files). Such 404s are recorded in the report but do not block success. If the file exists elsewhere in the source tree, the tool copies it into the package and rebuilds.

## Automatic installation

Missing components are installed into `~/.pc2android/` and verified:

| Component | Version | Source / check |
|---|---|---|
| JDK | Temurin 17 | Adoptium API, `java -version` ≥ 17 |
| Android platform | 36 | Google repository XML, SHA-1 verified |
| build-tools | 36.0.0 | Same, `aapt2` executable |
| Gradle | 8.14.5 | services.gradle.org, `gradle --version` |
| Chrome for Testing | 133.0.6943.141 | Chrome for Testing, executable |
| Python deps | Pillow, websocket-client | `~/.pc2android/venv` |

Downloads use the system `curl` and honor `HTTPS_PROXY` / `HTTP_PROXY`, including authenticated proxies. Installation is idempotent.

## Supported engines

| Engine | Status |
|---|---|
| RPG Maker MZ | verified |
| RPG Maker MV | verified |
| NW.js | verified |
| Electron (base.asar + app.asar) | verified (one player adapter) |
| Windows-native engines (QLIE, KiriKiri, ...) | not supported; use Winlator |

## How it works

1. **Engine detection and safe staging** — copies only the web runtime; large media is hard-linked, the `js/` tree is copied so plugin rewrites never touch the source game.
2. **Node shim and `asset-index.json`** — provides `require('fs')` for NW.js-oriented plugins; `fs.accessSync` answers from a generated asset index instead of spawning thousands of probes.
3. **Encrypted long-image splitting** — encrypted PNGs referenced by `plugins.js` taller than 2048 px are split, re-encrypted and the plugin parameters rewritten.
4. **Adaptive icons** — picks the largest source icon, generates mipmaps, `anydpi-v26` adaptive icons and round icons with a dominant-color background.
5. **APK-served preflight** — serves assets straight from the APK zip entries (Range/HEAD, keep-alive, encrypted-suffix MIME) and drives Chromium via CDP.
6. **Repair loop** — a 404 triggers a source-tree search (same path, encrypted variant, same filename), copies the file into the staged assets, rebuilds and re-runs the preflight, up to two passes.
7. **Determinism and atomic artifacts** — identical input produces an identical APK hash; the final APK is replaced only after verification and preflight pass.

## Advanced usage

The one-command mode is the default. For full control:

```bash
python3 pc2android.py \
  --source /path/to/game \
  --name "Game Name" \
  --application-id com.example.game \
  --out /path/to/output \
  --preflight
```

- `--engine auto|rmmz|rmmv|nwjs|electron`
- `--dry-run` — detect and stage only
- omit `--preflight` — build and verify without the browser run
- `--browser` — explicit Chromium/Chrome path
- `--keep-work` — keep the generated Android project

## Known limitations

- The preflight runs desktop Chromium, not Android WebView or a device GPU; real-device installation is the final acceptance.
- Electron support is one player adapter (`GameMain.html` + `base.asar`); other Electron shells may need adjustment.
- On Windows, use WSL (downloads and paths are POSIX-oriented).
- Icon sharpness is limited by the source icon (for example a 128 px icon).
- APKs close to 3 GB depend on device storage and installer speed.
- No game content is included and no DRM is bypassed; only package games you own or are authorized to distribute.

## License

MIT, see [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

RPG Maker is a trademark of Gotcha Gotcha Games Corporation. Android is a trademark of Google LLC. This project is not affiliated with or endorsed by them.
