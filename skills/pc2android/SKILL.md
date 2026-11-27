---
name: pc2android
description: 将 PC 上的 HTML5 / RPG Maker MV/MZ（NW.js）/ Electron 游戏一键打包为单个 Android APK。Use when the user wants to convert a PC game to Android, build an APK, port RPG Maker MV/MZ, NW.js or Electron projects, run pc2android, or needs automatic JDK/Android SDK/Gradle/Chrome installation with strict build verification and local preflight. 触发词：安卓打包、APK、单包安装、RPG Maker、NW.js、Electron、一键移植、本地预检。可选 RPG Maker 作弊：物品价格取负、敌人经验最高位前加 1。
license: MIT
metadata:
  author: SpellingDragon
  version: "1.2.0"
---

# pc2android

## Overview

一条命令把项目打包成单个 Android APK。脚本自动安装并校验缺失的 JDK 17、Android SDK 组件、Gradle 8.14.5、Chrome for Testing 与 Python 依赖；自动生成图标；构建后执行签名/对齐/CRC 校验与本地预检；预检发现可补齐的缺失资源会自动找回并重建。**只有退出码 0 才算成功。**

本 skill 遵循 Agent Skills 的 `SKILL.md` 约定，可被任何兼容该约定的 Agent 挂载。

## 使用

1. 确认项目路径存在（游戏根目录、`data_Win` 或 `www` 均可；Electron 为含 `resources/*.asar` 的目录）。
2. 运行本 skill 目录下的脚本。本目录即本 `SKILL.md` 所在目录，脚本路径为 `scripts/pc2android.py`：

```bash
cd <skill-dir>
python3 scripts/pc2android.py <project-path>
```

也可以直接用绝对路径：

```bash
python3 <skill-dir>/scripts/pc2android.py <project-path>
```

可选作弊（仅 RPG Maker 数据）：

```bash
python3 <skill-dir>/scripts/pc2android.py <project-path> --cheat
```

3. 检查退出码与输出：
   - 成功：`APK=...`、`SHA256=...`、`Preflight report: ...`
   - 失败：非零退出，读取 `<name>-preflight/report.json` 的 `failures` 与 `httpErrors`
4. 失败时按“故障处理”修复后重跑；不要跳过校验、不要手工改写报告、不要用旧包冒充成功。

## 作弊选项（可选）

RPG Maker MV/MZ 走通用标准数据文件；自定义 Electron 引擎（暴露 `window.__GAME_DATA__`）走定制适配。

- `data/Items.json`：`price > 0` 取负；`price == 0` 改为 `-0`；已是负数不变。
- `data/Enemies.json`：`exp != 0` 时在最高位前加 `1`（50→150，5→15，100→1100）；`exp == 0` 不变。
- 只改打包暂存副本，源游戏文件不动；通过临时文件 + 原子替换断开硬链接。数量写入 `report.json` 的 `cheat` 字段。
- 自定义 Electron 引擎：递归修改 `window.__GAME_DATA__` 内所有 `price`（规则同上）；该类引擎通常没有敌人 exp 字段。
- 找不到可改数据时，脚本按 `price / exp / gold / item / shop / enemy / 所持金 / 価格 / 経験値` 等关键字扫描资产，在错误中列出候选文件，并写入报告 `cheat.candidates`。
- 非 RPG Maker 且无 `__GAME_DATA__` 的项目会提示跳过，不影响构建。

## 输入画像与虚拟键盘

打包时生成 `input-profile.json`：

- `touch`：菜单/ADV/回合制或已有触屏按钮覆盖的游戏，不叠加控件。
- `rich`：存在 MZ3D/指针锁定等连续输入，或自定义按键较多且无触屏覆盖的游戏，APK 注入半透明虚拟键盘。

若 MZ3D 与 MessageSkip 同时存在，打包会把 MessageSkip 自动键从 A 改为 M，避免 A 被 MZ3D 的移动映射占用（写入 `remediation`）。

虚拟键盘按住=keydown、松开=keyup，支持多指；事件同时带 `key`/`code`/`keyCode`/`which`，兼容 MZ Input 与 BackLog 的 `code` 监听；顶部可隐藏、可调透明度，切后台自动释放按键。预检报告含 `inputProfile`（class/evidence/entropy/recommended_keys）。

## 成功契约：先穷尽自动修复，再判失败

退出码 0 只代表“全部通过”，但工具不会把能自动解决的问题丢给用户。判失败之前，它会按下面的顺序把能补的条件全部补齐：

1. **缺工具链**：自动下载安装 JDK 17、Android platform 36、build-tools 36.0.0、Gradle 8.14.5、Chrome for Testing；下载失败重试 3 次，支持带凭据代理；安装后逐项校验版本。
2. **缺 Python 依赖**：自动创建 `~/.pc2android/venv` 并重入执行。
3. **预检 404**：在源目录按同路径、加密变体、同名文件、备用根目录（含 Electron asar 解包树）搜索；找到就补进包。
4. **修复后**：自动重建 APK、重新签名/对齐/CRC、重新预检，最多两轮。
5. **网络/服务瞬断**：重试；已安装组件复用，不重复下载。
6. **仍失败**：非零退出，保留 `report.json`、日志与旧 APK；不输出半成品，不把“带条件的成功”当成功。

工具会把上述动作写入 `report.json` 的 `remediation` 字段，并在终端打印 `Remediation (N)` 清单，让“已经尽力”可审计。

最终门槛（全部满足才 exit 0）：

- APK 构建、`apksigner`、`zipalign`、全包 CRC 通过
- 预检到达标题/地图；图片就绪；无图片错误、WebGL 错误、JS 异常；字体就绪
- 所有已打包资源请求都被服务；已打包却 404 即失败
- 存档写入 → 读回 → 删除往返成功
- Electron 模式启动完成、舞台缩放正确、localStorage 往返成功

唯一不判失败：请求的文件在源游戏目录中本来就不存在（游戏自身的可选探测）。这不是放过打包缺陷——它本来就不在包里；若它其实在源目录其他位置，工具会自动补进包并重建。

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
- **预检失败**：报告的 `suggestions` 会列出错误信息中提到的、实际存在的已打包文件，便于定位。
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
- `--cheat`：启用上述 RPG Maker 作弊
- `--keep-work`：保留生成的 Android 工程

## 合规

只打包你拥有或已获授权的游戏；不要把游戏资源、APK、存档提交到开源仓库。RPG Maker 是 Gotcha Gotcha Games 的商标，Android 是 Google LLC 的商标。
