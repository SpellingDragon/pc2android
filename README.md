# game-pc2and

**电脑游戏，一键变成安卓 APK。**  
RPG Maker MV/MZ · NW.js · Electron · 自动环境 · 自动验证 · 虚拟键盘 · 可选作弊

[![CI](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml/badge.svg)](https://github.com/SpellingDragon/game-pc2and/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.en.md) · [下载最新版](https://github.com/SpellingDragon/game-pc2and/releases/latest) · [观看 28 秒演示](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4)

![game-pc2and：电脑游戏一键变成安卓 APK](docs/images/hero.png)

## 演示

![打包与运行演示](docs/images/demo-preview.gif)

[完整演示视频（真实构建日志、虚拟键盘、3D 镜头与预检报告）](https://github.com/SpellingDragon/game-pc2and/releases/latest/download/game-pc2and-demo.mp4)

## 使用

### 作为 Agent Skill

```bash
git clone https://github.com/SpellingDragon/game-pc2and.git
cd game-pc2and
```

把 `skills/pc2android` 复制到你的 Agent 的用户级或项目级 skills 目录，重新加载 skills 后：

```text
/pc2android /path/to/game
```

不同 Agent 的 skills 目录位置不同，按其文档挂载同一目录即可。

### 直接运行

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game
```

第一次运行会自动安装缺失的 JDK、Android SDK 组件、Gradle、Chrome for Testing 与 Python 依赖，需要联网和几 GB 磁盘空间。

输出在当前目录：

```text
<游戏名>-WebView.apk
<游戏名>-preflight/report.json
<游戏名>-preflight/title.png / map.png
```

退出码 0 才会输出 APK；失败时查看 `report.json` 的 `failures`、`suggestions`、`cheat.candidates` 与 `remediation`。

## 支持的引擎

| 类型 | 状态 |
|---|---|
| RPG Maker MZ | 已验证 |
| RPG Maker MV | 已验证 |
| NW.js 网页游戏 | 已验证 |
| Electron（asar） | 已验证 |
| Windows 原生引擎（QLIE、KiriKiri 等） | 不适用，建议 Winlator |

## 触屏与作弊

- 菜单/ADV/回合制游戏保持触屏操作；MZ3D 等输入复杂的游戏自动注入半透明虚拟键盘，可隐藏、可调透明度。
- 虚拟键盘按住即 keydown、松开即 keyup，支持多指。

```bash
python3 skills/pc2android/scripts/pc2android.py /path/to/game --cheat
```

- RPG Maker：物品 `price > 0` 取负，`price == 0` 变 `-0`；敌人 `exp != 0` 时最高位前加 `1`。
- 暴露 `window.__GAME_DATA__` 的自定义 Electron 引擎：递归修改其中的 `price`。
- 只改暂存副本，源游戏文件不动；数量写入 `report.json` 的 `cheat` 字段。

## 技术

识别引擎与入口 → 兼容修复 → WebView 单 APK → 从 APK 直接预检 → 输入画像与触屏/虚拟键盘。

<details>
<summary>高级参数与故障排查</summary>

```bash
python3 skills/pc2android/scripts/pc2android.py \
  --source /path/to/game \
  --name "Game Name" \
  --application-id com.example.game \
  --out /path/to/output \
  --preflight
```

- `--engine auto|rmmz|rmmv|nwjs|electron`
- `--dry-run`：只检测与暂存
- 省略 `--preflight`：只构建并校验
- `--browser <path>`：指定 Chromium
- `--keep-work`：保留生成的 Android 工程

下载失败检查 `HTTPS_PROXY` 与 curl 后重跑。

</details>

## 常见问题

**需要 root 吗？** 不需要。  
**需要 Android Studio 吗？** 不需要，SDK 组件会自动安装。  
**所有 PC 游戏都能转吗？** 当前面向 HTML5 / RPG Maker MV/MZ / NW.js / Electron。  
**游戏资源会进仓库吗？** 不会，请只打包你拥有或已获授权的游戏。  
**虚拟键盘会自动代打吗？** 不会，它只把触摸转成游戏按键。

## License

MIT，见 [LICENSE](LICENSE)。第三方致谢见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

RPG Maker 是 Gotcha Gotcha Games 的商标；Android 是 Google LLC 的商标。本项目与上述组织无关联。
