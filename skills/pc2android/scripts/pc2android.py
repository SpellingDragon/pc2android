#!/usr/bin/env python3
"""pc2android: package HTML5 / RPG Maker MV/MZ / Electron games into a single Android APK.

Usage:
    python3 pc2android.py /path/to/game

Missing JDK, Android SDK components, Gradle, Chrome for Testing and Python
dependencies are installed automatically into ~/.pc2android and verified.
Exit code 0 means the APK passed build verification and the local preflight.
"""
import argparse
import base64
import hashlib
import http.server
import io
import json
import math
import mimetypes
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import urllib.parse
import urllib.request
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path

WEB_DIRS = ['audio', 'data', 'effects', 'fonts', 'icon', 'img', 'locales', 'models', 'css', 'credits.html']
BACKUP_PATTERNS = ('*.bak*', '*.before-*', '.mz-timeline-backups', '_backup_*')
TOOLCHAIN_HOME = Path(os.environ.get('PC2ANDROID_HOME') or (Path.home() / '.pc2android')).expanduser()
REMEDIATION = []
NEG_ZERO = '__PC2ANDROID_NEG_ZERO__'


def record_remediation(message):
    REMEDIATION.append(message)
    print(f'[remediation] {message}', flush=True)

COMPAT_JS = r'''(function () {
    'use strict';
    function urlPath(value) {
        return String(value || '').replace(/^file:\/\//, '');
    }
    function xhr(path, method) {
        const request = new XMLHttpRequest();
        request.open(method || 'GET', urlPath(path), false);
        try {
            request.send();
            return { status: request.status, text: request.responseText || '' };
        } catch (error) {
            return { status: 0, text: '' };
        }
    }
    function exists(path) {
        const result = xhr(path, 'HEAD');
        return result.status > 0 && result.status < 400;
    }
    function dirname(path) {
        const value = urlPath(path).replace(/\\/g, '/');
        const index = value.lastIndexOf('/');
        return index < 0 ? '.' : index === 0 ? '/' : value.slice(0, index);
    }
    function join() {
        const parts = Array.from(arguments).filter(value => value !== undefined && value !== null && value !== '');
        if (!parts.length) return '.';
        return parts.join('/').replace(/\/{2,}/g, '/');
    }
    let assetIndexCache = null;
    function assetIndex() {
        if (assetIndexCache === null) {
            const files = new Set();
            const dirs = new Set();
            let available = false;
            try {
                const response = xhr('asset-index.json', 'GET');
                if (response.status >= 200 && response.status < 400) {
                    JSON.parse(response.text).forEach(function (path) {
                        files.add(path);
                        const parts = path.split('/');
                        for (let i = 1; i < parts.length; i++) {
                            dirs.add(parts.slice(0, i).join('/'));
                        }
                    });
                    available = true;
                }
            } catch (error) {
                available = false;
            }
            assetIndexCache = { files: files, dirs: dirs, available: available };
        }
        return assetIndexCache;
    }
    function assetPath(value) {
        return urlPath(value).replace(/\\/g, '/').replace(/^\.\//, '').replace(/^\/+/, '');
    }
    function assetExists(path) {
        const index = assetIndex();
        // Packages without a manifest keep the permissive shim behaviour.
        if (!index.available) return true;
        const value = assetPath(path);
        return index.files.has(value) || index.dirs.has(value);
    }
    const fs = {
        constants: { R_OK: 4, W_OK: 2, X_OK: 1, F_OK: 0 },
        accessSync: function (path) {
            if (!assetExists(path)) {
                const error = new Error('ENOENT: no such file or directory, access ' + path);
                error.code = 'ENOENT';
                throw error;
            }
        },
        existsSync: exists,
        readFileSync: function (path) { return xhr(path, 'GET').text; },
        readdirSync: function () { return []; },
        statSync: function () {
            return { isFile: function () { return true; }, isDirectory: function () { return false; } };
        },
        writeFileSync: function () {},
        mkdirSync: function () {},
        renameSync: function () {},
        unlinkSync: function () {},
        writeFile: function (path, data, callback) {
            if (typeof callback === 'function') callback(null);
        },
        mkdir: function (path, options, callback) {
            if (typeof callback === 'function') callback(null);
        },
        createReadStream: function () { return { pipe: function () {} }; },
        createWriteStream: function () {
            return { write: function () {}, end: function () {} };
        },
        promises: {
            mkdir: async function () {},
            writeFile: async function () {},
            readFile: async function (path) { return xhr(path, 'GET').text; }
        }
    };
    window.require = function (name) {
        if (name === 'fs') return fs;
        if (name === 'path') return { dirname: dirname, join: join, resolve: join, sep: '/' };
        if (name === 'child_process') return { exec: function () {} };
        if (name === 'os') return { platform: function () { return 'android'; }, tmpdir: function () { return '/tmp'; } };
        return {};
    };
    window.process = function () {};
    window.process.mainModule = { filename: 'index.html' };
    window.process.cwd = function () { return '.'; };
    window.process.platform = 'android';
    window.process.env = {};
    window.global = window;
    window.__dirname = '.';
    window.electronApp = {
        reveal: function () {},
        quit: function () {
            if (window.Android && typeof window.Android.quit === 'function') window.Android.quit();
        }
    };
})();
'''

ELECTRON_HEAD_MARKUP = '''<base href="../base.asar/"><style id="electron-player-mode-style">html.electron-player-mode,html.electron-player-mode body{width:100%;height:100%;margin:0;overflow:hidden;background:#000}html.electron-player-mode body>header{display:none!important}html.electron-player-mode body>main{width:100%;height:100%;margin:0;padding:0!important}html.electron-player-mode #settings{display:none!important}html.electron-player-mode #preview.view{display:flex!important;align-items:center;justify-content:center;width:100%;height:100%;max-width:none!important;margin:0!important;background:#000}html.electron-player-mode #preview>.wrap{display:flex!important;align-items:center;justify-content:center;width:100%;height:100%;margin:0!important;padding:0!important;border:0!important}html.electron-player-mode #stage.stage{flex:0 0 auto;width:1024px!important;height:830px!important;margin:0!important;aspect-ratio:auto!important;outline:0!important}html.electron-player-mode #preview>.card{display:none!important}html.electron-player-mode #preview>.wrap{transform-origin:center center!important;transform:scale(var(--electron-stage-scale,1))!important}</style>'''

ELECTRON_BOOTSTRAP_JS = r'''(() => {
    const apply = () => {
        document.documentElement.classList.add('electron-player-mode');
        const scale = Math.min(window.innerWidth / 1024, window.innerHeight / 830);
        document.documentElement.style.setProperty('--electron-stage-scale', String(scale));
    };
    if (document.documentElement) apply();
    else document.addEventListener('DOMContentLoaded', apply, { once: true });
})();'''

ELECTRON_START_JS = r'''(() => {
    const applyStageZoom = () => {
        const scale = Math.min(window.innerWidth / 1024, window.innerHeight / 830);
        document.documentElement.style.setProperty('--electron-stage-scale', String(scale));
    };
    window.addEventListener('resize', applyStageZoom, { passive: true });
    let startedFromBootButton = false;
    const bootButton = document.getElementById('v293BootDbg');
    if (bootButton) {
        bootButton.click();
        startedFromBootButton = true;
    }
    if (!startedFromBootButton) {
        document.getElementById('tabP')?.click();
        if (typeof window.v293PlaySplash === 'function') window.v293PlaySplash();
    }
    requestAnimationFrame(() => requestAnimationFrame(() => window.electronApp?.reveal()));
})();'''

NOTICES = '''This application uses compatibility patterns and Android WebView integration approaches from:

1. alabaster-android — https://github.com/moronigranja/alabaster-android (MIT)
2. rpgmaker-to-android — https://github.com/cellicom/rpgmaker-to-android (MIT)
3. Ludens — https://github.com/yoimerdr/ludens (Apache-2.0)
4. AndroidX WebKit — https://github.com/androidx/androidx (Apache-2.0)

The game code, images, audio, and other game assets remain the property of their respective rights holders.
'''

MAIN_ACTIVITY = r'''package app.rpgmport;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.AssetManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.webkit.WebViewAssetLoader;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

public class MainActivity extends Activity {
    private static final String GAME_URL = "https://appassets.androidplatform.net/__GAME_ENTRY__";
    private static final String GAME_HOST = "appassets.androidplatform.net";
    private static final boolean IS_ELECTRON = __IS_ELECTRON__;
    private static final boolean USE_VIRTUAL_KEYS = __USE_VIRTUAL_KEYS__;

    private FrameLayout root;
    private LinearLayout loadingOverlay;
    private WebView webView;
    private WebViewAssetLoader assetLoader;
    private AlertDialog errorDialog;
    private android.window.OnBackInvokedCallback backCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        loadingOverlay = new LinearLayout(this);
        loadingOverlay.setOrientation(LinearLayout.VERTICAL);
        loadingOverlay.setGravity(Gravity.CENTER);
        loadingOverlay.setBackgroundColor(Color.BLACK);
        ProgressBar progress = new ProgressBar(this);
        loadingOverlay.addView(progress);
        TextView status = new TextView(this);
        status.setText("正在加载游戏…");
        status.setTextColor(Color.WHITE);
        status.setTextSize(18);
        status.setPadding(0, 32, 0, 0);
        loadingOverlay.addView(status);
        root.addView(loadingOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        setImmersiveMode();

        assetLoader = new WebViewAssetLoader.Builder()
                .addPathHandler("/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();
        createWebView();
        registerBackHandler();
    }

    private void createWebView() {
        webView = new WebView(this);
        webView.setBackgroundColor(Color.BLACK);
        webView.setFocusable(true);
        webView.setFocusableInTouchMode(true);
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
        webView.setVerticalScrollBarEnabled(false);
        webView.setHorizontalScrollBarEnabled(false);
        webView.addJavascriptInterface(new AndroidBridge(), "Android");
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setUseWideViewPort(false);
        settings.setLoadWithOverviewMode(false);
        settings.setNeedInitialFocus(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            settings.setOffscreenPreRaster(true);
        }

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage message) {
                android.util.Log.d("RPGMPort", message.message());
                return true;
            }
        });
        webView.setWebViewClient(new GameWebViewClient());
        injectCompatibilityScript();
        root.addView(webView, 0, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        loadingOverlay.setVisibility(View.VISIBLE);
        webView.loadUrl(GAME_URL);
    }

    private void injectCompatibilityScript() {
        try {
            String script = readAsset("compat.js");
            if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                WebViewCompat.addDocumentStartJavaScript(
                        webView, script, Collections.singleton("https://" + GAME_HOST));
            }
        }
        catch (Exception error) {
            android.util.Log.w("RPGMPort", "Document-start shim unavailable", error);
        }
    }

    private final class GameWebViewClient extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (!GAME_HOST.equals(uri.getHost())) return null;
            return assetLoader.shouldInterceptRequest(uri);
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (GAME_HOST.equals(uri.getHost())) return false;
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            }
            catch (Exception ignored) {
            }
            return true;
        }

        @Override
        public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            if (GAME_URL.equals(url)) loadingOverlay.setVisibility(View.VISIBLE);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            if (!GAME_URL.equals(url)) return;
            loadingOverlay.setVisibility(View.GONE);
            view.requestFocus();
            if (IS_ELECTRON) {
                try {
                    view.evaluateJavascript(readAsset("electron-start.js"), null);
                }
                catch (IOException error) {
                    android.util.Log.w("RPGMPort", "Electron startup script unavailable", error);
                }
            }
            if (USE_VIRTUAL_KEYS) {
                try {
                    view.evaluateJavascript(readAsset("virtual-keys.js"), null);
                }
                catch (IOException error) {
                    android.util.Log.w("RPGMPort", "Virtual keyboard unavailable", error);
                }
            }
            view.evaluateJavascript(
                    "if (typeof StorageManager !== 'undefined') {"
                            + "StorageManager.isLocalMode = function() { return false; };"
                            + "}", null);
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) showLoadError(error.getDescription());
        }

        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            if (view != webView) return false;
            root.post(() -> {
                if (webView != null) {
                    root.removeView(webView);
                    webView.destroy();
                    webView = null;
                }
                createWebView();
            });
            return true;
        }
    }

    private void showLoadError(CharSequence description) {
        if (isFinishing() || isDestroyed()) return;
        if (errorDialog != null && errorDialog.isShowing()) errorDialog.dismiss();
        errorDialog = new AlertDialog.Builder(this)
                .setTitle("游戏加载失败")
                .setMessage(description)
                .setPositiveButton("重试", (dialog, which) -> {
                    if (webView != null) webView.reload();
                })
                .setNegativeButton("退出", (dialog, which) -> finish())
                .setCancelable(false)
                .show();
    }

    private void registerBackHandler() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            backCallback = this::handleBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, backCallback);
        }
    }

    private void handleBack() {
        if (webView == null) {
            finish();
            return;
        }
        String escape = "window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true}));"
                + "window.dispatchEvent(new KeyboardEvent('keyup',{key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true}));";
        webView.evaluateJavascript(escape, null);
    }

    @Override
    public void onBackPressed() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            super.onBackPressed();
        }
        else handleBack();
    }

    private String readAsset(String name) throws IOException {
        AssetManager assets = getAssets();
        try (InputStream input = assets.open(name);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private void setImmersiveMode() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.systemBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        }
        else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        setImmersiveMode();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && backCallback != null) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
        }
        if (errorDialog != null && errorDialog.isShowing()) errorDialog.dismiss();
        if (webView != null) {
            root.removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    private final class AndroidBridge {
        @JavascriptInterface
        public void quit() {
            runOnUiThread(MainActivity.this::finish);
        }
    }
}
'''


