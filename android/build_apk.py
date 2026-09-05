# -*- coding: utf-8 -*-
"""一键打包安卓 APK: 同步源码 -> JS语法检查 -> Gradle构建 -> 复制到 releases/。
由 build_apk.bat 调用; 也可直接 python build_apk.py 运行。"""
import json
import os
import re
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
SRC = os.path.join(os.path.dirname(BASE), "desktop")
DST = os.path.join(BASE, "app", "src", "main", "assets", "python_src")
SDK = os.path.join(os.path.dirname(BASE), "android-sdk")
GRADLE_BAT = os.path.join(SDK, "gradle-8.5", "bin", "gradle.bat")
JAVA_HOME = os.path.join(SDK, "jdk-17.0.20.1+1")
WEB_FILES = ("app.js", "index.html", "style.css", "qrcode.js", "favicon.ico")


def sync_src():
    if not os.path.exists(os.path.join(SRC, "app.py")):
        sys.exit("[错误] 未找到桌面版源码: " + SRC)
    os.makedirs(os.path.join(DST, "web"), exist_ok=True)
    shutil.copy2(os.path.join(SRC, "app.py"), os.path.join(DST, "app.py"))
    for f in WEB_FILES:
        p = os.path.join(SRC, "web", f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(DST, "web", f))
    print("[1/3] 已同步桌面版源码 -> assets")


def check_js():
    node = shutil.which("node")
    if not node:
        print("[提示] 未找到 node, 跳过语法检查")
        return
    target = os.path.join(DST, "web", "app.js")
    r = subprocess.call([node, "--check", target])
    if r != 0:
        sys.exit("[错误] app.js 存在语法错误, 已中止打包")
    print("[1.5/3] app.js 语法检查通过")


def build():
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    print("[2/3] 构建 APK (首次构建较慢, 之后增量)...")
    r = subprocess.call(["cmd", "/c", "gradlew.bat assembleDebug --no-daemon -q"],
                        cwd=BASE, env=env)
    if r != 0:
        sys.exit("[错误] 构建失败, 请检查上方报错信息")


def main():
    sync_src()
    check_js()
    build()
    gradle = open(os.path.join(BASE, "app", "build.gradle"), encoding="utf-8").read()
    ver = re.search(r'versionName "([^"]+)"', gradle).group(1)
    apk = os.path.join(BASE, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    out = os.path.join(ROOT, "releases", "洞鉴虚明-%s-debug.apk" % ver)
    shutil.copy2(apk, out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("[3/3] 完成: releases/洞鉴虚明-%s-debug.apk" % ver)


if __name__ == "__main__":
    main()
