# -*- coding: utf-8 -*-
"""内置浏览器标签页的 WebView2 控件池(仅供 app_window.py 窗口壳使用)。

每个标签页一个独立的 WebView2 控件(共享同一浏览器进程与用户数据目录 → 登录态
互通), 切换标签 = 控件可见性切换 —— 页面状态原样保留: 首页永不重载、视频切走
再切回继续播。关闭标签 = 控件 Dispose。

所有控件操作都在 UI 线程执行(内部经 form.BeginInvoke marshal), 管理器方法可从
任意线程调用。pythonnet 注意: 控件必须在 UI 线程创建(跨线程建父子会抛
ArgumentException); Anchor 必须显式四边(主控件 Dock=Fill 时其 Anchor 值不可拷贝)。
"""
import json
import os
import threading
import time

import app   # DATA_DIR(日志/用户脚本目录); 由 app_window 一并加载, 无循环依赖

NL = "\n"   # _dbg_log 落盘换行(历史上曾漏定义 → NameError 被吞, shell_debug.log 一直是空的)


# 自愈式智能深色: 脚本在页面内常驻定时器, 每1.2s重测背景亮度 ——
# 页面已深色(原生夜间/PreferredColorScheme生效) → 保持不动;
# 浅色页面(贴吧等不跟随系统的站) → 反色+色相旋转兜底(媒体类反转回原色);
# 主题晚于首次测量才应用(SPA懒应用)也会被自动追上; 白天模式由 mode 变量直接关断
# 统一主题脚本(自愈: 页面内定时器重测/重设):
# - B站: 用它自己的原生深色主题(cookie theme_style=dark, 官方顶栏开关写的就是这个),
#   效果是真正的原生深色; 白天模式清除该 cookie
# - 其他站点: 测量背景亮度, 浅色页面反色兜底(媒体类元素反转回原色), 已深色不动
# 页面诊断钩子: JS异常/资源加载失败经 window.open(msx-debug:...) 回传(不产生真实新窗)
_DEBUG_HOOK_JS = r"""
(function(){
  if (window.__msx_dbg) return;
  window.__msx_dbg = 1;
  function rep(s){
    try { window.open('msx-debug:' + String(s).slice(0, 220), '_blank'); } catch (e) {}
  }
  window.addEventListener('error', function(e){
    if (e.target && e.target !== window && (e.target.src || e.target.href)) {
      rep('RESFAIL ' + (e.target.tagName || '') + ' ' + String(e.target.src || e.target.href).slice(0, 150));
    } else {
      rep('JSERR ' + (e.message || '') + ' @' + String(e.filename || '').slice(-40) + ':' + (e.lineno || 0));
    }
  }, true);
  window.addEventListener('unhandledrejection', function(e){
    rep('REJ ' + String(e.reason).slice(0, 150));
  }, true);
  var of = fetch;
  window.fetch = function(){
    var args = arguments, url = String((args[0] && args[0].url) || args[0] || '');
    return of.apply(this, arguments).then(function(r){
      if (!r.ok) rep('FETCH ' + r.status + ' ' + url.slice(0, 140));
      return r;
    }, function(e){
      rep('FETCHFAIL ' + url.slice(0, 140) + ' ' + (e && e.message ? e.message : ''));
      throw e;
    });
  };
  var ox = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(m, u){
    this.__msx_u = String(u);
    return ox.apply(this, arguments);
  };
  var osend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(){
    var xhr = this, u = this.__msx_u || '';
    this.addEventListener('error', function(){ rep('XHRFAIL ' + u.slice(0, 140)); });
    this.addEventListener('load', function(){ if (xhr.status >= 400) rep('XHR' + xhr.status + ' ' + u.slice(0, 140)); });
    return osend.apply(this, arguments);
  };
})();
"""

_THEME_DARK_JS = r"""
window.__msx_theme_mode = 'dark';
(function(){
  function lum(rgb){
    var m = String(rgb).match(/[\d.]+/g);
    if (!m) return 255;
    if (m.length > 3 && parseFloat(m[3]) === 0) return -1;
    return 0.299*m[0] + 0.587*m[1] + 0.114*m[2];
  }
  function report(s){
    try { fetch("/api/shell_debug?s=" + encodeURIComponent(s), {cache:"no-store"}); } catch(e) {}
  }
  function cleanup(){
    var o = document.getElementById('__msx_dark');
    if (o) o.remove();
    document.documentElement.style.removeProperty('filter');
  }
  function tick(){
    if (window.__msx_theme_mode !== 'dark') { cleanup(); return; }
    var isBili = /(^|\.)bilibili\.com$/.test(location.hostname);
    if (isBili) {
      if (document.cookie.indexOf('theme_style=dark') === -1) {
        document.cookie = 'theme_style=dark; path=/; domain=.bilibili.com; max-age=31536000';
        report('bili-cookie-set');
        var n = +(sessionStorage.getItem('__msx_rl') || 0);
        if (n < 2) { sessionStorage.setItem('__msx_rl', String(n + 1)); location.reload(); return; }
      }
      cleanup();   // B站原生深色, 不做反色
      return;
    }
    var b = lum(getComputedStyle(document.body).backgroundColor);
    var h = lum(getComputedStyle(document.documentElement).backgroundColor);
    var base = (b >= 0 ? b : (h >= 0 ? h : 255));
    var o = document.getElementById('__msx_dark');
    if (base < 100) {
      if (o) o.remove();
      document.documentElement.style.removeProperty('filter');
    } else if (!o) {
      var st = document.createElement('style');
      st.id = '__msx_dark';
      st.textContent = 'html{filter:invert(0.90) hue-rotate(180deg);background:#fff}'
        + 'img,video,picture,canvas,svg,iframe,embed,object'
        + '{filter:invert(1) hue-rotate(180deg)}';
      (document.head || document.documentElement).appendChild(st);
      report('invert-injected ' + location.hostname);
    }
  }
  if (!window.__msx_theme_timer) window.__msx_theme_timer = setInterval(tick, 1500);
  tick();
})()
"""
_LIGHT_JS = r"""
window.__msx_theme_mode = 'light';
(function(){
  var old = document.getElementById('__msx_dark');
  if (old) old.remove();
  document.documentElement.style.removeProperty('filter');
  if (/(^|\.)bilibili\.com$/.test(location.hostname) &&
      document.cookie.indexOf('theme_style=dark') !== -1) {
    document.cookie = 'theme_style=; path=/; domain=.bilibili.com; max-age=0';
    var n = +(sessionStorage.getItem('__msx_rl') || 0);
    if (n < 2) { sessionStorage.setItem('__msx_rl', String(n + 1)); location.reload(); }
  }
})()
"""


