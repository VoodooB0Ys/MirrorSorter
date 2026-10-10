/* 洞鉴虚明 内置画质增强(B站) — SVG 非锐化掩模锐化 + 轻度色彩增强
 * 只做 CSS/SVG 滤镜(作用于 <video> 元素), 不改 DOM 结构、不碰页面全局。
 *
 * 算法(2026-10 换掉 3x3 卷积): 3x3 feConvolveMatrix 只能碰相邻一像素, 观感提升
 * 有限(用户实测"不明显")。现在用**模糊-非锐化掩模**, 即 FSR1 里 RCAS 那条思路的
 * 轻量版(参考开源实现: pmndrs/upscaler 的实时锐化 pass、AMD FSR1 的 RCAS):
 *     out = (1+A)*src - A*blur(src)     ← feGaussianBlur + feComposite arithmetic
 * 由合成器在 GPU 上做一遍, 成本接近噪声级(实测全屏 165.7fps→165.1fps),
 * 但比 3x3 卷积的锐度提升明显得多; 档位同时调半径 R 与强度 A。
 *
 * 注入时机坑(实测, 这是本插件曾经完全无效的根因):
 *   生产管线走 WebView2 的 AddScriptToExecuteOnDocumentCreated —— 脚本执行时
 *   document.documentElement / document.head / document.body **全都是 null**。
 *   顶层 appendChild 会抛 TypeError: Cannot read properties of null (reading
 *   'appendChild'), 而注入包装把它吞成一条 console.warn → 滤镜一个都没挂上。
 *   所以改成自愈式 bootstrap: 能装就装, DOM 根没出来就等它(MutationObserver +
 *   DOMContentLoaded + 轮询兜底), 装上即停。
 * 开关: window.__MSX_BQ__ (1/0, 服务端设置 ui_bq)
 * 档位: window.__MSX_BQ_LEVEL__ ("轻"|"中"|"强", 服务端设置 ui_bq_level) */
