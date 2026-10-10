/* bilibili 视频内置片段跳过(SponsorBlock 客户端, 数据源: bsbsb.top 社区)
   数据与 BilibiliSponsorBlock(小电视空降助手)社区共享 —— 用户在官方扩展/油猴版标注的
   恰饭/三连/片头片尾片段, 这里同样自动跳过。 */
(function () {
  "use strict";
  if (window.__msx_bsb) return;
  window.__msx_bsb = { segs: [], bv: "", enabled: localStorage.getItem("__msx_bsb_on") !== "0", skipped: 0 };

  var API = (function () {
    try { var o = localStorage.getItem("__msx_bsb_api"); if (o) return JSON.parse(o); } catch (e) {}
    return "https://bsbsb.top";
  })();
  var CATS = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview"];
  var CAT_NAME = { sponsor: "恰饭推广", selfpromo: "自我推广", interaction: "三连提醒",
                   intro: "片头", outro: "片尾", preview: "回顾/预告" };

  function bv() {
    var m = location.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/);
    return m ? m[1] : "";
  }
  function toast(msg) {
    try {
      var p = document.querySelector(".bpx-player-container") || document.body;
      var t = document.createElement("div");
      t.className = "__msx_bsb_toast";
      t.textContent = msg;
      t.style.cssText = "position:absolute;top:14px;left:50%;transform:translateX(-50%);"
        + "z-index:9999;background:rgba(0,0,0,.78);color:#fff;padding:8px 16px;"
        + "border-radius:8px;font:13px/1.4 system-ui,'Microsoft YaHei',sans-serif;"
        + "pointer-events:none;transition:opacity .4s";
      p.style.position = "relative";
      p.appendChild(t);
      setTimeout(function () { t.style.opacity = "0"; setTimeout(function () { t.remove(); }, 500); }, 2200);
    } catch (e) {}
  }
  function video() { return document.querySelector(".bpx-player-video-wrap video, video"); }

  function load() {
    var id = bv();
    if (!id || id === window.__msx_bsb.bv) return;
    window.__msx_bsb.bv = id;
    window.__msx_bsb.segs = [];
    window.__msx_bsb.skipped = 0;
    fetch(API + "/api/skipSegments?videoID=" + encodeURIComponent(id)
          + "&categories=" + encodeURIComponent(JSON.stringify(CATS)))
      .then(function (r) { return r.json(); })
      .then(function (list) {
        window.__msx_bsb.segs = (list || []).map(function (s) {
          return { a: s.segment[0], b: s.segment[1], cat: s.category, uuid: s.UUID };
        }).sort(function (x, y) { return x.a - y.a; });
        paint();
        if (window.__msx_bsb.segs.length) {
          toast("空降助手: 本视频有 " + window.__msx_bsb.segs.length + " 个可跳片段"
                + (window.__msx_bsb.enabled ? "（自动跳过已开启）" : "（点播放器右下角🧭开启）"));
        }
      })
      .catch(function () {});
  }
  function catColor(c) { return c === "sponsor" ? "#00d400" : c === "selfpromo" ? "#ffff34"
    : c === "interaction" ? "#cc00ff" : c === "intro" ? "#00ffff"
    : c === "outro" ? "#0202ef" : "#ff00ff"; }

  function paint() {
    try {
      var bar = document.querySelector(".bpx-player-progress");
      if (!bar) return;
      var old = bar.querySelectorAll(".__msx_bsb_mark");
      for (var i = 0; i < old.length; i++) old[i].remove();
      var v = video();
      if (!v || !v.duration || !window.__msx_bsb.segs.length) return;
      window.__msx_bsb.segs.forEach(function (s) {
        var d = document.createElement("div");
        d.className = "__msx_bsb_mark";
        d.title = "空降助手: " + (CAT_NAME[s.cat] || s.cat);
        d.style.cssText = "position:absolute;bottom:0;height:100%;opacity:.85;pointer-events:none;"
          + "left:" + (s.a / v.duration * 100) + "%;width:"
          + Math.max(0.4, (s.b - s.a) / v.duration * 100) + "%;background:" + catColor(s.cat);
        bar.appendChild(d);
      });
    } catch (e) {}
  }

  function onTime() {
    var v = video();
    if (!v || v.paused) return;
    var st = window.__msx_bsb;
    if (!st.enabled || !st.segs.length) return;
    for (var i = 0; i < st.segs.length; i++) {
      var s = st.segs[i];
      if (v.currentTime >= s.a - 0.25 && v.currentTime < s.b - 0.3) {
        v.currentTime = s.b + 0.05;
        st.skipped++;
        toast("已跳过: " + (CAT_NAME[s.cat] || s.cat) + " (" + Math.round(s.b - s.a) + "s)");
        break;
      }
    }
  }

  // 进度条片段着色 + 跳过开关入口
  function ui() {
    var bar = document.querySelector(".bpx-player-progress");
    if (bar && !bar.__msx_bsb_ui) {
      bar.__msx_bsb_ui = true;
      bar.style.position = "relative";
      var btn = document.createElement("div");
      btn.className = "__msx_bsb_mark";
      btn.title = "空降助手: 点击开/关自动跳过";
      btn.textContent = "🧭";
      btn.style.cssText = "position:absolute;right:0;top:-22px;z-index:30;cursor:pointer;"
        + "font-size:14px;filter:drop-shadow(0 0 3px rgba(0,0,0,.6))";
      btn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        st2.enabled = localStorage.getItem("__msx_bsb_on") !== "0";
        localStorage.setItem("__msx_bsb_on", st2.enabled ? "0" : "1");
        toast("空降助手自动跳过: " + (st2.enabled ? "已关闭" : "已开启"));
      });
      var st2 = window.__msx_bsb;
      bar.appendChild(btn);
    }
  }

  setInterval(function () {
    if (!/\/video\//.test(location.pathname)) return;
    load(); paint(); ui();
  }, 1000);
  /* 250ms 轮询只在播放页跑 —— 之前 onTime() 无条件查 DOM(每 250ms 一次
     document.querySelector), 在个人空间/首页等非播放页是纯开销。 */
  setInterval(function () { if (/\/video\//.test(location.pathname)) onTime(); }, 250);
  document.addEventListener("DOMContentLoaded", load);
  window.addEventListener("load", load);
})();