# ---- 内置油猴脚本环境: Vue + GM 垫片 + 净化大师 + 用户自定义脚本 ----
# 通道: AddScriptToExecuteOnDocumentCreatedAsync(每次文档创建自动执行, MAIN 上下文,
# 等价于 document-start 的油猴脚本)。扩展 API(AddBrowserExtensionAsync)在部分
# 环境返回"不支持"(0x80070032), 因此不依赖它。
def _dbg_log(msg):
    """壳诊断日志(数据目录/shell_debug.log): 无控制台环境下排查注入链路。"""
    try:
        import time as _t
        with open(os.path.join(app.DATA_DIR, "shell_debug.log"), "a",
                  encoding="utf-8") as f:
            print("[shell] " + msg, flush=True)
            # (历史笔误: 曾写成 "[" + NL + "] %s" % (t, msg) —— 一个 %s 配二元组,
            #  每次都 TypeError 被吞, 日志文件永远是空的, 只有 stdout 有)
            f.write("[%s] %s%s" % (_t.strftime("%H:%M:%S"), msg, NL))
    except Exception:
        pass


def _read_ext_file(name):
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.join(getattr(sys, "_MEIPASS", "") or "", "extensions"),
              os.path.join(here, "extensions")):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            try:
                data = open(p, encoding="utf-8").read()
                _dbg_log("read %s from %s (%d bytes)" % (name, d, len(data)))
                return data
            except Exception as e:
                _dbg_log("read %s FAILED: %r" % (name, e))
                return ""
    _dbg_log("read %s NOT FOUND (here=%s meipass=%s)"
             % (name, here, getattr(sys, "_MEIPASS", None)))
    return ""


def _user_scripts():
    """用户自定义油猴脚本: 数据目录 userscripts/*.user.js, 丢进去即生效。"""
    try:
        d = os.path.join(app.DATA_DIR, "userscripts")
        out = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".user.js", ".js")):
                    try:
                        out.append(open(os.path.join(d, fn), encoding="utf-8").read())
                    except Exception:
                        pass
        return out
    except Exception:
        return []


def _read_ext_bytes(name):
    """extensions/ 下二进制资源(Framegen 权重 .bin)。找不到返回空 bytes。"""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.join(getattr(sys, "_MEIPASS", "") or "", "extensions"),
              os.path.join(here, "extensions")):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    return f.read()
            except Exception:
                return b""
    return b""


# ---- AI补帧(Framegen, github.com/MONZikWasTaken/Framegen) ----
# 原项目是 Chrome 扩展: 内容脚本依赖 chrome.runtime.getURL(取权重/运行时)与
# chrome.storage.local(设置持久化)。我们的注入跑在页面主世界 —— 引导垫片把
# 权重/运行时以 base64 内嵌转 Blob URL 供其 fetch/import(动态 import Blob 模块
# 与扩展资源行为一致), chrome.storage 落到页面 localStorage(键 fc_*, 浮窗面板
# 的调整跨刷新保留)。代码 MIT; 模型权重仅限个人/非商用(见 framegen-NOTICE.txt)。
_FG_ASSETS = (
    ("rt/rt.js", "framegen-rt.js", "text/javascript"),
    ("rt/sr.js", "framegen-sr.js", "text/javascript"),
    ("assets/rt_v7s.bin", "framegen-weights-v7s.bin", "application/octet-stream"),
    ("assets/rt_v7s.json", "framegen-weights-v7s.json", "application/json"),
    ("assets/rt_sr.bin", "framegen-weights-sr.bin", "application/octet-stream"),
    ("assets/rt_sr.json", "framegen-weights-sr.json", "application/json"),
)
_FG_B64_CACHE = {}   # 文件名 -> base64(进程级缓存: 每个新标签页都重建注入脚本, 别反复编4MB)

_FG_BOOT_JS = r"""
(function () {
  "use strict";
  if (window.__msxFgBoot) return;
  window.__msxFgBoot = 1;
  var SRC = __MSX_FG_SRC__;
  var RES = {};
  try {
    for (var k in SRC) {
      var raw = atob(SRC[k].b64), u8 = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
      RES[k] = URL.createObjectURL(new Blob([u8], { type: SRC[k].mime }));
    }
    var C = window.chrome || (window.chrome = {});
    if (!C.runtime) C.runtime = {
      id: "msx-framegen",
      getURL: function (p) { return RES[p] || p; },
      sendMessage: function (_m, cb) { if (cb) cb({ ok: false }); },
      openOptionsPage: function () {},
      onMessage: { addListener: function () {} },
    };
    if (!C.storage) C.storage = {
      local: {
        get: function (defs, cb) {
          var out = {};
          try {
            for (var k2 in defs) {
              var raw2 = localStorage.getItem("fc_" + k2);
              out[k2] = raw2 === null ? defs[k2] : JSON.parse(raw2);
            }
            cb(out);
          } catch (e) { try { cb(defs); } catch (_) {} }
        },
        set: function (obj, cb) {
          try { for (var k3 in obj) localStorage.setItem("fc_" + k3, JSON.stringify(obj[k3])); }
          catch (e) {}
          if (cb) cb();
        },
      },
      onChanged: { addListener: function () {} },
    };
  } catch (e) { console.warn("[msx] framegen boot failed", e); }
})();
"""


