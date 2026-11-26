---
name: pc2android
description: 将 PC 上的 HTML5 / RPG Maker MV/MZ（NW.js）/ Electron 游戏一键打包为单个 Android APK。Use when the user wants to convert a PC game to Android, build an APK, port RPG Maker MV/MZ, NW.js or Electron projects, run pc2android, or needs automatic JDK/Android SDK/Gradle/Chrome installation with strict build verification and local preflight. 触发词：安卓打包、APK、单包安装、RPG Maker、NW.js、Electron、一键移植、本地预检。
argument-hint: <project-path>
---

# pc2android

## Overview

一条命令把项目打包成单个 Android APK。脚本自动安装并校验缺失的 JDK 17、Android SDK 组件、Gradle 8.14.5、Chrome for Testing 与 Python 依赖；自动生成图标；构建后执行签名/对齐/CRC 校验与本地预检；预检发现可补齐的缺失资源会自动找回并重建。**只有退出码 0 才算成功。**

## 使用

1. 确认项目路径存在（游戏根目录、`data_Win` 或 `www` 均可；Electron 为含 `resources/*.asar` 的目录）。
2. 在希望输出 APK 的目录运行（默认输出到当前工作目录）：

```bash
python3 <skill-dir>/scripts/pc2android.py <project-path>
```

`<skill-dir>` 是本 skill 的安装目录：

- 用户级挂载：`~/.qoder/skills/pc2android`
- 项目级挂载：`.qoder/skills/pc2android`

例如用户级：

```bash
python3 ~/.qoder/skills/pc2android/scripts/pc2android.py /path/to/game
```

3. 检查退出码与输出：
   - 成功：`APK=...`、`SHA256=...`、`Preflight report: ...`
   - 失败：非零退出，读取 `<name>-preflight/report.json` 的 `failures` 与 `httpErrors`
4. 失败时按“故障处理”修复后重跑；不要跳过校验、不要手工改写报告、不要用旧包冒充成功。

## 成功契约

退出码 0 表示以下全部成立，不存在“带条件的成功”：

- APK 构建、`apksigner`、`zipalign`、全包 CRC 通过
- 预检到达标题/地图；图片就绪；无图片错误、WebGL 错误、JS 异常；字体就绪
- 所有已打包资源请求都被服务；已打包却 404 即失败
- 存档写入 → 读回 → 删除往返成功
- Electron 模式启动完成、舞台缩放正确、localStorage 往返成功

唯一容忍：请求的文件在源游戏目录中本来就不存在（游戏自身的可选探测）。这类 404 记录在报告中，不阻止成功；若文件其实在源目录其他位置，脚本会自动补进包并重建。

## 自动安装

缺失项安装到 `~/.pc2android/`（`PC2ANDROID_HOME` 可覆盖），并逐项校验：

- JDK 17（Temurin）
- Android platform 36、build-tools 36.0.0（官方 SHA-1 校验）
- Gradle 8.14.5
- Chrome for Testing 133.0.6943.141
- Python 依赖（Pillow、websocket-client，`~/.pc2android/venv`）

下载走系统 `curl`，自动使用 `HTTPS_PROXY`/`HTTP_PROXY`（含带凭据代理）。安装幂等，可重复运行。

## 故障处理

- **下载失败**：检查 `HTTPS_PROXY`/`HTTP_PROXY` 与 curl；重跑即可，已下载组件会复用。
- **SDK/Gradle 安装失败**：删除 `~/.pc2android/` 对应子目录后重跑；脚本会重新下载并校验。
- **预检失败**：
  - `failures` 含 `Missing asset available in source`：脚本应已自动修复；若仍失败，检查源目录是否有同名文件/权限。
  - `Packaged asset not served`：打包或服务缺陷，保留 `report.json` 与 APK，按 bug 处理。
  - 图片/WebGL/字体错误：不要绕过；检查对应资源与插件兼容性。
- **不支持的 Windows 原生引擎（QLIE/KiriKiri 等）**：改用 Winlator，不要强行打包。
- **Electron 壳不同**：当前适配 `GameMain.html` + `base.asar`；其他壳可能需要调整脚本。

## 高级用法

需要控制应用名、包名、输出目录或跳过预检时：

```bash
python3 <skill-dir>/scripts/pc2android.py \
  --source <project-path> \
  --name "<app name>" \
  --application-id com.example.game \
  --out <output-dir> \
  --preflight
```

- `--engine auto|rmmz|rmmv|nwjs|electron`
- `--dry-run`：只检测与暂存
- 省略 `--preflight`：只构建并校验
- `--browser <path>`：指定 Chromium
- `--keep-work`：保留生成的 Android 工程

## 合规

只打包你拥有或已获授权的游戏；不要把游戏资源、APK、存档提交到开源仓库。RPG Maker 是 Gotcha Gotcha Games 的商标，Android 是 Google LLC 的商标。