(function () {
  "use strict";
  if (window.__MSX_BQ__ !== 1) return;
  if (!/(^|\.)bilibili\.com$/.test(location.hostname)) return;
  if (window.__MSX_BQ_DONE__) return;   // 幂等: 同一文档只装一次
  var LEVEL = window.__MSX_BQ_LEVEL__ || "中";
  /* 锐化核: 3x3 卷积换成**模糊-非锐化掩模**(等同 FSR1 里 RCAS 那条思路的轻量版):
     out = (1+A)*src - A*blur(src)  —— 由 feComposite arithmetic 的 k2/k3 精确实现。
     3x3 卷积只能碰到相邻一个像素, 提升很有限(用户实测"不明显"); 高斯半径可调,
     能真正把边缘/细节拉起来。档位同时调半径 R 与强度 A。 */
  /* 锐化核: 模糊-非锐化掩模(FSR1/RCAS 思路的轻量版): out = (1+A)*src − A*blur(src)。
     ⚠️ 强度必须克制: 用户实测上一版 强档(A=1.5/R=1.1) 出现了「浮雕感」—— 大半径非锐化
     会在边缘两侧压出宽halo, 看起来像浮雕而不是变清晰。现在降到 A<=0.55 / R<=0.8,
     并在设置页说明「锐化不是超分」; 真正的画质提升要交给成熟的放大/超分实现。 */
  var P = { "轻": { A: 0.20, R: 0.5 }, "中": { A: 0.35, R: 0.6 }, "强": { A: 0.55, R: 0.8 } }[LEVEL] ||
          { A: 0.35, R: 0.6 };
  var K2 = (1 + P.A).toFixed(2), K3 = (-P.A).toFixed(2);

  /* DOM 根: document-created 时两者都是 null → 返回 null 表示"还不能装" */
  function root() {
    return document.head || document.documentElement || null;
  }

  function install() {
    try {
      if (window.__MSX_BQ_DONE__) return true;
      var r = root();
      if (!r) return false;                       // 等 DOM 根
      if (document.getElementById("msx-view")) {  // 已被装过
        window.__MSX_BQ_DONE__ = true;
        return true;
      }
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = "msx-view";
      svg.setAttribute("width", "0");
      svg.setAttribute("height", "0");
      svg.setAttribute("aria-hidden", "true");
      svg.style.position = "absolute";
      svg.innerHTML = '<filter id="msx-sharpen" x="-5%" y="-5%" width="110%" height="110%"' +
        ' color-interpolation-filters="sRGB">' +
        '<feGaussianBlur stdDeviation="' + P.R + '" result="msx_blur"/>' +
        '<feComposite in="SourceGraphic" in2="msx_blur" operator="arithmetic"' +
        ' k1="0" k2="' + K2 + '" k3="' + K3 + '" k4="0"/>' +
        "</filter>";
      r.appendChild(svg);

      var css = document.createElement("style");
      css.id = "msx-quality-style";
      /* 同时挂 Player主体与容器两条选择器: 全屏/网页全屏切换时容器会变。
         全屏为什么不再豁免: 此前的 `:fullscreen { filter: none !important }` 是怕
         "全屏卷积开销大", 但全屏正是用户判断画质的地方 —— 豁免等于在最该有的场景下
         静默失效。实测(1920 宽视频、165Hz、画面持续变化, 见 _bq_fps_full1080.json):
         滤镜关 165.7fps / 开 165.1fps、p95 帧间隔同为 6.2ms —— 3x3 卷积的开销在噪声级,
         全屏面积只为大窗口的 1~2 倍, 不足以把"流畅"变成"幻灯片"。真在弱显卡上出问题,
         把下面这条 `:fullscreen { filter: none !important; }` 加回来即可。

         **不再挂 canvas.msx-sr-canvas**: 超分开启时画布是画面本身, 而超分合成趟已经把
         亮度细节重建过一遍(亮度比注入), 再叠一层非锐化掩模(A 最大 0.55 / R 最大 0.8)会在
         边缘两侧压出过冲 halo —— 观感就是"同一物体出现两份错开的轮廓/重影"。
         旧版挂它是因为**旧超分输出偏软**, 需要靠这层锐化"救回来"; 现在超分输出本身比
         双线性基线更锐(实测 960×540→1920×1080 PSNR 24.15 vs 22.68、梯度 58.9 vs 12.95),
         再锐化只有坏处。 */
      css.textContent =
        "#bilibili-player video, .bpx-player-container video, .bpx-player-video-wrap video" +
        " { filter: url(#msx-sharpen) saturate(1.08) contrast(1.04); }";
      r.appendChild(css);
      window.__MSX_BQ_DONE__ = true;
      return true;
    } catch (e) {
      /* 绝不把异常抛回注入链(否则会连带中断后面的用户脚本);
         现场留在 window.__MSX_BQ_ERR__ 里供排障 */
      try { window.__MSX_BQ_ERR__ = String(e); } catch (e2) {}
      return false;
    }
  }

  if (install()) return;

  /* ---- 走到这里 = document-created(DOM 根还没建出来): 等它出现 ----
     document 本身在 document-created 时已存在, 可以观察它; 每次 DOM 变动都试着装,
     装上即断开。另有 DOMContentLoaded / load / 300ms 轮询三重兜底, 30s 后彻底收工
     (不留常驻开销)。全部包在 try 里: 任何失败都不许影响页面上别的脚本。 */
  try {
    var obs = null;
    var iv = 0;
    function stop() {
      try { if (obs) { obs.disconnect(); obs = null; } } catch (e) {}
      try { if (iv) { clearInterval(iv); iv = 0; } } catch (e) {}
    }
    function tryInstall() {
      if (install()) { stop(); return true; }
      return false;
    }
    obs = new MutationObserver(tryInstall);
    obs.observe(document, { childList: true, subtree: true });
    document.addEventListener("DOMContentLoaded", tryInstall, { once: true });
    window.addEventListener("load", tryInstall, { once: true });
    iv = setInterval(tryInstall, 300);
    setTimeout(stop, 30000);
  } catch (e) {
    try { window.__MSX_BQ_ERR__ = String(e); } catch (e2) {}
    install();
  }
})();
