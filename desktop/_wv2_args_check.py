# -*- coding: utf-8 -*-
"""实证: 源码启动 app_window.py 后, WebView2 子进程命令行里有没有我们的开关。
   用于判断 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 环境变量是否被 pywebview 的
   CreationProperties.AdditionalBrowserArguments 覆盖。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Python314\python.exe"


def dump(tag):
    ps = (
        "$out=@(); "
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedgewebview2.exe' "
        "-or ($_.Name -like 'python*' -and $_.CommandLine -like '*app_window*') } | "
        "ForEach-Object { $out += ($_.Name + '|' + $_.ProcessId + '|' + $_.CommandLine) }; "
        "$out | Set-Content -Encoding UTF8 'D:\\Information Sorter\\desktop\\_wv2_dump.txt'"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    print("==== dump @%s ====" % tag)
    try:
        import io
        lines = io.open(os.path.join(HERE, "_wv2_dump.txt"), encoding="utf-8").read().splitlines()
    except Exception as e:
        print("no dump:", e)
        return
    hit = 0
    for ln in lines:
        flag = "HAS_FLAG" if "disable-direct-composition-video-overlays" in ln else ""
        # 只打印命令行关键部分
        parts = ln.split("|", 2)
        name = parts[0] if parts else "?"
        pid = parts[1] if len(parts) > 1 else "?"
        cmd = parts[2] if len(parts) > 2 else ""
        kinds = [w for w in ("--type=gpu-process", "--type=renderer", "--type=utility",
                             "--disable-features=ElasticOverscroll") if w in cmd]
        if "msedgewebview2" in name or "app_window" in cmd:
            print("%-18s pid=%-7s %s %s" % (name, pid, ",".join(kinds), flag))
            if flag:
                hit += 1
    print("-- 带我们的开关的子进程数: %d --" % hit)


def main():
    env = dict(os.environ)
    log = open(os.path.join(HERE, "_wv2_shell.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([PY, os.path.join(HERE, "app_window.py")],
                            cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(14)
        dump("t+14s")
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
