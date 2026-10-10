/* 洞鉴虚明 内置B站「最高画质」脚本
 *
 * 做什么: 让播放器用上**你这个账号本就有权拿到的最高档**——
 *   ① 请求 playurl 时带上 fnval=4048&fourk=1(要全部编码/4K 档位, 含 H.265/AV1),
 *      服务端会返回它允许的最高档, 而不是按页面默认值给低码率那路;
 *   ② 把"上次选择的画质"记忆键直接写成最高档(120=4K), 打开就是高画质;
 *   ③ 播放器就绪后best-effort把画质菜单里的最高可用档点一下(不可用的档位由 B站
 *      自己判定, 点不动就算了 —— 脚本不会伪造权限);
 *   ④ 回报实际拿到的源分辨率到 msx-debug 通道, 便于在壳日志里核对有没有真的升档。
 *
 * 不做什么(明确边界): 不用第三方代理绕会员/地区限制。本站能给你多高就是多高;
 *   没有大会员的账号, 4K/1080P高码率 那几档 B站 本来就不会给流, 本脚本也不会去伪造。
 *
 * 开关: window.__MSX_Q__ (1/0, 由壳按设置注入; 缺省视为开) */
(function () {
  "use strict";
  if (window.__MSX_QDONE__) return;
  if (window.__MSX_Q__ === 0) return;
  if (!/(^|\.)bilibili\.com$/.test(location.hostname)) return;
  window.__MSX_QDONE__ = 1;

  var MAXQN = 120;   // 120=4K, 116=1080P60, 112=1080P+, 80=1080P, 64=720P ...

  function report(s) {
    try { window.open("msx-debug:Q" + String(s).slice(0, 200), "_blank"); } catch (e) {}
  }

  /* ① 让 playurl 要全档位: fnval 位掩码 4048 覆盖 H.264/H.265/AV1 与 4K, fourk=1 放开四开 */
  function patchUrl(u) {
    try {
      var s = String(u);
      if (s.indexOf("/x/player/") < 0 && s.indexOf("playurl") < 0) return u;
      if (s.indexOf("fnval=") >= 0) return u;
      return s + (s.indexOf("?") < 0 ? "?" : "&") + "fnval=4048&fourk=1";
    } catch (e) { return u; }
  }
  try {
    var of = window.fetch;
    if (of) {
      window.fetch = function (input) {
        try {
          if (typeof input === "string") input = patchUrl(input);
          else if (input && input.url) input = new Request(patchUrl(input.url), input);
        } catch (e) {}
        return of.apply(this, arguments);
      };
    }
  } catch (e) {}
  try {
    var oo = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) {
      try { arguments[1] = patchUrl(u); } catch (e) {}
      return oo.apply(this, arguments);
    };
  } catch (e) {}

  /* ② 画质记忆: B站 用这几个键记"上次选的画质", 直接写到最高档 */
  try {
    localStorage.setItem("bp_video_quality", String(MAXQN));
    localStorage.setItem("bpx_player_quality", String(MAXQN));
    localStorage.setItem("player_quality", String(MAXQN));
  } catch (e) {}

  /* ③ 播放器就绪后点一次画质菜单里的最高可用档(点不动就放弃, 不伪造权限) */
  var tries = 0;
  var timer = setInterval(function () {
    tries++;
    try {
      var btn = document.querySelector(".bpx-player-ctrl-quality, .bilibili-player-video-btn-quality");
      if (btn) {
        btn.click();
        setTimeout(function () {
          var items = document.querySelectorAll(
            ".bpx-player-ctrl-quality-menu-item, .bilibili-player-video-quality-menu .bui-dropdown-item"
          );
          if (items && items.length) {
            for (var i = 0; i < items.length; i++) {           // 菜单通常按高→低排, 取第一个可点的
              var it = items[i];
              var disabled = it.className.indexOf("disabled") >= 0 ||
                             it.getAttribute("aria-disabled") === "true";
              if (!disabled) { try { it.click(); } catch (e) {} break; }
            }
          }
          try { document.body.click(); } catch (e) {}         // 收起菜单
        }, 350);
      }
    } catch (e) {}
    if (tries >= 20) clearInterval(timer);
  }, 1500);

  /* ④ 回报实际拿到的源分辨率(用于在壳日志里核对是否真的升档) */
  var n = 0;
  var t2 = setInterval(function () {
    try {
      var v = document.querySelector("#bilibili-player video, .bpx-player-container video");
      if (v && v.videoWidth) {
        report("REPORT" + n + " src=" + v.videoWidth + "x" + v.videoHeight +
               " qn=" + (window.__playinfo__ && window.__playinfo__.data &&
                         window.__playinfo__.data.quality) +
               " accept=" + (window.__playinfo__ && window.__playinfo__.data &&
                             (window.__playinfo__.data.accept_quality || []).join("/")));
      }
    } catch (e) {}
    if (++n >= 8) clearInterval(t2);
  }, 5000);
})();