def _framegen_boot_js():
    """组装 Framegen 引导垫片(资源 base64 表)。任一资源缺失返回空串(禁用)。"""
    import base64
    import json as _json
    src = {}
    for key, fname, mime in _FG_ASSETS:
        if fname not in _FG_B64_CACHE:
            data = _read_ext_bytes(fname)
            if not data:
                _dbg_log("framegen asset missing: %s" % fname)
                return ""
            _FG_B64_CACHE[fname] = base64.b64encode(data).decode("ascii")
        src[key] = {"mime": mime, "b64": _FG_B64_CACHE[fname]}
    return _FG_BOOT_JS.replace("__MSX_FG_SRC__", _json.dumps(src))


def _glob_match(pattern, host):
    """简化 @match 通配: *://*.bilibili.com/* 的 host 段 *.bilibili.com 含主域"""
    try:
        hostpat = pattern.split("://", 1)[-1].split("/", 1)[0]
        if hostpat.startswith("*."):
            dome = hostpat[2:]
            return host == dome or host.endswith("." + dome)
        return host == hostpat
    except Exception:
        return True


def _build_inject_script(port):
    """组装文档创建脚本: Vue + GM垫片 + 内置净化大师 + 用户脚本。"""
    vue = _read_ext_file("vue.global.prod.js")
    shim = _read_ext_file("gm-shim.js")
    # 净化大师默认禁用(其脚本环境曾干扰B站页面图片/评论加载, 用户反馈后移除);
    # 需要时设环境变量 MSX_CLEANER=1 重新启用
    builtin = _read_ext_file("bilibili-cleaner.user.js") if os.environ.get("MSX_CLEANER") == "1" else ""
    if not shim:
        _dbg_log("build_inject: NO SHIM -> inject disabled")
        return ""
    parts = [
        _DEBUG_HOOK_JS,
        "window.__MSX_PORT__ = %d;" % port,
        'try { fetch("http://127.0.0.1:" + window.__MSX_PORT__ +'
        ' "/api/shell_debug?s=userscript-env-injected", {mode: "no-cors"}); } catch (e) {}',
    ]
    # Vue/GM垫片只为净化大师服务 —— 它们会污染页面全局(曾致 B 站页面 Vue.use
    # 崩溃 → 评论/懒加载图片全挂), 净化大师禁用时绝不注入
    if builtin:
        if vue:
            parts.append(vue)   # var Vue = ...(全局, 供净化大师使用)
        if shim:
            parts.append(shim)
    if builtin:
        parts.append(
            'if ((function(){var h=location.hostname; return h==="bilibili.com"||'
            'h.endsWith(".bilibili.com")})()) {\n' + builtin + '\n}')
    bsb = "" if os.environ.get("MSX_NO_INJECT") else _read_ext_file("bilibili-sponsorblock.js")
    if bsb:
        parts.append('try {\n' + bsb + '\n} catch (e) { console.warn("[msx] sponsorblock", e); }')
    bq = _read_ext_file("bilibili-enhance.user.js")
    if bq:
        # 画质增强(锐化+色彩滤镜): 无 Vue/GM 依赖, 纯 CSS/SVG, 开关与档位走服务端设置
        bq_on = "0" if app.sget("ui_bq") == "0" else "1"
        bq_level = (app.sget("ui_bq_level") or "中").strip()[:6]
        if "<" in bq_level or '"' in bq_level:
            bq_level = "中"   # 白名单化: 该值会被拼进页面脚本
        parts.append('window.__MSX_BQ__ = %s; window.__MSX_BQ_LEVEL__ = "%s";\ntry {\n'
                     % (bq_on, bq_level) + bq + '\n} catch (e) { console.warn("[msx] quality", e); }')
    # 播放页自有UI(评论区/右侧推荐替换为自建面板): 开关 ui_ownui(默认开), 环境变量
    # MSX_NO_OWNUI=1 可整体停用。脚本自身按 hostname+路径门控, 只在 /video/ 激活;
    # 播放器 DOM 一律不碰 —— 超分管线红线。
    ownui = "" if os.environ.get("MSX_NO_OWNUI") else _read_ext_file("bilibili-ownui.user.js")
    if ownui:
        ownui_on = "0" if app.sget("ui_ownui") == "0" else "1"
        parts.append('window.__MSX_OWNUI__ = %s;\ntry {\n' % ownui_on + ownui
                     + '\n} catch (e) { console.warn("[msx] ownui", e); }')
    # 内置「最高画质」脚本已下线: 用户明确说"让播放器用上账号本就有权的最高档没有意义"
    # (extensions/bilibili-quality.user.js 保留备查, 不再注入)
    # ACNet(Anime4KCPP f8b4) WebGL2 多趟超分: 上游 GLSL 原文当字符串注入, 运行器在
    # extensions/acnet-webgl.js。**默认开**(由超分门控决定何时真正启用: need>1.2 且输出≤4.2Mpx);
    # 需要关掉时设环境变量 MSX_ACNET=0。
    if os.environ.get("MSX_ACNET") != "0":
        _acnet_js = _read_ext_file("acnet-webgl.js")
        _acnet_glsl = _read_ext_file(os.path.join("_acnet", "acnet_f8b4.glsl"))
        if _acnet_js and _acnet_glsl:
            parts.append('window.__MSX_ACNET__ = 1;\nwindow.__MSX_ACNET_GLSL__ = %s;\n'
                         % json.dumps(_acnet_glsl))
            if os.environ.get("MSX_ACNET_DEBUG"):
                parts.append('window.__MSX_ACNET_DEBUG__ = %s;\n'
                             % json.dumps(os.environ["MSX_ACNET_DEBUG"]))
            if os.environ.get("MSX_ACNET_MAXPASS"):
                parts.append('window.__MSX_ACNET_MAXPASS__ = %s;\n'
                             % json.dumps(os.environ["MSX_ACNET_MAXPASS"]))
            if os.environ.get("MSX_ACNET_WANTW"):
                parts.append('window.__MSX_ACNET_WANTW__ = %s;\n'
                             % json.dumps(os.environ["MSX_ACNET_WANTW"]))
            if os.environ.get("MSX_ACNET_IWMAX"):
                parts.append('window.__MSX_ACNET_IWMAX__ = %s;\n'
                             % json.dumps(os.environ["MSX_ACNET_IWMAX"]))
            parts.append('try {\n' + _acnet_js + '\n} catch (e) { console.warn("[msx] acnet", e); }')
            _dbg_log("acnet: injected glsl %d chars + runner %d chars"
                     % (len(_acnet_glsl), len(_acnet_js)))
    # AI补帧(Framegen)暂时下线(易出bug: document-start注入曾致渲染进程崩溃, 已改
    # DOMContentLoaded, 但保守起见整体停用)。注入代码保留, 需要>0重新启用:
    # 设环境变量 MSX_FG=1 并把设置键 ui_fg 置 1。
    fg_on = (os.environ.get("MSX_FG") == "1" and app.sget("ui_fg") == "1"
             and not os.environ.get("MSX_NO_FG"))
    sr_on = app.sget("ui_sr") == "1"
    if fg_on and sr_on:
        # 冲突兜底: 补帧与超分都要用画布接管视频画面, GPU 负担也翻倍 —— 只跑补帧
        # (设置页已做互斥, 这里防旧存档/环境变量组合出双开)
        sr_on = False
        _dbg_log("ui_fg + ui_sr both on -> sr suppressed")
    if sr_on:
        # AI超分(Anime4K WebGPU 实时超分): 库 3.4MB, 只在 B站页注入; 开关 ui_sr, 默认关
        sr_lib = _read_ext_file("anime4k-webgpu.js")
        sr_glue = _read_ext_file("bilibili-sr.user.js")
        if sr_lib and sr_glue:
            # 档位: 轻/标准/高/旗舰(默认高=CNNx2UL, 与旧版一致) —— 白名单化后拼进页面脚本
            sr_level = (app.sget("ui_sr_level") or "高").strip()[:4]
            if sr_level not in ("轻", "标准", "高", "旗舰"):
                sr_level = "高"
            # 超分分辨率倍率: 1 / 1.25 / 1.5 / 2 (白名单, 会被拼进页面脚本)。
            # 默认必须与设置页 UI 的默认(web/app.js 读档回显 `s.ui_sr_scale || "2"`)一致 ——
            # 旧壳默认 1.25 而 UI 显示 2, 用户看到的和实际跑的不一样。它决定网络输入档位
            # (档位越高越清晰越吃显卡, 无自动升降); 显卡吃不下时帧节奏门隔帧跑网络兜底。
            sr_scale = (app.sget("ui_sr_scale") or "2").strip()[:4]
            if os.environ.get("MSX_SR_SCALE"):        # 测试用覆盖
                sr_scale = os.environ["MSX_SR_SCALE"].strip()[:4]
            if sr_scale not in ("1", "1.25", "1.5", "2"):
                sr_scale = "2"
            # MSX_SR_DEBUG=1: 周期性回报超分状态(画布是否创建/原视频是否隐藏), 排障用
            sr_probe = ""
            if os.environ.get("MSX_SR_DEBUG"):
                # 排障探针: 回报超分画布状态 + 源/显示分辨率(1080p 是否进门控) + 弹幕层是否在
                # 画布之上(逐层读 computed z-index, 只要有一层 <= 画布层级就算不合格)
                sr_probe = ('var __srN = 0;\n'
                            'var __srT = setInterval(function () {\n'
                            '  try {\n'
                            '    var c = document.querySelector("canvas.msx-sr-canvas");\n'
                            '    var v = document.querySelector("#bilibili-player video, .bpx-player-container video");\n'
                            '    var zc = c ? (parseInt(getComputedStyle(c).zIndex, 10) || 0) : null;\n'
                            '    if (!window.__srTick) { window.__srTick = { f: 0, lt: Date.now() };\n'
                            '      (function l(){ window.__srTick.f++; requestAnimationFrame(l); })(); }\n'
                            '    var tk = window.__srTick, nw = Date.now();\n'
                            '    var fps = Math.round((tk.f - (tk.lf || 0)) * 1000 / Math.max(1, nw - tk.lt) * 10) / 10;\n'
                            '    tk.lf = tk.f; tk.lt = nw;\n'
                            '    var dq = -1, tq = -1;\n'
                            '    try { var q = v.getVideoPlaybackQuality();\n'
                            '      dq = q.droppedVideoFrames - (window.__srD0 || 0);\n'
                            '      tq = q.totalVideoFrames - (window.__srT0 || 0);\n'
                            '      window.__srD0 = q.droppedVideoFrames; window.__srT0 = q.totalVideoFrames; } catch (e) {}\n'
                            '    var dm = document.querySelectorAll(".bpx-player-row-dm-wrap,'
                            ' .bpx-player-bas-dm-wrap, .bpx-player-adv-dm-wrap,'
                            ' .bpx-player-cmd-dm-wrap, .bpx-player-dm-mask-wrap, .bili-danmaku-x-dm");\n'
                            '    var dmz = [], dmAbove = true;\n'
                            '    for (var i = 0; i < dm.length && i < 40; i++) {\n'
                            '      var z = parseInt(getComputedStyle(dm[i]).zIndex, 10);\n'
                            '      dmz.push(isNaN(z) ? "auto" : z);\n'
                            '      if (zc !== null && (isNaN(z) || z <= zc)) dmAbove = false;\n'
                            '    }\n'
                            '    window.open("msx-debug:SRPROBE" + __srN + " canvas=" + (c ? c.width + "x" + c.height : "none") +\n'
                            '      " videoHidden=" + !!(v && /msx-sr-hide/.test(v.className)) +\n'
                            '      " level=" + window.__MSX_SR_LEVEL__ +\n'
                            '      " src=" + (v && v.videoWidth ? v.videoWidth + "x" + v.videoHeight : "none") +\n'
                            '      " dispW=" + (v ? Math.round(v.getBoundingClientRect().width) : 0) +\n'
                            '      " dpr=" + (window.devicePixelRatio || 1) +\n'
                            '      " canvasZ=" + zc + " fps=" + fps +\n'
                            '      " dropPer5s=" + dq + "/" + tq +\n'
                            '      " mode=" + (window.__msxSrMode || "none") +\n'
                            '      " builds=" + (window.__msxSrBuilds || 0) +\n'
                            '      " dmCount=" + dm.length + " dmAboveCanvas=" + dmAbove +\n'
                            '      " dmZ=" + dmz.join("/"), "_blank");\n'
                            '  } catch (e) {}\n'
                            '  if (++__srN >= 12) clearInterval(__srT);\n'
                            '}, 5000);\n')
            parts.append(
                'if ((function(){var h=location.hostname; return h==="bilibili.com"||'
                'h.endsWith(".bilibili.com")})()) {\nwindow.__MSX_SR__ = 1;\n'
                'window.__MSX_SR_LEVEL__ = "%s";\n' % sr_level
                + 'window.__MSX_SR_SCALE__ = %s;\n' % sr_scale
                + ('window.__MSX_SR_FORCE_DISP__ = %s;\n' % os.environ["MSX_SR_FORCE_DISP"]
                   if os.environ.get("MSX_SR_FORCE_DISP") else "")
                + ('window.__MSX_SR_BYPASS_GATE__ = 1;\n' if os.environ.get("MSX_SR_BYPASS_GATE") else "")
                + ('window.__MSX_SR_FORCE_UP2X__ = 1;\n' if os.environ.get("MSX_SR_FORCE_UP2X") else "")
                + ('window.__MSX_SR_FORCE_K1__ = 1;\n' if os.environ.get("MSX_SR_FORCE_K1") else "")
                + ('window.__MSX_SR_TRY_FSR__ = 1;\n' if os.environ.get("MSX_SR_TRY_FSR") else "")
                + sr_lib + '\n' + sr_glue + '\n' + sr_probe + '\n}')
    if fg_on:
        # AI补帧(Framegen): 运行时+权重(~4MB base64)只在 B站页注入, 默认关
        fg_boot = _framegen_boot_js()
        cad = _read_ext_file("framegen-cadence.js")
        prof = _read_ext_file("framegen-profiles.js")
        cont = _read_ext_file("framegen-content.js")
        if fg_boot and cad and prof and cont:
            n = len(fg_boot) + len(cad) + len(prof) + len(cont)
            # MSX_FG_STUB=1: 照常注入全部资源(体积一致)但让 content.js 立即退出 ——
            # 区分"注入体积/IPC 问题"与"Framegen 运行时问题"的调试钩子
            stub = "window.__framegen = 1;\n" if os.environ.get("MSX_FG_STUB") else ""
            if os.environ.get("MSX_FG_DEBUG"):
                # 调试钩子: 预置 localStorage( Framegen 的 chrome.storage 垫片从这里读),
                # 打开它的调试 HUD, 验证引擎是否真的在跑(画面上应出现性能面板)
                stub += ('try { localStorage.setItem("fc_debug", "true");'
                         ' localStorage.setItem("fc_showFps", "true"); } catch (e) {}\n')
                # 25s后回报引擎状态: 视频存在/覆盖画布(overlay canvas)是否已创建/FG浮层是否就位
                stub += ('setTimeout(function () {\n'
                         '  try {\n'
                         '    var v = document.querySelector("#bilibili-player video, .bpx-player-container video");\n'
                         '    var sib = v && v.previousElementSibling;\n'
                         '    var any = v && v.parentElement && v.parentElement.querySelector("canvas");\n'
                         '    var msg = "FGPROBE video=" + !!v + " webgpu=" + !!navigator.gpu +\n'
                         '      " fg=" + !!window.__framegen +\n'
                         '      " sibCanvas=" + !!(sib && sib.tagName === "CANVAS") +\n'
                         '      " anyCanvas=" + !!any +\n'
                         '      " btn=" + !!document.querySelector(".fc-controls-root, .fc-panel");\n'
                         '    window.open("msx-debug:" + msg, "_blank");\n'
                         '  } catch (e) {}\n'
                         '}, 25000);\n')
            # Framegen 原版走 Chrome 扩展的 document_idle; 我们的通道是 document-created
            # (早于 DOM 解析), 实测 document-start 就跑它的引导/观察器会触发渲染进程
            # RESULT_CODE_KILLED_BAD_MESSAGE —— 垫片+引擎推迟到 DOMContentLoaded 执行
            parts.append(
                'if ((function(){var h=location.hostname; return h==="bilibili.com"||'
                'h.endsWith(".bilibili.com")})()) {(function(){\n'
                'function __msxFgRun() {\nwindow.__MSX_FG__ = 1;\n' + stub +
                fg_boot + '\n;\n' + cad + '\n;\n' + prof + '\n;\n' + cont + '\n}\n'
                'try { if (document.readyState === "loading")\n'
                '  document.addEventListener("DOMContentLoaded", __msxFgRun, { once: true });\n'
                'else __msxFgRun(); } catch (e) { console.warn("[msx] framegen", e); }\n'
                # Framegen 原设计要手动点浮层按钮才开始补帧 —— 桌面端开关(ui_fg)开着就
                # 自动点火: 检测到"播放中的视频 + 按钮未激活"时替用户点一次(每文档一次)
                '(function () {\n'
                '  var tries = 0;\n'
                '  var t = setInterval(function () {\n'
                '    tries++;\n'
                '    var b = document.querySelector(\'button[aria-label="Toggle Framegen"]\');\n'
                '    var v = document.querySelector("#bilibili-player video, .bpx-player-container video");\n'
                '    if (b && v && !v.paused && b.dataset.active !== "true") {\n'
                '      try { b.click(); } catch (e) {}\n'
                '      clearInterval(t);\n'
                '    }\n'
                '    if (tries > 60) clearInterval(t);\n'
                '  }, 1000);\n'
                '})();\n'
                '})();}')
            _dbg_log("framegen inject assembled (%d chars)" % n)
        else:
            _dbg_log("framegen inject SKIPPED (asset files missing)")
    if os.environ.get("MSX_TEST_PLAYBACK"):
        # 对照测试钩子: MSX_TEST_PLAYBACK="定位秒,暂停秒" (如 "120,126") ——
        # B站页自动: 跳到定位秒 → 静音播放 → 到暂停秒暂停 → 点「网页全屏」。
        # 全关/开超分两组对照都走同一时间点, 截图才有可比性。
        try:
            _t0, _t1 = [float(x) for x in os.environ["MSX_TEST_PLAYBACK"].split(",")[:2]]
            parts.append(
                'if ((function(){var h=location.hostname; return h==="bilibili.com"||'
                'h.endsWith(".bilibili.com")})()) {(function(){\n'
                'var tries = 0;\n'
                '(function go(){\n'
                '  var v = document.querySelector("#bilibili-player video");\n'
                '  if (!v || !v.videoWidth) { if (++tries < 90) setTimeout(go, 500); return; }\n'
                '  v.muted = true;\n'
                '  try { v.currentTime = %d; } catch (e) {}\n'
                '  v.play();\n'
                '  var iv = setInterval(function () {\n'
                '    if (v.currentTime >= %d) {\n'
                '      v.pause(); clearInterval(iv);\n'
                '      var b = document.querySelector(".bpx-player-ctrl-web");\n'
                '      if (b) b.click();\n'
                '    }\n'
                '  }, 150);\n'
                '})();\n'
                '})();}' % (_t0, _t1))
            _dbg_log("test playback hook: seek=%s pause=%s" % (_t0, _t1))
        except Exception:
            _dbg_log("MSX_TEST_PLAYBACK parse failed")
    for us in _user_scripts():
        parts.append('try { (function(){\n' + us + '\n})(); }'
                     ' catch (e) { console.warn("[msx] userscript", e); }')
    return "\n;\n".join(parts)


