/* 临时诊断探针(MSX_PROBE=1 时注入): 顶栏隐藏链取证, sendBeacon 回传本地收集器。
 * 不做任何修改, 只读 computed style; 三个时间点各报一次, 捕捉"闪一下再消失"。 */
(function () {
  function info() {
    try {
      var out = { url: location.href, t: Date.now(),
        ownui: (window.__MSX_OWNUI__ === undefined ? "undef" : String(window.__MSX_OWNUI__)),
        mf: (window.__MSX_MF__ === undefined ? "undef" : (window.__MSX_MF__ ? "present" : "null")),
        err: "" };
      function cls(n) { return String(n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className || "").slice(0, 90); }
      function snap(sel) {
        var el = document.querySelector(sel);
        if (!el) return null;
        var cs = getComputedStyle(el);
        var chain = [];
        var n = el;
        while (n && n !== document.documentElement) {
          var s = getComputedStyle(n);
          if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) === 0) {
            chain.push({ tag: n.tagName, id: n.id || "", cls: cls(n), why: s.display + "/" + s.visibility + "/" + s.opacity });
          }
          n = n.parentElement;
        }
        var r = el.getBoundingClientRect();
        return { display: cs.display, vis: cs.visibility, rect: [Math.round(r.width), Math.round(r.height)], top: Math.round(r.top), hiddenChain: chain };
      }
      out.main = snap("#biliMainHeader");
      // 顶栏内部结构: 子元素逐个 computed display(innerHTML 太大, 只列骨架)
      try {
        var barEl = document.querySelector(".bili-header__bar") || document.querySelector("#biliMainHeader");
        if (barEl) {
          out.barTree = (function walk(n, depth) {
            if (depth > 4) return null;
            var s = getComputedStyle(n);
            var o = { tag: n.tagName, id: n.id || "", cls: String(n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className || "").slice(0, 60), disp: s.display, vis: s.visibility, op: s.opacity, kids: [] };
            if (s.display !== "none") {
              for (var i = 0; i < n.children.length && i < 12; i++) {
                var k = walk(n.children[i], depth + 1);
                if (k) o.kids.push(k);
              }
            }
            return o;
          })(barEl, 0);
        }
      } catch (e3) { out.barTreeErr = String(e3); }
      out.scrollY = Math.round(window.scrollY || window.pageYOffset || 0);
      out.vh = Math.round(window.innerHeight);
      // 层叠上下文取证: 谁在 header 上面盖了不透明层
      out.roots = Array.prototype.map.call(
        document.querySelectorAll("#video-page-app, #app, #biliMainHeader"), function (el) {
          var s = getComputedStyle(el);
          var r = el.getBoundingClientRect();
          return { sel: el.id, pos: s.position, z: s.zIndex, bg: String(s.backgroundColor),
                   top: Math.round(r.top), h: Math.round(r.height), disp: s.display };
        });
      // header 视口内是否有不透明元素覆盖(取 header 中心点做 elementFromPoint)
      try {
        var mh = document.querySelector("#biliMainHeader");
        if (mh) {
          var mr = mh.getBoundingClientRect();
          var hit = document.elementFromPoint(mr.left + mr.width / 2, mr.top + mr.height / 2);
          out.hitAtHeader = hit ? { tag: hit.tagName, id: hit.id || "", cls: cls(hit) } : null;
        }
      } catch (e2) {}
      out.header = snap(".bili-header");
      out.bar = snap(".bili-header__bar");
      try {
        var lem = document.querySelector(".left-entry-main");
        out.lemFirst = lem && lem.children[0] ? lem.children[0].outerHTML.replace(/<path[^>]*>/g, "<path/>").replace(/\s+/g, " ").slice(0, 1200) : null;
      } catch (e4) {}
      var le = document.querySelector(".left-entry");
      out.left = le ? {
        disp: getComputedStyle(le).display, parentCls: cls(le.parentElement),
        firstLiHTML: le.children[0] ? le.children[0].outerHTML.replace(/<path[^>]*>/g, "<path/>").replace(/\s+/g, " ").slice(0, 1500) : null,
        kids: Array.prototype.map.call(le.children, function (c) {
          return { tag: c.tagName, cls: cls(c).slice(0, 40), txt: (c.textContent || "").replace(/\s+/g, "").slice(0, 12), disp: getComputedStyle(c).display };
        })
      } : null;
      out.kids = Array.prototype.map.call(
        Array.prototype.slice.call(document.body.children, 0, 12), function (c) {
        return { tag: c.tagName, id: c.id || "", cls: cls(c).slice(0, 50), disp: getComputedStyle(c).display };
      });
      return out;
    } catch (e) { return { err: String(e), url: location.href,
      ownui: (window.__MSX_OWNUI__ === undefined ? "undef" : String(window.__MSX_OWNUI__)),
      mf: (window.__MSX_MF__ === undefined ? "undef" : String(window.__MSX_MF__ && "present")) }; }
  }
  function report() {
    try { navigator.sendBeacon("http://127.0.0.1:38117/probe", JSON.stringify(info())); } catch (e) {}
  }
  [1500, 6000, 12000, 18000].forEach(function (ms) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { setTimeout(report, ms); }, { once: true });
    else setTimeout(report, ms);
  });
})();
