"""安卓壳的 Python 启动器: 由 MainActivity 调用, 在后台线程把 app.py 当主程序跑。

app.py 假定自己运行在"代码目录可写 + 家目录存在"的桌面环境:
  - DATA_DIR 支持 MIRRORSORTER_DATA_DIR 环境变量覆盖 → 指到应用私有目录
  - web 静态资源按 APP_DIR/web 相对查找 → 启动前由 Java 侧把 assets 解到 filesDir/app
  - webbrowser/taskkill 等桌面专属调用 → 这里打补丁或交给线程内异常吞掉
"""
import os
import runpy
import sys
import threading


def start(app_dir, data_dir):
    os.environ["MIRRORSORTER_DATA_DIR"] = data_dir
    os.environ.setdefault("HOME", data_dir)

    sys.argv = ["app.py"]   # 不带 --auto-open, app.py 就不会尝试 webbrowser.open
    if getattr(sys, "frozen", False):
        del sys.frozen      # 防 APP_DIR 误取 sys.executable 目录

    try:
        import webbrowser
        webbrowser.open = lambda *a, **k: False
    except Exception:
        pass

    threading.Thread(target=_serve, args=(app_dir,), daemon=True).start()


def _serve(app_dir):
    os.chdir(app_dir)
    try:
        runpy.run_path(os.path.join(app_dir, "app.py"), run_name="__main__")
    except SystemExit:
        pass   # app.py 端口被占等场景会 sys.exit, 壳进程不受影响
    except BaseException:
        import traceback
        traceback.print_exc()
