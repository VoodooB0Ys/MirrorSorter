/* GM_* 兼容垫片 —— 让油猴脚本在无管理器的环境(WebView2 内置扩展)中运行
   实现: GM_get/set/deleteValue + listValues + 增删值监听(localStorage 序列化)
         GM_registerMenuCommand(收集后在页面右上角渲染入口按钮)
         unsafeWindow = window (world:MAIN 下即页面全局) */
(function () {
  "use strict";
  var PFX = "__msx_gm_";
  function rawGet(k) { try { return localStorage.getItem(PFX + k); } catch (e) { return null; } }
  function rawSet(k, v) { try { localStorage.setItem(PFX + k, v); } catch (e) {} }
  var listeners = {};   // key -> [{id, fn}]
  var lid = 0;
  window.GM_getValue = function (k, d) {
    var raw = rawGet(k);
    if (raw === null || raw === undefined) return d;
    try { return JSON.parse(raw); } catch (e) { return raw; }
  };
  window.GM_setValue = function (k, v) {
    rawSet(k, JSON.stringify(v));
    (listeners[k] || []).forEach(function (l) {
      try { l.fn(k, v, v); } catch (e) {}
    });
  };
  window.GM_deleteValue = function (k) { try { localStorage.removeItem(PFX + k); } catch (e) {} };
  window.GM_listValues = function () {
    var out = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key && key.indexOf(PFX) === 0) out.push(key.slice(PFX.length));
      }
    } catch (e) {}
    return out;
  };
  window.GM_addValueChangeListener = function (k, fn) {
    var id = ++lid;
    (listeners[k] = listeners[k] || []).push({ id: id, fn: fn });
    return id;
  };
  window.GM_removeValueChangeListener = function (k, id) {
    var arr = listeners[k] || [];
    var i = arr.findIndex(function (l) { return l.id === id; });
    if (i >= 0) arr.splice(i, 1);
  };
  window.unsafeWindow = window;
  var menus = [];
  window.GM_registerMenuCommand = function (name, fn) {
    menus.push({ name: String(name), fn: fn });
    render();
    return menus.length;
  };
  function render() {
    if (document.getElementById("__msx_gm_menu_btn") || !menus.length) return;
    var mount = function () {
      var b = document.createElement("div");
      b.id = "__msx_gm_menu_btn";
      b.textContent = "净化";
      b.title = "bilibili 页面净化大师 设置";
      b.style.cssText = "position:fixed;right:14px;bottom:64px;z-index:2147483646;"
        + "padding:7px 12px;border-radius:16px;background:rgba(22,26,36,.9);color:#fff;"
        + "font:12px/1 system-ui,'Microsoft YaHei',sans-serif;cursor:pointer;"
        + "box-shadow:0 4px 16px rgba(0,0,0,.35);user-select:none";
      b.addEventListener("click", function () {
        var old = document.getElementById("__msx_gm_menu");
        if (old) { old.remove(); return; }
        var m = document.createElement("div");
        m.id = "__msx_gm_menu";
        m.style.cssText = "position:fixed;right:14px;bottom:96px;z-index:2147483647;"
          + "background:#161920;color:#e8eaf0;border:1px solid #2a2e38;border-radius:10px;"
          + "padding:6px;box-shadow:0 8px 30px rgba(0,0,0,.5);min-width:180px";
        menus.forEach(function (mi) {
          var it = document.createElement("div");
          it.textContent = mi.name;
          it.style.cssText = "padding:8px 12px;border-radius:6px;cursor:pointer;font-size:13px";
          it.addEventListener("mouseenter", function () { it.style.background = "#22262f"; });
          it.addEventListener("mouseleave", function () { it.style.background = "none"; });
          it.addEventListener("click", function () { m.remove(); try { mi.fn(); } catch (e) {} });
          m.appendChild(it);
        });
        document.body.appendChild(m);
        setTimeout(function () {
          document.addEventListener("click", function once(ev) {
            if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", once); }
          });
        }, 0);
      });
      (document.body || document.documentElement).appendChild(b);
    };
    if (document.body) mount();
    else document.addEventListener("DOMContentLoaded", mount);
  }
})();
