/* 洞鉴虚明 · B站播放页自有UI (v0.3.1.30) —— 注入脚本(document-start, MAIN world)
 *
 * 门控: window.__MSX_OWNUI__ === 1(壳按设置键 ui_ownui 注入) 且 www.bilibili.com/video/*。
 * 作用: 评论区 + 右侧栏整块替换为自有UI(Shadow DOM, 数据走B站官方web API+壳内登录态);
 *       播放器/弹幕/三连工具条 DOM 一律不碰 —— 超分管线红线(shell_pages 另行注入)。
 * 降级: 任一区域选择器找不到/接口异常 → 静默保持原生, 绝不让页面白屏。
 * v0.3.1.28: 弹幕面板的渲染是**追加式**的(只往末尾添行), 数据源缩短/换新时无法重画,
 * 于是「切隐藏屏蔽」看起来毫无反应。现在 chunk() 认源数组的**引用变化**与 oid 变化,
 * 一变就 hardReset() 清空重建; 开关也强制 hardReset 后再画。
 * 主题: 读 theme_style cookie 跟随B站原生深浅(壳内深色由 CookieManager 写入)。
 */
(function () {
  "use strict";
  if (window.__msxOwnui) return;
  window.__msxOwnui = 1;
  if (window.__MSX_OWNUI__ !== 1) return;
  if (location.hostname !== "www.bilibili.com") return;
  if (!/^\/video\//.test(location.pathname)) return;

  /* ---------------- 启动遮罩: 自有UI挂载前, 原生评论区/右栏不裸露 ----------------
   * 用户反馈: 加载时会先闪出一下B站原生UI。根因: 面板要等"头部就绪闸门"过后才隐藏
   * 原生容器(见 whenHeaderSettled 注释), 这段窗口期原生评论区/右栏可见。
   * 遮罩用 visibility:hidden(保留布局)而非 display:none —— 不影响 B站头部新旧
   * 切换时对容器的几何测量/IntersectionObserver, 不会再触发顶栏消失问题。
   * 挂载完成(或15s兜底失败)即摘除; SPA 路由重建时重新戴上。 */
  function veilHold(ttl) {
    // 连带申请"壳级防闪幕布"延后揭幕: 改版面前别把原生评论区/右栏露出来。
    // ttl 到期自动失效, 所以即使本脚本异常也不会把页面永久盖住。
    try {
      if (window.__msx_veil_hold) { window.__msx_veil_hold("ownui", ttl); return; }
      var H = window.__msx_veil_holds || (window.__msx_veil_holds = {});
      H["ownui"] = Date.now() + ttl;   // 幕布脚本还没跑: 先排队
    } catch (e) {}
  }
  function veilRelease() {
    try {
      if (window.__msx_veil_release) { window.__msx_veil_release("ownui"); return; }
      var H = window.__msx_veil_holds;
      if (H) delete H["ownui"];
    } catch (e) {}
  }
  /* 文档根可能还不存在 —— 壳用 AddScriptToExecuteOnDocumentCreated 注入, 与浏览器扩展的
     document-start 同点: 文档刚创建、解析器还没建 <html>, document.head 与
     document.documentElement 都是 null。原写法 (document.head || document.documentElement)
     .appendChild(...) 会抛 TypeError 并被 catch 吞掉 ⇒ 样式永不生效。
     实测(desktop/_ad_probe.js, Edge + 壳真实注入串): #msx-ownui-adblock 与
     #msx-ownui-boot-style 根本没进 DOM —— 广告屏蔽与防闪遮罩一直是失效的;
     且 catch 里的 bootMaskOff() 会立刻 veilRelease(), 防闪幕布白申请。
     这里统一等根节点出现(解析器建 <html> 那一帧 MutationObserver 就会回调)再挂,
     DOMContentLoaded 兜底。 */
  function whenRoot(fn) {
    try { if (document.documentElement) { fn(); return; } } catch (e) {}
    try {
      var mo = new MutationObserver(function () {
        try { if (document.documentElement) { mo.disconnect(); fn(); } } catch (e) {}
      });
      mo.observe(document, { childList: true, subtree: true });
    } catch (e) {}
    try { document.addEventListener("DOMContentLoaded", fn, { once: true }); } catch (e) {}
  }
  function mountStyle(id, css) {
    whenRoot(function () {
      try {
        if (document.getElementById(id)) return;
        var st = document.createElement("style");
        st.id = id;
        st.textContent = css;
        (document.head || document.documentElement).appendChild(st);
      } catch (e) {}
    });
  }
  var maskOff = false;   // bootMaskOff 已调用过 → 晚到的 whenRoot 回调别再戴遮罩

  function bootMaskOn() {
    whenRoot(function () {
      if (maskOff) return;
      try { document.documentElement.classList.add("msx-ownui-boot"); } catch (e) {}
    });
    /* 防闪 hold: 只盖住"最初一瞬"。壳幕布自己的揭幕闸门是 LATE=900ms(见 shell_pages
       的 _NOFLASH_JS), 所以这里给 700ms —— 不会再往后拖揭幕(用户反馈: 播放页比浏览器
       慢 2s+, 就是被这个 hold 拖到 2.2s 才露画面)。原生评论区/右栏由下面的
       msx-ownui-boot 类全程遮着, 挂好再摘, 不靠幕布兜。 */
    veilHold(700);
  }
  function bootMaskOff() {
    maskOff = true;
    try { document.documentElement.classList.remove("msx-ownui-boot"); } catch (e) {}
    veilRelease();    // 自有UI已挂好(或放弃挂载) → 允许揭幕
  }
  bootMaskOn();
  mountStyle("msx-ownui-boot-style",
    "html.msx-ownui-boot #commentapp,html.msx-ownui-boot #comment,"
    + "html.msx-ownui-boot .comment-container,html.msx-ownui-boot .comment,"
    + "html.msx-ownui-boot .right-container,html.msx-ownui-boot #right-container"
    + "{visibility:hidden!important}");

  /* ---------------- 广告屏蔽(bug11): 播放页右栏的"大广告框" ----------------
   * 现场实测(探针 desktop/_biliref.js, BV1tGpT6oEQy)右栏底部那个大推广块就是:
   *   div.ad-report.ad-floor-exp.right-bottom-banner (350x175)
   *     └ a.ad-report-inner / div.ad-floor-cover.b-img
   * 另有视频页的"特别推荐"卡 .video-page-special-card-small(同类推广位)。全部去掉。
   * 用 CSS 常驻规则而不是 JS 删除 —— 广告是懒加载插入的, JS 删会漏掉后插入的节点。
   * ⚠ 不要动 .bpx-player-adv-dm-wrap: 那是播放器的高级弹幕层, 不是广告。 */
  mountStyle("msx-ownui-adblock",
    ".ad-report,.ad-report-inner,.ad-floor-exp,.ad-floor-cover,.right-bottom-banner,"
    + ".video-page-special-card-small,.slide-ad-exp,.video-card-ad-small,"
    + ".bili-video-card__info--ad,.adblock-tips{display:none!important}");

  /* ---------------- 顶栏精简: 只去左侧频道导航(保留LOGO/搜索框/用户区) ----------------
   * 不整块隐藏 left-entry(部分登录态变体下会连带整条栏消失), 改为逐条目处理:
   * 只隐藏文本命中导航标签的子条目; 含 LOGO/搜索/用户区的元素一律跳过。 */
  var NAV_LABELS = ["首页", "番剧", "直播", "游戏中心", "会员购", "漫画", "赛事", "下载客户端"];
  function slimNav() {
    try {
      qsa(".bili-header .left-entry").forEach(function (ul) {
        // 两种头部变体统一取"条目容器": 旧 mini-header = left-entry 本体;
        // 新 bili-header__menu = left-entry > .left-entry-main(整条藏它会把 LOGO 一起带走)
        var host = qs(":scope > .left-entry-main", ul) || ul;
        qsa(":scope > *", host).forEach(function (el) {
          // 搜索/用户区(及任何包含它们的包装)绝不动
          if (qs(".center-search-container, .right-entry", el)) return;
          // LOGO 融合条目(新旧变体都存在): 只留 LOGO 本体, 文字/箭头/弹出层全藏
          var logo = qs(".big-logo, .mini-header__logo", el);
          if (logo) {
            qsa('[class*="popover"], [class*="channel"], .trigger-text, .trigger-arrow, .mini-header__title', el)
              .forEach(function (n) { n.style.setProperty("display", "none", "important"); });
            qsa("a", el).forEach(function (a) {
              if (!qs(".big-logo, .mini-header__logo", a)) {   // 纯文字锚(如"首页") → 整个隐藏
                var at = String(a.textContent || "").replace(/\s+/g, "");
                if (at && at.length <= 14 && NAV_LABELS.some(function (k) { return at.indexOf(k) >= 0; })) {
                  a.style.setProperty("display", "none", "important");
                }
                return;
              }
              Array.prototype.slice.call(a.childNodes).forEach(function (n) {   // LOGO 锚: 清直接文本节点
                if (n.nodeType === 3 && String(n.textContent).replace(/\s+/g, "")) n.textContent = "";
              });
              qsa(":scope > *", a).forEach(function (n) {   // 子元素: 非LOGO的svg(箭头)与文字标签(≤12字)都藏
                var cn = n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className;
                var isLogo = /(mini-header__logo|big-logo)/.test(String(cn));
                var nt = String(n.textContent || "").replace(/\s+/g, "");
                if (!isLogo && (/svg/i.test(n.tagName) || (nt && nt.length <= 12))) {
                  n.style.setProperty("display", "none", "important");
                }
              });
            });
            return;
          }
          // 命中规则: 文本以导航标签开头, 或文本很短(≤14)且包含标签
          var t = String(el.textContent || "").replace(/\s+/g, "");
          var hit = NAV_LABELS.some(function (k) { return t.indexOf(k) === 0; })
            || (t.length > 0 && t.length <= 14
                && NAV_LABELS.some(function (k) { return t.indexOf(k) >= 0; }));
          if (hit) el.style.setProperty("display", "none", "important");
        });
      });
    } catch (e) { /* 静默 */ }
  }
  slimNav();

  /* ---------------- 播放页推荐筛选(与信息流 machine_filter 同源的页面端子集) ----------------
   * 规则由壳注入 window.__MSX_MF__; 缺失/null 时回退原始列表。 */
  var MF = window.__MSX_MF__ || null;
  function normTitle(t) {
    return String(t || "").toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
  }
  function emoBad(t) {
    t = String(t || "").toLowerCase();
    var ex = (t.match(/[!！‼⁉]/g) || []).length;
    var qm = (t.match(/[?？]/g) || []).length;
    if (ex >= 3 || (ex >= 2 && t.length <= 16)) return 1;
    if (qm >= 4 || (qm >= 3 && t.length <= 14) || (ex >= 1 && qm >= 2)) return 1;
    if (/([\u4e00-\u9fff])\1{3}/.test(t) || /([\u4e00-\u9fff]{2})\1\1/.test(t)) return 1;
    var i;
    for (i = 0; i < MF.flame.length; i++) if (t.indexOf(MF.flame[i]) >= 0) return 1;
    for (i = 0; i < MF.cb.length; i++) if (t.indexOf(MF.cb[i]) >= 0) return 1;
    if (MF.cbre) { try { if (new RegExp(MF.cbre).test(t)) return 1; } catch (e) {} }
    return 0;
  }
  function mfPass(b) {
    var now = Date.now() / 1000;
    var views = Number(b.stat && b.stat.view) || 0;
    var dur = Number(b.duration) || 0;
    if (MF.min_views && views < MF.min_views) return false;
    if (MF.max_views && views > MF.max_views) return false;
    if (MF.max_dur && dur > MF.max_dur) return false;
    if (MF.min_dur && dur < MF.min_dur) return false;
    if (MF.max_age && b.pubdate && now - Number(b.pubdate) > MF.max_age * 30.44 * 86400) return false;
    var blob = String(b.title || "") + " " + String(b.desc || "") + " " + String(b.owner && b.owner.name || "");
    var low = blob.toLowerCase();
    var i;
    for (i = 0; i < MF.dkws.length; i++) if (low.indexOf(MF.dkws[i].toLowerCase()) >= 0) return false;
    var bl = String(b.owner && b.owner.name || "");
    for (i = 0; i < MF.ablock.length; i++) if (bl === MF.ablock[i]) return false;
    if (MF.emotion && emoBad(b.title)) return false;
    return true;
  }
  function mfFilter(list) {
    if (!MF || !MF.on || !Array.isArray(list)) return list || [];
    var kept = list.filter(mfPass);
    if (MF.dedup) {
      kept.sort(function (a, b) { return (Number(b.stat && b.stat.view) || 0) - (Number(a.stat && a.stat.view) || 0); });
      var keptN = [], seen = [];
      kept.forEach(function (b) {
        var nt = normTitle(b.title);
        if (!nt) { keptN.push(b); return; }
        for (var i = 0; i < seen.length; i++) {
          if (nt === seen[i] || (nt.length > 6 && seen[i].length > 6 && (nt.indexOf(seen[i]) >= 0 || seen[i].indexOf(nt) >= 0))) return;
        }
        seen.push(nt);
        keptN.push(b);
      });
      kept = keptN;
    }
    return kept;
  }
  /* 相关:探索 = 7:3 交错(每7条相关后插3条探索; 任一列表耗尽则接续剩余) */
  function mixRel(rel, unrel) {
    var out = [], i = 0, j = 0;
    while (i < rel.length || j < unrel.length) {
      for (var k = 0; k < 7 && i < rel.length; k++) out.push({ b: rel[i++], explore: false });
      for (var k2 = 0; k2 < 3 && j < unrel.length; k2++) out.push({ b: unrel[j++], explore: true });
    }
    return out;
  }

  /* ---------------- 评论筛选(与壳"其他设置 · 评论筛选"同源) ----------------
   * window.__MSX_CF__ = { blk_on, blk:[用户名...], lvl:1..5 }
   * 规则在页面本地执行, 不消耗服务端资源:
   *   · blk_on 时, 用户名命中"软件黑名单"的评论不渲染;
   *   · 用户 B站等级低于 lvl 的评论不渲染(lvl<=1 等于不过滤)。
   * 取不到等级信息时不过滤, 避免误伤。
   * 注(2026-10-08): 权威过滤已挪到 extensions/bilibili-cfilter.user.js —— 它在
   * document-start 劫持 api.bilibili.com/x/v2/reply* 的响应, 对 B站原生评论区同样生效
   * (本脚本自建评论区的挂载点 #comment/.comment-container/.comment 在现版 B站 全落空,
   *  评论区根元素实为 #commentapp, 面板挂不上)。下面 cfPass 仅作自建面板的兜底。 */
  var CF = window.__MSX_CF__ || null;
  /* v0.3.1.31: 黑名单改成字典查表 O(1)。旧实现是 `for (i < CF.blk.length) if (nm === CF.blk[i])`
     —— 每条评论线性扫整个名单(43 个名字 × 200 条评论 = 8600 次字符串比较/页)。
     与 dmfilter / cfilter 保持一致: Object.create(null) 哈希, 键名不会撞 Object 原型。 */
  var CF_BLK = null;
  if (CF && CF.blk_on && CF.blk && CF.blk.length) {
    CF_BLK = Object.create(null);
    for (var _bi = 0; _bi < CF.blk.length; _bi++) {
      var _bn = String(CF.blk[_bi] == null ? "" : CF.blk[_bi]).trim();
      if (_bn) CF_BLK[_bn] = 1;
    }
    if (!Object.keys(CF_BLK).length) CF_BLK = null;
  }
  function cfPass(r) {
    if (!CF) return true;
    var m = r && r.member;
    if (CF_BLK) {
      var nm = String(m && m.uname || "").trim();
      if (nm && CF_BLK[nm]) return false;
    }
    var lv = Number(CF.lvl) || 0;
    if (lv > 1) {
      var cur = m && m.level_info ? Number(m.level_info.current_level) : NaN;
      if (isFinite(cur) && cur < lv) return false;
    }
    return true;
  }

  /* ---------------- 工具 ---------------- */
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* 侧栏推荐标题截断: 只留 n 个"字"(按码点切, 不劈开 emoji/代理对)。
     标题变短 → 条目高度稳定, 缩略图才能放大(见 buildRelated 的 .it .cv)。 */
  function clipTitle(s, n) {
    var t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    var a = Array.from(t);
    return a.length > n ? a.slice(0, n).join("") + "…" : t;
  }
  function cookie(name) {
    var m = String(document.cookie || "").match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  }
  function isDark() { return /(?:^|;\s*)theme_style=dark/.test(String(document.cookie || "")); }
  function fmtCount(n) {
    n = Number(n) || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
    if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
    return String(n);
  }
  function fmtDur(sec) {
    sec = Number(sec) || 0;
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s2 = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s2).padStart(2, "0");
  }
  function timeago(unix) {
    var d = (Date.now() / 1000) - (Number(unix) || 0);
    if (d < 60) return "刚刚";
    if (d < 3600) return Math.floor(d / 60) + "分钟前";
    if (d < 86400) return Math.floor(d / 3600) + "小时前";
    if (d < 86400 * 30) return Math.floor(d / 86400) + "天前";
    var dt = new Date(Number(unix) * 1000);
    return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0") + "-" + String(dt.getDate()).padStart(2, "0");
  }

  /* ---------------- API 层(页面上下文, cookie 自动携带) ---------------- */
  var CSRF = cookie("bili_jct") || "";
  function apiGet(url) {
    return fetch(url, { credentials: "include" }).then(function (r) { return r.json(); });
  }
  function apiPost(url, params) {
    var body = new URLSearchParams(params);
    return fetch(url, {
      method: "POST", credentials: "include", body: body,
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      referrer: location.href,
    }).then(function (r) { return r.json(); });
  }
  var user = { login: false, face: "", uname: "", mid: 0 };

  /* ---------------- 全局状态 ---------------- */
  var S = {
    bvid: "", aid: 0, owner: null, stat: null,
    hosts: [],        // 自有 host 元素(teardown 时移除)
    hidden: [],       // 被隐藏的原生节点(restore 时恢复)
    styles: [],       // 注入页面的 <style>(teardown 时移除)
    tick: null, lastPath: location.pathname,
    cmtPage: 1, cmtSort: 3, degraded: false,
  };

  /* ---------------- 元信息 ---------------- */
  function loadMeta() {
    try {
      var st = window.__INITIAL_STATE__ || {};
      var vd = st.videoData || {};
      S.bvid = vd.bvid || st.bvid || (location.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/) || [])[1] || "";
      S.aid = Number(vd.aid || st.aid || 0) || 0;
      S.owner = vd.owner || null;
      S.stat = vd.stat || null;
    } catch (e) { /* 走兜底 */ }
    var needView = !S.bvid || !S.aid || !S.owner;
    var p = needView
      ? apiGet("https://api.bilibili.com/x/web-interface/view?bvid=" + encodeURIComponent(S.bvid))
        .then(function (j) {
          if (j && j.code === 0 && j.data) {
            S.aid = j.data.aid; S.owner = j.data.owner; S.stat = j.data.stat; S.bvid = j.data.bvid;
          }
        })
      : Promise.resolve();
    return p;
  }

  /* ---------------- 主题调色板 ---------------- */
  function palette() {
    return isDark()
      ? { bg: "#18191c", card: "#222325", text: "#e3e5e7", sub: "#9499a0",
          line: "#2f3238", hover: "#2a2c30", accent: "#fb7299", input: "#2a2c30" }
      : { bg: "#f6f7f8", card: "#ffffff", text: "#18191c", sub: "#9499a0",
          line: "#e3e5e7", hover: "#f0f1f3", accent: "#fb7299", input: "#f0f1f3" };
  }
  function baseCSS(p) {
    return ":host{all:initial;display:block;width:100%;font:14px/1.6 'PingFang SC','Microsoft YaHei',sans-serif;color:" + p.text + "}"
      + "*{box-sizing:border-box;margin:0;padding:0}"
      + ".wrap{background:" + p.bg + ";color:" + p.text + "}"
      + "a{color:inherit;text-decoration:none;cursor:pointer}"
      + "button{cursor:pointer;border:1px solid " + p.line + ";background:" + p.card + ";color:" + p.text
      + ";border-radius:6px;padding:5px 14px;font-size:13px}"
      + "button:hover{border-color:" + p.accent + ";color:" + p.accent + "}"
      + "button.pri{background:" + p.accent + ";border-color:" + p.accent + ";color:#fff}"
      + "button:disabled{opacity:.5;cursor:default}"
      + "img{border-radius:50%;object-fit:cover;background:" + p.hover + "}"
      + ".sub{color:" + p.sub + ";font-size:12px}"
      + ".toast{position:sticky;top:8px;margin:0 8px;padding:8px 12px;border-radius:8px;background:"
      + p.card + ";border:1px solid " + p.accent + ";color:" + p.text + ";font-size:13px;display:none;z-index:9}"
      + ".toast.show{display:block}";
  }

  function toastIn(root, msg) {
    var t = qs(".toast", root);
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._tm);
    t._tm = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  /* ================= 右栏: 相关推荐(原生 UP主卡保留) =================
   * 只接管"相关推荐"区。**保留 B站原生 UP主信息卡 .up-panel-container**
   * (头像 / 名称 / 发消息 / 简介 / 充电 / 关注按钮) —— 用户要求这个"关注界面"
   * 必须留着; 旧实现把整个右栏替换成自建卡, 连关注按钮一起关掉了。
   * 用一条 style 规则隐藏右栏里"除 UP主卡与自建面板之外"的原生块, 这样懒加载
   * 迟到的推荐列表/广告也会被盖住(纯 JS 隐藏会漏掉后插入的节点)。 */
  function buildRelated() {
    var rc = qs(".right-container") || qs("#right-container");
    if (!rc) return false;
    var inner = qs(".right-container-inner", rc) || rc;
    var hostEl = document.createElement("div");
    hostEl.id = "msx-ownui-related";
    hostEl.dataset.msxOwn = "1";
    var root = hostEl.attachShadow({ mode: "open" });
    var p = palette();
    root.innerHTML = "<style>" + baseCSS(p)
      + ".wrap{padding:0 0 16px}"
      + ".rel{font-size:19px;font-weight:700;padding:6px 12px}"   /* 本轮: 标题字号加大(15→19), 一眼读成标题 */
      + ".it{display:flex;gap:10px;padding:9px 12px;border-radius:10px;cursor:pointer}"
      + ".it:hover{background:" + p.hover + "}"
      + ".it .cv{position:relative;flex:0 0 60%;min-width:150px;max-width:300px;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:" + p.hover + "}"
      + ".it .cv img{width:100%;height:100%;object-fit:cover;border-radius:8px;display:block}"
      + ".it .cv .du{position:absolute;right:4px;bottom:4px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;padding:0 4px;border-radius:4px;line-height:16px}"
      + ".it .tx{flex:1;min-width:0}"
      + ".it .tt{font-size:13px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}"
      + ".it .tt .ex{color:" + p.accent + ";border:1px solid " + p.accent + ";border-radius:3px;font-size:10px;padding:0 3px;margin-left:3px}"
      + ".it .mt{margin-top:4px}"
      + ".err{padding:12px}"
      + "</style>"
      + '<div class="wrap"><div class="toast"></div>'
      + '<div class="rel">相关推荐</div><div id="msxRel" class="sub" style="padding:8px 12px">加载中…</div></div>';
    // 相关推荐(+可选筛选与7:3探索混排)
    var listEl = root.getElementById("msxRel");
    var relTitle = qs(".rel", root);
    function renderItem(b, explore) {
      var d = document.createElement("div");
      d.className = "it";
      d.dataset.bvid = b.bvid || "";
      d.innerHTML = '<div class="cv"><img loading="lazy" src="'
        + esc(String(b.pic || "").replace(/^http:/, "https:")) + '@416w_234h.webp" referrerpolicy="no-referrer">'
        + '<span class="du">' + fmtDur(b.duration) + '</span></div>'
        + '<div class="tx"><div class="tt">' + esc(clipTitle(b.title, 20)) + (explore ? ' <span class="ex">探索</span>' : '') + '</div>'
        + '<div class="mt sub">' + esc(b.owner && b.owner.name || "") + '</div>'
        + '<div class="sub">' + fmtCount(b.stat && b.stat.view) + '播放 ' + fmtCount(b.stat && b.stat.danmaku) + '弹幕</div></div>';
      d.addEventListener("click", function () {
        location.href = "https://www.bilibili.com/video/" + d.dataset.bvid;
      });
      return d;
    }
    function renderList(mixed) {
      listEl.innerHTML = "";
      mixed.forEach(function (m) { listEl.appendChild(renderItem(m.b, m.explore)); });
    }
    apiGet("https://api.bilibili.com/x/web-interface/archive/related?bvid=" + encodeURIComponent(S.bvid))
      .then(function (j) {
        if (!j || j.code !== 0 || !j.data || !j.data.length) throw (j && j.message) || "empty";
        listEl.className = "";
        var filteredRel = mfFilter(j.data);
        if (!MF || !MF.on) {   // 关闭筛选 → 原始顺序直出
          renderList(j.data.map(function (b) { return { b: b, explore: false }; }));
        } else {
          relTitle.innerHTML = '相关推荐 <span class="sub" style="font-weight:400;font-size:12px">已按机器筛选 · 相关:探索=7:3</span>';
          apiGet("https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1")
            .then(function (pj) {
              var pool = (pj && pj.code === 0 && pj.data && pj.data.list || [])
                .filter(function (b) {   // 探索池: 去掉当前视频与已在相关列表里的条目
                  if (b.bvid === S.bvid) return false;
                  var nt = normTitle(b.title);
                  return !filteredRel.some(function (r) {
                    return r.bvid === b.bvid || (nt && normTitle(r.title) === nt);
                  });
                });
              renderList(mixRel(filteredRel, mfFilter(pool)));
            })
            .catch(function () {   // popular 失败 → 只显示过滤后的相关列表
              renderList(filteredRel.map(function (b) { return { b: b, explore: false }; }));
            });
        }
      })
      .catch(function (e) {
        listEl.textContent = "相关推荐加载失败(" + e + ")";
      });
    /* ⚠️ v0.3.1.26 顺序要紧: 整块 UI 先在**离屏**建完 → 再挂进页面 → 最后才藏原生右栏。
       旧顺序是"先挂空壳 → 再填内容 → 提前藏原生", 页面会先露一眼原生推荐/空白,
       再被我们的面板顶掉 —— 就是用户反馈的"侧边栏闪烁一下"。 */
    inner.appendChild(hostEl);
    S.hosts.push(hostEl);
    if (!qs("#msx-ownui-right-style")) {
      var rs = document.createElement("style");
      rs.id = "msx-ownui-right-style";
      rs.textContent =
        ".right-container>*:not(.right-container-inner):not(.up-panel-container):not(.teleport):not([data-msx-own=\"1\"]),"
        + "#right-container>*:not(.right-container-inner):not(.up-panel-container):not(.teleport):not([data-msx-own=\"1\"]),"
        + ".right-container-inner>*:not(.up-panel-container):not(.teleport):not([data-msx-own=\"1\"])"
        + "{display:none!important}";
      (document.head || document.documentElement).appendChild(rs);
      S.styles.push(rs);
    }
    if (!qs("#msx-ownui-page-pad")) {
      /* 用户要求(m03204)「播放页左侧宽一点, 把视频挤得更居中」+ 报修(m03496)「那条窗口级留白
         太宽、像一条独立边框, 而且 B站顶栏左侧空出一块」:
         —— 窗口级留白已取消(控件与窗口等宽, 顶栏从窗口左缘连续, 见 shell_pages._tab_geo);
         —— 左侧这点空白改在**页面内部**留: 只给内容容器加左内边距。顶栏 .bili-header 是容器
            之外的节点, 完全不受影响; 整块内容(视频 + 右栏)一起右移, 两者间距不变、右侧不裁。
         数值一处可调: 4vw ≈ 内容右移 20+ 逻辑像素; 播放器全屏时禁用这条(全屏不该留边)。 */
      var padSel = qs("#mirror-vdcon") ? "#mirror-vdcon" : ".video-container-v1";
      var ps = document.createElement("style");
      ps.id = "msx-ownui-page-pad";
      ps.textContent = "html:not(:fullscreen) " + padSel
        + "{padding-left:4vw!important;box-sizing:border-box!important}";
      (document.head || document.documentElement).appendChild(ps);
      S.styles.push(ps);
      var syncPad = function () { ps.disabled = !!document.fullscreenElement; };
      document.addEventListener("fullscreenchange", syncPad);
      syncPad();
    }
    return true;
  }


  /* ================= 右栏: 弹幕列表(数据源: bilibili-dmfilter) =================
   * B站 原生弹幕列表(#danmukuBox)被右栏接管规则一并藏掉了(v0.3.1.2 起), 用户要求做回来,
   * 并加"跳转发送者主页"。数据由 bilibili-dmfilter 解析 seg.so 后发布到
   * window.__msxDanmaku(已按「弹幕评论筛选」过滤), 这里只管渲染:
   *   · 行 = 时间 / 弹幕内容 / 发送时间(与原生列表同款三栏);
   *   · 增量渲染(每批 120 行, 滚动到底自动续) —— 大视频几万条也不卡;
   *   · 点行 → dmfilter.resolveFull(midHash) → 壳逆推+验真 → 唯一解直接开 space.bilibili.com;
   *     多个账号都能对上时弹候选框让用户自己挑; 一个都对不上(≥11 位新 UID)明确提示。
   * (绝不乱跳 —— B站对 11 位以上新 UID 只给哈希, 逆推不出来。) */
  function buildDanmaku() {
    var DMF = window.__msxDanmaku || null;
    if (!DMF) return false;                 // dmfilter 不在(极少见) → 不挂, 免得只有个空壳
    var rc = qs(".right-container") || qs("#right-container");
    if (!rc) return false;
    var inner = qs(".right-container-inner", rc) || rc;
    var hostEl = document.createElement("div");
    hostEl.id = "msx-ownui-danmaku";
    hostEl.dataset.msxOwn = "1";
    var root = hostEl.attachShadow({ mode: "open" });
    var p = palette();
    root.innerHTML = "<style>" + baseCSS(p)
      + ".wrap{position:relative;margin:10px 0 14px;border-radius:10px;background:" + p.card + ";overflow:hidden}"
      + ".hd{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:10px 14px 8px}"
      + ".hd b{font-size:15px}"
      + ".hd .hr{display:flex;align-items:center;gap:8px;margin-left:auto}"
      + ".hd .fo{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}"
      + ".hd .fo .ar{display:inline-block;width:10px;text-align:center;color:" + p.sub + ";font-size:11px}"
      + ".tg{padding:2px 9px;font-size:12px;line-height:18px;border-radius:6px;flex:none}"
      + ".tg.on{background:" + p.accent + ";border-color:" + p.accent + ";color:#fff}"
      + ".cols,.rw{display:grid;grid-template-columns:60px 1fr 92px;gap:8px;align-items:baseline;padding:5px 14px}"
      + ".cols{color:" + p.sub + ";font-size:12px;border-bottom:1px solid " + p.line + ";padding-bottom:7px}"
      + ".box{max-height:460px;overflow-y:auto;overscroll-behavior:contain}"
      + ".rw{font-size:13px;cursor:pointer}"
      + ".rw:hover{background:" + p.hover + "}"
      + ".rw .t{color:" + p.sub + ";font-size:12px;font-variant-numeric:tabular-nums}"
      + ".rw .x{word-break:break-word}"
      + ".rw .c{color:" + p.sub + ";font-size:12px;text-align:right;font-variant-numeric:tabular-nums}"
      + ".rw.blk{background:rgba(251,114,153,.07)}"
      + ".rw.blk .x{color:" + p.sub + "}"
      + ".rw .wy{display:inline-block;margin-right:6px;padding:0 5px;border-radius:4px;font-size:11px;"
      + "line-height:16px;background:rgba(251,114,153,.18);color:" + p.accent + ";vertical-align:1px}"
      + ".more{padding:8px 14px;text-align:center;color:" + p.sub + ";font-size:12px;cursor:pointer}"
      + ".tip{padding:12px 14px;color:" + p.sub + ";font-size:12px}"
      + ".pick{position:absolute;inset:0;display:none;align-items:center;justify-content:center;"
      + "background:rgba(0,0,0,.42);z-index:20}"
      + ".pick.on{display:flex}"
      + ".pick .pb{width:min(340px,90%);max-height:78%;overflow-y:auto;background:" + p.card
      + ";border:1px solid " + p.line + ";border-radius:10px;padding:12px}"
      + ".pick .pb h4{font-size:14px;margin-bottom:5px}"
      + ".pick .pi{display:flex;align-items:center;gap:9px;padding:7px 8px;border-radius:8px;cursor:pointer}"
      + ".pick .pi:hover{background:" + p.hover + "}"
      + ".pick .pi img{width:32px;height:32px;flex:none}"
      + ".pick .pi .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
      + ".pick .pc{text-align:right;margin-top:9px}"
      + "</style>"
      + '<div class="wrap"><div class="toast"></div>'
      + '<div class="hd"><b class="fo" id="msxDmFold" title="点击折叠 / 展开弹幕列表">'
      + '<span class="ar" id="msxDmAr">▾</span>弹幕列表 <span class="sub" id="msxDmN"></span></b>'
      + '<span class="hr"><button type="button" class="tg" id="msxDmBlk"'
      + ' title="在列表里显示被「弹幕评论筛选」屏蔽掉的弹幕（只看不改：播放器里的过滤不受影响）">显示屏蔽</button>'
      + '<span class="sub" style="font-size:12px">点行查发送者主页</span></span></div>'
      + '<div class="cols" id="msxDmCols"><span>时间</span><span>弹幕内容</span><span style="text-align:right">发送时间</span></div>'
      + '<div class="box" id="msxDmBox"><div class="tip" id="msxDmTip">加载中…</div>'
      + '<div class="more" id="msxDmMore" style="display:none">显示更多</div></div>'
      + '<div class="pick" id="msxDmPick"></div></div>';
    var box = root.getElementById("msxDmBox");
    var tipEl = root.getElementById("msxDmTip");
    var moreEl = root.getElementById("msxDmMore");
    var nEl = root.getElementById("msxDmN");
    var pickEl = root.getElementById("msxDmPick");
    var blkBtn = root.getElementById("msxDmBlk");
    /* 折叠/展开(bug5): 标题本身就是开关 —— 点一下把列头与列表收起来, 只留一条标题行。
       状态记住(localStorage 可能被禁用 → try/catch 兜住)。
       用户报修(m03496): **默认折叠** —— 打开播放页时弹幕列表就是收起的, 想看再手动展开。
       所以只有用户显式展开过(存 "0")才展开, 没存过/存过 "1" 都是折叠。 */
    var colsEl = root.getElementById("msxDmCols");
    var arEl = root.getElementById("msxDmAr");
    var folded = true;
    try { folded = localStorage.getItem("msx_dm_fold") !== "0"; } catch (e) {}
    function applyFold() {
      if (colsEl) colsEl.style.display = folded ? "none" : "";
      box.style.display = folded ? "none" : "";
      if (arEl) arEl.textContent = folded ? "▸" : "▾";
      if (folded && pickEl) pickEl.classList.remove("on");
    }
    applyFold();
    root.getElementById("msxDmFold").addEventListener("click", function () {
      folded = !folded;
      try { localStorage.setItem("msx_dm_fold", folded ? "1" : "0"); } catch (e) {}
      applyFold();
    });
    var R = 0, lastOid = null, lastRowsRef = null, lastHiddenRef = null, lastShowBlk = null;
    /* 整体重画: 行全删 + 游标归零 + 视图缓存作废 */
    function hardReset() {
      qsa(".rw", box).forEach(function (n) { n.remove(); });
      R = 0;
      VIEW = null;
    }
    /* 「显示屏蔽」: 只切这个列表的视图, 不碰播放器里的过滤。
       默认值记住上次选择(localStorage 可能被禁用 → try/catch 兜住)。 */
    var showBlk = false;
    try { showBlk = localStorage.getItem("msx_dm_show_blk") === "1"; } catch (e) {}
    function syncBlkBtn() {
      if (!blkBtn) return;
      var n = (DMF.hidden || []).length;
      var pend = 0;
      try { pend = DMF.pendingCount ? DMF.pendingCount() : 0; } catch (e) {}
      /* 没有屏蔽项但还有画像没到 → 显示「…」: 否则用户会以为筛选没生效(实际是在等壳
         后台限速查账号资料, 查到一个就一条条更新)。 */
      blkBtn.textContent = (showBlk ? "隐藏屏蔽" : "显示屏蔽")
        + (n ? " " + n : (pend ? " …" : ""));
      blkBtn.title = n
        ? "在列表里显示被「弹幕评论筛选」屏蔽掉的弹幕（只看不改：播放器里的过滤不受影响）"
        : (pend ? "正在补账号画像（壳在后台限速查，查到一个就生效一条）—— 稍等片刻"
                : "在列表里显示被「弹幕评论筛选」屏蔽掉的弹幕（只看不改：播放器里的过滤不受影响）");
      blkBtn.classList.toggle("on", showBlk);
    }
    if (blkBtn) blkBtn.addEventListener("click", function () {
      showBlk = !showBlk;
      try { localStorage.setItem("msx_dm_show_blk", showBlk ? "1" : "0"); } catch (e) {}
      hardReset();                          // 视图变了 → 整体重画
      syncBlkBtn();
      chunk();
    });
    function closePick() { pickEl.classList.remove("on"); pickEl.innerHTML = ""; }
    pickEl.addEventListener("click", function (ev) { if (ev.target === pickEl) closePick(); });
    /* 多候选选择框: hash 逆不唯一(B站弹幕只给 8 位哈希, 几个账号都能对上) → 让用户自己挑 */
    function pickSender(cands, h) {
      var html = '<div class="pb"><h4>这条弹幕的哈希对应多个账号</h4>'
        + '<div class="sub" style="margin-bottom:7px">B站弹幕只给 8 位哈希，下面 '
        + cands.length + ' 个账号都能对上（#' + esc(h) + '）；点一个打开它的主页</div>';
      for (var i = 0; i < cands.length; i++) {
        var c = cands[i];
        html += '<div class="pi" data-mid="' + esc(c.mid) + '">'
          + (c.face ? '<img src="' + esc(c.face) + '" referrerpolicy="no-referrer" alt="">' : '')
          + '<span class="nm">' + esc(c.name || ("UID " + c.mid)) + '</span>'
          + '<span class="sub">' + esc(c.mid) + '</span></div>';
      }
      html += '<div class="pc"><button type="button" id="msxDmPickX">取消</button></div></div>';
      pickEl.innerHTML = html;
      pickEl.classList.add("on");
      var bx = pickEl.querySelector("#msxDmPickX");
      if (bx) bx.addEventListener("click", closePick);
      qsa(".pi", pickEl).forEach(function (n) {
        n.addEventListener("click", function () {
          var m = n.dataset.mid;
          closePick();
          if (m) window.open("https://space.bilibili.com/" + m, "_blank");
        });
      });
    }
    function fmtT(ms) {
      var sec = Math.floor((Number(ms) || 0) / 1000);
      var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s2 = sec % 60;
      return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s2).padStart(2, "0");
    }
    function fmtD(ts) {
      var d = new Date(Number(ts) * 1000);
      if (isNaN(d.getTime())) return "";
      return String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0")
        + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    }
    function rowEl(o) {
      var blk = !!o.why;                    // 只有 FEED.hidden 里的行带 why
      var el = document.createElement("div");
      el.className = "rw" + (blk ? " blk" : "");
      el.dataset.h = o.h || "";
      el.innerHTML = '<span class="t">' + esc(fmtT(o.t)) + '</span><span class="x">'
        + (blk ? '<span class="wy"' + (String(o.why).indexOf("候选") >= 0
            ? ' title="B站弹幕只给发送者 ID 的 8 位哈希, 该哈希对上了多个账号 ——'
              + '这条是按「哈希对上多个账号时」的策略判定的, 不一定是它发的"' : '')
            + '>' + esc(o.why) + '</span>' : '') + esc(o.text)
        + '</span><span class="c">' + esc(fmtD(o.ct)) + '</span>';
      el.addEventListener("click", function () {
        var h = el.dataset.h;
        if (!h) { toastIn(root, "该弹幕没有发送者信息"); return; }
        if (el.dataset.busy === "1") return;         // 连点: 同一条只解一次
        el.dataset.busy = "1";
        var fin = function () { el.dataset.busy = ""; };
        var tm = setTimeout(fin, 15000);             // 兜底: 请求卡住也要能再点
        toastIn(root, "正在解析发送者…");
        var pr = DMF.resolveFull ? DMF.resolveFull(h)
          : DMF.resolve(h).then(function (m) {
              return { ok: true, mid: m, ncand: 0, cands: [], why: "" };
            });
        pr.then(function (j) {
          clearTimeout(tm); fin();
          if (!j || j.ok === false) { toastIn(root, "解析失败：弹幕哈希不合法"); return; }
          var m = j.mid ? String(j.mid) : "";
          if (m) { window.open("https://space.bilibili.com/" + m, "_blank"); return; }
          var cs = j.cands || [];
          if (cs.length > 1) { pickSender(cs, h); return; }
          if (cs.length === 1) { window.open("https://space.bilibili.com/" + cs[0].mid, "_blank"); return; }
          toastIn(root, j.why === "newuid"
            ? "查不到发送者：该 UID 在 11 位以上，B站只给哈希，无法还原"
            : "查不到发送者：B站只给哈希，无法还原该 UID");
        })["catch"](function () {
          clearTimeout(tm); fin();
          toastIn(root, "解析失败，请稍后再试");
        });
      });
      return el;
    }
    /* 当前要渲染的行: 关 → 只有保留的; 开 → 保留 + 被屏蔽的(按时间合并)。
       合并结果缓存(滚动到底会反复调 chunk, 每次都重排会白烧 CPU)。 */
    var VIEW = null;
    function view() {
      if (VIEW) return VIEW;
      var rows = DMF.rows || [];
      if (!showBlk) { VIEW = rows; return VIEW; }
      var all = rows.concat(DMF.hidden || []);
      all.sort(function (a, b) { return (a.t || 0) - (b.t || 0); });
      VIEW = all;
      return VIEW;
    }
    function chunk() {
      if (lastOid !== DMF.oid) {           // 换视频/换P → 清空重挂
        lastOid = DMF.oid;
        hardReset();
      }
      /* ⚠️ v0.3.1.28: 源列表(rows/hidden)每次重算都是**新数组**(dmfilter.refilter 整体替换)
         —— 用引用变化判定"必须整体重画"。旧写法只追加(`if (end > R)`): 画像回填后有行从
         「保留」挪进「屏蔽」时列表长度可能不变 → 界面永远不动; 关掉「显示屏蔽」列表变短
         时已画出来的屏蔽行也删不掉。用户看到的就是"切了条数一条没变"。 */
      if (DMF.rows !== lastRowsRef || DMF.hidden !== lastHiddenRef || showBlk !== lastShowBlk) {
        lastRowsRef = DMF.rows;
        lastHiddenRef = DMF.hidden;
        lastShowBlk = showBlk;
        hardReset();
      }
      var rows = view();
      var end = Math.min(rows.length, R + 120);
      if (end > R) {
        var frag = document.createDocumentFragment();
        for (var i = R; i < end; i++) frag.appendChild(rowEl(rows[i]));
        box.insertBefore(frag, moreEl);
        R = end;
      }
      /* 只报总行数: 屏蔽条数由按钮自己带("隐藏屏蔽 3")。
         早先在这里补「（含屏蔽 N）」会把头部顶到第二行, 一开一关还跟着抖动。 */
      nEl.textContent = rows.length ? ("共 " + rows.length + " 条") : "";
      tipEl.style.display = rows.length ? "none" : "";
      if (!rows.length) tipEl.textContent = R ? "" : "加载中…（弹幕按分段陆续到位）";
      moreEl.style.display = (R < rows.length) ? "" : "none";
    }
    var pend = 0;
    DMF.onChange = function () {           // 新分段/换视频 → 节流重画
      VIEW = null;
      syncBlkBtn();                        // 屏蔽条数是流式涨上来的, 按钮计数跟着走
      if (pend) return;
      pend = setTimeout(function () { pend = 0; try { chunk(); } catch (e) {} }, 120);
    };
    moreEl.addEventListener("click", chunk);
    box.addEventListener("scroll", function () {
      if (box.scrollTop + box.clientHeight >= box.scrollHeight - 40) chunk();
    }, { passive: true });
    syncBlkBtn();
    chunk();                        // 第一屏先在离屏渲染好
    /* ⚠️ v0.3.1.26: 整块 UI 建完才挂上去 —— 挂空壳再填内容会让侧栏闪一下(同 buildRelated)。 */
    var rel = qs("#msx-ownui-related", inner);
    if (rel && rel.parentNode === inner) inner.insertBefore(hostEl, rel);
    else inner.appendChild(hostEl);
    S.hosts.push(hostEl);
    S.dmFeed = DMF;
    return true;
  }

  /* ================= 评论区 ================= */
  function buildComments() {
    var box = qs("#comment") || qs(".comment-container") || qs(".comment");
    if (!box) return false;
    var parent = box.parentNode;
    if (!parent) return false;
    var anchor = box.nextSibling;          // 挂载位置先记下, 建完再插
    var hostEl = document.createElement("div");
    hostEl.id = "msx-ownui-comments";
    hostEl.dataset.msxOwn = "1";
    var root = hostEl.attachShadow({ mode: "open" });
    var p = palette();
    root.innerHTML = "<style>" + baseCSS(p)
      + ".wrap{margin-top:8px;border-radius:10px;background:" + p.card + ";padding:12px 16px}"
      + ".hd{display:flex;align-items:center;gap:14px;border-bottom:1px solid " + p.line + ";padding-bottom:8px;margin-bottom:8px}"
      + ".hd b{font-size:15px}"
      + ".hd .sort{cursor:pointer;color:" + p.sub + ";font-size:13px}"
      + ".hd .sort.on{color:" + p.accent + ";font-weight:600}"
      + ".pub{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid " + p.line + "}"
      + ".pub img{width:38px;height:38px}"
      + ".pub textarea{flex:1;height:64px;resize:vertical;border:1px solid " + p.line
      + ";border-radius:8px;background:" + p.input + ";color:" + p.text + ";padding:8px;font:13px/1.5 inherit}"
      + ".pub textarea:focus{outline:none;border-color:" + p.accent + "}"
      + ".pub .ops{display:flex;justify-content:flex-end;margin-top:6px}"
      + ".cm{display:flex;gap:10px;padding:12px 0;border-bottom:1px solid " + p.line + "}"
      + ".cm:last-child{border-bottom:none}"
      + ".cm img{width:38px;height:38px;flex:0 0 38px}"
      + ".cm .bd{flex:1;min-width:0}"
      + ".cm .nm{font-size:13px;color:" + p.sub + "}"
      + ".cm .nm .tag{color:" + p.accent + ";border:1px solid " + p.accent + ";border-radius:3px;font-size:10px;padding:0 3px;margin-left:4px}"
      + ".cm .msg{margin-top:2px;font-size:14px;word-break:break-word;white-space:pre-wrap}"
      + ".cm .msg img{width:24px;height:24px;vertical-align:-6px;border-radius:4px;margin:0 1px}"
      + ".cm .ops{margin-top:4px;display:flex;gap:16px;align-items:center}"
      + ".cm .ops .op{cursor:pointer;color:" + p.sub + ";font-size:12px}"
      + ".cm .ops .op:hover{color:" + p.accent + "}"
      + ".cm .ops .op.liked{color:" + p.accent + "}"
      + ".cm .like:before{content:'\\2661 '}"
      + ".cm .like.liked:before{content:'\\2665 '}"
      + ".sublist{margin-left:48px;border-left:2px solid " + p.line + ";padding-left:10px}"
      + ".sublist .cm{padding:8px 0}"
      + ".sublist .cm img{width:28px;height:28px;flex:0 0 28px}"
      + ".pager{display:flex;justify-content:center;gap:6px;padding:12px 0}"
      + ".pager button{min-width:32px;padding:4px 10px}"
      + ".pager button.cur{background:" + p.accent + ";border-color:" + p.accent + ";color:#fff}"
      + ".note{padding:10px 0;text-align:center}"
      + "</style>"
      + '<div class="wrap"><div class="toast"></div>'
      + '<div class="hd"><b>评论 <span id="msxCnt" class="sub"></span></b>'
      + '<span class="sort" data-sort="3">按热度</span><span class="sort" data-sort="2">按时间</span></div>'
      + '<div id="msxPub"></div><div id="msxCm" class="sub">加载中…</div><div class="pager" id="msxPg"></div></div>';
    S.cmtRoot = root;
    renderPubBox();
    qsa(".sort", root).forEach(function (el) {
      el.classList.toggle("on", el.dataset.sort === String(S.cmtSort));
      el.addEventListener("click", function () {
        if (S.cmtSort === Number(el.dataset.sort)) return;
        S.cmtSort = Number(el.dataset.sort);
        qsa(".sort", root).forEach(function (x) { x.classList.toggle("on", x === el); });
        loadComments(1);
      });
    });
    loadComments(1);
    /* ⚠️ v0.3.1.26: UI 建完才挂 + 才藏原生评论区 —— 否则会先露一眼原生/空白再被顶掉。 */
    parent.insertBefore(hostEl, anchor);
    S.hosts.push(hostEl);
    box.style.setProperty("display", "none", "important");
    S.hidden.push(box);
    return true;
  }

  function renderPubBox() {
    var root = S.cmtRoot;
    var el = root.getElementById("msxPub");
    if (!user.login) {
      el.innerHTML = '<div class="note sub">登录后可发表评论（右上角头像 → 登录B站）</div>';
      return;
    }
    el.innerHTML = '<div class="pub"><img src="' + esc(user.face) + '" referrerpolicy="no-referrer">'
      + '<div style="flex:1"><textarea id="msxMsg" placeholder="发一条友善的评论"></textarea>'
      + '<div class="ops"><button class="pri" id="msxSend">发表</button></div></div></div>';
    var btn = root.getElementById("msxSend");
    btn.addEventListener("click", function () {
      var msg = root.getElementById("msxMsg").value.trim();
      if (!msg) return;
      btn.disabled = true;
      apiPost("https://api.bilibili.com/x/v2/reply/add",
              { oid: S.aid, type: 1, message: msg, csrf: CSRF, plat: 1 })
        .then(function (j) {
          btn.disabled = false;
          if (j.code !== 0) { toastIn(root, "发表失败: " + (j.message || j.code)); return; }
          root.getElementById("msxMsg").value = "";
          toastIn(root, "已发表");
          loadComments(1);
        })
        .catch(function (e) { btn.disabled = false; toastIn(root, "发表失败: " + e); });
    });
  }

  function emoteHTML(msg, emote) {
    var html = esc(msg);
    if (emote) {
      Object.keys(emote).forEach(function (k) {
        var u = emote[k] && emote[k].url;
        if (u) html = html.split(esc(k)).join(
          '<img src="' + esc(u.replace(/^http:/, "https:")) + '" referrerpolicy="no-referrer" alt="' + esc(k) + '">');
      });
    }
    return html;
  }

  function commentNode(r, isSub) {
    var el = document.createElement("div");
    el.className = "cm";
    var liked = Number(r.action) === 1;
    var tag = (S.owner && r.member && Number(r.member.mid) === Number(S.owner.mid))
      ? '<span class="tag">UP</span>' : "";
    el.innerHTML = '<img loading="lazy" src="' + esc((r.member && r.member.avatar || "").replace(/^http:/, "https:"))
      + '" referrerpolicy="no-referrer"><div class="bd"><div class="nm">' + esc(r.member && r.member.uname || "用户")
      + tag + '</div><div class="msg">' + emoteHTML(r.content && r.content.message, r.content && r.content.emote) + '</div>'
      + '<div class="ops"><span class="op like' + (liked ? " liked" : "") + '" data-rpid="' + esc(r.rpid) + '">'
      + fmtCount(r.like) + '</span>'
      + (isSub ? "" : '<span class="op reply">回复</span>')
      + '<span class="op sub" style="cursor:default">' + timeago(r.ctime) + '</span></div>'
      + '<div class="subbox"></div></div>';
    // 点赞
    qs(".like", el).addEventListener("click", function () {
      if (!user.login) { toastIn(S.cmtRoot, "登录后可点赞"); return; }
      var on = liked;
      apiPost("https://api.bilibili.com/x/v2/reply/action",
              { oid: S.aid, rpid: r.rpid, type: 1, action: on ? 0 : 1, csrf: CSRF })
        .then(function (j) {
          if (j.code !== 0) { toastIn(S.cmtRoot, "操作失败: " + (j.message || j.code)); return; }
          liked = !on;
          var n = Number(r.like || 0) + (liked ? 1 : -1);
          r.like = Math.max(0, n);
          var sp = qs(".like", el);
          sp.classList.toggle("liked", liked);
          sp.textContent = fmtCount(r.like);
        }).catch(function (e) { toastIn(S.cmtRoot, "操作失败: " + e); });
    });
    // 回复框
    var rbtn = qs(".reply", el);
    if (rbtn) {
      rbtn.addEventListener("click", function () {
        var sb = qs(".subbox", el);
        if (qs(".pub", sb)) { sb.innerHTML = ""; return; }
        if (!user.login) { toastIn(S.cmtRoot, "登录后可回复"); return; }
        var d = document.createElement("div");
        d.className = "pub";
        d.innerHTML = '<div style="flex:1"><textarea placeholder="回复 ' + esc(r.member.uname) + '"></textarea>'
          + '<div class="ops"><button class="pri">回复</button></div></div>';
        var ta = qs("textarea", d), btn = qs("button", d);
        btn.addEventListener("click", function () {
          var msg = ta.value.trim();
          if (!msg) return;
          btn.disabled = true;
          apiPost("https://api.bilibili.com/x/v2/reply/add",
                  { oid: S.aid, type: 1, message: msg, csrf: CSRF, plat: 1,
                    root: r.rpid, parent: r.rpid })
            .then(function (j) {
              btn.disabled = false;
              if (j.code !== 0) { toastIn(S.cmtRoot, "回复失败: " + (j.message || j.code)); return; }
              toastIn(S.cmtRoot, "已回复");
              loadSubs(r, el, qs(".sublist", el), true);
            }).catch(function (e) { btn.disabled = false; toastIn(S.cmtRoot, "回复失败: " + e); });
        });
        sb.appendChild(d);
        ta.focus();
      });
    }
    return el;
  }

  function loadSubs(r, el, box, force) {
    if (box && box.dataset.loaded === "1" && !force) { box.style.display = box.style.display === "none" ? "" : "none"; return; }
    box.innerHTML = '<div class="sub sub" style="padding:6px 0">加载中…</div>';
    box.dataset.loaded = "1";
    apiGet("https://api.bilibili.com/x/v2/reply/reply?type=1&oid=" + S.aid + "&root=" + r.rpid + "&pn=1&ps=10")
      .then(function (j) {
        if (!j || j.code !== 0) throw (j && j.message) || j.code;
        var reps = (j.data && j.data.replies) || [];
        box.innerHTML = "";
        var list = document.createElement("div");
        list.className = "sublist";
        box.appendChild(list);
        reps.filter(cfPass).forEach(function (s2) { list.appendChild(commentNode(s2, true)); });
        var page = j.data && j.data.page;
        if (page && page.count > reps.length) {
          var more = document.createElement("div");
          more.className = "sub op";
          more.style.cssText = "cursor:pointer;padding:6px 0;color:#fb7299";
          more.textContent = "展开更多回复(共" + page.count + "条)";
          var pn = 1;
          more.addEventListener("click", function () {
            pn += 1;
            apiGet("https://api.bilibili.com/x/v2/reply/reply?type=1&oid=" + S.aid
                   + "&root=" + r.rpid + "&pn=" + pn + "&ps=10")
              .then(function (j2) {
                if (j2.code !== 0) { more.textContent = "加载失败"; return; }
                (j2.data.replies || []).filter(cfPass).forEach(function (s3) { list.appendChild(commentNode(s3, true)); });
                if (pn * 10 >= page.count) more.remove();
              });
          });
          box.appendChild(more);
        }
      })
      .catch(function (e) { box.innerHTML = '<div class="sub" style="padding:6px 0">子回复加载失败(' + e + ')</div>'; });
  }

  function loadComments(pn) {
    var root = S.cmtRoot;
    if (!S.aid) {
      root.getElementById("msxCm").textContent = "未取到视频 aid，评论不可用";
      return;
    }
    S.cmtPage = pn;
    var boxEl = root.getElementById("msxCm");
    boxEl.innerHTML = '<div class="note sub">加载中…</div>';
    apiGet("https://api.bilibili.com/x/v2/reply?type=1&oid=" + S.aid
           + "&pn=" + pn + "&ps=20&sort=" + S.cmtSort)
      .then(function (j) {
        if (!j || j.code !== 0) throw (j && j.message) || j.code;
        var d = j.data || {};
        root.getElementById("msxCnt").textContent = d.count ? "(" + fmtCount(d.count) + ")" : "";
        var reps = d.replies || [];
        if (!reps.length) { boxEl.innerHTML = '<div class="note sub">还没有评论</div>'; renderPager(d.page); return; }
        boxEl.innerHTML = "";
        var frag = document.createDocumentFragment();
        // 置顶
        if (d.upper && d.upper.top) frag.appendChild(commentNode(d.upper.top, false));
        reps.filter(cfPass).forEach(function (r) {
          var el = commentNode(r, false);
          if (Number(r.count) > 0) {
            var sub = document.createElement("div");
            sub.className = "subbox";
            el.querySelector(".bd").appendChild(sub);
            var tgl = document.createElement("div");
            tgl.className = "op sub";
            tgl.style.cssText = "cursor:pointer;margin-top:4px;color:#fb7299";
            tgl.textContent = "共 " + r.count + " 条回复 ▾";
            tgl.addEventListener("click", function () { loadSubs(r, el, sub); });
            el.querySelector(".bd").insertBefore(tgl, sub);
          }
          frag.appendChild(el);
        });
        boxEl.appendChild(frag);
        renderPager(d.page);
      })
      .catch(function (e) {
        S.degraded = true;
        boxEl.innerHTML = '<div class="note">评论加载失败(' + esc(e) + ')<div class="sub">接口可能已升级签名校验，自有UI已降级只读</div></div>';
      });
  }

  function renderPager(page) {
    var root = S.cmtRoot;
    var pg = root.getElementById("msxPg");
    pg.innerHTML = "";
    if (!page || !page.count) return;
    var total = Math.min(Math.ceil(page.count / (page.size || 20)), 200);
    var from = Math.max(1, S.cmtPage - 2), to = Math.min(total, from + 4);
    function mk(n, label, cur) {
      var b = document.createElement("button");
      b.textContent = label;
      if (cur) b.className = "cur";
      b.addEventListener("click", function () { if (!cur) loadComments(n); });
      return b;
    }
    if (from > 1) pg.appendChild(mk(1, "首页"));
    for (var i = from; i <= to; i++) pg.appendChild(mk(i, String(i), i === S.cmtPage));
    if (to < total) pg.appendChild(mk(total, "末页"));
  }

  /* ================= 挂载/卸载 ================= */
  function enforce() {
    // 幂等: 防止 Vue 复活被隐藏的原生节点
    S.hidden.forEach(function (n) {
      if (n && n.isConnected && n.style.display !== "none") {
        n.style.setProperty("display", "none", "important");
      }
    });
  }
  function teardown() {
    S.hosts.forEach(function (h) { if (h && h.parentNode) h.parentNode.removeChild(h); });
    S.hidden.forEach(function (n) { if (n) n.style.removeProperty("display"); });
    S.styles.forEach(function (s) { if (s && s.parentNode) s.parentNode.removeChild(s); });
    S.hosts = []; S.hidden = []; S.styles = []; S.cmtRoot = null;
    S.cmtPage = 1;
    if (S.dmFeed) { try { S.dmFeed.onChange = null; } catch (e) {} S.dmFeed = null; }
  }
  function init() {
    loadMeta().then(function () {
      apiGet("https://api.bilibili.com/x/web-interface/nav").then(function (j) {
        if (j && j.code === 0 && j.data && j.data.isLogin) {
          user.login = true;
          user.face = (j.data.face || "").replace(/^http:/, "https:");
          user.uname = j.data.uname || "";
          user.mid = j.data.mid || 0;
        }
        buildRelated();
        try { buildDanmaku(); } catch (e) {}
        buildComments();
        bootMaskOff();   // 自有UI已挂上, 摘启动遮罩
      }).catch(function () {
        buildRelated();
        try { buildDanmaku(); } catch (e) {}
        buildComments();
        bootMaskOff();
      });
    });
  }

  /* B站头部就绪闸门: 播放页加载中会把旧 mini-header 整体换成新 bili-header__menu,
   * 实测(2026-10-08 截图+DOM双探针取证): 在切换完成前隐藏 #comment/右栏容器
   * 会打断 B站 自己的头部渲染 —— 旧头部被拆、新头部不绘制, 顶栏整条消失。
   * 因此: 必须等新头部出现(或超时兜底)再隐藏原生容器/挂载自有UI。 */
  function whenHeaderSettled(cb) {
    var t0 = Date.now();
    (function poll() {
      var fresh = qs(".bili-header__menu") || qs(".left-entry-main");
      if (fresh || Date.now() - t0 > 8000) setTimeout(cb, 350);   // 切完稳一下再动手
      else setTimeout(poll, 200);
    })();
  }

  function waitAndStart() {
    bootMaskOn();   // (重)戴遮罩: 路由重建期间新页面的原生容器同样不裸露
    // 等评论/右栏容器出现(懒加载), 15s 找不到就放弃 → 原生页面兜底
    var t0 = Date.now();
    (function tryOnce() {
      var okC = qs("#comment") || qs(".comment-container") || qs(".comment");
      var okR = qs(".right-container") || qs("#right-container");
      if (okC || okR) { whenHeaderSettled(init); return; }
      else if (Date.now() - t0 < 15000) setTimeout(tryOnce, 500);
      else bootMaskOff();   // 放弃挂载 → 恢复原生页面可见
    })();
  }

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once: true });
    else fn();
  }
  onReady(function () {
    waitAndStart();
    // SPA 路由 watcher: BV 变化 → teardown + 重建; enforce 兜底防 Vue 复活
    S.tick = setInterval(function () {
      enforce();
      slimNav();   // 顶栏导航条目可能被 Vue 重挂, 周期性补隐
      if (location.pathname === S.lastPath) return;
      S.lastPath = location.pathname;
      teardown();
      if (/^\/video\//.test(S.lastPath)) {
        // 复位后重新门控(切到非视频页不再挂载)
        if (window.__MSX_OWNUI__ === 1) waitAndStart();
      } else {
        bootMaskOff();   // 离开视频页: .comment 是通用类名, 遮罩不能带到别的页面
      }
    }, 800);
  });
})();
