/* 洞鉴虚明 · B站播放页自有UI (v0.3.2) —— 注入脚本(document-start, MAIN world)
 *
 * 门控: window.__MSX_OWNUI__ === 1(壳按设置键 ui_ownui 注入) 且 www.bilibili.com/video/*。
 * 作用: 评论区 + 右侧栏整块替换为自有UI(Shadow DOM, 数据走B站官方web API+壳内登录态);
 *       播放器/弹幕/三连工具条 DOM 一律不碰 —— 超分管线红线(shell_pages 另行注入)。
 * 降级: 任一区域选择器找不到/接口异常 → 静默保持原生, 绝不让页面白屏。
 * 主题: 读 theme_style cookie 跟随B站原生深浅(壳内深色由 CookieManager 写入)。
 */
(function () {
  "use strict";
  if (window.__msxOwnui) return;
  window.__msxOwnui = 1;
  if (window.__MSX_OWNUI__ !== 1) return;
  if (location.hostname !== "www.bilibili.com") return;
  if (!/^\/video\//.test(location.pathname)) return;

  /* ---------------- 工具 ---------------- */
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
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

  /* ================= 右栏: UP主卡 + 相关推荐 ================= */
  function buildRelated() {
    var rc = qs(".right-container") || qs("#right-container");
    if (!rc) return false;
    qsa(":scope > *", rc).forEach(function (n) {
      if (n.dataset && n.dataset.msxOwn === "1") return;
      n.style.setProperty("display", "none", "important");
      S.hidden.push(n);
    });
    var hostEl = document.createElement("div");
    hostEl.id = "msx-ownui-related";
    hostEl.dataset.msxOwn = "1";
    rc.appendChild(hostEl);
    S.hosts.push(hostEl);
    var root = hostEl.attachShadow({ mode: "open" });
    var p = palette();
    root.innerHTML = "<style>" + baseCSS(p)
      + ".wrap{padding:0 0 16px}"
      + ".up{display:flex;align-items:center;gap:10px;padding:12px;border-radius:10px;background:" + p.card + ";margin-bottom:10px}"
      + ".up img{width:44px;height:44px}"
      + ".up .nm{font-weight:600;font-size:14px}"
      + ".up .fans{margin-top:2px}"
      + ".rel{font-size:15px;font-weight:600;padding:4px 12px}"
      + ".it{display:flex;gap:8px;padding:8px 12px;border-radius:10px;cursor:pointer}"
      + ".it:hover{background:" + p.hover + "}"
      + ".it .cv{position:relative;flex:0 0 152px;height:86px;border-radius:8px;overflow:hidden;background:" + p.hover + "}"
      + ".it .cv img{width:152px;height:86px;border-radius:8px;display:block}"
      + ".it .cv .du{position:absolute;right:4px;bottom:4px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;padding:0 4px;border-radius:4px;line-height:16px}"
      + ".it .tx{flex:1;min-width:0}"
      + ".it .tt{font-size:13px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}"
      + ".it .mt{margin-top:4px}"
      + ".err{padding:12px}"
      + "</style>"
      + '<div class="wrap"><div class="toast"></div><div class="up" id="msxUp"><span class="sub">加载中…</span></div>'
      + '<div class="rel">相关推荐</div><div id="msxRel" class="sub" style="padding:8px 12px">加载中…</div></div>';
    // UP卡
    var upEl = root.getElementById("msxUp");
    if (S.owner) {
      upEl.innerHTML = '<img src="' + esc((S.owner.face || "").replace(/^http:/, "https:")) + '" referrerpolicy="no-referrer">'
        + '<div style="flex:1;min-width:0"><div class="nm">' + esc(S.owner.name) + '</div>'
        + '<div class="fans sub" id="msxFans">…</div></div>';
      apiGet("https://api.bilibili.com/x/relation/stat?vmid=" + S.owner.mid)
        .then(function (j) {
          if (j && j.code === 0 && root.getElementById("msxFans")) {
            root.getElementById("msxFans").textContent = "粉丝 " + fmtCount(j.data.follower);
          }
        }).catch(function () {});
    } else {
      upEl.innerHTML = '<span class="sub">未取到UP主信息</span>';
    }
    // 相关推荐
    var listEl = root.getElementById("msxRel");
    apiGet("https://api.bilibili.com/x/web-interface/archive/related?bvid=" + encodeURIComponent(S.bvid))
      .then(function (j) {
        if (!j || j.code !== 0 || !j.data || !j.data.length) throw (j && j.message) || "empty";
        listEl.className = "";
        listEl.innerHTML = j.data.map(function (b) {
          return '<div class="it" data-bvid="' + esc(b.bvid) + '">'
            + '<div class="cv"><img loading="lazy" src="' + esc(String(b.pic || "").replace(/^http:/, "https:")) + '@200w_112h.webp" referrerpolicy="no-referrer">'
            + '<span class="du">' + fmtDur(b.duration) + '</span></div>'
            + '<div class="tx"><div class="tt">' + esc(b.title) + '</div>'
            + '<div class="mt sub">' + esc(b.owner && b.owner.name || "") + '</div>'
            + '<div class="sub">' + fmtCount(b.stat && b.stat.view) + '播放 ' + fmtCount(b.stat && b.stat.danmaku) + '弹幕</div></div></div>';
        }).join("");
        qsa(".it", listEl).forEach(function (el) {
          el.addEventListener("click", function () {
            location.href = "https://www.bilibili.com/video/" + el.dataset.bvid;
          });
        });
      })
      .catch(function (e) {
        listEl.textContent = "相关推荐加载失败(" + e + ")";
      });
    return true;
  }

  /* ================= 评论区 ================= */
  function buildComments() {
    var box = qs("#comment") || qs(".comment-container") || qs(".comment");
    if (!box) return false;
    box.style.setProperty("display", "none", "important");
    S.hidden.push(box);
    var parent = box.parentNode;
    if (!parent) return false;
    var hostEl = document.createElement("div");
    hostEl.id = "msx-ownui-comments";
    hostEl.dataset.msxOwn = "1";
    parent.insertBefore(hostEl, box.nextSibling);
    S.hosts.push(hostEl);
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
        reps.forEach(function (s2) { list.appendChild(commentNode(s2, true)); });
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
                (j2.data.replies || []).forEach(function (s3) { list.appendChild(commentNode(s3, true)); });
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
        reps.forEach(function (r) {
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
    S.hosts = []; S.hidden = []; S.cmtRoot = null;
    S.cmtPage = 1;
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
        buildComments();
      }).catch(function () {
        buildRelated();
        buildComments();
      });
    });
  }

  function waitAndStart() {
    // 等评论/右栏容器出现(懒加载), 15s 找不到就放弃 → 原生页面兜底
    var t0 = Date.now();
    (function tryOnce() {
      var okC = qs("#comment") || qs(".comment-container") || qs(".comment");
      var okR = qs(".right-container") || qs("#right-container");
      if (okC || okR) init();
      else if (Date.now() - t0 < 15000) setTimeout(tryOnce, 500);
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
      if (location.pathname === S.lastPath) return;
      S.lastPath = location.pathname;
      teardown();
      if (/^\/video\//.test(S.lastPath)) {
        // 复位后重新门控(切到非视频页不再挂载)
        if (window.__MSX_OWNUI__ === 1) waitAndStart();
      }
    }, 800);
  });
})();