def create_pool(win, on_title, on_newtab, theme_getter=None, port=8000, on_nav=None):
    """创建控件池。须在窗口壳启动后调用(任意线程, 内部自行 marshal)。

    on_title(tab_key, title) / on_newtab(tab_key, url): 站点控件的
    DocumentTitleChanged / NewWindowRequested(UI 线程)回调。
    on_nav(tab_key, url, can_back, can_fwd): 导航状态变化回调 —— 历史可用态/URL
    变化(含首页标签)都经这里推给 ShellApi → 导航工具栏; 非激活标签的推送由
    ShellApi 侧忽略。控件未就绪/标签已关时不上报。
    返回管理器: init/create/show/hide/dispose/get_nav/push_nav/reload/goto/
    navigate_history —— 均以 tab_key 为键(key 0 = 信息流主控件); 控件对象由池内部持有。
    """
    import System.Windows.Forms as WinForms  # noqa: F401  提前加载 CLR(pythonnet)
    box = {"form": None, "theme": None, "stopping": False, "port": port,
           "on_nav": on_nav}

    def _ui(fn):
        """marshal 到 UI 线程(窗体未绑定时直接执行, 供测试注入)。"""
        form = box.get("form")
        if form is None:
            fn()
            return

        def go():
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
        try:
            if form.InvokeRequired:
                form.BeginInvoke(WinForms.MethodInvoker(go))
            else:
                go()
        except Exception:
            import traceback
            traceback.print_exc()

    class _Pool:
        ctrls = {0: None}   # tab_key -> WebView2 控件(0 = 信息流主控件)
        _bili_reloaded = {}  # id(ctrl) -> 上次重载时的主题(B站深色cookie生效防循环)
        _dead = set()        # 已关闭标签的key: 迟到的create直接丢弃(防僵尸控件)

        def _is_dark(self):
            try:
                return (theme_getter() or "dark") != "light"
            except Exception:
                return True

        def _apply_theme(self, c, dark):
            """给站点页面注入/移除自动深色(反色+色相旋转, 媒体类元素反转回原色)。"""
            def go():
                try:
                    core = c.CoreWebView2
                    if core is None:
                        return
                    try:   # 站点自身支持 prefers-color-scheme 的跟随(如已适配暗色的站点)
                        from Microsoft.Web.WebView2.Core import CoreWebView2PreferredColorScheme
                        core.Profile.PreferredColorScheme = CoreWebView2PreferredColorScheme(
                            2 if dark else 1)   # Dark/Light
                    except Exception:
                        pass
                    # ---- B站原生深色: 官方深色开关就是 theme_style cookie
                    #      (顶栏头像区的深浅切换写它), 用 CookieManager 直接设置 ----
                    try:
                        cm = core.CookieManager
                        want = "dark" if dark else ""
                        if want:
                            cm.AddOrUpdateCookie(cm.CreateCookie(
                                "theme_style", "dark", ".bilibili.com", "/"))
                        else:
                            cm.DeleteCookies("theme_style", "https://www.bilibili.com")
                        src = str(c.Source or "")
                        # 当前页在B站且该标签还没为此主题重载过 → 重载一次让原生深色生效
                        # (防循环: 同一标签同一主题只重载一次)
                        if ("bilibili.com" in src
                                and self._bili_reloaded.get(id(c)) != dark):
                            self._bili_reloaded[id(c)] = dark
                            _dbg_log("bili cookie set dark=%s -> reload" % dark)
                            core.Reload()
                    except Exception:
                        pass   # 关窗时序中控件可能刚好销毁, 静默即可
                    # ---- 其他站点: 智能反色兜底(自愈定时器在脚本内) ----
                    core.ExecuteScriptAsync(_THEME_DARK_JS if dark else _LIGHT_JS)
                except Exception:
                    import traceback
                    print("[theme] ERROR:", flush=True)
                    traceback.print_exc()
            _ui(go)

        def _theme_watch(self):
            """轮询主题(主页切黑夜/白天会实时写库) → 对所有站点页面重新应用。"""
            import time as _t
            while not box["stopping"]:
                try:
                    dark = self._is_dark()
                    if dark != box["theme"]:
                        box["theme"] = dark
                        for k, c in list(_Pool.ctrls.items()):
                            if k != 0 and c is not None:
                                self._apply_theme(c, dark)
                except Exception:
                    pass
                _t.sleep(3)

        def _wire_nav(self, c, tab_key):
            """给控件挂导航状态上报(HistoryChanged/SourceChanged → on_nav 回调)。
            控件 core 未就绪时等 CoreWebView2InitializationCompleted 再挂 ——
            主控件(0号)常在 gui_ready 时早已就绪, 走立即挂分支。"""
            if not box.get("on_nav"):
                return

            def emit(_s=None, _a=None):
                try:
                    if tab_key in _Pool._dead:   # 标签已关: 迟到事件丢弃
                        return
                    core = c.CoreWebView2
                    if core is None:
                        return
                    box["on_nav"](tab_key, str(c.Source or core.Source or ""),
                                  bool(core.CanGoBack), bool(core.CanGoForward))
                except Exception:
                    pass

            def on_core_ready(_s, _a):
                try:
                    core = c.CoreWebView2
                    core.HistoryChanged += emit
                    core.SourceChanged += emit
                except Exception:
                    _dbg_log("wire_nav(%s) subscribe FAILED: %r" % (tab_key, _s))

            try:
                if c.CoreWebView2 is not None:
                    on_core_ready(None, None)
                else:
                    c.CoreWebView2InitializationCompleted += on_core_ready
            except Exception as e:
                _dbg_log("wire_nav(%s) FAILED: %r" % (tab_key, e))

        def init(self):
            """等原生窗体就绪, 记录主控件, 打无边框缩放补丁(WS_THICKFRAME)。"""
            end = time.time() + 30
            form = None
            while time.time() < end:
                f = win.native
                if f is not None and f.IsHandleCreated:
                    form = f
                    break
                time.sleep(0.2)
            if form is None:
                raise RuntimeError("native window form not ready")
            box["form"] = form
            box["theme"] = self._is_dark()
            threading.Thread(target=self._theme_watch, daemon=True,
                             name="msx-theme-watch").start()

            def init_ui():
                try:
                    import ctypes
                    from System.Drawing import Color as _C
                    form.BackColor = _C.FromArgb(15, 17, 21)   # 控件缝隙不露窗体白底
                    u32 = ctypes.windll.user32
                    hwnd = form.Handle.ToInt32()
                    # 缩放走自建贴边热区(edge 面板), 不再用 THICKFRAME ——
                    # THICKFRAME 会让 DWM 在 Win10 上画一圈浅色边框线(不可调色), 白线的来源
                    try:   # 关掉 DWM 非客户区渲染
                        val = ctypes.c_int(2)   # DWMWA_NCRENDERING_POLICY = DISABLED
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, 2, ctypes.byref(val), ctypes.sizeof(val))
                    except Exception:
                        pass
                    try:   # Win10 给所有窗口画 1px 系统色边框(浅色主题下发白) → 强制深色边框
                        on = ctypes.c_int(1)   # TRUE
                        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE(新旧两个属性号)
                            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                hwnd, attr, ctypes.byref(on), ctypes.sizeof(on))
                    except Exception:
                        pass
                    _Pool.ctrls[0] = form.webview
                    # 信息流主控件也上报导航状态(地址栏显示/后退前进按钮启停)
                    self._wire_nav(form.webview, 0)
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(init_ui)

        def create(self, url, tab_key):
            """新标签页控件(异步初始化; 标题/新页事件回调 app_window)。"""

            def go():
                try:
                    if tab_key in _Pool._dead:   # 标签已关: 丢弃迟到的创建(否则造出不可见
                        _Pool._dead.discard(tab_key)   # 僵尸控件, 还会抢走键盘焦点)
                        return
                    from Microsoft.Web.WebView2.WinForms import (
                        WebView2, CoreWebView2CreationProperties)
                    from System.Drawing import Color, Point, Size
                    from System import Uri
                    form = box["form"]
                    home = self._ctrl(0)
                    cp = home.CreationProperties
                    props = CoreWebView2CreationProperties()
                    props.UserDataFolder = cp.UserDataFolder
                    props.AdditionalBrowserArguments = cp.AdditionalBrowserArguments
                    c = WebView2()
                    c.CreationProperties = props
                    try:
                        c.DefaultBackgroundColor = Color.FromArgb(255, 15, 17, 21)
                    except Exception:
                        pass
                    # 外部页签画面整体右移20px(逻辑): 左侧留空隙, 右缘与滚动条位置不变;
                    # Anchor 四边让窗口缩放时这个 inset 原样保持。home(信息流)不动。
                    dx = int(20 * float(getattr(form, "_scale", 1.0) or 1.0)) \
                        if tab_key != 0 else 0
                    c.Location = Point(home.Location.X + dx, home.Location.Y)
                    c.Size = Size(home.Size.Width - dx, home.Size.Height)
                    c.Anchor = (WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Bottom |
                                WinForms.AnchorStyles.Left | WinForms.AnchorStyles.Right)
                    c.Visible = False

                    def on_ready(_s, _a):
                        core = c.CoreWebView2
                        core.DocumentTitleChanged += lambda _s2, _a2: on_title(
                            tab_key, core.DocumentTitle)
                        try:   # 内置油猴环境: 净化大师 + 用户自定义脚本(每次文档创建自动执行)
                            _dbg_log("on_ready: registering document-created script")
                            inj = _build_inject_script(box.get("port") or 8000)
                            if inj:
                                core.AddScriptToExecuteOnDocumentCreatedAsync(inj)
                                _dbg_log("on_ready: AddScript called (%d chars)" % len(inj))
                            else:
                                _dbg_log("on_ready: empty inject script!")
                        except Exception as e:
                            _dbg_log("on_ready FAILED: %r" % e)

                        def on_newwin(_s2, a2):
                            a2.Handled = True
                            u = str(a2.get_Uri())
                            if u.startswith("msx-debug:"):
                                _dbg_log(u[len("msx-debug:"):])
                                return
                            on_newtab(tab_key, u)
                        core.NewWindowRequested += on_newwin

                        def on_nav_done(_s2, _a2):   # 每次整页加载完按当前主题补注入
                            self._apply_theme(c, self._is_dark())
                        core.NavigationCompleted += on_nav_done
                        self._apply_theme(c, self._is_dark())
                    c.CoreWebView2InitializationCompleted += on_ready
                    self._wire_nav(c, tab_key)   # 导航状态上报(地址栏/按钮启停)
                    form.Controls.Add(c)
                    _Pool.ctrls[tab_key] = c
                    c.EnsureCoreWebView2Async(None)
                    c.Source = Uri(url)
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(go)

        def _ctrl(self, key):
            c = _Pool.ctrls.get(key)
            if c is None and key == 0 and box.get("form") is not None:
                # 自愈: init_ui 若因时序失败, ctrls[0] 永久缺位 → show(0) 永远失活,
                # 关掉外部标签后信息流再也回不来(整窗黑屏)。
                try:
                    c = box["form"].webview
                    _Pool.ctrls[0] = c
                except Exception:
                    pass
            return c

        def show(self, key):
            def go():
                try:
                    c = self._ctrl(key)
                    if c is not None:
                        c.Visible = True
                        c.Focus()
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(go)

        def hide(self, key):
            def go():
                try:
                    c = self._ctrl(key)
                    if c is not None:
                        c.Visible = False
                except Exception:
                    pass
            _ui(go)

        def dispose(self, key):
            def go():
                try:
                    _Pool._dead.add(key)   # 迟到的create看到即弃
                    c = _Pool.ctrls.pop(key, None)
                    if c is not None:
                        c.Visible = False
                        box["form"].Controls.Remove(c)
                        c.Dispose()
                except Exception:
                    pass
            _ui(go)

        def navigate_history(self, key, back):
            """当前标签页内回退/前进(back=False)。执行了导航返回 True, 无历史返回 False。
            必须在 UI 线程调用(鼠标侧键过滤器就在 UI 线程) —— 跨线程时 BeginInvoke
            异步返回 False, 调用方会误判"无历史"。"""
            done = {"v": False}

            def go():
                try:
                    c = self._ctrl(key)
                    core = c.CoreWebView2 if c is not None else None
                    if core is None:
                        return
                    if back and core.CanGoBack:
                        core.GoBack()
                        done["v"] = True
                    elif not back and core.CanGoForward:
                        core.GoForward()
                        done["v"] = True
                except Exception:
                    pass
            _ui(go)
            return done["v"]

        def push_nav(self, key):
            """读当前控件导航状态并主动推一次 on_nav(切标签后补推, 防地址栏显示旧 URL)。"""
            if not box.get("on_nav"):
                return

            def go():
                try:
                    c = self._ctrl(key)
                    core = c.CoreWebView2 if c is not None else None
                    if core is None:
                        return
                    box["on_nav"](key, str(c.Source or ""),
                                  bool(core.CanGoBack), bool(core.CanGoForward))
                except Exception:
                    pass
            _ui(go)

        def reload(self, key):
            """当前标签页整页重载(⟳)。"""
            def go():
                try:
                    c = self._ctrl(key)
                    core = c.CoreWebView2 if c is not None else None
                    if core is not None:
                        core.Reload()
                except Exception:
                    pass
            _ui(go)

        def goto(self, key, url):
            """当前标签页内导航到 url(地址栏回车)。调用方已做 http(s) 白名单校验。"""
            def go():
                try:
                    from System import Uri
                    c = self._ctrl(key)
                    if c is not None:
                        c.Source = Uri(url)
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(go)

    return _Pool()
