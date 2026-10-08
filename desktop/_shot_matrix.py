# -*- coding: utf-8 -*-
"""顶栏消失矩阵取证: 改单个设置 → 起壳(带探针) → 截图 → 复原设置。
每轮: ui_bq / ui_sr / ownui / 全开, 截图 _shot_<cfg>.png + 探针报告。"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _e2e_shell import wait_window  # noqa: E402
from PIL import ImageGrab  # noqa: E402

u32 = ctypes.windll.user32
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
DB = r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db"


def force_shot(hwnd, path):
    u32.ShowWindow(hwnd, 9)
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)
    u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    return r.right - r.left, r.bottom - r.top


def set_kv(k, v):
    conn = sqlite3.connect(DB, timeout=5)
    conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, str(v)))
    conn.commit()
    conn.close()


def get_kv(k):
    conn = sqlite3.connect(DB, timeout=5)
    row = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    conn.close()
    return row[0] if row else None


def run_cfg(tag, env_extra=None, wait=16):
    out_png = os.path.join(HERE, "_shot_%s.png" % tag)
    out_jl = os.path.join(HERE, "_probe_out.jsonl")
    if os.path.exists(out_jl):
        os.remove(out_jl)
    env = dict(os.environ, MSX_PROBE="1")
    env.update(env_extra or {})
    log = open(os.path.join(HERE, "_shot_shell.log"), "a", encoding="utf-8")
    log.write("\n===== CFG %s env=%s =====\n" % (tag, env_extra))
    log.flush()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app_window.py"), "--e2e-open", URL],
        cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        hwnd, title, _ = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print("%-10s NO WINDOW" % tag)
            return
        time.sleep(wait)
        w, h = force_shot(hwnd, out_png)
        print("%-10s shot %dx%d" % (tag, w, h))
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
    # 摘录本轮探针的视频页报告
    time.sleep(1)
    import io
    if os.path.exists(out_jl):
        for line in io.open(out_jl, encoding="utf-8").read().splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "www.bilibili.com/video" in d.get("url", "") and d.get("main"):
                m = d["main"]
                print("    probe main disp=%s top=%s rect=%s hidden=%d scrollY=%s hit=%s" % (
                    m["display"], m.get("top"), m.get("rect"), len(m.get("hiddenChain") or []),
                    d.get("scrollY"), json.dumps(d.get("hitAtHeader"), ensure_ascii=False)[:80]))
                break


def main():
    cases = [
        ("bq_on",    {}),                          # 基线(现状): 画质增强开
        ("bq_off",   {"ui_bq": "0"}),              # 关画质增强
        ("sr_off",   {"ui_bq": "0", "ui_sr": "0"}),  # 再关超分
        ("ownui_off", {"ui_bq": "0", "ui_sr": "0", "MSX_NO_OWNUI": "1"}),  # 再关自有UI
    ]
    saved = {}
    for tag, cfg in cases:
        for k, v in cfg.items():
            if k.startswith("ui_") and k not in saved:
                saved[k] = get_kv(k)
                set_kv(k, v)
        env = {k: v for k, v in cfg.items() if not k.startswith("ui_")}
        run_cfg(tag, env_extra=env)
    # 复原
    for k, v in saved.items():
        if v is None:
            conn = sqlite3.connect(DB, timeout=5)
            conn.execute("DELETE FROM settings WHERE k=?", (k,))
            conn.commit()
            conn.close()
        else:
            set_kv(k, v)
    print("settings restored:", saved)


if __name__ == "__main__":
    sys.exit(main())
