# -*- coding: utf-8 -*-
"""实证: 启动 exe(洞鉴虚明v0.3.1.7dsk) 后抓 WebView2 浏览器进程命令行, 与源码跑对照。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "洞鉴虚明v0.3.1.7dsk.exe")


def dump(tag):
    ps = (
        "$out=@(); "
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedgewebview2.exe' -or "
        "$_.Name -like '洞鉴*' } | "
        "ForEach-Object { $out += ($_.Name + '|' + $_.ProcessId + '|' + $_.CommandLine) }; "
        "$out | Set-Content -Encoding UTF8 'D:\\Information Sorter\\desktop\\_wv2_dump_exe.txt'"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    import io
    try:
        lines = io.open(os.path.join(HERE, "_wv2_dump_exe.txt"), encoding="utf-8").read().splitlines()
    except Exception as e:
        print("no dump:", e)
        return
    print("==== exe dump @%s ====" % tag)
    for ln in lines:
        parts = ln.split("|", 2)
        name = parts[0] if parts else "?"
        cmd = parts[2] if len(parts) > 2 else ""
        if name.lower().endswith(".exe") and "msedgewebview2" not in name:
            print("APP  %s" % name)
            continue
        if "--type=" in cmd:
            continue   # 只看浏览器进程
        flag = "HAS_FLAG" if "disable-direct-composition-video-overlays" in cmd else "no-flag"
        udf = ""
        for tok in cmd.split():
            if tok.startswith("--user-data-dir"):
                udf = tok
        print("BROWSER pid=%s %s  %s" % (parts[1] if len(parts) > 1 else "?", flag, udf))


def main():
    if not os.path.isfile(EXE):
        print("EXE MISSING:", EXE)
        return 1
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    try:
        time.sleep(16)
        dump("t+16s")
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
    # 兜底: 杀掉可能残留的 webview2(仅当父已死)
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