def run(command, env=None, cwd=None):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, env=env, cwd=cwd)


def game_root(source):
    source = source.resolve()
    if (source / 'data_Win').is_dir():
        return source / 'data_Win'
    if (source / 'www').is_dir():
        return source / 'www'
    return source


def detect_crysirror_engine(source):
    root = game_root(source)
    if any(root.glob('*.xp3')):
        return 'crysirror'
    if (root / 'Gameexe.dat').is_file():
        return 'crysirror'
    if (root / 'Gameexe.ini').is_file() and (root / 'Seen.txt').is_file():
        return 'crysirror'
    if (root / 'nscript.dat').is_file():
        return 'crysirror'
    if any(root.glob('*.mjo')) and any(root.glob('*.arc')):
        return 'crysirror'
    if any(root.glob('*.int')) and any(root.glob('*.cst')):
        return 'crysirror'
    return None


def detect_engine(source):
    root = game_root(source)
    crysirror = detect_crysirror_engine(source)
    if crysirror:
        return crysirror
    if (root / 'js/rmmz_core.js').is_file():
        return 'rmmz'
    if (root / 'js/rpg_core.js').is_file():
        return 'rmmv'
    if (root / 'app.asar').is_file() or (source / 'Data/resources/base.asar').is_file():
        return 'electron'
    if (root / 'package.json').is_file() and (root / 'index.html').is_file():
        return 'nwjs'
    if (root / 'Game.exe').is_file():
        return 'windows'
    return 'unknown'


def find_crysirror_source(explicit=None):
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        Path(os.environ['CRYSIRROR_SOURCE']).expanduser() if os.environ.get('CRYSIRROR_SOURCE') else None,
        TOOLCHAIN_HOME / 'crysirror',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = candidate.resolve()
        if (path / 'platform/android/app/build.gradle.kts').is_file() and (path / 'tools/package_platform.py').is_file():
            return path
    raise RuntimeError(
        'CrySirror source not found. Clone https://github.com/xmoezzz/CrySirror.git '
        'into ~/.pc2android/crysirror, set CRYSIRROR_SOURCE, or pass --crysirror-source.'
    )


def rust_version(env=None):
    try:
        output = subprocess.check_output(['rustc', '--version'], text=True, stderr=subprocess.STDOUT, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return (0, 0)
    match = re.search(r'rustc (\d+)\.(\d+)', output)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def ensure_crysirror_source():
    try:
        return find_crysirror_source()
    except RuntimeError:
        git = shutil.which('git')
        if not git:
            raise RuntimeError('git is required to fetch the CrySirror source automatically')
        target = TOOLCHAIN_HOME / 'crysirror'
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        run([git, 'clone', '--depth', '1', 'https://github.com/xmoezzz/CrySirror.git', str(target)])
        return find_crysirror_source()


def ensure_rust_toolchain():
    if rust_version() >= (1, 95):
        return None
    rustup = shutil.which('rustup')
    if not rustup:
        raise RuntimeError('Rust 1.95+ is required. Install rustup and run: rustup toolchain install 1.95.0')
    run([rustup, 'toolchain', 'install', '1.95.0', '--profile', 'minimal'])
    output = subprocess.check_output(
        [rustup, 'run', '1.95.0', 'rustc', '--version'],
        text=True, stderr=subprocess.STDOUT, timeout=60
    )
    match = re.search(r'rustc (\d+)\.(\d+)', output)
    if not match or (int(match.group(1)), int(match.group(2))) < (1, 95):
        raise RuntimeError('Rust 1.95.0 installation did not produce a usable rustc')
    return '1.95.0'


def install_ndk(sdk, java_home=None):
    import xml.etree.ElementTree as ElementTree
    sdk = Path(sdk)
    base = 'https://dl.google.com/android/repository/'
    curl = shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for NDK download')
    result = subprocess.run(
        [curl, '-fsSL', '--max-time', '180', base + 'repository2-3.xml'],
        stdout=subprocess.PIPE, check=True
    )
    root = ElementTree.fromstring(result.stdout)
    host_os = 'macosx' if platform.system() == 'Darwin' else 'linux'
    packages = []
    for package in root.iter():
        if not package.tag.endswith('remotePackage'):
            continue
        path = package.get('path', '')
        if not path.startswith('ndk;'):
            continue
        version = path.split(';', 1)[1]
        if not re.fullmatch(r'\d+(?:\.\d+)+', version):
            continue
        for archive in package.iter():
            if not archive.tag.endswith('archive'):
                continue
            host = next((child.text.strip() for child in archive if child.tag.endswith('host-os')), '')
            if host and host != host_os:
                continue
            complete = next(child for child in archive if child.tag.endswith('complete'))
            meta = {child.tag.split('}')[-1]: (child.text or '').strip() for child in complete}
            packages.append((tuple(int(part) for part in version.split('.')), version, meta))
            break
    if not packages:
        raise RuntimeError('No stable Android NDK found in the Google repository')
    _, version, meta = max(packages, key=lambda item: item[0])
    archive = TOOLCHAIN_HOME / 'downloads' / Path(meta['url']).name
    download_file(base + meta['url'], archive)
    if str(archive.stat().st_size) != meta['size']:
        raise RuntimeError(f'Size mismatch for NDK {version}')
    if hashlib.sha1(archive.read_bytes()).hexdigest() != meta['checksum']:
        raise RuntimeError(f'Checksum mismatch for NDK {version}')
    temp = TOOLCHAIN_HOME / 'downloads' / ('extract-ndk-' + version)
    shutil.rmtree(temp, ignore_errors=True)
    extract_archive(archive, temp)
    archive.unlink(missing_ok=True)
    target = sdk / 'ndk' / version
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = list(temp.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        shutil.move(str(entries[0]), target)
    else:
        target.mkdir(parents=True, exist_ok=True)
        for item in entries:
            shutil.move(str(item), target / item.name)
    shutil.rmtree(temp, ignore_errors=True)
    if not (target / 'toolchains/llvm/prebuilt').is_dir():
        raise RuntimeError('NDK install did not produce a usable LLVM toolchain')
    print(f'Installed Android NDK {version} at {target}', flush=True)
    record_remediation(f'installed Android NDK {version}')
    return target

def ensure_ndk(sdk, java_home=None):
    try:
        return find_ndk(sdk)
    except RuntimeError:
        if not sdk:
            raise
        install_ndk(sdk, java_home)
        return find_ndk(sdk)


def find_ndk(sdk):
    candidates = [
        Path(os.environ['ANDROID_NDK_HOME']).expanduser() if os.environ.get('ANDROID_NDK_HOME') else None,
        Path(os.environ['ANDROID_NDK_ROOT']).expanduser() if os.environ.get('ANDROID_NDK_ROOT') else None,
    ]
    if sdk:
        candidates += sorted((Path(sdk) / 'ndk').glob('*'), key=lambda path: path.name, reverse=True)
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / 'toolchains/llvm/prebuilt').is_dir():
            return path.resolve()
    raise RuntimeError(
        'Android NDK not found. Install an NDK with Android Studio/SDK Manager and set ANDROID_NDK_HOME.'
    )


def ensure_cargo_ndk(toolchain=None):
    if (Path.home() / '.cargo/bin/cargo-ndk').is_file():
        return
    try:
        subprocess.check_output(['cargo', 'ndk', '--version'], text=True, stderr=subprocess.STDOUT, timeout=30)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    env = os.environ.copy()
    command = ['cargo', 'install', 'cargo-ndk', '--locked']
    if toolchain:
        command = ['rustup', 'run', toolchain] + command
        env['PATH'] = str(Path.home() / '.cargo/bin') + os.pathsep + env.get('PATH', '')
    run(command, env=env)


def install_gradle9(home):
    archive = home / 'downloads/gradle-9.1.0-bin.zip'
    download_file('https://services.gradle.org/distributions/gradle-9.1.0-bin.zip', archive)
    target = home / 'gradle-9.1.0'
    shutil.rmtree(target, ignore_errors=True)
    extract_archive(archive, home)
    archive.unlink(missing_ok=True)
    binary = target / 'bin/gradle'
    version = gradle_version(binary)
    if not binary.is_file() or not version or tuple(map(int, version.split('.')[:2])) < (9, 1):
        raise RuntimeError('Gradle install did not produce Gradle 9.1+')
    binary.chmod(0o755)
    print(f'Installed Gradle 9.1 at {target}', flush=True)
    record_remediation(f'installed Gradle 9.1 at {target}')
    return binary


def find_gradle9(explicit=None):
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        Path(os.environ['CRYSIRROR_GRADLE']).expanduser() if os.environ.get('CRYSIRROR_GRADLE') else None,
        TOOLCHAIN_HOME / 'gradle-9.1.0/bin/gradle',
        Path('.tools/gradle-9.1.0/bin/gradle').resolve(),
        shutil.which('gradle'),
    ]
    candidates += [
        path
        for path in (Path.home() / '.gradle/wrapper/dists/gradle-9.1.0-bin').glob('*/gradle-9.1.0/bin/gradle')
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        version = gradle_version(Path(candidate))
        if version and tuple(map(int, version.split('.')[:2])) >= (9, 1):
            return Path(candidate).expanduser().resolve()
    return install_gradle9(TOOLCHAIN_HOME)


def ensure_sdk_licenses(sdk):
    licenses = Path(sdk) / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / 'android-sdk-license').write_text(
        '\n8933bad161af4178b1185d1a37fbf41ea5269c55\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n',
        encoding='utf-8'
    )
    (licenses / 'android-sdk-preview-license').write_text(
        '\n84831b9409646a918e30573bab4c9c91346d8abd\n', encoding='utf-8'
    )


def ensure_crysirror_toolchain(args):
    source = ensure_crysirror_source()
    rust_toolchain = ensure_rust_toolchain()
    ensure_cargo_ndk(rust_toolchain)
    if args.sdk:
        ensure_sdk_licenses(args.sdk)
    ndk = ensure_ndk(args.sdk, args.java_home)
    gradle = find_gradle9(getattr(args, 'crysirror_gradle', None))
    return {'source': source, 'ndk': ndk, 'gradle': gradle, 'rust_toolchain': rust_toolchain}


def remove_backup_files(root):
    for pattern in BACKUP_PATTERNS:
        for path in root.rglob(pattern):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    for path in root.rglob('.DS_Store'):
        path.unlink()
    for name in ['tools', 'swiftshader', 'save']:
        directory = root / name
        if directory.is_dir():
            shutil.rmtree(directory)
    for path in root.rglob('*'):
        if path.is_file() and (path.suffix.lower() in ('.exe', '.dll') or path.name in {
            'node.dll', 'nw.dll', 'icudtl.dat', 'snapshot_blob.bin', 'v8_context_snapshot.bin',
            'resources.pak', 'chrome_100_percent.pak', 'chrome_200_percent.pak',
            'nw_100_percent.pak', 'nw_200_percent.pak', 'notification_helper.exe'
        }):
            path.unlink()


def copy_common(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in WEB_DIRS:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, destination / name, dirs_exist_ok=True, copy_function=copy_file)
        elif item.is_file():
            shutil.copy2(item, destination / name)
    js = source / 'js'
    if js.is_dir():
        # split_large_plugin_images rewrites js/plugins.js in place, so the
        # js tree must not share inodes with the source game.
        shutil.copytree(js, destination / 'js', dirs_exist_ok=True, copy_function=shutil.copy2)
    for name in ['index.html', 'package.json', 'compat.js', 'THIRD_PARTY_NOTICES.txt']:
        item = source / name
        if item.is_file():
            shutil.copy2(item, destination / name)
    remove_backup_files(destination)


