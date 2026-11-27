// SPDX-License-Identifier: MIT
package org.crysirror.player;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;
import org.json.JSONException;
import org.json.JSONObject;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity implements SurfaceHolder.Callback {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private TextView status;
    private SurfaceHolder activeSurface;
    private String game;
    private long session;
    private long generation;
    private int surfaceWidth;
    private int surfaceHeight;
    private boolean surfaceReady;
    private boolean gameReady;
    private boolean resumed;
    private long down;
    private float px, py;

    private final Runnable poll = new Runnable() { public void run() {
        if (session != 0) {
            JSONObject result = command(object("op", "status"));
            String phase = result.optString("phase");
            if (phase.equals("failed")) {
                status.setText(result.optString("message"));
                status.setVisibility(View.VISIBLE);
            } else if (phase.equals("running")) {
                status.setVisibility(View.GONE);
            } else if (phase.equals("waiting_surface")) {
                status.setText(result.optString("message", "Waiting for display…"));
                status.setVisibility(View.VISIBLE);
            } else if (phase.equals("paused")) {
                status.setText("Paused");
                status.setVisibility(View.VISIBLE);
            } else if (phase.equals("stopped")) {
                status.setText("Stopped");
                status.setVisibility(View.VISIBLE);
            }
        }
        main.postDelayed(this, 250);
    }};

    static JSONObject object(Object... pairs) {
        JSONObject result = new JSONObject();
        try {
            for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1]);
        } catch (JSONException error) {
            throw new IllegalArgumentException(error);
        }
        return result;
    }

    static JSONObject parse(String value) {
        try {
            return new JSONObject(value);
        } catch (JSONException error) {
            return object("error", error.toString());
        }
    }

    JSONObject command(JSONObject request) {
        return session == 0 ? new JSONObject() : parse(Native.command(session, request.toString()));
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout root = new FrameLayout(this);
        setContentView(root);
        hideSystemBars();
        SurfaceView view = new SurfaceView(this);
        root.addView(view, new FrameLayout.LayoutParams(-1, -1));
        activeSurface = view.getHolder();
        activeSurface.addCallback(this);
        status = new TextView(this);
        status.setText("Loading game data…");
        root.addView(status, new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        view.setOnTouchListener((surface, event) -> {
            if (session == 0) return false;
            px = event.getX();
            py = event.getY();
            int action = event.getActionMasked();
            boolean pressed = action != MotionEvent.ACTION_UP && action != MotionEvent.ACTION_CANCEL;
            if (pressed) down |= 1L << 16;
            else down &= ~(1L << 16);
            command(object("op", "input", "x", px, "y", py, "down", down));
            return true;
        });
        JSONObject init = parse(Native.initialize(getApplicationContext()));
        if (init.has("error")) {
            status.setText(init.optString("error"));
            return;
        }
        io.execute(() -> {
            try {
                String path = prepareBundledGame();
                main.post(() -> {
                    game = path;
                    gameReady = true;
                    startIfReady();
                });
            } catch (Exception error) {
                main.post(() -> {
                    status.setText("Bundled game copy failed: " + error.getMessage());
                    status.setVisibility(View.VISIBLE);
                });
            }
        });
        main.post(poll);
    }

    void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                return;
            }
        }
        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        );
    }

    String prepareBundledGame() throws IOException {
        File target = new File(getFilesDir(), "games/bundled");
        if (!target.isDirectory() || target.list() == null || target.list().length == 0) {
            copyAssetTree("game", target);
        }
        return target.getAbsolutePath();
    }

    void copyAssetTree(String path, File target) throws IOException {
        android.content.res.AssetManager assets = getAssets();
        try (InputStream in = assets.open(path)) {
            try (OutputStream out = new FileOutputStream(target)) {
                byte[] buffer = new byte[131072];
                int count;
                while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            }
            return;
        } catch (FileNotFoundException notAFile) {
            // Directory entry; recurse below.
        }
        if (!target.isDirectory() && !target.mkdirs()) throw new IOException("Create " + target);
        String[] children = assets.list(path);
        if (children == null) throw new IOException("Read " + path);
        for (String child : children) copyAssetTree(path + "/" + child, new File(target, child));
    }

    void startIfReady() {
        if (session != 0 || !gameReady || !surfaceReady || surfaceWidth <= 0 || surfaceHeight <= 0 || activeSurface == null || game == null) return;
        String saves = new File(getFilesDir(), "saves/" + new File(game).getName()).getAbsolutePath();
        JSONObject result = parse(Native.start(
            object("game", game, "state_dir", saves, "nls", "auto").toString(),
            activeSurface.getSurface(), surfaceWidth, surfaceHeight
        ));
        session = result.optLong("session", 0);
        generation = result.optLong("surface_generation", 0);
        if (result.has("error")) {
            status.setText(result.optString("error"));
            status.setVisibility(View.VISIBLE);
        }
        command(object("op", "pause", "paused", !resumed));
    }

    @Override public void surfaceCreated(SurfaceHolder holder) {
        if (holder == activeSurface) hideSystemBars();
    }

    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        if (holder != activeSurface) return;
        surfaceWidth = width;
        surfaceHeight = height;
        surfaceReady = true;
        if (session == 0) {
            startIfReady();
            return;
        }
        if (holder.getSurface() == null) return;
        JSONObject result = parse(Native.attach(session, holder.getSurface(), width, height));
        generation = result.optLong("surface_generation", 0);
        if (result.has("error")) {
            status.setText(result.optString("error"));
            status.setVisibility(View.VISIBLE);
        }
    }

    @Override public void surfaceDestroyed(SurfaceHolder holder) {
        if (holder != activeSurface) return;
        surfaceReady = false;
        down = 0;
        if (session != 0 && generation != 0) {
            Native.detach(session, generation);
            generation = 0;
        }
    }

    long keyMask(int key) {
        switch (key) {
            case KeyEvent.KEYCODE_ENTER: return 1;
            case KeyEvent.KEYCODE_SPACE: return 2;
            case KeyEvent.KEYCODE_DPAD_LEFT: return 1L << 2;
            case KeyEvent.KEYCODE_DPAD_RIGHT: return 1L << 3;
            case KeyEvent.KEYCODE_DPAD_UP: return 1L << 4;
            case KeyEvent.KEYCODE_DPAD_DOWN: return 1L << 5;
            case KeyEvent.KEYCODE_TAB: return 1L << 6;
            case KeyEvent.KEYCODE_SHIFT_LEFT:
            case KeyEvent.KEYCODE_SHIFT_RIGHT: return 1L << 11;
            case KeyEvent.KEYCODE_CTRL_LEFT:
            case KeyEvent.KEYCODE_CTRL_RIGHT: return 1L << 12;
            case KeyEvent.KEYCODE_ESCAPE: return 1L << 19;
            default: return 0;
        }
    }

    @Override public boolean onKeyDown(int key, KeyEvent event) {
        long mask = keyMask(key);
        if (session != 0 && mask != 0) {
            down |= mask;
            command(object("op", "input", "x", px, "y", py, "down", down));
            return true;
        }
        return super.onKeyDown(key, event);
    }

    @Override public boolean onKeyUp(int key, KeyEvent event) {
        long mask = keyMask(key);
        if (session != 0 && mask != 0) {
            down &= ~mask;
            command(object("op", "input", "x", px, "y", py, "down", down));
            return true;
        }
        return super.onKeyUp(key, event);
    }

    @Override protected void onPause() {
        resumed = false;
        down = 0;
        command(object("op", "pause", "paused", true));
        super.onPause();
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        hideSystemBars();
        command(object("op", "pause", "paused", false));
    }

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    @Override protected void onDestroy() {
        main.removeCallbacks(poll);
        if (session != 0) {
            if (generation != 0) Native.detach(session, generation);
            Native.destroy(session);
        }
        io.shutdownNow();
        super.onDestroy();
    }

    @Override public void onBackPressed() {
        if (session != 0) {
            command(object("op", "input", "x", px, "y", py, "down", 1L << 17));
            command(object("op", "input", "x", px, "y", py, "down", 0));
        } else {
            super.onBackPressed();
        }
    }
}