def extract_asar(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    run(['npx', '--yes', '@electron/asar', 'extract', str(archive), str(destination)])


def stage_electron(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    base = source / 'Data/resources/base.asar'
    overlay = source / 'Data/resources/app.asar'
    if not overlay.exists():
        overlay = source / 'app.asar'
    if base.exists():
        extract_asar(base, destination / 'base.asar')
    else:
        base = source / 'base.asar'
        if base.exists():
            extract_asar(base, destination / 'base.asar')
    if overlay.exists():
        extract_asar(overlay, destination / 'app.asar')
    if not (destination / 'base.asar').is_dir() and not (destination / 'app.asar').is_dir():
        raise RuntimeError('No base.asar or app.asar content extracted')
    if not (destination / 'base.asar').is_dir():
        raise RuntimeError('Expected base.asar content under base.asar/')
    if not (destination / 'app.asar').is_dir():
        raise RuntimeError('Expected app.asar content under app.asar/')
    overlay_entry = destination / 'app.asar/GameMain.html'
    if overlay_entry.is_file() and '__BSE_APP_DIR__' in overlay_entry.read_text(encoding='utf-8'):
        html = overlay_entry.read_text(encoding='utf-8')
        marker = ELECTRON_HEAD_MARKUP
        if marker not in html:
            html, count = re.subn(r'(<head(?:\s[^>]*)?>)', r'\1' + marker, html, count=1)
            if count != 1:
                raise RuntimeError('Unable to insert static Electron base URL')
            overlay_entry.write_text(html, encoding='utf-8')
    copy_common(source, destination)


def copy_file(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        os.link(source, destination)


def replace_image_reference(value, target, chunks):
    if isinstance(value, list):
        result = []
        count = 0
        for item in value:
            if item == target:
                result.extend(chunks)
                count += 1
            else:
                replaced, nested = replace_image_reference(item, target, chunks)
                result.append(replaced)
                count += nested
        return result, count
    if isinstance(value, dict):
        result = {}
        count = 0
        for key, item in value.items():
            replaced, nested = replace_image_reference(item, target, chunks)
            result[key] = replaced
            count += nested
        return result, count
    return value, 0


def split_large_plugin_images(assets, max_height=2048):
    plugins_file = assets / 'js/plugins.js'
    system_file = assets / 'data/System.json'
    image_root = assets / 'img/system'
    if not plugins_file.is_file() or not system_file.is_file() or not image_root.is_dir():
        return
    system = json.loads(system_file.read_text(encoding='utf-8'))
    if not system.get('hasEncryptedImages'):
        return
    key = bytes.fromhex(system.get('encryptionKey', ''))
    if len(key) != 16:
        return
    match = re.search(r'var \$plugins\s*=\s*(\[.*\])\s*;?\s*$', plugins_file.read_text(encoding='utf-8'), re.S)
    if not match:
        return
    plugins = json.loads(match.group(1))
    changed = False
    for path in sorted(image_root.rglob('*.png_')):
        raw = path.read_bytes()
        if len(raw) < 40:
            continue
        header = bytearray(raw[:40])
        for index in range(16):
            header[16 + index] ^= key[index]
        if not header[16:24] == b'\x89PNG\r\n\x1a\n':
            continue
        width, height = struct.unpack('>II', header[32:40])
        if height <= max_height:
            continue
        relative = path.relative_to(image_root).as_posix()[:-5]
        chunks = [f'{relative}_part{index:02d}' for index in range((height + max_height - 1) // max_height)]
        refs = 0
        for plugin in plugins:
            parameters = plugin.get('parameters', {})
            for name, value in parameters.items():
                if not isinstance(value, str) or relative not in value:
                    continue
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                replaced, count = replace_image_reference(parsed, relative, chunks)
                if count:
                    parameters[name] = json.dumps(replaced, ensure_ascii=False)
                    refs += count
        if not refs:
            continue
        try:
            from PIL import Image
        except ImportError as error:
            raise RuntimeError('Splitting large plugin images requires Pillow: python3 -m pip install Pillow') from error
        data = bytearray(raw[16:])
        for index in range(16):
            data[index] ^= key[index]
        with Image.open(io.BytesIO(data)) as image:
            for index, top in enumerate(range(0, height, max_height)):
                part = image.crop((0, top, width, min(top + max_height, height)))
                buffer = io.BytesIO()
                part.save(buffer, format='PNG')
                payload = bytearray(buffer.getvalue())
                for offset in range(16):
                    payload[offset] ^= key[offset]
                chunk_path = path.with_name(f'{path.name[:-5]}_part{index:02d}.png_')
                chunk_path.write_bytes(bytes.fromhex('5250474d560000000003010000000000') + payload)
        changed = True
        print(f'Split large plugin image: {relative} {width}x{height} -> {len(chunks)} parts')
    if changed:
        plugins_file.write_text('var $plugins = ' + json.dumps(plugins, ensure_ascii=False, separators=(',', ':')) + ';\n', encoding='utf-8')


def uses_access_sync(assets):
    for path in (assets / 'js').rglob('*.js'):
        text = path.read_text(encoding='utf-8', errors='ignore')
        if '.accessSync(' in text:
            return True
    return False


def write_asset_manifest(assets):
    manifest = assets / 'asset-index.json'
    paths = sorted(
        path.relative_to(assets).as_posix()
        for path in assets.rglob('*')
        if path.is_file() and path != manifest
    )
    manifest.write_text(json.dumps(paths, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Asset index: {len(paths)} files')


def entry_path(assets, engine='auto'):
    names = ('app.asar/GameMain.html', 'base.asar/GameMain.html', 'index.html') if engine == 'electron' else ('index.html', 'app.asar/GameMain.html', 'base.asar/GameMain.html')
    for name in names:
        if (assets / name).is_file():
            return name
    raise RuntimeError('No index.html or Electron GameMain.html found')


def write_launcher_icons(app, candidates):
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError('Launcher icon generation requires Pillow: python3 -m pip install Pillow') from error

    def pixel_area(path):
        with Image.open(path) as image:
            return image.width * image.height

    with Image.open(max(candidates, key=pixel_area)) as opened:
        image = opened.convert('RGBA')
    side = min(image.size)
    if image.width != image.height:
        left = (image.width - side) // 2
        top = (image.height - side) // 2
        image = image.crop((left, top, left + side, top + side))
    res = app / 'src/main/res'
    for density, size in {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}.items():
        directory = res / f'mipmap-{density}'
        directory.mkdir(parents=True, exist_ok=True)
        scaled = image.resize((size, size), Image.LANCZOS)
        scaled.save(directory / 'ic_launcher.png')
        scaled.save(directory / 'ic_launcher_round.png')
    foreground_size = 432
    safe_size = 280
    foreground = Image.new('RGBA', (foreground_size, foreground_size), (0, 0, 0, 0))
    offset = (foreground_size - safe_size) // 2
    foreground.alpha_composite(image.resize((safe_size, safe_size), Image.LANCZOS), (offset, offset))
    (res / 'mipmap-xxxhdpi').mkdir(parents=True, exist_ok=True)
    foreground.save(res / 'mipmap-xxxhdpi/ic_launcher_foreground.png')
    from collections import Counter
    opaque = [pixel for pixel in image.getdata() if pixel[3] > 200]
    if opaque:
        buckets = Counter((pixel[0] // 16, pixel[1] // 16, pixel[2] // 16) for pixel in opaque)
        bucket = buckets.most_common(1)[0][0]
        members = [pixel for pixel in opaque if (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16) == bucket]
        background = tuple(sum(pixel[index] for pixel in members) // len(members) for index in range(3))
    else:
        background = (255, 255, 255)
    (res / 'values').mkdir(parents=True, exist_ok=True)
    (res / 'values/colors.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        f'    <color name="ic_launcher_background">#{background[0]:02X}{background[1]:02X}{background[2]:02X}</color>\n'
        '</resources>\n',
        encoding='utf-8',
    )
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n'
    )
    anydpi = res / 'mipmap-anydpi-v26'
    anydpi.mkdir(parents=True, exist_ok=True)
    (anydpi / 'ic_launcher.xml').write_text(adaptive, encoding='utf-8')
    (anydpi / 'ic_launcher_round.xml').write_text(adaptive, encoding='utf-8')


STANDARD_ACTIONS = {
    'ok', 'cancel', 'escape', 'shift', 'control', 'tab', 'pageup', 'pagedown',
    'left', 'right', 'up', 'down', 'menu', 'debug'
}


def key_def(identifier, label, key_code, code, key, group='extra'):
    return {'id': identifier, 'label': label, 'keyCode': key_code, 'code': code, 'key': key, 'group': group}


def base_virtual_keys():
    return [
        key_def('up', '↑', 38, 'ArrowUp', 'ArrowUp', 'dpad'),
        key_def('down', '↓', 40, 'ArrowDown', 'ArrowDown', 'dpad'),
        key_def('left', '←', 37, 'ArrowLeft', 'ArrowLeft', 'dpad'),
        key_def('right', '→', 39, 'ArrowRight', 'ArrowRight', 'dpad'),
        key_def('ok', 'OK', 13, 'Enter', 'Enter', 'action'),
        key_def('cancel', 'ESC', 27, 'Escape', 'Escape', 'action'),
        key_def('shift', 'DASH', 16, 'ShiftLeft', 'Shift', 'action'),
    ]


def analyze_input_profile(assets, engine):
    plugin_dir = assets / 'js/plugins'
    plugin_text = ''
    plugin_names = []
    if plugin_dir.is_dir():
        for path in plugin_dir.glob('*.js'):
            plugin_text += path.read_text(encoding='utf-8', errors='ignore')
        plugins_file = assets / 'js/plugins.js'
        if plugins_file.is_file():
            match = re.search(r'var \$plugins\s*=\s*(\[.*\])\s*;?\s*$', plugins_file.read_text(encoding='utf-8', errors='ignore'), re.S)
            if match:
                try:
                    plugin_names = [item.get('name', '') for item in json.loads(match.group(1)) if item.get('status')]
                except ValueError:
                    pass
    mz3d = 'mz3d' in plugin_names
    pointer_lock = 'pointerLockElement' in plugin_text
    command_buttons = 'CommandButton' in plugin_text or 'Sprite_InputHandle' in plugin_text
    continuous = mz3d or pointer_lock
    action_freq = {}
    for name in re.findall(r"Input\.is(?:Triggered|Pressed|Down|Repeated)\(['\"]([^'\"]+)", plugin_text):
        action_freq[name] = action_freq.get(name, 0) + 1
    for name in re.findall(r'INPUT_CLASS_([A-Z_]+)', plugin_text):
        name = name.lower()
        action_freq[name] = action_freq.get(name, 0) + 1
    custom_actions = sorted(set(action_freq) - STANDARD_ACTIONS)
    if action_freq:
        total = sum(action_freq.values())
        entropy = -sum((count / total) * math.log2(count / total) for count in action_freq.values())
        h_norm = entropy / math.log2(len(action_freq)) if len(action_freq) > 1 else 0.0
    else:
        h_norm = 0.0
    if continuous:
        klass, confidence = 'rich', 0.95
    elif command_buttons:
        klass, confidence = 'touch', 0.85
    elif len(custom_actions) >= 4:
        klass, confidence = 'rich', 0.65
    else:
        klass, confidence = 'touch', 0.6
    keys = base_virtual_keys()
    if 'tab' in action_freq or 'messageskip' in plugin_text.lower():
        keys.append(key_def('tab', 'SKIP', 9, 'Tab', 'Tab'))
    if 'MessageSkip' in plugin_names:
        if mz3d:
            keys.append(key_def('auto_m', 'AUTO', 77, 'KeyM', 'm'))
        else:
            keys.append(key_def('auto_a', 'AUTO', 65, 'KeyA', 'a'))
    elif 'INPUT_CLASS_AUTO' in plugin_text:
        keys.append(key_def('auto_q', 'AUTO', 81, 'KeyQ', 'q'))
    if 'AltRight' in plugin_text:
        keys.append(key_def('alt_right', 'LOG', 18, 'AltRight', 'Alt'))
    if mz3d:
        keys.append(key_def('camera_q', 'Q', 81, 'KeyQ', 'q'))
        keys.append(key_def('camera_e', 'E', 69, 'KeyE', 'e'))
    if 'INPUT_CLASS_SELECT' in plugin_text:
        keys.append(key_def('select_e', 'SEL', 69, 'KeyE', 'e'))
    if 'INPUT_CLASS_START' in plugin_text:
        keys.append(key_def('start_r', 'START', 82, 'KeyR', 'r'))
    if 'INPUT_CLASS_MENU' in plugin_text:
        keys.append(key_def('menu_c', 'MENU', 67, 'KeyC', 'c'))
    if 'INPUT_CLASS_CTRL' in plugin_text and not mz3d:
        keys.append(key_def('ctrl', 'CTRL', 17, 'ControlLeft', 'Control'))
    if 'TMCommonEventKey' in plugin_names:
        for digit in (0, 1, 9):
            if f'commonKey{digit}' in plugin_text:
                keys.append(key_def(f'digit{digit}', str(digit), 48 + digit, f'Digit{digit}', str(digit)))
    evidence = []
    if mz3d:
        evidence.append('MZ3D')
    if pointer_lock:
        evidence.append('pointerLock')
    if command_buttons:
        evidence.append('touchButtons')
    if custom_actions:
        evidence.append('customActions=' + str(len(custom_actions)))
    return {
        'class': klass,
        'confidence': confidence,
        'entropy': {
            'H_norm': round(h_norm, 4),
            'H_res': 1.0 if continuous else round(min(len(custom_actions) / 8, 1.0), 4),
            'rate_ratio': 1.0 if continuous else 0.2,
        },
        'evidence': evidence,
        'recommended_keys': [key['id'] for key in keys],
        'keys': keys,
    }


VIRTUAL_KEYS_TEMPLATE = r'''(() => {
  'use strict';
  if (window.__pc2aVirtualKeysInstalled) return;
  window.__pc2aVirtualKeysInstalled = true;
  const PROFILE = __PROFILE_JSON__;
  const keys = PROFILE.keys;
  const byId = Object.fromEntries(keys.map(k => [k.id, k]));
  const pressed = new Set();
  const root = document.createElement('div');
  root.id = 'pc2a-virtual-keys';
  const style = document.createElement('style');
  style.textContent = `
    #pc2a-virtual-keys{position:fixed;inset:0;z-index:2147483000;pointer-events:none;--vk-size:56px;--vk-gap:8px;--vk-alpha:.28;font-family:sans-serif;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
    .pc2a-vk-cluster{position:absolute;display:grid;gap:var(--vk-gap);pointer-events:none}
    .pc2a-vk-btn{width:var(--vk-size);height:var(--vk-size);border-radius:50%;border:1px solid rgba(255,255,255,.4);background:rgba(0,0,0,var(--vk-alpha));color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;pointer-events:auto;touch-action:none;box-sizing:border-box}
    .pc2a-vk-btn.pc2a-vk-active{background:rgba(255,255,255,calc(var(--vk-alpha) + .3));color:#000}
    #pc2a-vk-toggle{position:absolute;top:8px;right:8px;width:44px;height:38px;border-radius:10px;border:1px solid rgba(255,255,255,.4);background:rgba(0,0,0,var(--vk-alpha));color:#fff;pointer-events:auto;touch-action:none;font-size:10px}
    #pc2a-vk-opacity{position:absolute;top:18px;right:60px;width:110px;pointer-events:auto;touch-action:none}
    #pc2a-virtual-keys.pc2a-vk-hidden .pc2a-vk-cluster{display:none}
  `;
  function fire(type, def) {
    const target = document.querySelector('canvas') || document.body || document.documentElement;
    const event = new KeyboardEvent(type, {bubbles: true, cancelable: true, key: def.key, code: def.code});
    Object.defineProperty(event, 'keyCode', {value: def.keyCode});
    Object.defineProperty(event, 'which', {value: def.keyCode});
    Object.defineProperty(event, 'charCode', {value: 0});
    target.dispatchEvent(event);
  }
  function down(def) { if (!pressed.has(def.id)) { pressed.add(def.id); fire('keydown', def); } }
  function up(def) { if (pressed.delete(def.id)) fire('keyup', def); }
  function releaseAll() { [...pressed].forEach(id => up(byId[id])); }
  function makeButton(def) {
    const button = document.createElement('div');
    button.className = 'pc2a-vk-btn';
    button.textContent = def.label;
    button.dataset.id = def.id;
    button.addEventListener('pointerdown', event => {
      event.preventDefault(); event.stopPropagation();
      try { button.setPointerCapture(event.pointerId); } catch (_) {}
      button.classList.add('pc2a-vk-active');
      down(def);
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => button.addEventListener(type, event => {
      event.preventDefault(); event.stopPropagation();
      button.classList.remove('pc2a-vk-active');
      up(def);
    }));
    button.addEventListener('contextmenu', event => event.preventDefault());
    return button;
  }
  function cluster() { const el = document.createElement('div'); el.className = 'pc2a-vk-cluster'; return el; }
  function build() {
    if (!document.body) { document.addEventListener('DOMContentLoaded', build, {once: true}); return; }
    document.body.appendChild(style);
    document.body.appendChild(root);
    const dpad = cluster();
    dpad.style.left = '16px'; dpad.style.bottom = '16px';
    dpad.style.gridTemplateColumns = 'repeat(3, var(--vk-size))';
    const layout = [null, 'up', null, 'left', null, 'right', null, 'down', null];
    layout.forEach(id => {
      if (id) dpad.appendChild(makeButton(byId[id]));
      else { const spacer = document.createElement('div'); spacer.style.width = 'var(--vk-size)'; spacer.style.height = 'var(--vk-size)'; dpad.appendChild(spacer); }
    });
    const right = cluster();
    right.style.right = '16px'; right.style.bottom = '16px';
    right.style.gridTemplateColumns = 'repeat(3, var(--vk-size))';
    const extras = keys.filter(k => k.group === 'extra');
    const actions = keys.filter(k => k.group === 'action');
    extras.concat(actions).forEach(def => right.appendChild(makeButton(def)));
    const toggle = document.createElement('button');
    toggle.id = 'pc2a-vk-toggle'; toggle.textContent = 'KEYS';
    toggle.addEventListener('click', () => root.classList.toggle('pc2a-vk-hidden'));
    const opacity = document.createElement('input');
    opacity.id = 'pc2a-vk-opacity'; opacity.type = 'range'; opacity.min = '0.1'; opacity.max = '0.7'; opacity.step = '0.05';
    try { opacity.value = localStorage.getItem('pc2a-vk-alpha') || '0.28'; } catch (_) { opacity.value = '0.28'; }
    root.style.setProperty('--vk-alpha', opacity.value);
    opacity.addEventListener('input', () => {
      root.style.setProperty('--vk-alpha', opacity.value);
      try { localStorage.setItem('pc2a-vk-alpha', opacity.value); } catch (_) {}
    });
    root.append(dpad, right, toggle, opacity);
    window.addEventListener('blur', releaseAll);
    document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });
  }
  window.__pc2aVirtualKeys = {
    root,
    down(id) { if (byId[id]) down(byId[id]); },
    up(id) { if (byId[id]) up(byId[id]); },
    press(id) { if (byId[id]) { down(byId[id]); setTimeout(() => up(byId[id]), 60); } },
    releaseAll
  };
  build();
})();
'''


def build_virtual_keys_js(profile):
    payload = json.dumps({'keys': profile['keys']}, ensure_ascii=False)
    return VIRTUAL_KEYS_TEMPLATE.replace('__PROFILE_JSON__', payload)


def fix_message_skip_conflicts(assets):
    plugins_file = assets / 'js/plugins.js'
    if not plugins_file.is_file():
        return False
    text = plugins_file.read_text(encoding='utf-8', errors='ignore')
    if 'MessageSkip' not in text or 'mz3d' not in text:
        return False
    new_text, count = re.subn(r'"オートキー"\s*:\s*"A"', '"オートキー":"M"', text)
    if not count:
        new_text, count = re.subn(r'"AutoKey"\s*:\s*"A"', '"AutoKey":"M"', new_text)
    if count:
        plugins_file.write_text(new_text, encoding='utf-8')
        record_remediation('remapped MessageSkip auto key A->M to avoid MZ3D movement conflict')
        return True
    return False


def write_project(workdir, args, engine):
    app = workdir / 'app'
    (app / 'src/main/java/app/rpgmport').mkdir(parents=True, exist_ok=True)
    (app / 'src/main/assets').mkdir(parents=True, exist_ok=True)
    (app / 'src/main/res/drawable').mkdir(parents=True, exist_ok=True)
    entry = entry_path(app / 'src/main/assets', engine)
    fix_message_skip_conflicts(app / 'src/main/assets')
    profile = getattr(args, 'input_profile', None) or analyze_input_profile(app / 'src/main/assets', engine)
    args.input_profile = profile
    assets_dir = app / 'src/main/assets'
    (assets_dir / 'input-profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    use_virtual_keys = profile['class'] == 'rich'
    if use_virtual_keys:
        (assets_dir / 'virtual-keys.js').write_text(build_virtual_keys_js(profile), encoding='utf-8')
    (app / 'src/main/java/app/rpgmport/MainActivity.java').write_text(
        MAIN_ACTIVITY.replace('__GAME_ENTRY__', urllib.parse.quote(entry)).replace('__IS_ELECTRON__', 'true' if engine == 'electron' else 'false').replace('__USE_VIRTUAL_KEYS__', 'true' if use_virtual_keys else 'false'), encoding='utf-8')
    (app / 'src/main/assets/compat.js').write_text(
        COMPAT_JS + ('\n' + ELECTRON_BOOTSTRAP_JS if engine == 'electron' else ''), encoding='utf-8')
    if engine == 'electron':
        (app / 'src/main/assets/electron-start.js').write_text(ELECTRON_START_JS, encoding='utf-8')
    (app / 'src/main/assets/THIRD_PARTY_NOTICES.txt').write_text(NOTICES, encoding='utf-8')
    (workdir / 'settings.gradle').write_text('''pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = 'RPGMPortWebView'
include ':app'
''', encoding='utf-8')
    (workdir / 'build.gradle').write_text('''buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath 'com.android.tools.build:gradle:8.13.2'
    }
}
''', encoding='utf-8')
    (workdir / 'gradle.properties').write_text('''android.useAndroidX=true
android.builder.sdkDownload=false
android.nonTransitiveRClass=true
org.gradle.jvmargs=-Xmx4g -Dfile.encoding=UTF-8
''', encoding='utf-8')
    (workdir / 'local.properties').write_text(f'sdk.dir={args.sdk or ""}\n', encoding='utf-8')
    (app / 'build.gradle').write_text(f'''apply plugin: 'com.android.application'

android {{
    namespace 'app.rpgmport'
    compileSdk 36
    buildToolsVersion '36.0.0'

    defaultConfig {{
        applicationId '{args.application_id}'
        resValue 'string', 'app_name', '{args.name}'
        minSdk 26
        targetSdk 36
        versionCode 1
        versionName '1.0'
    }}

    androidResources {{
        noCompress 'json', 'ogg', 'ogg_', 'mp3', 'm4a', 'm4a_', 'png', 'png_', 'jpg', 'jpg_', 'webp', 'webm', 'wasm', 'woff', 'woff2', 'ttf', 'otf', 'asar'
    }}

    compileOptions {{
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }}

    buildTypes {{
        debug {{
            minifyEnabled false
        }}
    }}
}}

dependencies {{
    implementation 'androidx.webkit:webkit:1.17.1'
}}
''', encoding='utf-8')
    (app / 'src/main/AndroidManifest.xml').write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-feature android:glEsVersion="0x00020000" android:required="true" />
    <application
        android:allowBackup="false"
        android:enableOnBackInvokedCallback="true"
        android:hardwareAccelerated="true"
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:label="@string/app_name"
        android:largeHeap="true"
        android:resizeableActivity="true"
        android:supportsRtl="true"
        android:theme="@android:style/Theme.Material.NoActionBar.Fullscreen"
        android:usesCleartextTraffic="false">
        <activity
            android:name=".MainActivity"
            android:configChanges="orientation|screenSize|keyboard|keyboardHidden|smallestScreenSize|screenLayout|density|navigation|uiMode"
            android:exported="true"
            android:launchMode="singleTask"
            android:screenOrientation="sensorLandscape">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
''', encoding='utf-8')
    if engine == 'electron':
        icon_candidates = (
            sorted((app / 'src/main/assets/base.asar/Assets').glob('AppIcon_*.png')) +
            sorted((app / 'src/main/assets/app.asar/Assets').glob('AppIcon_*.png')) +
            [game_root(args.source) / 'icon/icon.png']
        )
    else:
        icon_candidates = [game_root(args.source) / 'icon/icon.png']
    candidates = [candidate for candidate in icon_candidates if candidate.is_file()]
    if not candidates:
        raise RuntimeError('No suitable PNG launcher icon found')
    write_launcher_icons(app, candidates)


def replace_once(text, old, new):
    if old not in text:
        raise RuntimeError(f'CrySirror patch target not found: {old[:80]}')
    return text.replace(old, new, 1)


def kotlin_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def patch_crysirror_activity(text):
    text = replace_once(text, '    private boolean importing;\n', '    private boolean importing;\n    private boolean bundledAutoPlay;\n    private String bundledError;\n')
    text = replace_once(text, '        game=getPreferences(0).getString("game",null);\n', '''        game=getPreferences(0).getString("game",null);
        try {
            if(getAssets().list("game").length>0) prepareBundledGame();
        } catch(Exception e) {
            bundledError="Bundled game unavailable: "+e.getMessage();
        }
''')
    text = replace_once(text, '    void library() {\n', '''    private boolean prepareBundledGame() {
        File target=new File(getFilesDir(),"games/bundled");
        try {
            if(!target.isDirectory() || target.list()==null || target.list().length==0) copyAssetTree("game",target);
            game=target.getAbsolutePath();
            getPreferences(0).edit().putString("game",game).apply();
            bundledAutoPlay=true;
            return true;
        } catch(Exception e) {
            bundledError="Bundled game copy failed: "+e.getMessage();
            return false;
        }
    }
    private void copyAssetTree(String path, File target) throws IOException {
        android.content.res.AssetManager assets=getAssets();
        try(InputStream in=assets.open(path)) {
            try(OutputStream out=new FileOutputStream(target)) {
                byte[] buffer=new byte[131072];
                int count;
                while((count=in.read(buffer))!=-1) out.write(buffer,0,count);
            }
            return;
        } catch(FileNotFoundException notAFile) {
            // Directory entry; recurse below.
        }
        if(!target.isDirectory() && !target.mkdirs()) throw new IOException("Create "+target);
        String[] children=assets.list(path);
        if(children==null) throw new IOException("Read "+path);
        for(String child:children) copyAssetTree(path+"/"+child,new File(target,child));
    }
    private boolean isBundledGame() {
        return game!=null && new File(game).getName().equals("bundled");
    }
    void library() {
''')
    text = replace_once(text, '        TextView title=new TextView(this);title.setText("CrySirror");title.setTextSize(30);root.addView(title);\n', '        TextView title=new TextView(this);title.setText(getString(org.crysirror.player.R.string.app_name));title.setTextSize(30);root.addView(title);\n')
    text = replace_once(text, '        status=new TextView(this);status.setText("Choose a game folder to import into application storage.");root.addView(status);\n', '        status=new TextView(this);status.setText(isBundledGame() ? "Bundled game ready." : "Choose a game folder to import into application storage.");root.addView(status);\n')
    text = replace_once(text, '''        importButton=button(root,"Import game folder…",()->startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),1));
        importButton.setEnabled(!importing);
''', '''        if(!isBundledGame()) {
            importButton=button(root,"Import game folder…",()->startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),1));
            importButton.setEnabled(!importing);
        }
''')
    text = replace_once(text, '        if(game!=null) probe(play,game);\n', '        if(game!=null) probe(play,game);\n        if(bundledError!=null) status.setText(bundledError);\n')
    text = replace_once(text, '                status.setText(info.optString("title")+" · "+info.optString("engine"));play.setEnabled(!importing);\n', '''                status.setText(info.optString("title")+" · "+info.optString("engine"));play.setEnabled(!importing);
                if(bundledAutoPlay) {bundledAutoPlay=false;play();}
''')
    return text


def build_crysirror_native(args, toolchain, workdir, abi):
    output = workdir / 'jniLibs'
    shutil.rmtree(output, ignore_errors=True)
    rust_target = {'arm64-v8a': 'aarch64-linux-android', 'x86_64': 'x86_64-linux-android'}[abi]
    if toolchain.get('rust_toolchain'):
        run(['rustup', 'target', 'add', '--toolchain', toolchain['rust_toolchain'], rust_target])
    else:
        run(['rustup', 'target', 'add', rust_target])
    env = os.environ.copy()
    env['ANDROID_HOME'] = str(args.sdk)
    env['ANDROID_NDK_HOME'] = str(toolchain['ndk'])
    env['ANDROID_NDK_ROOT'] = str(toolchain['ndk'])
    if toolchain.get('rust_toolchain'):
        env['RUSTUP_TOOLCHAIN'] = toolchain['rust_toolchain']
    command = [
        'cargo', 'ndk', '--link-libcxx-shared', '-P', '28', '-t', abi, '-o', str(output),
        'rustc', '--release', '-p', 'crysirror-mobile', '--lib', '--crate-type', 'cdylib',
        '--', '-l', 'dylib=c++_shared',
    ]
    if toolchain.get('rust_toolchain'):
        command = ['rustup', 'run', toolchain['rust_toolchain']] + command
        env['PATH'] = str(Path.home() / '.cargo/bin') + os.pathsep + env.get('PATH', '')
    run(command, cwd=toolchain['source'], env=env)
    for library in ('libcrysirror_mobile.so', 'libc++_shared.so'):
        if not (output / abi / library).is_file():
            raise RuntimeError(f'CrySirror native library missing: {abi}/{library}')
    tools = list((toolchain['ndk'] / 'toolchains/llvm/prebuilt').glob('*/bin/llvm-nm'))
    if tools:
        symbols = subprocess.check_output(
            [str(tools[0]), '-D', '--defined-only', str(output / abi / 'libcrysirror_mobile.so')],
            text=True
        )
        for method in ('initialize', 'probe', 'start', 'attach', 'detach', 'command', 'destroy'):
            if 'Java_org_crysirror_player_Native_' + method not in symbols:
                raise RuntimeError(f'CrySirror native library is missing JNI {method}')
    return output


def prepare_crysirror_project(args, toolchain, workdir, jni):
    abi = getattr(args, 'crysirror_abi', 'arm64-v8a')
    project = workdir / 'crysirror-android'
    shutil.rmtree(project, ignore_errors=True)
    shutil.copytree(
        toolchain['source'] / 'platform/android', project,
        ignore=shutil.ignore_patterns('.gradle', 'build', 'local.properties', '.DS_Store')
    )
    app = project / 'app'
    gradle = app / 'build.gradle.kts'
    text = gradle.read_text(encoding='utf-8')
    text = replace_once(text, 'applicationId = "org.crysirror.player"', f'applicationId = {kotlin_string(args.application_id)}')
    text = replace_once(text, 'versionCode = 2', 'versionCode = 1')
    text = replace_once(text, 'versionName = "0.2.0"', 'versionName = "1.0"')
    text = replace_once(
        text,
        'ndk { abiFilters += (project.findProperty("crysirrorAbis") as String? ?: "arm64-v8a").split(\',\') }',
        f'ndk {{ abiFilters += listOf({kotlin_string(abi)}) }}'
    )
    text = replace_once(text, '    sourceSets { getByName("main") { jniLibs.srcDirs("../../../target/platform/android/jniLibs") } }\n', '')
    gradle.write_text(text, encoding='utf-8')

    values = app / 'src/main/res/values'
    values.mkdir(parents=True, exist_ok=True)
    (values / 'strings.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <string name="app_name">{escape(args.name)}</string>\n'
        '</resources>\n', encoding='utf-8'
    )

    manifest = app / 'src/main/AndroidManifest.xml'
    text = replace_once(manifest.read_text(encoding='utf-8'), 'android:label="CrySirror"', 'android:label="@string/app_name"')
    manifest.write_text(text, encoding='utf-8')

    activity = app / 'src/main/java/org/crysirror/player/MainActivity.java'
    activity.write_text(patch_crysirror_activity(activity.read_text(encoding='utf-8')), encoding='utf-8')

    assets = app / 'src/main/assets'
    game_assets = assets / 'game'
    game_assets.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        args.source, game_assets, dirs_exist_ok=True,
        copy_function=shutil.copy2,
        ignore=shutil.ignore_patterns('.git', '.DS_Store')
    )
    licenses = assets / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(toolchain['source'] / 'LICENSE', licenses / 'CrySirror-MPL-2.0.txt')
    for name in ('NOTICE.md', 'NOTICE'):
        notice = toolchain['source'] / name
        if notice.is_file():
            shutil.copy2(notice, licenses / name)
            break

    jni_dir = app / 'src/main/jniLibs'
    jni_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(jni / abi, jni_dir / abi, dirs_exist_ok=True)
    (project / 'local.properties').write_text(f'sdk.dir={args.sdk}\n', encoding='utf-8')
    (project / 'gradle.properties').write_text(
        'org.gradle.jvmargs=-Xmx4g\nandroid.useAndroidX=true\n', encoding='utf-8'
    )
    return project


def build_crysirror_apk(args, project, gradle):
    env = os.environ.copy()
    env['JAVA_HOME'] = str(args.java_home)
    env['ANDROID_HOME'] = str(args.sdk)
    gradle_home = args.out / '.android-port-cache/gradle9'
    gradle_home.mkdir(parents=True, exist_ok=True)
    env['GRADLE_USER_HOME'] = str(gradle_home)
    run([gradle, '-p', project, '--no-daemon', ':app:assembleDebug'], env=env)
    apk = project / 'app/build/outputs/apk/debug/app-debug.apk'
    if not apk.is_file():
        raise RuntimeError(f'CrySirror APK not produced: {apk}')
    return apk


def verify_crysirror_apk(args, apk, abi):
    verify(args, apk)
    with zipfile.ZipFile(apk) as archive:
        names = set(archive.namelist())
        for library in ('libcrysirror_mobile.so', 'libc++_shared.so'):
            if f'lib/{abi}/{library}' not in names:
                raise RuntimeError(f'CrySirror APK is missing lib/{abi}/{library}')
        if not any(name.startswith('assets/game/') for name in names):
            raise RuntimeError('CrySirror APK is missing bundled game data')


def run_crysirror_pipeline(args):
    if not args.source.is_dir():
        raise RuntimeError(f'Source directory not found: {args.source}')
    abi = getattr(args, 'crysirror_abi', 'arm64-v8a')
    print(json.dumps({
        'source': str(args.source), 'engine': 'crysirror', 'name': args.name,
        'application_id': args.application_id, 'abi': abi,
    }, ensure_ascii=False, indent=2))
    workdir = args.out / '.crysirror-work'
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        stage = workdir / 'game'
        shutil.copytree(
            args.source, stage, dirs_exist_ok=True, copy_function=shutil.copy2,
            ignore=shutil.ignore_patterns('.git', '.DS_Store')
        )
        print(f'Dry run complete. Staged CrySirror game: {stage}')
        return
    toolchain = ensure_crysirror_toolchain(args)
    if getattr(args, 'crysirror_skip_native', False):
        jni = workdir / 'jniLibs'
        for library in ('libcrysirror_mobile.so', 'libc++_shared.so'):
            if not (jni / abi / library).is_file():
                raise RuntimeError(f'Prebuilt CrySirror native library missing: {jni / abi / library}')
    else:
        jni = build_crysirror_native(args, toolchain, workdir, abi)
    project = prepare_crysirror_project(args, toolchain, workdir, jni)
    apk = build_crysirror_apk(args, project, toolchain['gradle'])
    verify_crysirror_apk(args, apk, abi)
    final = args.out / f'{args.name}-CrySirror.apk'
    temporary = final.with_name(final.name + '.tmp')
    temporary.unlink(missing_ok=True)
    shutil.move(str(apk), temporary)
    os.replace(temporary, final)
    print(f'APK={final}')
    print(f'SHA256={sha256(final)}')
    print('Scope=CrySirror native Android runtime; build/signature verified; headless preflight not applicable')
    if REMEDIATION:
        print(f'Remediation ({len(REMEDIATION)}):')
        for item in REMEDIATION:
            print(f'  - {item}')
    if not args.keep_work:
        shutil.rmtree(workdir)


def build(args, workdir, engine):
    env = os.environ.copy()
    env['JAVA_HOME'] = str(args.java_home)
    env['ANDROID_HOME'] = str(args.sdk)
    gradle_home = args.out / '.android-port-cache/gradle'
    gradle_home.mkdir(parents=True, exist_ok=True)
    env['GRADLE_USER_HOME'] = str(gradle_home)
    proxy = urllib.parse.urlsplit(os.environ.get('HTTPS_PROXY', ''))
    if proxy.hostname:
        user = urllib.parse.unquote(proxy.username or '')
        password = urllib.parse.unquote(proxy.password or '')
        env['GRADLE_OPTS'] = (
            f'-Dhttp.proxyHost={proxy.hostname} -Dhttp.proxyPort={proxy.port or 80} '
            f'-Dhttps.proxyHost={proxy.hostname} -Dhttps.proxyPort={proxy.port or 80} '
            f'-Dhttp.proxyUser={user} -Dhttp.proxyPassword={password}'
        )
    command = [args.gradle, '-p', workdir, '--no-daemon', ':app:assembleDebug']
    for attempt in range(2):
        try:
            run(command, env)
            break
        except subprocess.CalledProcessError:
            if attempt == 1:
                raise
            print('Gradle failed; retrying once with the persistent cache', flush=True)
    output = workdir / 'app/build/outputs/apk/debug/app-debug.apk'
    if not output.is_file():
        raise RuntimeError(f'APK not produced: {output}')
    return output


def verify(args, apk):
    tools = args.sdk / 'build-tools/36.0.0'
    run([tools / 'apksigner', 'verify', '--verbose', apk])
    run([tools / 'zipalign', '-c', '4', apk])
    badging = subprocess.check_output([tools / 'aapt2', 'dump', 'badging', apk], text=True)
    print(badging)
    with zipfile.ZipFile(apk) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f'CRC verification failed: {bad}')
        count = sum(1 for name in archive.namelist() if name.startswith('assets/'))
        print(f'assets={count}, entries={len(archive.namelist())}, bytes={apk.stat().st_size}')


class ZipHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        self.serve_asset(True)

    def do_HEAD(self):
        self.serve_asset(False)

    def serve_asset(self, include_body):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip('/') or self.server.entry
        name = 'assets/' + path
        if '..' in Path(name).parts:
            self.send_error(403)
            return
        try:
            info = self.server.archive.getinfo(name)
        except KeyError:
            self.send_error(404)
            return
        start, end = 0, info.file_size - 1
        status = 200
        range_header = self.headers.get('Range')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match:
                self.send_error(416)
                return
            start_text, end_text = match.groups()
            if start_text:
                start = int(start_text)
            else:
                length = int(end_text)
                start = max(0, info.file_size - length)
            if end_text:
                end = min(int(end_text), end)
            if start >= info.file_size or start > end:
                self.send_error(416)
                return
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', self.server.mime_type(path))
        self.send_header('Content-Length', str(length))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{info.file_size}')
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        if not include_body:
            return
        try:
            with self.server.archive_lock:
                with self.server.archive.open(name) as source:
                    source.seek(start)
                    remaining = length
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


class LocalServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def mime_type(path):
    for suffix, mime in {
        '.png_': 'image/png', '.ogg_': 'audio/ogg', '.m4a_': 'audio/mp4',
        '.js': 'text/javascript', '.json': 'application/json', '.wasm': 'application/wasm'
    }.items():
        if path.lower().endswith(suffix):
            return mime
    return mimetypes.guess_type(path)[0] or 'application/octet-stream'


class BrowserSession:
    def __init__(self, url):
        try:
            import websocket
        except ImportError as error:
            raise RuntimeError('Local preflight requires websocket-client: python3 -m pip install "websocket-client>=1.8,<2"') from error
        self.socket = websocket.create_connection(url, suppress_origin=True, timeout=15)
        self.next_id = 0
        self.exceptions = []
        self.http_errors = []

    def call(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        self.socket.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get('id') == request_id:
                if 'error' in message:
                    raise RuntimeError(f'DevTools {method}: {message["error"]}')
                return message.get('result', {})
            if message.get('method') == 'Runtime.exceptionThrown':
                details = message['params']['exceptionDetails']
                self.exceptions.append(details.get('exception', {}).get('description') or details.get('text'))
            elif message.get('method') == 'Network.responseReceived':
                response = message['params']['response']
                url = response.get('url', '')
                # Browsers probe /favicon.ico on their own; WebView never does
                # and no game references it.
                if response.get('status', 0) >= 400 and not url.endswith('/favicon.ico'):
                    self.http_errors.append({'status': response['status'], 'url': url})

    def evaluate(self, expression, await_promise=False):
        result = self.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True, 'awaitPromise': await_promise
        })
        if result.get('exceptionDetails'):
            details = result['exceptionDetails']
            script_id = details.get('scriptId')
            line = details.get('lineNumber')
            if script_id and line is not None:
                try:
                    self.call('Debugger.enable')
                    source = self.call('Debugger.getScriptSource', {'scriptId': script_id}).get('scriptSource', '')
                    lines = source.splitlines()
                    details['sourceSnippet'] = lines[max(0, line - 4):line + 5]
                except Exception:
                    pass
            raise RuntimeError(json.dumps(details, ensure_ascii=False))
        return result.get('result', {}).get('value')

    def wait_for(self, expression, ready, timeout=45):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.evaluate(expression)
            if ready(last):
                return last
            time.sleep(0.25)
        raise TimeoutError(f'Browser state did not become ready: {last}')

    def screenshot(self, path):
        data = self.call('Page.captureScreenshot', {'format': 'png', 'fromSurface': True})['data']
        path.write_bytes(base64.b64decode(data))

    def close(self):
        self.socket.close()


STATE_EXPRESSION = r'''(() => {
    const scene = typeof SceneManager !== 'undefined' ? SceneManager._scene : null;
    const renderer = typeof Graphics !== 'undefined' ? (Graphics._app && Graphics._app.renderer) || Graphics._renderer : null;
    let glError = null;
    if (renderer && renderer.gl) {
        while (renderer.gl.getError() !== 0) {}
        if (Graphics._app && Graphics._app.stage) renderer.render(Graphics._app.stage);
        glError = renderer.gl.getError();
    }
    let imagesReady = null;
    let imageError = null;
    if (typeof ImageManager !== 'undefined' && ImageManager.isReady) {
        try {
            imagesReady = ImageManager.isReady();
        } catch (error) {
            imagesReady = false;
            imageError = Array.isArray(error) ? error.slice(0, 2).join(': ') : String(error);
        }
    }
    return {
        scene: scene ? scene.constructor.name : null,
        started: scene ? scene._started : null,
        busy: scene && typeof scene.isBusy === 'function' ? scene.isBusy() : false,
        fadeDuration: scene ? scene._fadeDuration : null,
        imagesReady,
        imageError,
        fontsReady: typeof FontManager !== 'undefined' && FontManager.isReady ? FontManager.isReady() : null,
        commandActive: scene && scene._commandWindow ? scene._commandWindow.active : null,
        commandBusy: scene && scene._commandWindow && scene._commandWindow.isBusy ? scene._commandWindow.isBusy() : null,
        messageBusy: typeof $gameMessage !== 'undefined' && $gameMessage ? $gameMessage.isBusy() : null,
        documentFocus: document.hasFocus(),
        gameActive: typeof SceneManager !== 'undefined' && SceneManager.isGameActive ? SceneManager.isGameActive() : null,
        mapId: typeof $gameMap !== 'undefined' && $gameMap ? $gameMap.mapId() : null,
        player: typeof $gamePlayer !== 'undefined' && $gamePlayer ? [$gamePlayer.x, $gamePlayer.y] : null,
        frame: typeof Graphics !== 'undefined' && Graphics.frameCount,
        glError,
        maxTextureSize: renderer && renderer.gl ? renderer.gl.getParameter(renderer.gl.MAX_TEXTURE_SIZE) : null,
        mainError: typeof main !== 'undefined' && main.error ? String(main.error) : null
    };
})()'''

SAVE_EXPRESSION = r'''(async () => {
    const saveName = DataManager.makeSavename(99);
    await DataManager.saveGame(99);
    if (StorageManager.updateForageKeys) await StorageManager.updateForageKeys();
    let directBytes = null;
    if (typeof localforage !== 'undefined' && StorageManager.forageKey) {
        const data = await localforage.getItem(StorageManager.forageKey(saveName));
        directBytes = data && (data.byteLength || data.length);
    }
    const exists = StorageManager.exists ? StorageManager.exists(saveName) : null;
    await DataManager.loadGame(99);
    const loaded = {
        mapId: $gameMap.mapId(),
        player: [$gamePlayer.x, $gamePlayer.y]
    };
    if (StorageManager.remove) await StorageManager.remove(saveName);
    if (StorageManager.updateForageKeys) await StorageManager.updateForageKeys();
    return {exists, directBytes, loaded};
})()'''


GENERIC_EXPRESSION = r'''(() => ({
    ready: document.readyState,
    title: document.title,
    bodyTextLength: document.body ? document.body.innerText.trim().length : 0,
    bodyPreview: document.body ? document.body.innerText.trim().slice(0, 500) : '',
    controls: [...document.querySelectorAll('button, input, a, [role="button"]')].filter(element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden').length,
    canvasCount: document.querySelectorAll('canvas').length,
    bodyChildren: document.body ? [...document.body.children].map(element => element.tagName).slice(0, 30) : [],
    electronApp: !!window.electronApp,
    baseURI: document.baseURI,
    baseTag: document.querySelector('base') ? document.querySelector('base').href : null,
    appDir: window.__BSE_APP_DIR__ || null,
    releaseMode: window.__releaseMode,
    gameDataType: typeof GameData,
    stateType: typeof state,
    settingsDisplay: document.querySelector('#settings') ? getComputedStyle(document.querySelector('#settings')).display : null,
    previewDisplay: document.querySelector('#preview') ? getComputedStyle(document.querySelector('#preview')).display : null,
    stageClass: document.querySelector('#stage') ? document.querySelector('#stage').className : null,
    stageZoom: document.querySelector('#preview > .wrap') ? getComputedStyle(document.querySelector('#preview > .wrap')).transform : null,
    stageRect: (() => {
        const rect = document.querySelector('#stage')?.getBoundingClientRect();
        return rect ? [rect.width, rect.height] : null;
    })(),
    bodyClass: document.body ? document.body.className : '',
    documentClass: document.documentElement ? document.documentElement.className : '',
    lastDiv: (() => {
        if (!document.body) return null;
        const element = [...document.body.children].reverse().find(item => item.tagName === 'DIV');
        if (!element) return null;
        const style = getComputedStyle(element);
        return {id: element.id, className: element.className, display: style.display, visibility: style.visibility, opacity: style.opacity, children: element.childElementCount, html: element.innerHTML.slice(0, 500)};
    })(),
    visibleImages: [...document.images].filter(image => image.getClientRects().length && getComputedStyle(image).visibility !== 'hidden').length
}))()'''

GENERIC_STORAGE_EXPRESSION = r'''(async () => {
    const key = '__rmmz_preflight__';
    localStorage.setItem(key, 'ok');
    const value = localStorage.getItem(key);
    localStorage.removeItem(key);
    return {ok: value === 'ok', cleared: localStorage.getItem(key) === null};
})()'''


def run_rpgm_preflight(session, report_dir):
    startup = session.wait_for(STATE_EXPRESSION, lambda state: state and state['scene'] in ('Scene_Splash', 'Scene_Title', 'Scene_Map') and state['started'], timeout=60)
    if startup['scene'] == 'Scene_Splash':
        session.evaluate("""(() => {
            const scene = SceneManager._scene;
            if (typeof scene.gotoTitle === 'function') scene.gotoTitle();
            else SceneManager.goto(Scene_Title);
            return true;
        })()""")
    title = session.wait_for(STATE_EXPRESSION, lambda state: state and state['scene'] in ('Scene_Title', 'Scene_Map') and state['started'] and not state['busy'], timeout=60)
    session.screenshot(report_dir / 'title.png')
    if title['scene'] == 'Scene_Title':
        session.evaluate("""(() => {
            const scene = SceneManager._scene;
            if (scene._commandWindow && typeof scene._commandWindow.processOk === 'function') scene._commandWindow.processOk();
            else scene.commandNewGame();
            return true;
        })()""")
    current = session.wait_for(STATE_EXPRESSION, lambda state: state and state['scene'] == 'Scene_Map', timeout=60)
    settle_deadline = time.monotonic() + 20
    while (current['busy'] or (current['fadeDuration'] or 0) > 0) and time.monotonic() < settle_deadline:
        time.sleep(0.25)
        current = session.evaluate(STATE_EXPRESSION)
    message_deadline = time.monotonic() + 5
    while time.monotonic() < message_deadline and not current['messageBusy']:
        time.sleep(0.25)
        current = session.evaluate(STATE_EXPRESSION)
    if current['messageBusy']:
        time.sleep(1.0)
        current = session.evaluate(STATE_EXPRESSION)
    session.screenshot(report_dir / 'map.png')
    save = session.evaluate(SAVE_EXPRESSION, await_promise=True)
    failures = []
    for label, state in (('title', title), ('map', current)):
        if state.get('imageError'):
            failures.append(f'{label} image error: {state["imageError"]}')
        if state.get('imagesReady') is False:
            failures.append(f'{label} images are not ready')
        if state.get('glError') not in (None, 0):
            failures.append(f'{label} WebGL error: {state["glError"]}')
        if state.get('fontsReady') is False:
            failures.append(f'{label} fonts are not ready')
        if state.get('mainError'):
            failures.append(f'{label} error: {state["mainError"]}')
    if current['mapId'] is None:
        failures.append('Map did not load')
    if not save['exists'] or not save['loaded']['mapId']:
        failures.append('Save/load verification failed')
    return title, current, save, failures


def run_generic_preflight(session, report_dir):
    session.screenshot(report_dir / 'startup.png')
    startup = session.wait_for(GENERIC_EXPRESSION, lambda state: state['ready'] == 'complete' and (state['bodyTextLength'] > 0 or state['controls'] > 0 or state['canvasCount'] > 0), timeout=60)
    session.screenshot(report_dir / 'title.png')
    save = session.evaluate(GENERIC_STORAGE_EXPRESSION, await_promise=True)
    failures = []
    if startup['bodyTextLength'] == 0 and startup['controls'] == 0:
        failures.append('No visible text or interactive controls')
    if not save['ok'] or not save['cleared']:
        failures.append('localStorage round-trip failed')
    return startup, None, save, failures


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def find_browser(explicit):
    candidates = [Path(explicit)] if explicit else []
    for base in (TOOLCHAIN_HOME / 'chrome', Path('.tools/chrome').resolve()):
        if base.is_dir():
            candidates += list(base.glob('chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
            candidates += list(base.glob('chrome-linux64/chrome'))
    if not explicit:
        candidates += [
            Path.home() / 'Library/Caches/rmmz-preflight/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
            Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
            Path('/Applications/Chromium.app/Contents/MacOS/Chromium'),
            Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'
        ]
        candidates += [shutil.which(name) for name in ('google-chrome', 'chromium', 'chromium-browser')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError('No Chromium browser found. Pass --browser /path/to/chrome')


def find_asset_in_source(source, rel, assets):
    roots = []
    try:
        roots.append(game_root(source))
    except Exception:
        pass
    roots += [source, source / 'data_Win', source / 'www']
    for root in roots:
        for candidate in (root / rel, root / (rel + '_')):
            if candidate.is_file():
                return candidate
    name = Path(rel).name
    for root in roots + [assets / 'base.asar', assets / 'app.asar']:
        for sub in ('img', 'audio', 'data', 'fonts'):
            base = root / sub
            if not base.is_dir():
                continue
            for candidate in base.rglob(name):
                if candidate.is_file():
                    return candidate
            for candidate in base.rglob('*'):
                if candidate.is_file() and candidate.name.lower() == name.lower():
                    return candidate
    return None


def repair_assets(assets, source, http_errors):
    repairs = []
    seen = set()
    for error in http_errors:
        if error.get('status') != 404:
            continue
        rel = urllib.parse.unquote(urllib.parse.urlsplit(error['url']).path).lstrip('/')
        if not rel or rel in seen:
            continue
        seen.add(rel)
        target = assets / rel
        if target.is_file():
            continue
        candidate = find_asset_in_source(source, rel, assets)
        if not candidate:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        repairs.append((rel, str(candidate)))
        record_remediation(f'repaired missing asset: {rel} <- {candidate}')
    if repairs and (assets / 'asset-index.json').is_file():
        write_asset_manifest(assets)
    return repairs


def preflight_apk(args, apk, assets, report_dir, engine):
    browser = find_browser(args.browser)
    version = subprocess.check_output([browser, '--version'], text=True).strip()
    entry = entry_path(assets, engine)
    report_dir.mkdir(parents=True, exist_ok=True)
    process = None
    session = None
    server = None
    with zipfile.ZipFile(apk) as archive, tempfile.TemporaryDirectory(prefix='rmmz-preflight-') as profile:
        server = LocalServer(('127.0.0.1', 0), ZipHandler)
        server.archive = archive
        server.archive_lock = threading.Lock()
        server.entry = entry
        server.mime_type = mime_type
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        debug_port = free_port()
        url = f'http://127.0.0.1:{port}/{urllib.parse.quote(entry)}'
        command = [
            browser, '--headless=new', '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--disable-sync', '--disable-extensions',
            '--mute-audio', '--autoplay-policy=no-user-gesture-required',
            '--window-size=1230,703', f'--remote-debugging-port={debug_port}',
            f'--user-data-dir={profile}', 'about:blank'
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            target = None
            while time.monotonic() < deadline:
                try:
                    targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json/list', timeout=2))
                    target = next((item for item in targets if item.get('type') == 'page'), None)
                    if target:
                        break
                except Exception:
                    pass
                time.sleep(0.25)
            if not target:
                raise RuntimeError('Chromium did not expose a DevTools page target')
            session = BrowserSession(target['webSocketDebuggerUrl'])
            session.call('Runtime.enable')
            session.call('Network.enable')
            session.call('Page.enable')
            session.call('Page.addScriptToEvaluateOnNewDocument', {'source': (assets / 'compat.js').read_text(encoding='utf-8')})
            session.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
            session.call('Page.bringToFront')
            navigation = session.call('Page.navigate', {'url': url})
            if navigation.get('errorText'):
                raise RuntimeError(f'Chromium navigation failed: {navigation["errorText"]}')
            if engine == 'electron':
                session.wait_for(GENERIC_EXPRESSION, lambda state: state['ready'] == 'complete', timeout=60)
                session.evaluate((assets / 'electron-start.js').read_text(encoding='utf-8'))
                mode = 'electron-generic'
                title, current, save, failures = run_generic_preflight(session, report_dir)
                scope = 'Electron packaged startup, DOM and localStorage; gameplay is not scripted; Android WebView and device GPU require device validation'
            else:
                mode = 'rpgm'
                title, current, save, failures = run_rpgm_preflight(session, report_dir)
                scope = 'Packaged web assets and Chromium gameplay; Android WebView and device GPU require device validation'
            failures.extend(session.exceptions)
            suggestions = []
            for failure in failures:
                for token in re.findall(r'[\w./-]+\.(?:png|jpg|jpeg|webp|ogg|m4a|json|js)', str(failure)):
                    if (assets / token).is_file() and token not in suggestions:
                        suggestions.append(token)
            for error in session.http_errors:
                if error['status'] != 404:
                    failures.append(f'HTTP {error["status"]}: {error["url"]}')
                    continue
                path = urllib.parse.unquote(urllib.parse.urlsplit(error['url']).path).lstrip('/')
                if any(candidate.is_file() for candidate in (assets / path, assets / (path + '_'))):
                    failures.append(f'Packaged asset not served: {path}')
                elif find_asset_in_source(args.source, path, assets):
                    failures.append(f'Missing asset available in source: {path}')
            report = {
                'ok': not failures,
                'mode': mode,
                'scope': scope,
                'browser': str(browser),
                'browserVersion': version,
                'apkSha256': sha256(apk),
                'title': title,
                'map': current,
                'save': save,
                'exceptions': session.exceptions,
                'httpErrors': session.http_errors,
                'failures': failures,
                'remediation': list(REMEDIATION),
                'cheat': getattr(args, 'cheat_summary', None),
                'suggestions': suggestions,
                'inputProfile': getattr(args, 'input_profile', None)
            }
            report_path = report_dir / 'report.json'
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'Preflight report: {report_path}')
            return report
        except Exception as error:
            failure_report = {
                'ok': False,
                'mode': locals().get('mode'),
                'scope': 'Packaged web assets and Chromium startup; Android WebView and device GPU require device validation',
                'browser': str(browser),
                'browserVersion': version,
                'apkSha256': sha256(apk),
                'title': locals().get('title'),
                'map': locals().get('current'),
                'save': locals().get('save'),
                'exceptions': session.exceptions if session else [],
                'httpErrors': session.http_errors if session else [],
                'failures': [str(error)],
                'remediation': list(REMEDIATION),
                'cheat': getattr(args, 'cheat_summary', None),
                'suggestions': [],
                'inputProfile': getattr(args, 'input_profile', None)
            }
            report_path = report_dir / 'report.json'
            report_path.write_text(json.dumps(failure_report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'Preflight report: {report_path}')
            return failure_report
        finally:
            if session:
                session.close()
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            if server:
                server.shutdown()
                server.server_close()
            thread.join(timeout=5)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def java_major(home):
    try:
        output = subprocess.check_output(
            [str(home / 'bin/java'), '-version'], text=True, stderr=subprocess.STDOUT, timeout=60
        )
        match = re.search(r'version "(\d+)', output)
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.SubprocessError):
        return 0


def gradle_version(path):
    try:
        output = subprocess.check_output(
            [str(path), '--version'], text=True, stderr=subprocess.STDOUT, timeout=120
        )
        match = re.search(r'Gradle (\d+\.\d+\.\d+)', output)
        return match.group(1) if match else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def find_sdk():
    candidates = [
        os.environ.get('ANDROID_HOME'),
        os.environ.get('ANDROID_SDK_ROOT'),
        TOOLCHAIN_HOME / 'android-sdk',
        Path('.tools/android-sdk').resolve(),
        Path.home() / 'Library/Android/sdk',
        Path.home() / 'Android/Sdk',
        Path('/opt/android-sdk'),
        Path('/usr/lib/android-sdk'),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / 'build-tools/36.0.0').is_dir() and (path / 'platforms/android-36').is_dir():
            return path.resolve()
    raise RuntimeError('Android SDK with build-tools 36.0.0 and platform 36 not found. Set ANDROID_HOME or pass --sdk.')


def find_java_home():
    candidates = []
    for base in (TOOLCHAIN_HOME / 'jdk-17', Path('.tools/jdk-17').resolve()):
        if base.is_dir():
            candidates += [base / 'Contents/Home', *base.glob('*/Contents/Home'), *base.glob('jdk-17*')]
    if os.environ.get('JAVA_HOME'):
        candidates.append(os.environ['JAVA_HOME'])
    if sys.platform == 'darwin':
        try:
            candidates.append(subprocess.check_output(
                ['/usr/libexec/java_home'], text=True, stderr=subprocess.DEVNULL
            ).strip())
        except (OSError, subprocess.CalledProcessError):
            pass
        candidates += [
            '/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home',
            '/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home',
        ]
    candidates += ['/usr/lib/jvm/default-java', '/usr/lib/jvm/java-17-openjdk-amd64']
    for candidate in candidates:
        if candidate and (Path(candidate) / 'bin/java').is_file() and java_major(Path(candidate)) >= 17:
            return Path(candidate).expanduser().resolve()
    raise RuntimeError('JDK 17+ not found. Set JAVA_HOME or pass --java-home.')


def find_gradle():
    candidates = [
        TOOLCHAIN_HOME / 'gradle-8.14.5/bin/gradle',
        Path('.tools/gradle-8.14.5/bin/gradle').resolve(),
    ]
    if os.environ.get('GRADLE_HOME'):
        candidates.append(Path(os.environ['GRADLE_HOME']) / 'bin/gradle')
    candidates.append(shutil.which('gradle'))
    candidates += [
        path
        for path in (Path.home() / '.gradle/wrapper/dists/gradle-8.14.5-bin').glob('*/gradle-8.14.5/bin/gradle')
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and gradle_version(Path(candidate)) == '8.14.5':
            return Path(candidate).expanduser().resolve()
    raise RuntimeError('Gradle 8.14.5 not found. Install Gradle or pass --gradle.')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Package HTML5 / RPG Maker MV/MZ / Electron / supported VN games into a single Android APK.',
        epilog='One command: python3 pc2android.py /path/to/game'
    )
    parser.add_argument('--source', required=True, type=Path, help='Game folder or data_Win/www folder')
    parser.add_argument('--name', required=True, help='Display name used for APK filename and Android label')
    parser.add_argument('--application-id', required=True, help='Android package id, e.g. com.example.game')
    parser.add_argument('--out', type=Path, default=Path('.'), help='Output directory (default: current directory)')
    parser.add_argument('--engine', choices=['auto', 'rmmz', 'rmmv', 'nwjs', 'electron', 'crysirror'], default='auto')
    parser.add_argument('--dry-run', action='store_true', help='Only detect and stage files, do not build APK')
    parser.add_argument('--preflight', action='store_true', help='Validate packaged scenes and saves in headless Chromium')
    parser.add_argument('--browser', type=Path, help='Chrome or Chromium executable used by --preflight')
    parser.add_argument('--keep-work', action='store_true', help='Keep the generated Android project')
    parser.add_argument('--cheat', action='store_true', help='Apply RPG Maker cheats: negate item prices and prepend 1 to nonzero enemy exp')
    parser.add_argument('--sdk', type=Path, help='Android SDK root (default: ANDROID_HOME or ~/Library/Android/sdk)')
    parser.add_argument('--java-home', type=Path, help='JDK 17+ home (default: JAVA_HOME or /usr/libexec/java_home)')
    parser.add_argument('--gradle', type=Path, help='Gradle 8.14.5 executable (default: gradle on PATH)')
    args = parser.parse_args()
    args.source = args.source.expanduser().resolve()
    args.out = args.out.expanduser().resolve()
    args.crysirror_source = Path(
        os.environ.get('CRYSIRROR_SOURCE') or (TOOLCHAIN_HOME / 'crysirror')
    ).expanduser()
    args.crysirror_abi = os.environ.get('CRYSIRROR_ABI', 'arm64-v8a')
    args.crysirror_gradle = (
        Path(os.environ['CRYSIRROR_GRADLE']).expanduser()
        if os.environ.get('CRYSIRROR_GRADLE') else None
    )
    args.crysirror_skip_native = False
    args.sdk = Path(args.sdk).expanduser().resolve() if args.sdk else None
    args.java_home = Path(args.java_home).expanduser().resolve() if args.java_home else None
    args.gradle = Path(args.gradle).expanduser().resolve() if args.gradle else None
    args.crysirror_source = Path(args.crysirror_source).expanduser().resolve() if args.crysirror_source else None
    args.crysirror_gradle = Path(args.crysirror_gradle).expanduser().resolve() if args.crysirror_gradle else None
    if args.dry_run and args.preflight:
        parser.error('--preflight cannot be combined with --dry-run')
    if args.preflight:
        args.browser = find_browser(args.browser)
    if not args.application_id or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+', args.application_id):
        parser.error('--application-id must be a valid Java package name')
    if not args.dry_run:
        requested = detect_engine(args.source) if args.engine == 'auto' else args.engine
        toolchain = ensure_toolchain(
            need_browser=args.preflight,
            need_gradle=requested != 'crysirror'
        )
        args.sdk = args.sdk or toolchain['sdk']
        args.java_home = args.java_home or toolchain['java_home']
        args.gradle = args.gradle or toolchain['gradle']
        if args.preflight and not args.browser:
            args.browser = toolchain.get('browser')
        if not args.sdk.is_dir() or not args.java_home.is_dir():
            parser.error('Set --sdk and --java-home to an Android SDK root and JDK 17 home')
    return args


def patch_rpgm_json(path, patcher):
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return 0
    count = patcher(data)
    if count:
        temporary = path.with_name(path.name + '.cheat-tmp')
        text = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace(f'"{NEG_ZERO}"', '-0')
        temporary.write_text(text, encoding='utf-8')
        os.replace(temporary, path)
    return count


def patch_item_prices(data):
    count = 0
    entries = data.values() if isinstance(data, dict) else data
    for item in entries:
        if not isinstance(item, dict):
            continue
        price = item.get('price')
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            continue
        if price > 0:
            item['price'] = -price
            count += 1
        elif price == 0:
            item['price'] = NEG_ZERO
            count += 1
    return count


def patch_enemy_exp(data):
    count = 0
    entries = data.values() if isinstance(data, dict) else data
    for enemy in entries:
        if not isinstance(enemy, dict):
            continue
        exp = enemy.get('exp')
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp == 0:
            continue
        sign = -1 if exp < 0 else 1
        enemy['exp'] = sign * int('1' + str(abs(int(exp))))
        count += 1
    return count


CHEAT_KEYWORDS = (
    'price', 'exp', 'gold', 'money', 'item', 'shop', 'enemy', 'experience',
    '所持金', '価格', 'ゴールド', '経験値', 'アイテム'
)
TEXT_SUFFIXES = {'.js', '.json', '.html', '.txt', '.xml', '.csv', '.ini', '.cfg'}


def scan_keyword_files(assets, keywords, limit=8):
    hits = []
    for path in assets.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore').lower()
        except OSError:
            continue
        counts = {keyword: text.count(keyword.lower()) for keyword in keywords}
        score = sum(counts.values())
        if score:
            hits.append((score, path.relative_to(assets).as_posix(), counts))
    hits.sort(reverse=True)
    return hits[:limit]


def format_keyword_counts(counts):
    return ', '.join(f'{keyword}={count}' for keyword, count in counts.items() if count)


def patch_price_tree(value):
    count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'price' and isinstance(item, (int, float)) and not isinstance(item, bool):
                if item > 0:
                    value[key] = -item
                    count += 1
                elif item == 0:
                    value[key] = NEG_ZERO
                    count += 1
            else:
                count += patch_price_tree(item)
    elif isinstance(value, list):
        for item in value:
            count += patch_price_tree(item)
    return count


def patch_gamedata_prices(assets):
    count = 0
    for path in assets.rglob('GameData.js'):
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        marker = 'window.__GAME_DATA__'
        if marker not in text:
            continue
        start = text.index('{', text.index(marker))
        end = text.rindex('}') + 1
        try:
            data = json.loads(text[start:end])
        except ValueError:
            continue
        changed = patch_price_tree(data)
        if not changed:
            continue
        body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace(f'"{NEG_ZERO}"', '-0')
        temporary = path.with_name(path.name + '.cheat-tmp')
        temporary.write_text(text[:start] + body + text[end:], encoding='utf-8')
        os.replace(temporary, path)
        count += changed
    return count


def apply_cheats(assets):
    seen = set()
    items = enemies = 0
    for root in (assets / 'data', assets / 'base.asar/data', assets / 'app.asar/data', assets / 'www/data'):
        root = root.resolve()
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        items += patch_rpgm_json(root / 'Items.json', patch_item_prices)
        enemies += patch_rpgm_json(root / 'Enemies.json', patch_enemy_exp)
    items += patch_gamedata_prices(assets)
    summary = {'items': items, 'enemies': enemies, 'candidates': []}
    if items or enemies:
        print(f'Cheat: negated {items} item price(s), boosted {enemies} enemy exp value(s)', flush=True)
        if items and not enemies:
            print('Cheat: no enemy exp fields found; only prices were patched', flush=True)
    else:
        print('Cheat: no supported RPG Maker/GameData price or exp fields found; skipped', flush=True)
        hits = scan_keyword_files(assets, CHEAT_KEYWORDS)
        if hits:
            print('Cheat candidates (inspect these files to add an adapter):', flush=True)
            for _, relative, counts in hits:
                print(f'  - {relative} ({format_keyword_counts(counts)})', flush=True)
        summary['candidates'] = [relative for _, relative, _ in hits]
    return summary


def run_pipeline(args):
    if not args.source.is_dir():
        raise RuntimeError(f'Source directory not found: {args.source}')
    engine = detect_engine(args.source) if args.engine == 'auto' else args.engine
    if engine == 'crysirror':
        return run_crysirror_pipeline(args)
    if engine in ('windows', 'unknown'):
        raise RuntimeError('This tool handles RPG Maker MV/MZ, NW.js, Electron and CrySirror-supported VN engines. Use Winlator for unsupported Windows-native engines.')
    print(json.dumps({'source': str(args.source), 'engine': engine, 'name': args.name, 'application_id': args.application_id}, ensure_ascii=False, indent=2))
    workdir = args.out / '.android-port-work'
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    if engine == 'electron':
        stage_electron(args.source, workdir / 'app/src/main/assets')
    else:
        copy_common(game_root(args.source), workdir / 'app/src/main/assets')
    if engine in ('rmmz', 'rmmv'):
        split_large_plugin_images(workdir / 'app/src/main/assets')
    write_project(workdir, args, engine)
    assets = workdir / 'app/src/main/assets'
    if engine in ('rmmz', 'rmmv') and uses_access_sync(assets):
        write_asset_manifest(assets)
    if args.cheat:
        args.cheat_summary = apply_cheats(assets)
    if args.dry_run:
        print(f'Dry run complete. Staged project: {workdir}')
        return
    apk = build(args, workdir, engine)
    verify(args, apk)
    if args.preflight:
        report_dir = args.out / f'{args.name}-preflight'
        for attempt in range(3):
            report = preflight_apk(args, apk, assets, report_dir, engine)
            if report['ok']:
                break
            repairs = repair_assets(assets, args.source, report.get('httpErrors') or [])
            if not repairs or attempt == 2:
                raise RuntimeError(f'Local preflight failed: {report.get("failures")}')
            print(f'Repaired {len(repairs)} missing asset(s) from source; rebuilding')
            record_remediation(f'rebuilt after asset repair (pass {attempt + 1})')
            apk = build(args, workdir, engine)
            verify(args, apk)
    final = args.out / f'{args.name}-WebView.apk'
    temporary = final.with_name(final.name + '.tmp')
    if temporary.exists():
        temporary.unlink()
    shutil.move(str(apk), temporary)
    os.replace(temporary, final)
    print(f'APK={final}')
    print(f'SHA256={sha256(final)}')
    if REMEDIATION:
        print(f'Remediation ({len(REMEDIATION)}):')
        for item in REMEDIATION:
            print(f'  - {item}')
    if not args.keep_work:
        shutil.rmtree(workdir)


def derive_identity(project):
    try:
        root = game_root(project)
    except Exception:
        root = project
    name = None
    index = root / 'index.html'
    if index.is_file():
        match = re.search(r'<title[^>]*>(.*?)</title>', index.read_text(encoding='utf-8', errors='ignore'), re.I | re.S)
        if match:
            name = re.sub(r'\s+', ' ', match.group(1)).strip()
    if not name:
        package = root / 'package.json'
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding='utf-8'))
                name = data.get('productName') or (data.get('window') or {}).get('title')
            except (OSError, ValueError):
                pass
    if not name:
        name = project.name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', name).strip() or 'Game'
    slug = re.sub(r'[^a-z0-9]', '', name.lower())
    if not re.fullmatch(r'[a-z][a-z0-9]{2,}', slug):
        slug = 'g' + hashlib.sha1(name.encode('utf-8')).hexdigest()[:10]
    return name, f'com.pc2android.{slug}'


def download_file(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for downloads. Install curl and retry.')
    print(f'Downloading {url}', flush=True)
    for attempt in range(3):
        result = subprocess.run(
            [curl, '-fL', '--retry', '3', '--connect-timeout', '30', '--progress-bar', '-o', str(destination), url],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        if result.returncode == 0 and destination.is_file() and destination.stat().st_size >= 1024 * 1024:
            print(f'  saved {destination.stat().st_size / 1e6:.0f} MB', flush=True)
            return
        destination.unlink(missing_ok=True)
        if attempt == 2:
            raise RuntimeError(f'Download failed: {url}: {result.stdout[-1000:]}')
        print(f'  retry {attempt + 1}/2', flush=True)
        record_remediation(f'retrying download ({attempt + 1}/2): {url}')
        time.sleep(2)


def extract_archive(archive, destination, strip=0):
    archive = Path(archive)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith('.zip'):
        unzip = shutil.which('unzip')
        if unzip:
            subprocess.run([unzip, '-q', str(archive), '-d', str(destination)], check=True)
        else:
            with zipfile.ZipFile(archive) as source:
                source.extractall(destination)
        return
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if strip:
            for member in members:
                member.path = member.path.split('/', strip)[strip] if '/' in member.path else member.path
        source.extractall(destination, members=members)


def install_jdk(home):
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'darwin':
        os_name, arch = 'mac', ('aarch64' if machine == 'arm64' else 'x64')
    elif system == 'linux':
        os_name, arch = 'linux', ('aarch64' if machine in ('aarch64', 'arm64') else 'x64')
    else:
        raise RuntimeError(f'Automatic JDK install is not supported on {system}. Install JDK 17+ and set JAVA_HOME.')
    url = f'https://api.adoptium.net/v3/binary/latest/17/ga/{os_name}/{arch}/jdk/hotspot/normal/eclipse'
    archive = home / 'downloads/jdk.tar.gz'
    download_file(url, archive)
    target = home / 'jdk-17'
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    extract_archive(archive, target, strip=1)
    archive.unlink(missing_ok=True)
    for candidate in [target / 'Contents/Home', *target.glob('*/Contents/Home'), *target.glob('jdk-17*')]:
        if (candidate / 'bin/java').is_file() and java_major(candidate) >= 17:
            print(f'Installed JDK 17 at {candidate}', flush=True)
            record_remediation(f'installed JDK 17 at {candidate}')
            return candidate
    raise RuntimeError('JDK install did not produce a working java binary')


def install_android_sdk(home):
    import xml.etree.ElementTree as ElementTree
    sdk = home / 'android-sdk'
    sdk.mkdir(parents=True, exist_ok=True)
    base = 'https://dl.google.com/android/repository/'
    curl = shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for downloads. Install curl and retry.')
    result = subprocess.run([curl, '-fsSL', '--max-time', '120', base + 'repository2-3.xml'],
                            stdout=subprocess.PIPE, check=True)
    root = ElementTree.fromstring(result.stdout)
    host_os = 'macosx' if platform.system() == 'Darwin' else 'linux'

    def archive_for(path):
        for package in root.iter():
            if not package.tag.endswith('remotePackage') or package.get('path') != path:
                continue
            for archive in package.iter():
                if not archive.tag.endswith('archive'):
                    continue
                host = next((child.text.strip() for child in archive if child.tag.endswith('host-os')), '')
                if host and host != host_os:
                    continue
                complete = next(child for child in archive if child.tag.endswith('complete'))
                return {child.tag.split('}')[-1]: (child.text or '').strip() for child in complete}
        raise RuntimeError(f'Package not found in Android repository: {path}')

    for path, relative in (('platforms;android-36', 'platforms/android-36'), ('build-tools;36.0.0', 'build-tools/36.0.0')):
        target = sdk / relative
        if (target / ('android.jar' if path.startswith('platforms') else 'aapt2')).is_file():
            continue
        meta = archive_for(path)
        archive = home / 'downloads' / Path(meta['url']).name
        download_file(base + meta['url'], archive)
        if str(archive.stat().st_size) != meta['size']:
            raise RuntimeError(f'Size mismatch for {meta["url"]}')
        if hashlib.sha1(archive.read_bytes()).hexdigest() != meta['checksum']:
            raise RuntimeError(f'Checksum mismatch for {meta["url"]}')
        temp = home / 'downloads' / ('extract-' + Path(meta['url']).stem)
        shutil.rmtree(temp, ignore_errors=True)
        extract_archive(archive, temp)
        archive.unlink(missing_ok=True)
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        entries = list(temp.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            shutil.move(str(entries[0]), target)
        else:
            target.mkdir(parents=True, exist_ok=True)
            for item in entries:
                shutil.move(str(item), target / item.name)
        shutil.rmtree(temp, ignore_errors=True)
        print(f'Installed {path}', flush=True)
    (sdk / 'licenses').mkdir(parents=True, exist_ok=True)
    (sdk / 'licenses/android-sdk-license').write_text(
        '\n8933bad161af4178b1185d1a37fbf41ea5269c55\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n',
        encoding='utf-8')
    (sdk / 'licenses/android-sdk-preview-license').write_text(
        '\n84831b9409646a918e30573bab4c9c91346d8abd\n', encoding='utf-8')
    if not (sdk / 'build-tools/36.0.0/aapt2').is_file() or not (sdk / 'platforms/android-36/android.jar').is_file():
        raise RuntimeError('Android SDK install did not produce the required components')
    print(f'Installed Android SDK at {sdk}', flush=True)
    record_remediation(f'installed Android platform 36 and build-tools 36.0.0 at {sdk}')
    return sdk


def install_gradle(home):
    archive = home / 'downloads/gradle-8.14.5-bin.zip'
    download_file('https://services.gradle.org/distributions/gradle-8.14.5-bin.zip', archive)
    target = home / 'gradle-8.14.5'
    shutil.rmtree(target, ignore_errors=True)
    extract_archive(archive, home)
    archive.unlink(missing_ok=True)
    binary = target / 'bin/gradle'
    if not binary.is_file() or gradle_version(binary) != '8.14.5':
        raise RuntimeError('Gradle install did not produce Gradle 8.14.5')
    binary.chmod(0o755)
    print(f'Installed Gradle 8.14.5 at {target}', flush=True)
    record_remediation(f'installed Gradle 8.14.5 at {target}')
    return binary


def install_chrome(home):
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'darwin':
        arch = 'mac-arm64' if machine == 'arm64' else 'mac-x64'
        folder = 'chrome-mac-arm64' if machine == 'arm64' else 'chrome-mac-x64'
        binary_rel = Path(folder) / 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
    elif system == 'linux':
        arch, folder = 'linux64', 'chrome-linux64'
        binary_rel = Path(folder) / 'chrome'
    else:
        raise RuntimeError(f'Automatic Chrome install is not supported on {system}. Install Chrome and pass --browser.')
    url = f'https://storage.googleapis.com/chrome-for-testing-public/133.0.6943.141/{arch}/{folder}.zip'
    target = home / 'chrome'
    archive = home / 'downloads/chrome.zip'
    download_file(url, archive)
    shutil.rmtree(target, ignore_errors=True)
    extract_archive(archive, target)
    archive.unlink(missing_ok=True)
    binary = target / binary_rel
    if not binary.is_file():
        raise RuntimeError('Chrome for Testing install did not produce the browser binary')
    binary.chmod(0o755)
    print(f'Installed Chrome for Testing at {binary}', flush=True)
    record_remediation(f'installed Chrome for Testing at {binary}')
    return binary


def ensure_toolchain(need_browser=True, need_gradle=True):
    TOOLCHAIN_HOME.mkdir(parents=True, exist_ok=True)
    try:
        java_home = find_java_home()
    except RuntimeError:
        java_home = install_jdk(TOOLCHAIN_HOME)
    try:
        sdk = find_sdk()
    except RuntimeError:
        sdk = install_android_sdk(TOOLCHAIN_HOME)
    gradle = None
    if need_gradle:
        try:
            gradle = find_gradle()
        except RuntimeError:
            gradle = install_gradle(TOOLCHAIN_HOME)
    browser = None
    if need_browser:
        try:
            browser = find_browser(None)
        except RuntimeError:
            browser = install_chrome(TOOLCHAIN_HOME)
    return {'sdk': sdk, 'java_home': java_home, 'gradle': gradle, 'browser': browser}


def ensure_python_deps():
    try:
        import PIL  # noqa: F401
        import websocket  # noqa: F401
        return
    except ImportError:
        pass
    venv_dir = TOOLCHAIN_HOME / 'venv'
    python = venv_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        import venv
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(python), '-m', 'pip', 'install', 'Pillow>=10,<13', 'websocket-client>=1.8,<2'], check=True)
    record_remediation(f'installed Python dependencies into {venv_dir}')
    os.execv(str(python), [str(python), str(Path(__file__).resolve())] + sys.argv[1:])


def one_command(project, cheat=False):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise RuntimeError(f'Project directory not found: {project}')
    name, application_id = derive_identity(project)
    engine = detect_engine(project)
    toolchain = ensure_toolchain(
        need_browser=engine != 'crysirror',
        need_gradle=engine != 'crysirror'
    )
    args = types.SimpleNamespace(
        source=project, name=name, application_id=application_id, out=Path.cwd(),
        engine=engine, dry_run=False, preflight=True, browser=toolchain.get('browser'), keep_work=False,
        cheat=cheat, sdk=toolchain['sdk'], java_home=toolchain['java_home'], gradle=toolchain.get('gradle'),
    )
    run_pipeline(args)


def main():
    run_pipeline(parse_args())


if __name__ == '__main__':
    try:
        if '--help' not in sys.argv and '-h' not in sys.argv:
            ensure_python_deps()
        if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
            one_command(Path(sys.argv[1]), cheat='--cheat' in sys.argv[2:])
        else:
            main()
    except subprocess.CalledProcessError as error:
        print(f'Command failed: {error}', file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
