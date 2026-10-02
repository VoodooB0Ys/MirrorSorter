/* 洞鉴虚明 v0.2.9.0a - 前端逻辑 */
"use strict";

const $ = (s) => document.querySelector(s);

/* ---------- 链接跳转统一出口 ----------
   桌面窗口壳(pywebview, 见 app_window.py)内 → 应用内置浏览器窗口;
   普通浏览器/安卓壳 → 原生 window.open(安卓壳的 WebView 会转交系统浏览器)。
   壳未就绪/桥被拒/返回非 ok 时都退回 window.open, 任何环境都能打开。 */
function openExternal(url) {
  try {
    const a = window.pywebview && window.pywebview.api;
    if (a && a.open_in_app) {
      Promise.resolve(a.open_in_app(url, "")).then((r) => {
        if (r !== "ok") window.open(url, "_blank", "noopener");
      }).catch(() => window.open(url, "_blank", "noopener"));
      return;
    }
  } catch (_) {}
  window.open(url, "_blank", "noopener");
}

const pageLeaseToken = crypto.randomUUID ? crypto.randomUUID() :
  (Date.now().toString(36) + Math.random().toString(36).slice(2));
function sendPageLease(action, keepalive = false) {
  const text = JSON.stringify({ action, token: pageLeaseToken });
  if (keepalive && navigator.sendBeacon) {
    navigator.sendBeacon("/api/shutdown", new Blob([text], { type: "application/json" }));
    return;
  }
  fetch("/api/shutdown", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: text,
    keepalive,
  }).catch(() => {});
}
sendPageLease("alive");
window.addEventListener("pageshow", () => sendPageLease("alive"));
window.addEventListener("pagehide", () => sendPageLease("leaving", true));
/* 存活心跳: 浏览器被直接杀掉/崩溃时 pagehide 发不出, 服务端租约看门狗按最后心跳判超时收尾。
   后台隐藏页的定时器会被节流到~1分钟, 间隔取30s、服务端宽限150s */
setInterval(() => sendPageLease("alive"), 30000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) sendPageLease("alive");
});

/* UI 自动更新: 记录加载时的前端文件指纹, 服务端文件变化后自动重载, 不再依赖手动 Ctrl+F5 */
let _uiVersion = null;
setInterval(async () => {
  try {
    const r = await fetch("/api/ui_version", { cache: "no-store" });
    const d = await r.json();
    if (!_uiVersion) { _uiVersion = d.version; return; }
    if (d.version && d.version !== _uiVersion) location.reload();
  } catch (_) {}
}, 30000);

const messageButtons = {
  bili: "#biliMessage",
  tieba: "#tbMessage",
  xhh: "#xhhMessage",
};
function updateMessageButton(platform, data) {
  const button = $(messageButtons[platform]);
  if (!button) return;
  const loggedIn = !!(data && data.logged_in);
  button.classList.toggle("hidden", !loggedIn);
  const badge = button.querySelector(".message-badge");
  const unread = Number.isFinite(data && data.unread) ? Math.max(0, data.unread) : 0;
  if (badge) {
    badge.textContent = unread > 99 ? "99+" : (unread ? String(unread) : "");
    badge.classList.toggle("hidden", unread <= 0);
  }
  button.onclick = (e) => {
    e.stopPropagation();
    if (data && data.url) openExternal(data.url);
  };
}
async function loadMessages() {
  try {
    const d = await api("/api/messages");
    ["bili", "tieba", "xhh"].forEach((platform) => updateMessageButton(platform, d[platform]));
  } catch (_) {
    ["bili", "tieba", "xhh"].forEach((platform) => updateMessageButton(platform, null));
  }
}
setInterval(loadMessages, 60000);

const state = {
  tab: "status",           // status | history | saved
  savedKind: "later",      // later | fav
  q: "",
  liked: new Set(),
  disliked: new Set(),
};

/* ---------- 工具 ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  return r.json();
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// 账号昵称展示: 后端取不到真名时会存成占位串"已登录"(或为空),
// 这里视为无昵称, 避免与"已登录"状态标签重复(如"已登录已登录")
function acctName(nick) {
  const n = String(nick || "").trim();
  return (n && n !== "已登录") ? n : "";
}
function fmt(n) {
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(n);
}
function ago(ts) {
  if (!ts) return "";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + "分钟前";
  if (s < 86400) return Math.floor(s / 3600) + "小时前";
  if (s < 86400 * 30) return Math.floor(s / 86400) + "天前";
  const d = new Date(ts * 1000);
  return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
}
function toast(msg, opt = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (opt.warn ? " warn" : "");
  el.innerHTML = "<span>" + esc(msg) + "</span>";
  if (opt.undo) {
    const b = document.createElement("button");
    b.textContent = "撤销";
    b.onclick = () => { opt.undo(); el.remove(); };
    el.appendChild(b);
  }
  $("#toastWrap").appendChild(el);
  if (!opt.sticky) setTimeout(() => el.remove(), opt.undo ? 5000 : 2600);
}

/* ---------- 主题（白天/黑夜 + 强调色） ---------- */
const ACCENTS = [
  { id: "pink", color: "#fb7299", name: "粉红" },
  { id: "blue", color: "#4a9eff", name: "蓝" },
  { id: "green", color: "#2fbf71", name: "绿" },
  { id: "purple", color: "#a06bff", name: "紫" },
  { id: "orange", color: "#ff8f4d", name: "橙" },
  { id: "cyan", color: "#23c4d4", name: "青" },
  { id: "gray", color: "#B1B1B1", name: "灰" },
  { id: "golden", color: "#d5c000", name: "金" },
];
const CUSTOM_ACCENTS_KEY = "customAccents";
const HEX_COLOR_RE = /^#[0-9a-f]{6}$/i;
function customAccents() {
  try {
    const colors = JSON.parse(localStorage.getItem(CUSTOM_ACCENTS_KEY) || "[]");
    return Array.isArray(colors) ? colors.filter((c) => HEX_COLOR_RE.test(c)) : [];
  } catch (_) {
    return [];
  }
}
function saveCustomAccents(colors) {
  localStorage.setItem(CUSTOM_ACCENTS_KEY, JSON.stringify([...new Set(colors)].slice(0, 24)));
}
/* 强调色/主题变化时推送给悬浮球(桌面小组件, 颜色跟随主界面) */
function pushBallTheme() {
  const theme = localStorage.getItem("theme") || "dark";
  const accent = localStorage.getItem("accent") || "pink";
  const custom = accent.startsWith("custom:") ? accent.slice(7) : "";
  const hit = ACCENTS.find((a) => a.id === accent);
  const hex = HEX_COLOR_RE.test(custom) ? custom : (hit ? hit.color : "#fb7299");
  fetch("/api/floatball", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme: { accent: hex, light: theme === "light", accent_id: accent } }),
  }).catch(() => {});
}
/* 桌面壳内 WebView2 的 localStorage 与用户平时用的浏览器相互独立;
   首次在壳内启动(无本地主题)时, 从服务端设置镜像恢复主界面强调色/深浅 */
(function restoreThemeFromServer() {
  if (localStorage.getItem("theme")) return;
  fetch("/api/ui_theme", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    if (!d || !d.theme) return;
    localStorage.setItem("theme", d.theme);
    if (d.accent_id) localStorage.setItem("accent", d.accent_id);
    else if (d.accent_hex) localStorage.setItem("accent", "custom:" + d.accent_hex);
    applyTheme();
  }).catch(() => {});
})();

function applyTheme() {
  const theme = localStorage.getItem("theme") || "dark";
  const accent = localStorage.getItem("accent") || "pink";
  const customColor = accent.startsWith("custom:") ? accent.slice(7) : "";
  document.body.classList.remove("light");
  ACCENTS.forEach((a) => document.body.classList.remove("accent-" + a.id));
  document.body.style.removeProperty("--accent");
  if (theme === "light") document.body.classList.add("light");
  if (HEX_COLOR_RE.test(customColor)) document.body.style.setProperty("--accent", customColor);
  else if (accent !== "pink") document.body.classList.add("accent-" + accent);
  pushBallTheme();   // 悬浮球跟随主界面强调色/深浅主题
  document.querySelectorAll("#themeRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.theme === theme));
  const row = document.getElementById("accentRow");
  if (row) {
    row.innerHTML = ACCENTS.map((a) =>
      `<span class="sw ${accent === a.id ? "on" : ""}" data-id="${a.id}" style="background:${a.color}" title="${a.name}"></span>`).join("");
    row.querySelectorAll(".sw").forEach((s) => s.addEventListener("click", () => {
      localStorage.setItem("accent", s.dataset.id);
      applyTheme();
    }));
  }
  const customRow = document.getElementById("customAccentRow");
  if (customRow) {
    customRow.querySelectorAll(".custom-accent-swatch").forEach((s) => s.remove());
    customAccents().forEach((color) => {
      const swatch = document.createElement("span");
      swatch.className = "custom-accent-swatch" + (accent === "custom:" + color ? " on" : "");
      swatch.style.backgroundColor = color;
      swatch.title = "使用自定义颜色 " + color;
      swatch.addEventListener("click", () => {
        localStorage.setItem("accent", "custom:" + color);
        applyTheme();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "custom-accent-remove";
      remove.textContent = "×";
      remove.title = "删除自定义颜色";
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        saveCustomAccents(customAccents().filter((c) => c !== color));
        if (localStorage.getItem("accent") === "custom:" + color) localStorage.setItem("accent", "pink");
        applyTheme();
      });
      swatch.appendChild(remove);
      customRow.appendChild(swatch);
    });
  }
}
function addCustomAccent(color) {
  const normalized = color.toLowerCase();
  if (!HEX_COLOR_RE.test(normalized)) return;
  saveCustomAccents([normalized, ...customAccents()]);
  localStorage.setItem("accent", "custom:" + normalized);
  applyTheme();
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("#themeRow .seg");
  if (b) { localStorage.setItem("theme", b.dataset.theme); applyTheme(); }
});
applyTheme();   // 立即应用, 避免闪烁
const customAccentInput = document.getElementById("customAccentInput");
if (customAccentInput) customAccentInput.addEventListener("change", (e) => addCustomAccent(e.target.value));

/* ---------- 显示方式 + 封面大小 + 预览放大 ---------- */
function applyView() {
  const layout = localStorage.getItem("layout") || "single";
  const cov = localStorage.getItem("cover") || "m";
  const reason = localStorage.getItem("reason") || "hide";   // 推荐理由默认隐藏(省高度)
  const list = document.getElementById("feedList");
  list.classList.toggle("dual", layout === "dual");
  list.classList.toggle("big", layout === "big");
  list.classList.remove("cov-s", "cov-m", "cov-l");
  list.classList.add("cov-" + cov);
  document.body.classList.toggle("hide-reason", reason === "hide");
  document.querySelectorAll("#layoutRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.layout === layout));
  document.querySelectorAll("#coverRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.cov === cov));
  document.querySelectorAll("#pvRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.v === String(pvZoom())));
  document.querySelectorAll("#reasonRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.reason === reason));
}
const PV_ZOOMS = [1, 1.5, 2, 3, 4];   // 1 = 原位不放大(只看不动), 其余为放大倍率
function pvZoom() {
  const v = parseFloat(localStorage.getItem("pv_zoom")) || 2;
  return PV_ZOOMS.includes(v) ? v : 2;
}
function applyPvZoom() {
  document.documentElement.style.setProperty("--pv-zoom", String(pvZoom()));
  document.querySelectorAll("#pvRow .seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.v === String(pvZoom())));
}
applyPvZoom();
document.addEventListener("click", (e) => {
  const lb = e.target.closest("#layoutRow .seg");
  if (lb) { localStorage.setItem("layout", lb.dataset.layout); applyView(); }
  const cb = e.target.closest("#coverRow .seg");
  if (cb) { localStorage.setItem("cover", cb.dataset.cov); applyView(); }
  const rb = e.target.closest("#reasonRow .seg");
  if (rb) { localStorage.setItem("reason", rb.dataset.reason); applyView(); }
  const pb = e.target.closest("#pvRow .seg");
  if (pb) { localStorage.setItem("pv_zoom", pb.dataset.v); applyPvZoom(); }
});
applyView();

/* ---------- 信息流 ---------- */
/* ---------- 信息流(无限滚动: 边刷边补) ---------- */
const SEARCH_SORTS = { default: "综合排序", views: "播放量排序", pubdate: "最新发布", hot: "最新热门" };
const feedState = { offset: 0, q: "", loading: false, gen: 0, timer: null, ctrl: null,
  session: (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36)),
  statusSynced: false,
  sort: (function () {
    const s = localStorage.getItem("searchSort");
    return s && SEARCH_SORTS[s] ? s : "default";
  })() };
function updateFeedMeta(d) {
  const meta = $("#feedMeta");
  if (feedState.q) {
    let extra = (d && d.platform_hits > 0)
      ? " · <b>贴吧/小黑盒×" + d.platform_hits + "</b>"
      : ' <span class="tip">· 贴吧/小黑盒：暂无匹配</span>';
    if (d && d.deep_terms && d.deep_terms.length)
      extra += ' <span class="tip">· 已深挖：' + esc(d.deep_terms.join(" / ")) + "</span>";
    meta.innerHTML = "搜索「<b>" + esc(feedState.q) + "</b>」 · 已加载 <b>" + feedState.offset +
      "</b> 条 · " + (SEARCH_SORTS[feedState.sort] || SEARCH_SORTS.default) +
      " · 下滑自动续" + extra;
    return;
  }
  const srcHtml = "来源：<b>" + esc(d.source) + "</b>";
  let html = "已加载 <b>" + feedState.offset + "</b> 条 · 下滑自动续";
  if (d.partitions) html += " · 分区：<b>" + esc(d.partitions) + "</b>";
  if (d.offline) html += ' <span class="warn">⚠ 离线示例数据</span>';
  if (d.account) html += " · <b>账号画像已生效</b>";
  if (d.ai) html += " · <b>AI 已开启</b>(档位:" + esc(d.mode) + ")";
  else html += ' <span class="warn">· AI 未配置（按热度排序）</span>';
  if (d.slots && Object.keys(d.slots).length)
    html += ' · <span class="tip">套餐：' + esc(Object.keys(d.slots).map((k) => k + d.slots[k]).join(" / ")) + "</span>";
  // 手机版来源行默认收起为一行: 状态信息在前, 冗长的来源列表放最后(点按展开可见)
  meta.innerHTML = (window.matchMedia("(max-width: 1023.9px)").matches ? html + " · " + srcHtml : srcHtml + " · " + html);
}
function clearFeed() {
  const list = $("#feedList");
  [...list.children].forEach((c) => { if (c.id !== "feedMore") c.remove(); });
  feedState.offset = 0;
}
function cancelFeedWork() {
  if (feedState.timer) {
    clearTimeout(feedState.timer);
    feedState.timer = null;
  }
  if (feedState.ctrl) {
    try { feedState.ctrl.abort(); } catch (_) {}
    feedState.ctrl = null;
  }
  feedState.loading = false;
}
function scheduleFeedFetch(gen, delay) {
  if (feedState.timer) clearTimeout(feedState.timer);
  feedState.timer = setTimeout(() => {
    feedState.timer = null;
    if (gen === feedState.gen) feedFetch(false, gen);
  }, delay);
}
async function feedFetch(force, gen) {
  gen = gen || feedState.gen;
  if (feedState.loading) return;
  feedState.loading = true;
  const ctrl = new AbortController();
  feedState.ctrl = ctrl;
  const q = feedState.q;
  const offset = feedState.offset;
  const more = $("#feedMore");
  more.classList.add("show");
  more.textContent = offset === 0
    ? "正在获取…（AI 筛查首批内容）"
    : "正在获取更多…（AI 筛查中）";
  try {
    const p = [];
    if (q) {
      p.push("q=" + encodeURIComponent(q));
      p.push("sort=" + encodeURIComponent(feedState.sort));
    }
    if (force) p.push("refresh=1");
    if (!q) p.push("rid=" + encodeURIComponent(feedState.session));
    p.push("offset=" + offset, "limit=30");
    const endpoint = q ? "/api/search" : "/api/feed";
    const d = await api(endpoint + (p.length ? "?" + p.join("&") : ""), { signal: ctrl.signal });
    if (gen !== feedState.gen || q !== feedState.q) return;
    const nextOffset = Number.isInteger(d.next_offset) ? d.next_offset : d.offset;
    if (Number.isInteger(nextOffset) && nextOffset >= 0) {
      feedState.offset = nextOffset;
    }
    if (d.items && d.items.length) {
      // 客户端纵深防御: 同一 key 不重复渲染(服务端会话去重之外的双保险)
      const existing = new Set(
        [...document.querySelectorAll("#feedList .card")].map((c) => c.dataset.key));
      const fresh = d.items.filter((it) => !existing.has(it.key));
      if (fresh.length) {
        const el = document.createElement("div");
        el.innerHTML = fresh.map(cardHTML).join("");
        const list = $("#feedList");
        while (el.firstChild) list.insertBefore(el.firstChild, $("#feedMore"));
        renderCardState();
        warmPreviews(fresh);
      }
      more.classList.remove("show");
      // 首批内容就绪 → 面板自动同步一次: 漏斗仪表盘来自后端构建统计,
      // 冷启动时面板先于构建完成加载, 不同步的话必须手点刷新才能看到。
      if (!feedState.statusSynced && !q) {
        feedState.statusSynced = true;
        loadPanel();
      }
    }
    updateFeedMeta(d);
    if (q) {
      if (d.building && (!d.items || !d.items.length)) {
        more.textContent = "正在搜索更多（近义词深挖中，约需10秒）…";
        scheduleFeedFetch(gen, 2500);
      } else if (!d.items || !d.items.length) {
        if (d.has_more) {
          more.textContent = "正在跳过不符合规则的结果…";
          scheduleFeedFetch(gen, 300);
        } else {
          more.textContent = feedState.offset === 0
            ? "没有找到相关内容，换个关键词试试"
            : "已到底 · 近义词深挖无更多内容";
        }
      } else if (d.offset >= (d.total || 0) && d.end) {
        more.textContent = "已到底（近义词深挖完毕，共 " + feedState.offset + " 条）";
      } else if (d.offset >= (d.total || 0)) {
        more.textContent = "本波到底，正在近义词深挖更多内容…";
        scheduleFeedFetch(gen, 400);
      } else {
        more.textContent = "继续下滑加载更多…";
      }
    } else if (d.building && (!d.items || !d.items.length)) {
      more.classList.add("show");
      more.textContent = "内容池补充中，将自动续上…";
      scheduleFeedFetch(gen, 700);   // 首批≥10条即绘制, 快速轮询
    } else if (d.building) {
      // 已有内容, 后台继续补全: 静默续上, 不显示阻塞提示
      more.textContent = "继续下滑加载更多…";
      scheduleFeedFetch(gen, 1500);
    } else if (!d.items || !d.items.length) {
      more.textContent = d.has_more ? "正在继续加载…" : "已到底，点「↻」开始新一轮";
      if (d.has_more) scheduleFeedFetch(gen, 300);
    } else {
      more.textContent = "继续下滑加载更多…";
      // 短页不代表到底: 可能是规则门禁后的稀疏页, 继续消费后端游标。
      if (d.has_more && d.items.length < 30) scheduleFeedFetch(gen, 300);
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;
    if (gen === feedState.gen) more.textContent = "加载失败：" + e;
  } finally {
    if (feedState.ctrl === ctrl) feedState.ctrl = null;
    if (gen === feedState.gen) feedState.loading = false;
  }
}
/* 搜索排序条: 仅搜索模式显示; 切换只带 sort 参数重排服务端缓存池, 不重新抓源(秒切) */
function applySortBar() {
  const bar = $("#sortBar");
  if (!bar) return;
  bar.classList.toggle("hidden", !feedState.q);
  bar.querySelectorAll("button[data-sort]").forEach((b) =>
    b.classList.toggle("on", b.dataset.sort === feedState.sort));
}
$("#sortBar").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-sort]");
  if (!b || b.dataset.sort === feedState.sort) return;
  feedState.sort = b.dataset.sort;
  localStorage.setItem("searchSort", feedState.sort);
  applySortBar();
  loadFeed();   // 不带force: 不发refresh=1, 服务端同一波次池直接重排
});
function loadFeed(force) {
  feedState.gen += 1;
  applySortBar();
  // 会话语义: 仅强制刷新/清池时重置会话(新 rid → 服务端已返回集合清零);
  // 设置变更等普通重建保留会话, 服务端会话去重继续防止同一内容反复出现。
  if (force || !feedState.session) {
    feedState.session = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36);
  }
  cancelFeedWork();
  clearFeed();
  feedFetch(force, feedState.gen);
}
new IntersectionObserver((es) => {
  es.forEach((e) => { if (e.isIntersecting) feedFetch(false, feedState.gen); });
}, { root: $("#feedList"), threshold: 0 }).observe($("#feedMore"));
function cardHTML(it) {
  const isTb = it.platform === "贴吧";
  const isXhh = it.platform === "小黑盒";
  const letter = esc((it.platform || "B").charAt(0));
  // 后端 following 是关注池/关注作者标记；兼容 JSON 布尔值和旧接口字符串值。
  const follow = it.following === true || it.following === 1 ||
    it.following === "1" || it.following === "true" || it.src === "关注";
  // 贴吧/小黑盒: 无播放量, 显示 点击量/点赞/评论(有值才显示, 避免一串"0"); B站保持 播放/弹幕/评论
  const stats = (isTb || isXhh)
    ? '<div class="thumb-stats">' +
      (it.views ? '<span>👁 ' + fmt(it.views) + '</span>' : '') +
      (it.likes ? '<span>👍 ' + fmt(it.likes) + '</span>' : '') +
      (it.reply ? '<span>💬 ' + fmt(it.reply) + '</span>' : '') +
      '</div>'
    : '<div class="thumb-stats"><span>▶ ' + fmt(it.views || 0) + '</span>' +
      (it.danmaku != null ? '<span>弹 ' + fmt(it.danmaku) + '</span>' : '') +
      (it.reply != null ? '<span>评 ' + fmt(it.reply) + '</span>' : '') + '</div>';
  const covers = (it.covers && it.covers.length) ? it.covers.slice(0, 2)
    : (it.cover ? [it.cover] : []);
  // 贴吧无图 → 不显示封面区(只留简介); 有图 → 横排最多两张
  let cover = "";
  if ((isTb || isXhh) && !covers.length) {
    cover = "";
  } else {
    const imgs = covers.map((c) =>
      '<img loading="lazy" referrerpolicy="no-referrer" src="' + esc(c) +
      '" alt="" onerror="this.style.display=\'none\'">').join("");
    cover = '<div class="thumb-wrap' + (covers.length > 1 ? " multi" : "") +
      '"><span class="ph">' + letter + "</span>" + imgs + stats + "</div>";
  }
  const desc = it.desc ? '<div class="desc" data-desc="' + esc(it.desc) + '">' + esc(it.desc) +
    (isTb && it.reply != null ? ' <span class="tb-reply">💬 ' + fmt(it.reply) + "</span>" : "") +
    "</div>" : "";
  const reason = it.reason ? '<div class="reason">' + esc(it.reason) + "</div>" : "";
  const authorHtml = follow ? '<span class="follow-author">' + esc(it.author) + '</span>' : esc(it.author);
  const bvid = (!isTb && !isXhh) ? ((it.url || "").match(/BV[0-9A-Za-z]{8,12}/) || [])[0] : "";
  const playBtn = (bvid && !isTouchDevice())
    ? '<button data-act="play" data-bvid="' + bvid + '" data-title="' + esc(it.title) + '" title="站内播放"><i class="ic ic-play"></i></button>' : "";
  const tid = isTb ? ((it.url || "").match(/\/p\/(\d+)/) || [])[1] : "";
  const xhhLid = isXhh ? ((it.url || "").match(/bbs\/link\/([0-9a-f]+)/) || [])[1] : "";
  const readBtn = tid ? '<button data-act="read" data-tid="' + tid + '" title="站内阅读"><i class="ic ic-read"></i></button>' :
    (xhhLid ? '<button data-act="xread" data-lid="' + xhhLid + '" title="站内预览"><i class="ic ic-read"></i></button>' : "");
  const meta = (isTb || isXhh)
    ? '<div class="meta-text">' + esc(it.author || "用户") + " · " + ago(it.pubdate) +
      (isTb ? " · " + esc(it.tname || "") : "") + "</div>"
    : '<div class="meta-text">' + authorHtml + " · 播放 " + fmt(it.views) + " · " + ago(it.pubdate) + "</div>";
  return `<article class="card${follow ? " follow" : ""}" data-key="${esc(it.key)}" data-url="${esc(it.url)}" data-author="${esc(it.author)}" data-platform="${esc(it.platform)}" data-xlid="${esc(it.xlid || "")}" data-ltag="${esc(it.ltag || "")}">
    <h3><span class="badge ${isTb ? "plat-tb" : isXhh ? "plat-xhh" : "plat-bili"}">${esc(it.platform)}</span>${follow ? '<span class="follow-tag">关注的人</span>' : ''}${esc(it.title)}</h3>
    ${cover ? '<div class="card-media">' + cover +
      '<div class="card-side">' + meta + desc + '</div></div>'
      : '<div class="card-media no-cover"><div class="card-side">' + meta + desc + '</div></div>'}
    <div class="acts">
      ${playBtn}${readBtn}
      <button data-act="refresh" title="换一条(刷新)"><i class="ic ic-next"></i></button>
      <button data-act="like" title="喜欢"><i class="ic ic-like"></i></button>
      <button data-act="dislike" class="dis" title="不喜欢"><i class="ic ic-dislike"></i></button>
      <button data-act="block" title="屏蔽作者"><i class="ic ic-block"></i></button>
    </div>
    ${reason}
  </article>`;
}
function renderCardState() {
  document.querySelectorAll(".card").forEach((card) => {
    const key = card.dataset.key;
    const like = card.querySelector('[data-act="like"]');
    const dis = card.querySelector('[data-act="dislike"]');
    like.classList.toggle("on", state.liked.has(key));
    dis.classList.toggle("on", state.disliked.has(key));
  });
}

/* ---------- 封面缩略播放(悬停B站预览) ---------- */
const pvCache = new Map();   // bvid -> src | null(失败)
let pvTimer = null, pvOverlay = null;   // 当前播放浮层

/* 预热: 列表加载后提前请求预览地址(后端缓存1h), 鼠标放上即秒开 */
function warmPreviews(items) {
  if (isTouchDevice()) return;   // 触屏无悬停预览(点按会误触mouseover), 预热纯属浪费流量
  (items || []).slice(0, 6).forEach((it) => {
    const m = (it.url || "").match(/BV[0-9A-Za-z]{8,12}/);
    if (!m || pvCache.has(m[0])) return;
    api("/api/preview?bvid=" + encodeURIComponent(m[0]))
      .then((d) => pvCache.set(m[0], d.ok ? d.src : null))
      .catch(() => pvCache.set(m[0], null));
  });
}

/* 播放浮层: fixed 定位在封面位置放大(倍率=设置), body级最高层, 不受任何滚动容器裁剪 */
function stopPreview() {
  if (!pvOverlay) return;
  const v = pvOverlay.querySelector("video");
  if (v) { try { v.pause(); } catch (_) {} }
  pvOverlay.remove();
  pvOverlay = null;
}
async function startPreview(tw) {
  const mBv = (tw.closest(".card")?.dataset.url || "").match(/BV[0-9A-Za-z]{8,12}/);
  if (!mBv) return;
  const bvid = mBv[0];
  let src = pvCache.get(bvid);
  if (src === undefined) {
    try {
      const d = await api("/api/preview?bvid=" + encodeURIComponent(bvid));
      src = d.ok ? d.src : null;
    } catch (_) { src = null; }
    pvCache.set(bvid, src);
  }
  if (!src) return;
  stopPreview();
  const rect = tw.getBoundingClientRect();
  const zoom = pvZoom();
  // ×1 = 原位播放: 浮层就是封面本身那么大, 贴回卡片原位即可, 不做任何位移
  const ovW = Math.round(rect.width * zoom);
  const ovH = Math.round(rect.height * zoom);
  const feed = document.getElementById("feedList");
  const feedRect = feed.getBoundingClientRect();
  let left, top;
  if (zoom === 1) {
    left = Math.round(rect.left);
    top = Math.round(rect.top);
  } else {
    // 统一中心锚点: 预览以封面中心对称放大(中列视觉=原位, 不朝任何方向飘),
    // 越出信息流左右边界的方向被钳回(左列贴左缘/最后一列整体左移, 不压设置栏与右侧面板)。
    left = Math.round(rect.left + rect.width / 2 - ovW / 2);   // 中心锚点
    // 窗口窄时高倍率会算得比信息流还宽, 此时夹取会退化成"永远贴左缘"。
    // 这种情况按信息流居中放, 至少视觉上是平衡的(两侧对称溢出), 比死贴一边好看。
    left = (ovW >= feedRect.width)
      ? Math.round(feedRect.left + (feedRect.width - ovW) / 2)
      : Math.max(feedRect.left, Math.min(left, Math.round(feedRect.left + feedRect.width - ovW)));
    top = Math.round(rect.top);
  }
  const ov = document.createElement("div");
  ov.className = "pv-overlay" + (zoom === 1 ? " pv-flat" : "");
  ov.style.left = left + "px";
  ov.style.top = top + "px";
  ov.style.width = ovW + "px";
  ov.style.height = ovH + "px";
  const img = tw.querySelector("img");
  ov.innerHTML = (img && img.src ? '<img src="' + esc(img.src) + '" referrerpolicy="no-referrer" alt="">' : "") +
    '<video muted loop playsinline preload="auto"></video>';
  document.body.appendChild(ov);
  pvOverlay = ov;
  const v = ov.querySelector("video");
  v.addEventListener("canplay", () => v.classList.add("pv-ok"));   // 数据就绪才显示画面
  v.addEventListener("error", () => stopPreview());
  v.src = src;
  v.load();
  const p = v.play();
  if (p && p.catch) p.catch(() => stopPreview());
  setTimeout(() => {   // 8s 未就绪则放弃
    if (pvOverlay === ov && !v.classList.contains("pv-ok")) stopPreview();
  }, 8000);
}
function startPvTimer(tw) {
  clearTimeout(pvTimer);
  pvTimer = setTimeout(() => startPreview(tw), 250);   // 悬停250ms才播, 防滑过
}
/* 触发范围 = 封面缩略图(thumb-wrap)本身; 播放浮层 pointer-events:none 不干扰。
   触屏设备整段禁用: 手机上点按封面会先触发模拟mouseover → 误触预览播放(用户已要求移除) */
$("#feedList").addEventListener("mouseover", (e) => {
  if (isTouchDevice()) return;
  const tw = e.target.closest(".thumb-wrap");
  if (!tw) return;
  if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".thumb-wrap") === tw)
    return;   // 封面内移动不重新计时(否则子元素切换会无限重置)
  startPvTimer(tw);
});
$("#feedList").addEventListener("mouseout", (e) => {
  const tw = e.target.closest(".thumb-wrap");
  if (!tw) return;
  if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".thumb-wrap") === tw)
    return;   // 封面内移动不停止
  clearTimeout(pvTimer);
  stopPreview();
});
/* ---------- B站站内播放(官方embed播放器, 不跳出App) ---------- */
let playerBox = null;
function openPlayer(bvid, title) {
  if (!bvid) return;
  closePlayer();
  playerBox = document.createElement("div");
  playerBox.id = "playerBox";
  playerBox.innerHTML =
    '<div class="pb-column">' +
    '<div class="pb-head"><span class="pb-badge">B站·站内播放</span>' +
    '<span class="pb-title">' + esc(title || bvid) + '</span>' +
    '<span class="pb-tip">看不了/想看高清? 点右侧跳B站</span>' +
    '<button id="pbJump" data-url="https://www.bilibili.com/video/' + esc(bvid) + '">跳B站</button>' +
    '<button id="pbClose">✕</button></div>' +
    '<div class="pb-body"><iframe src="https://player.bilibili.com/player.html?bvid=' + esc(bvid) +
    '&page=1&high_quality=1&danmaku=0&autoplay=1" allowfullscreen="true" allow="autoplay; encrypted-media; fullscreen" scrolling="no" frameborder="0"></iframe></div>' +
    '</div>';
  document.body.appendChild(playerBox);
  $("#pbClose").onclick = closePlayer;
  $("#pbJump").onclick = () => openExternal("https://www.bilibili.com/video/" + bvid);
}
function closePlayer() {
  if (playerBox) { playerBox.remove(); playerBox = null; }
}

/* ---------- 贴吧站内阅读(wap页解析, 服务端 /api/tieba/thread) ---------- */
let readerBox = null;
let readerBoxXlid = "";   // 小黑盒数字linkid(跳转App深链用)
let readerBoxLtag = "";   // 小黑盒link_tag(heybox://link 唤起参数)
function openReader(tid, title) {
  if (!tid) return;
  closeReader();
  readerBox = document.createElement("div");
  readerBox.id = "readerBox";
  readerBox.innerHTML =
    '<div class="rb-head"><span class="rb-title">' + esc(title || "贴吧帖子") + '</span>' +
    '<span class="rb-tip">只含首批楼层, 看全楼点右上跳转</span>' +
    '<button id="rbJump" data-url="https://tieba.baidu.com/p/' + esc(tid) + '">跳贴吧</button>' +
    '<button id="rbClose">✕</button></div>' +
    '<div class="rb-body"><div class="rb-loading">正在加载帖子…</div></div>';
  document.body.appendChild(readerBox);
  $("#rbClose").onclick = closeReader;
  $("#rbJump").onclick = () => openExternal("https://tieba.baidu.com/p/" + tid);
  api("/api/tieba/thread?tid=" + encodeURIComponent(tid)).then((d) => {
    if (!readerBox) return;
    const body = readerBox.querySelector(".rb-body");
    if (!d.ok) {
      body.innerHTML = '<div class="rb-empty">' + esc(d.error || "加载失败") + '</div>';
      return;
    }
    let h = '<div class="rb-thread-title">' + esc(d.title) + '</div>';
    d.floors.forEach((f) => {
      const av = f.avatar
        ? '<img class="rb-avatar" loading="lazy" referrerpolicy="no-referrer" src="' + esc(f.avatar) +
          '" alt="" onerror="this.remove()">'
        : '<span class="rb-avatar rb-avatar-ph">' + esc((f.author || "贴")[0]) + '</span>';
      h += '<div class="rb-floor"><div class="rb-floor-head">' + av +
        '<b>' + esc(f.author) + '</b>' +
        (f.lz ? '<em class="rb-lz">楼主</em>' : '') +
        '<span class="rb-floor-meta">' + esc(f.time) + (f.floor ? ' · ' + f.floor + '楼' : '') + '</span></div>' +
        (f.text ? '<div class="rb-text">' + esc(f.text) + '</div>' : '') +
        (f.images || []).map((u) =>
          '<img class="rb-img" loading="lazy" referrerpolicy="no-referrer" src="' + esc(u) +
          '" alt="" onerror="this.remove()">').join("") +
        '</div>';
    });
    if (!d.floors.length) h += '<div class="rb-empty">没有解析到楼层内容，点右上角跳转App查看</div>';
    body.innerHTML = h;
    body.scrollTop = 0;
  }).catch(() => {
    const b = readerBox && readerBox.querySelector(".rb-body");
    if (b) b.innerHTML = '<div class="rb-empty">加载失败，请稍后再试</div>';
  });
}
function closeReader() {
  if (readerBox) { readerBox.remove(); readerBox = null; }
}
/* 小黑盒帖子唤起URL: 官方App的RouterActivity对 heybox://<host> 按host分发(反编译实测),
   host=link → 读link_id/link_tag参数构造BBSLinkObj直接进帖子详情页。
   id 必须传 App 内部语义的数字 linkid(xlid): 传网页端16进制id帖子能打开但评论会报"帖子id错误"。
   #web= 供壳在App未装时回落到网页(网页仍用16进制id)。ltag=feed里的link_tag。 */
function xhhJumpUrl(webUrl, id, ltag) {
  let u = "heybox://link?link_id=" + encodeURIComponent(id || "");
  if (ltag) u += "&link_tag=" + encodeURIComponent(ltag);
  return u + "#web=" + encodeURIComponent(webUrl);
}
async function openXhhReader(lid, title) {
  if (!lid) return;
  closeReader();
  readerBox = document.createElement("div");
  readerBox.id = "readerBox";
  readerBox.innerHTML =
    '<div class="rb-head"><span class="rb-title">' + esc(title || "小黑盒帖子") + '</span>' +
    '<span class="rb-tip">预览正文与热门评论</span>' +
    '<button id="rbJump" data-url="https://www.xiaoheihe.cn/app/bbs/link/' + esc(lid) + '">跳小黑盒</button>' +
    '<button id="rbClose">✕</button></div>' +
    '<div class="rb-body"><div class="rb-loading">正在加载帖子…</div></div>';
  document.body.appendChild(readerBox);
  $("#rbClose").onclick = closeReader;
  $("#rbJump").onclick = () => {
    const webUrl = "https://www.xiaoheihe.cn/app/bbs/link/" + lid;
    if (window.AndroidBridge) {
      if (window.AndroidBridge.hasApp && !window.AndroidBridge.hasApp("com.max.xiaoheihe")) {
        toast("未安装小黑盒App，可继续在本地预览");
        return;
      }
      // 安卓壳: 官方 heybox://link 协议进帖子, id用数字linkid(评论要用, hex会报帖子id错误);
      // App未装时壳回落到 #web= 网页; 无xlid时退化用网页hex
      openExternal(xhhJumpUrl(webUrl, readerBoxXlid || lid, readerBoxLtag));
    } else {
      // 桌面端: 直接开网页(heybox://深链留给安卓壳; 浏览器没装PC客户端时协议跳转体验差)
      openExternal(webUrl);
    }
  };
  const d = await api("/api/xhh/thread?link_id=" + encodeURIComponent(lid)).catch(() => null);
  if (!readerBox) return;
  readerBoxXlid = d && d.xlid ? d.xlid : "";
  readerBoxLtag = d && d.ltag ? d.ltag : "";
  const body = readerBox.querySelector(".rb-body");
  if (!d || !d.ok) {
    body.innerHTML = '<div class="rb-empty">' + esc((d && d.error) || "加载失败") + '</div>';
    return;
  }
  const meta = [];
  if (d.author) meta.push(esc(d.author));
  if (d.create_at) meta.push(ago(d.create_at));
  const degradeTip = d.degraded
    ? '<div class="rb-warn">⚠ 小黑盒暂时限制访问，仅显示部分内容；看完整帖子点右上角跳转</div>' : "";
  let h = '<div class="rb-thread-title">' + esc(d.title) + '</div>' +
    (meta.length ? '<div class="rb-floor-head" style="margin-bottom:10px"><b>' + meta.join('</b><span>·</span><b>') + '</b></div>' : '');
  h += degradeTip;
  h += d.content ? '<div class="rb-text">' + esc(d.content) + '</div>' : "";
  h += (d.images || []).map((u) =>
    '<img class="rb-img" loading="lazy" referrerpolicy="no-referrer" src="' + esc(u) +
    '" alt="" onerror="this.remove()">').join("");
  if ((d.comments || []).length) {
    h += '<div class="rb-comments-title">热门评论' +
      (d.comment_total ? ' · 共' + esc(String(d.comment_total)) + '条' : '') + '</div>';
    d.comments.forEach((c) => {
      h += '<div class="rb-floor"><div class="rb-floor-head"><b>' + esc(c.author) + '</b>' +
        '<span>' + (c.create_at ? ago(c.create_at) : '') + '</span></div>' +
        (c.text ? '<div class="rb-text">' + esc(c.text) + '</div>' : '') +
        (c.images || []).map((u) =>
          '<img class="rb-img" loading="lazy" referrerpolicy="no-referrer" src="' + esc(u) +
          '" alt="" onerror="this.remove()">').join("") +
        '</div>';
    });
  }
  body.innerHTML = h;
  body.scrollTop = 0;
}

/* 滚动/窗口缩放时停止浮层(fixed 定位不跟随滚动, 停止更合理) */
$("#feedList").addEventListener("scroll", () => stopPreview());
window.addEventListener("resize", () => stopPreview());

$("#feedList").addEventListener("click", async (e) => {
  if (window.__lpSuppress) { window.__lpSuppress = false; return; }   // 长按菜单后的点击不再触发跳转
  const btn = e.target.closest("button");
  const card = e.target.closest(".card");
  if (!card) return;
  const key = card.dataset.key, author = card.dataset.author;
  if (btn) {
    e.stopPropagation();
    const act = btn.dataset.act;
    if (act === "go") return openItem(key, card.dataset.url);
    if (act === "play") return openPlayer(btn.dataset.bvid, btn.dataset.title);
    if (act === "read") return openReader(btn.dataset.tid, "");
    if (act === "xread") return openXhhReader(btn.dataset.lid, "");
    if (act === "refresh") {
      const r = await api("/api/feed/refresh", { method: "POST", body: { key, q: feedState.q } });
      if (r.ok && r.item) {
        const tmp = document.createElement("div");
        tmp.innerHTML = cardHTML(r.item);
        card.replaceWith(tmp.firstElementChild);
        renderCardState();
        warmPreviews([r.item]);
        toast("已换一条");
      } else toast("暂无可用内容替换", { warn: true });
      return;
    }
    if (act === "like") {
      const on = !state.liked.has(key);
      state.liked[on ? "add" : "delete"](key);
      await api("/api/feedback", { method: "POST", body: { key, action: on ? "like" : "undolike" } });
      renderCardState();
      if (on) toast("已标记喜欢，同类内容会优先");
      return;
    }
    if (act === "dislike") {
      const on = !state.disliked.has(key);
      state.disliked[on ? "add" : "delete"](key);
      await api("/api/feedback", { method: "POST", body: { key, action: on ? "dislike" : "undodislike" } });
      renderCardState();
      if (on) {
        // 后端已把该条目加入负反馈硬排除: 立即从信息流移除, 不等下一次规划。
        const card = e.target.closest(".card");
        if (card) card.remove();
        toast("已标记不喜欢，这类内容不会再出现");
      }
      return;
    }
    if (act === "block") {
      await api("/api/blacklist", { method: "POST", body: { kind: "author", value: author } });
      // 服务端负反馈过滤即刻生效: 直接移除该作者的所有在屏卡片, 无需整流重建。
      document.querySelectorAll(`#feedList .card[data-author="${CSS.escape(author)}"]`)
        .forEach((c) => c.remove());
      toast("已屏蔽作者：" + author, {
        undo: () => api("/api/blacklist", { method: "POST", body: { kind: "author", value: author, remove: true } }),
      });
      loadBlacklist();
      return;
    }
  } else {
    openCard(card, key);
  }
});
function cardHeading(card) {
  const h = card.querySelector("h3");
  if (!h) return "";
  return [...h.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
}
/* 卡片主体点按: 安卓壳先查目标App是否安装, 未装时改开本地播放器/阅读器
   (旧版逐级回落到系统浏览器, 手机没装官方App时体验极差);
   桌面浏览器或已装App → 照旧外跳。长按菜单的"跳转软件"保留强制外跳作逃生门。 */
function openCard(card, key) {
  const plat = card.dataset.platform || "";
  const url = card.dataset.url || "";
  if (window.AndroidBridge && window.AndroidBridge.hasApp) {
    const hexId = (url.match(/bbs\/link\/([0-9a-f]+)/) || [])[1] || "";
    if (plat === "贴吧" && !AndroidBridge.hasApp("com.baidu.tieba")) {
      const tid = (url.match(/\/p\/(\d+)/) || [])[1] || "";
      if (tid) {
        toast("未安装贴吧，已在本地阅读");
        return openReader(tid, cardHeading(card));
      }
    } else if (plat === "小黑盒" && !AndroidBridge.hasApp("com.max.xiaoheihe")) {
      const lid = card.dataset.xlid || hexId;
      if (lid) {
        toast("未安装小黑盒，已在本地预览");
        return openXhhReader(lid, cardHeading(card));
      }
    } else if (plat === "B站" && !AndroidBridge.hasApp("tv.danmaku.bili")) {
      const bvid = (url.match(/BV[0-9A-Za-z]{8,12}/) || [])[0] || "";
      if (bvid) {
        toast("未安装B站客户端，已在本地播放");
        return openPlayer(bvid, cardHeading(card));
      }
    }
    if (plat === "小黑盒" && (card.dataset.xlid || hexId)) {
      // 安卓壳: 官方 heybox://link?link_id= 协议进帖子, id用数字linkid(评论要用),
      // 无xlid时退化用hex(至少能打开)
      return openItem(key, xhhJumpUrl(url, card.dataset.xlid || hexId, card.dataset.ltag));
    }
    return openItem(key, url);
  }
  return openItem(key, url);   // 桌面端: 直接开网页(heybox://深链留给安卓壳)
}
function openItem(key, url) {
  api("/api/click", { method: "POST", body: { key } });
  openExternal(url);
}

/* ---------- 简介悬浮提示(跟随白天/黑夜主题, 避免原生title刺眼) ---------- */
const descTip = document.createElement("div");
descTip.className = "desc-tip";
document.body.appendChild(descTip);
let tipVisible = false, tipCur = "", hoverOn = false, hoverStart = 0;
const TIP_DELAY = 500;   // 悬停1秒后才显示全文预览
function tipShow(text) {
  if (!tipVisible || tipCur !== text) {
    descTip.textContent = text;
    tipCur = text;
    descTip.classList.add("on");
    tipVisible = true;
  }
}
function tipHideNow() {   // 移出即关闭
  hoverOn = false;
  if (tipVisible) {
    descTip.classList.remove("on");
    tipVisible = false;
    tipCur = "";
  }
}
function tipPos(x, y) {
  const pad = 14;
  let px = x + pad, py = y + pad;
  const r = descTip.getBoundingClientRect();
  if (px + r.width > innerWidth - 10) px = x - r.width - pad;
  if (py + r.height > innerHeight - 10) py = y - r.height - pad;
  descTip.style.left = px + "px";
  descTip.style.top = py + "px";
}
// elementFromPoint 逐帧判定鼠标是否真在简介上, 悬停满1s才显示, 移出立即关闭
document.addEventListener("mousemove", (e) => {
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const d = el && el.closest ? el.closest(".desc") : null;
  if (d && d.dataset.desc) {
    if (!hoverOn) { hoverOn = true; hoverStart = performance.now(); }
    if (!tipVisible && performance.now() - hoverStart >= TIP_DELAY) {
      tipShow(d.dataset.desc);
      tipPos(e.clientX, e.clientY);
    } else if (tipVisible) {
      tipPos(e.clientX, e.clientY);
    }
  } else {
    tipHideNow();
  }
});

/* ---------- 大面板 ---------- */
$("#panelTabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.tab = b.dataset.tab;
  document.querySelectorAll("#panelTabs button").forEach((x) =>
    x.classList.toggle("on", x === b));
  loadPanel();
});
$("#btnHistory").onclick = () => switchTab("history");
$("#btnSaved").onclick = () => switchTab("saved");
function switchTab(tab) {
  const btn = document.querySelector(`#panelTabs button[data-tab="${tab}"]`);
  if (btn) btn.click();
}

async function loadPanel() {
  if (state.tab === "status") return loadStatus();
  if (state.tab === "history") return loadHistory();
  if (state.tab === "saved") return loadSaved();
}
async function loadStatus() {
  const d = await api("/api/status");
  const sources = d.sources.map((s) => {
    const dot = s.status === "正常" ? "ok" : "gray";
    if (s.name === "贴吧") {
      const login = s.logged_in ? '<b class="ok-text">已登录</b>' : "未登录";
      return `<div class="src-row"><span class="dot ${dot}"></span><b>${esc(s.name)}</b>
        <span class="info">${login} · 吧数 <b>${s.bars}</b> · 最近抓取 ${ago(s.last_fetch)} · 本次会话 <b>${s.fetched_today}</b> 条</span></div>`;
    }
    return `<div class="src-row"><span class="dot ${dot}"></span><b>${esc(s.name)}</b>
      <span class="info">最近抓取 ${ago(s.last_fetch)} · 今日入库 <b>${s.fetched_today}</b> · 筛掉 <b>${s.filtered_today}</b></span></div>`;
  }).join("");
  const st = d.summary;
  const pct = st.received ? Math.round(st.filtered / st.received * 100) : 0;
  const sc = d.ai_scoring || {};
  let scLine = "";
  if (sc.total > 0) {
    const done = sc.done >= sc.total ? "已完成" : `进行中 ${sc.done}/${sc.total}`;
    scLine = `<div class="line"><span>AI 筛选</span><b>${done}${sc.full ? " · 全量模式" : ""}</b></div>`;
  }
  const fn = d.funnel || {};
  const counts = fn.counts || {};
  const labels = fn.labels || {};
  let funnelHtml = "";
  if (counts.fetched) {
    const order = ["fetched", "partition", "age", "news", "views", "duration", "emotion",
      "dislike", "blocklist", "help", "xhh", "dedup", "follow_window", "cooldown", "cooldown_relaxed", "ai_gate", "kept",
      "build_full", "build_quick"];
    const parts = order.filter((k) => counts[k]).map((k) =>
      `<span title="${esc(labels[k] || k)}">${esc(labels[k] || k)} −${counts[k]}</span>`).join(" · ");
    funnelHtml = `<div class="funnel-line">${parts}</div>`;
  }
  $("#panelBody").innerHTML = `
    ${sources}
    ${funnelHtml}
    <div class="summary">
      <div class="line"><span>今日收录</span><b>${st.received} 条</b></div>
      <div class="line"><span>AI 过滤器剔除</span><b>${st.filtered} 条（${pct}%）</b></div>
      ${scLine}
      <div class="line"><span>AI 调用</span><b>${st.ai_calls} 次</b></div>
      <div class="line"><span>今日 AI 成本</span><b>约 ¥${st.cost < 0.01 ? st.cost.toFixed(4) : st.cost.toFixed(2)}</b></div>
      <div class="line"><span>AI 状态</span><b>${d.ai_on ? "已开启" : (d.ai ? "已配置·总开关关" : "未配置")}</b></div>
      <button onclick="loadFeed();loadPanel();">立即刷新数据</button>
    </div>`;
}
async function loadHistory() {
  const d = await api("/api/history");
  $("#panelBody").innerHTML = d.items.map((it) => `
    <div class="list-item">
      <div class="t" data-url="${esc(it.url || it.key)}">${esc(it.title)}</div>
      <div class="s"><span>${esc(it.platform)}</span><span>${ago(it.ts)}</span></div>
    </div>`).join("") || '<div class="meta-line" style="border:none">还没有浏览记录</div>';
  $("#panelBody").querySelectorAll(".t").forEach((el) =>
    el.addEventListener("click", () => openExternal(el.dataset.url)));
}
async function loadSaved() {
  const kind = state.savedKind;
  const d = await api("/api/saved?kind=" + kind);
  $("#panelBody").innerHTML = `
    <div style="margin-bottom:10px;display:flex;gap:8px">
      <button class="tb ${kind === "later" ? "on2" : ""}" data-k="later">稍后再看</button>
      <button class="tb" data-k="fav">收藏</button>
    </div>` +
    d.items.map((it) => `
      <div class="list-item">
        <div class="t" data-url="${esc(it.url || it.key)}">${esc(it.title)}</div>
        <div class="s"><span>${esc(it.platform)}</span><span>${ago(it.ts)}</span>
          <button class="del" data-del="${esc(it.key)}">移除</button></div>
      </div>`).join("") || '<div class="meta-line" style="border:none">列表为空</div>';
  $("#panelBody").querySelectorAll(".t").forEach((el) =>
    el.addEventListener("click", () => openExternal(el.dataset.url)));
  $("#panelBody").querySelectorAll("[data-del]").forEach((el) =>
    el.addEventListener("click", async () => {
      await api("/api/saved", { method: "POST", body: { key: el.dataset.del, kind, remove: true } });
      loadSaved();
    }));
  $("#panelBody").querySelectorAll("[data-k]").forEach((b) =>
    b.addEventListener("click", () => { state.savedKind = b.dataset.k; loadSaved(); }));
}

/* ---------- AI 聊天(多会话 · 流式 · 快捷指令 chips) ---------- */
let curConv = 0;
let convs = [];
let streamCtrl = null;   // 当前流式请求的 AbortController

/* 会话条目上下文("问AI"): 长按卡片写入, 聊天框顶部显示, ✕切出 */
let chatCtx = { key: "", title: "" };
function setChatCtxBar() {
  const bar = $("#chatCtxBar");
  if (!bar) return;
  if (chatCtx.key) {
    $("#chatCtxTitle").textContent = "正在聊：" + (chatCtx.title || "一条内容");
    bar.classList.remove("hidden");
  } else {
    bar.classList.add("hidden");
  }
}
async function chatCtxApply(key, title) {
  chatCtx = { key: key || "", title: title || "" };
  setChatCtxBar();
  try {
    await api("/api/chat/ctx", { method: "POST", body: { cid: curConv, key: key || "", title: title || "" } });
  } catch (_) {}
}
async function askAiAboutCard(card) {
  const key = card.dataset.key || "";
  if (!key) return;
  await chatCtxApply(key, cardHeading(card).slice(0, 40));
  openChatPanel();
  toast("已带上这条内容，直接提问即可");
}
function openChatPanel() {
  if (window.matchMedia("(max-width: 1023.9px)").matches) {
    if (window.__setMobileView) window.__setMobileView("chat");
  } else if ($("#chat").classList.contains("collapsed")) {
    $("#chatCollapse").click();   // 折叠中 → 展开(复用折叠逻辑与宽度记忆)
  }
  setTimeout(() => { const i = $("#chatInput"); if (i) i.focus(); }, 80);
}
function msgEl(role, content, chips) {
  const w = document.createElement("div");
  w.className = "msg " + role;
  const t = document.createElement("div");
  t.className = "msg-text";
  t.textContent = content || "";
  w.appendChild(t);
  if (chips && chips.length) w.appendChild(chipsRow(chips));
  return w;
}
function chipsRow(chips) {
  const row = document.createElement("div");
  row.className = "chips-row";
  chips.forEach((c) => {
    const b = document.createElement("button");
    b.textContent = c.label;
    b.title = c.cmd;
    b.onclick = () => sendChatText(c.label, c.payload);   // payload=确认通道(后端直接执行)
    row.appendChild(b);
  });
  return row;
}
async function loadConvs() {
  const d = await api("/api/conv");
  convs = d.convs || [];
  curConv = d.cur || 0;
  chatCtx = d.ctx || { key: "", title: "" };   // 每个会话独立的条目上下文, 切换即同步
  setChatCtxBar();
  $("#convItems").innerHTML = convs.length ? convs.map((c) => `
    <div class="conv-item ${c.id === curConv ? "on" : ""}" data-id="${c.id}">
      <span class="conv-name" title="${esc(c.title)}">${esc(c.title)}<em>${c.count || ""}</em></span>
      <span class="conv-ops">
        <button class="conv-rename" title="重命名">✎</button>
        <button class="conv-del" title="删除">✕</button>
      </span>
    </div>`).join("") : '<div class="conv-none">暂无对话</div>';
  const cur = convs.find((c) => c.id === curConv);
  $("#convTitle").textContent = cur ? cur.title : "AI 聊天";
  $("#convTitle").title = "点击重命名";
  document.querySelectorAll("#convItems .conv-item").forEach((el) => {
    const id = +el.dataset.id;
    el.querySelector(".conv-name").addEventListener("click", () => selectConv(id));
    el.querySelector(".conv-rename").addEventListener("click", (e) => { e.stopPropagation(); renameConv(id); });
    el.querySelector(".conv-del").addEventListener("click", (e) => { e.stopPropagation(); delConv(id); });
  });
}
async function selectConv(id) {
  if (streamCtrl) return toast("请先等待当前回复完成或点停止", { warn: true });
  await api("/api/conv/select", { method: "POST", body: { id } });
  closeConvList();
  loadConvs();
  loadChat();
}
async function renameConv(id) {
  const cur = convs.find((c) => c.id === id);
  const t = prompt("对话标题：", cur ? cur.title : "");
  if (!t || !t.trim()) return;
  await api("/api/conv/rename", { method: "POST", body: { id, title: t.trim() } });
  loadConvs();
  loadChat();
}
async function delConv(id) {
  if (!confirm("删除该对话及其全部消息？此操作不可恢复。")) return;
  await api("/api/conv/delete", { method: "POST", body: { id } });
  closeConvList();
  loadConvs();
  loadChat();
}
async function newConv() {
  if (streamCtrl) return toast("请先等待当前回复完成或点停止", { warn: true });
  await api("/api/conv", { method: "POST", body: {} });
  closeConvList();
  loadConvs();
  loadChat();
  $("#chatInput").focus();
}
function openConvList() { $("#convList").classList.remove("hidden"); loadConvs(); }
function closeConvList() { $("#convList").classList.add("hidden"); }

async function loadChat() {
  if (!curConv) await loadConvs();
  const d = await api("/api/chat?cid=" + curConv);
  const box = $("#chatMsgs");
  box.innerHTML = "";
  if (!d.messages || !d.messages.length) {
    box.innerHTML = '<div class="chat-empty">告诉 AI 你想看什么样的信息</div>';
  } else {
    d.messages.forEach((m) => {
      if (!(m.content || "").trim()) return;   // 跳过空气泡(历史bug残留)
      box.appendChild(msgEl(m.role, m.content));
    });
    if (!box.childElementCount)
      box.innerHTML = '<div class="chat-empty">告诉 AI 你想看什么样的信息</div>';
  }
  box.scrollTop = box.scrollHeight;
}
async function sendChat() {
  const input = $("#chatInput");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  await sendChatText(msg);
}
async function sendChatText(text, payload) {
  if (streamCtrl) return;
  const box = $("#chatMsgs");
  const empty = box.querySelector(".chat-empty");
  if (empty) empty.remove();
  box.appendChild(msgEl("user", text));
  const aiEl = msgEl("ai", "");
  const aiText = aiEl.querySelector(".msg-text");
  box.appendChild(aiEl);
  box.scrollTop = box.scrollHeight;
  $("#chatSend").disabled = true;
  $("#chatStop").classList.remove("hidden");
  const ctrl = new AbortController();
  streamCtrl = ctrl;
  let intent = null, chips = [], reply = "", done = false, sentCid = curConv, trig = null;
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, cid: curConv, stream: true, payload: payload || undefined }),
      signal: ctrl.signal,
    });
    if (!r.ok || !r.body) {
      reply = "请求失败（HTTP " + r.status + "）";
      done = true;
    } else if ((r.headers.get("Content-Type") || "").indexOf("application/json") >= 0) {
      // AI未配置/总开关关闭等场景: 后端回普通JSON而非SSE(旧版解析不出任何事件 → 空气泡)
      const d = await r.json();
      reply = d.reply || "";
      chips = d.chips || [];
      trig = d.trigger || null;
      sentCid = d.cid || sentCid;
      if (d.ctx !== undefined) { chatCtx = d.ctx || { key: "", title: "" }; setChatCtxBar(); }
      done = true;
    } else {
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done: d } = await reader.read();
        if (d) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let ev = "message", data = {};
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) { try { data = JSON.parse(line.slice(5).trim()); } catch (_) {} }
          }
          if (ev === "meta") { intent = data.intent; chips = data.chips || []; sentCid = data.cid || sentCid; }
          else if (data.ctx !== undefined) { chatCtx = data.ctx || { key: "", title: "" }; setChatCtxBar(); }
          else if (ev === "think") {   // 推理模型: 思考阶段先占位, 出字再替换
            if (!aiText.textContent) {
              aiText.textContent = "🧠 思考中…";
              aiEl.classList.add("thinking");
            }
          }
          else if (ev === "delta") {
            aiEl.classList.remove("thinking");
            reply += data.t || "";
            aiText.textContent = reply;
            box.scrollTop = box.scrollHeight;
          } else if (ev === "done") {
            reply = data.reply || reply;
            chips = data.chips || chips;
            trig = data.trigger || null;
            done = true;
          }
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      reply = reply || "（已停止生成）";
      aiText.textContent = reply;
    } else {
      reply = "请求出错：" + (e.message || e);
      aiText.textContent = reply;
    }
  } finally {
    streamCtrl = null;
    $("#chatSend").disabled = false;
    $("#chatStop").classList.add("hidden");
  }
  // 意图搜索 → 左侧立即切为该关键词搜索流
  if (intent && intent.action === "search" && intent.keywords && intent.keywords.length) {
    feedState.q = intent.keywords.join(" ");
    $("#searchInput").value = feedState.q;
    loadFeed();
    toast("已切换搜索：" + feedState.q);
  }
  // 会话可能被命令切换(如"开个新对话") → 重新加载显示
  if (sentCid !== curConv) {
    curConv = sentCid;
    loadConvs();
    loadChat();
    if (done) {   // 把 chips 补到最后一条 AI 消息
      const last = document.querySelector("#chatMsgs .msg.ai:last-child");
      if (last && chips.length) last.appendChild(chipsRow(chips));
    }
  } else if (done) {
    aiText.textContent = reply;
    aiEl.querySelectorAll(".chips-row").forEach((x) => x.remove());
    if (chips.length) aiEl.appendChild(chipsRow(chips));
    box.scrollTop = box.scrollHeight;
  } else {
    aiText.textContent = reply;
  }
  if (done) loadConvs();   // 刷新标题/消息数
  // 命令改设置(如"写进厌恶备注") → 立即刷新设置栏 + 信息流, 让用户立刻看到生效
  if (trig === "settings") { loadSettings(); loadStatus(); loadFeed(); }
  else if (trig === "random") { loadFeed(); }
}
$("#chatSend").onclick = sendChat;
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
$("#chatStop").onclick = () => { if (streamCtrl) { try { streamCtrl.abort(); } catch (_) {} } };
$("#convBtn").onclick = (e) => { e.stopPropagation(); $("#convList").classList.toggle("hidden"); loadConvs(); };
$("#convNew").onclick = (e) => { e.stopPropagation(); newConv(); };
$("#convNew2").onclick = newConv;
$("#convTitle").addEventListener("click", () => renameConv(curConv));
document.addEventListener("click", (e) => { if (!e.target.closest("#chat header")) closeConvList(); });
$("#chatClear").onclick = async () => {
  if (streamCtrl) return toast("请先等待当前回复完成或点停止", { warn: true });
  if (!confirm("清空当前对话的所有消息？")) return;
  await api("/api/chat/clear", { method: "POST", body: { cid: curConv } });
  loadChat(); loadConvs();
};
$("#chatCtxClear").onclick = () => chatCtxApply("", "");
/* 折叠到最右: 省出空间 */
$("#chatCollapse").onclick = () => {
  const collapsed = $("#chat").classList.toggle("collapsed");
  $("#sp2").classList.toggle("hidden", collapsed);   // 折叠时隐藏聊天框分割条
  localStorage.setItem("chat_collapsed", collapsed ? "1" : "");
  if (!collapsed) {   // 展开时恢复记忆宽度
    const r = localStorage.getItem("w_chat");
    if (r) $("#chat").style.width = clampSide(parseInt(r, 10) || 280, 220, $("#chat")) + "px";
  }
};
if (localStorage.getItem("chat_collapsed")) {
  $("#chat").classList.add("collapsed");
  $("#sp2").classList.add("hidden");
}

/* ---------- 设置 ---------- */
/* 模型组合框: 下拉预设(datalist) + 可手输; 命中预设自动带出对应 API 地址 */
const MODEL_BASE = {
  "deepseek-v4-flash": "https://api.deepseek.com/v1",
  "deepseek-v4-pro": "https://api.deepseek.com/v1",
  "deepseek-v4-flash-vision-exp": "https://api.deepseek.com/v1",
  "deepseek-chat": "https://api.deepseek.com/v1",
  "deepseek-reasoner": "https://api.deepseek.com/v1",
  "glm-4-plus": "https://open.bigmodel.cn/api/paas/v4",
  "glm-4-flash": "https://open.bigmodel.cn/api/paas/v4",
  "qwen-turbo": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "qwen-plus": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "qwen-max": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "gpt-4o-mini": "https://api.openai.com/v1",
  "gpt-4o": "https://api.openai.com/v1",
  "qwen3:8b": "http://127.0.0.1:11434/v1",
  "qwen3:4b": "http://127.0.0.1:11434/v1",
};
function modelValue() { return $("#cfgModel").value.trim() || "deepseek-chat"; }
/* 自研下拉: 点箭头全量展示(分组), 点选即填, 也可继续直接输入 */
$("#modelDropBtn").onclick = (e) => {
  e.stopPropagation();
  $("#modelDrop").classList.toggle("show");
};
document.addEventListener("click", (e) => {
  if (!e.target.closest("#modelPick")) $("#modelDrop").classList.remove("show");
});
$("#modelDrop").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-m]");
  if (!b) return;
  $("#cfgModel").value = b.dataset.m;
  $("#modelDrop").classList.remove("show");
  if (!baseCustom) refreshBase();
});
let baseCustom = false;
function refreshBase() {
  const b = MODEL_BASE[modelValue()];
  if (b) $("#cfgBase").value = b;
  $("#baseToggle").textContent = baseCustom ? "恢复自动" : "自定义地址";
}
$("#baseToggle").onclick = () => {
  baseCustom = !baseCustom;
  $("#cfgBase").readOnly = !baseCustom;
  refreshBase();
};
$("#cfgModel").addEventListener("change", () => {
  if (!baseCustom) refreshBase();   // 自动模式: 随模型切地址
});
function effectiveBase() {
  if (baseCustom) return $("#cfgBase").value.trim();
  return MODEL_BASE[modelValue()] || $("#cfgBase").value.trim() || "https://api.deepseek.com/v1";
}

async function loadSettings() {
  const s = await api("/api/settings");
  $("#cfgModel").value = s.ai_model || "deepseek-chat";
  const auto = MODEL_BASE[modelValue()];
  baseCustom = !!(s.ai_base && auto && s.ai_base !== auto);   // 曾手动改过 → 保持手动模式
  $("#cfgBase").readOnly = !baseCustom;
  if (baseCustom) $("#cfgBase").value = s.ai_base;
  else refreshBase();
  $("#cfgKey").value = s.ai_key || "";
  $("#prefsNote").value = s.prefs_note || "";
  $("#dislikeNote").value = s.dislike_note || "";
  $("#modeSelect").value = s.mode || "标准";
  $("#cfgStatus").textContent = s.ai_ok ? "已配置 ✓" : "未配置";
  $("#cfgStatus").className = "hint " + (s.ai_ok ? "ok" : "off");
  applyAiModeBtn(s.ai_full_scan === "1");
  applyAiMainBtn(s.ai_enabled === "1");
  aiModeVals = s;
  loadTiebaSettings();
  refreshFloatBall();
  return s;
}
/* ---------- 各平台推送设置(动态权重行 + 贴吧吧列表) ---------- */
let platWs = {};   // name -> weight
function loadTiebaSettings() {
  api("/api/tieba").then((d) => {
    $("#tbBars").value = (d.bars || []).join("\n");
    if ($("#tbAllowHelp")) $("#tbAllowHelp").checked = d.allow_help !== false;
    platWs = {};
    (d.platforms || [{ name: "B站", weight: d.bili_weight || 90 },
                     { name: "贴吧", weight: d.tb_weight || 25 }])
      .forEach((p) => { platWs[p.name] = p.weight; });
    renderPlatWeights();
    fillTbBrowseKw();
  }).catch(() => {});
}
function renderPlatWeights() {
  const box = $("#platWeightRows");
  if (!box) return;
  // 权重档位: 0/25/50/75/100
  const levels = [0, 25, 50, 75, 100];
  box.innerHTML = Object.keys(platWs).map((name) => `
    <div class="row pw-row">
      <span class="wlabel">${esc(name)}</span>
      ${levels.map((w) =>
        `<button data-w="${w}" class="seg ${platWs[name] === w ? "on" : ""}"
          data-name="${esc(name)}">${w}</button>`).join("")}
    </div>`).join("");
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("#platWeightRows .seg");
  if (b) {
    platWs[b.dataset.name] = parseInt(b.dataset.w, 10);
    renderPlatWeights();
    savePlatWeights();   // 点击即保存生效
  }
});
async function savePlatWeights() {
  const body = {};
  // 通用: 平台名 → 权重key映射(B站→bili_weight, 贴吧→tb_weight, 小黑盒→xhh_weight)
  const KEYMAP = { "B站": "bili_weight", "贴吧": "tb_weight", "小黑盒": "xhh_weight" };
  Object.keys(platWs).forEach((name) => {
    if (KEYMAP[name]) body[KEYMAP[name]] = String(platWs[name]);
  });
  const r = await api("/api/tieba", { method: "POST", body });
  if (r.ok) { toast("平台权重已保存，正在重建信息流"); loadFeed(); loadStatus(); }
  else toast("权重保存失败：" + (r.error || ""), { warn: true });
}
$("#tbSave").onclick = async () => {
  const bars = $("#tbBars").value.split(/[\n,，、;；]+/).map((s) => s.trim()).filter(Boolean);
  const body = { bars, allow_help: $("#tbAllowHelp").checked };
  const r = await api("/api/tieba", { method: "POST", body });
  $("#tbStatus").textContent = r.ok ? "已保存 ✓" : ("失败：" + (r.error || ""));
  if (r.ok) { toast("吧列表/求助帖设置已保存，正在重建信息流"); loadFeed(); loadStatus(); }
};
/* AI 总开关 */
function applyAiMainBtn(on) {
  const btn = $("#aiMainToggle");
  btn.textContent = on ? "已开启 · 点击关闭" : "开启 AI 功能";
  btn.classList.toggle("off", !on);
  $("#aiMainState").textContent = on
    ? "状态：已开启（评分/聊天/自动加词按下方配置运行）"
    : "状态：已关闭（不消耗 token）";
}
/* ---------- AI 悬浮窗(仅电脑版): 设置开关 + 状态 ---------- */
function applyFloatBallBtn(enabled, running, supported, reason, hint) {
  const btn = $("#floatBallToggle"), st = $("#floatBallState");
  if (!btn) return;
  btn.textContent = enabled ? "已开启 · 点击关闭" : "开启悬浮窗";
  btn.classList.toggle("off", !enabled);
  if (!supported) {
    // 说清到底缺什么, 别只丢一句"环境不支持"
    st.textContent = "状态：本机无法显示悬浮球 —— " + (reason || "当前环境不支持")
      + (hint ? "\n处理办法：" + hint : "");
  } else {
    st.textContent = enabled
      ? (running ? "状态：悬浮球运行中 · 单击小球打开 AI 助手，右键小球退出"
                 : "状态：已开启但悬浮球未运行（重启应用后自动出现）")
      : "状态：未开启";
  }
}
async function refreshFloatBall() {
  if (window.AndroidBridge) {   // Android 共用同一前端, 悬浮窗是桌面专属
    $("#floatBallSec").classList.add("hidden");
    return;
  }
  try {
    const d = await api("/api/floatball");
    applyFloatBallBtn(d.enabled, d.running, d.supported, d.reason, d.hint);
  } catch (_) {}
}
$("#floatBallToggle").onclick = async () => {
  const btn = $("#floatBallToggle");
  const on = !btn.classList.contains("off");
  try {
    const d = await api("/api/floatball", { method: "POST", body: { enabled: !on } });
    applyFloatBallBtn(!on, d.running, d.supported, d.reason, d.hint);
    if (d.supported) toast(!on ? "悬浮球已出现在屏幕上" : "悬浮窗已关闭");
    else toast("本机无法显示悬浮球：" + (d.reason || "环境不支持"), { warn: true });
    if (!on && d.supported) {   // 拉起后复查: 进程闪退(如缺 tkinter)给出提示
      setTimeout(async () => {
        try {
          const s = await api("/api/floatball");
          applyFloatBallBtn(s.enabled, s.running, s.supported, s.reason, s.hint);
          if (s.enabled && !s.running) {
            toast(s.spawn_error || "悬浮球启动失败，请重启应用后再试", { warn: true });
          }
        } catch (_) {}
      }, 1500);
    }
  } catch (e) {
    toast("操作失败：" + (e.message || e), { warn: true });
  }
};

// 「找不到球？」: 悬浮球可能被拖到屏幕边缘外、被别的置顶窗口盖住、或位置记在换屏前的坐标上。
// 点这里让球自己回到右侧醒目位置并闪三下。
//
// 关键: 请求「发出去」不等于球「收到了」。球是独立进程, 靠 2.5s 一次的心跳来取信号,
// 所以这里不能一发出就报成功 —— 必须等球的回执(ack)才算数; 等不到就如实说"没响应",
// 让用户知道该去重新开启悬浮窗, 而不是对着一个永远没反馈的按钮反复点。
$("#floatBallFind").onclick = async () => {
  try {
    const d = await api("/api/floatball");
    if (!d.supported) {
      toast("本机无法显示悬浮球：" + (d.reason || "环境不支持"), { warn: true });
      return;
    }
    if (!d.running) {
      toast("悬浮球当前没在运行，先点「开启悬浮窗」", { warn: true });
      return;
    }
    const r = await api("/api/ball_recall_go");
    const want = (r && r.n) || 0;
    toast("已发出定位请求，正在等悬浮球响应…");
    // 球最坏要等一个完整心跳周期(2.5s)才会取到信号, 所以给 9×600ms ≈ 5.4s 的余量
    for (let i = 0; i < 9; i++) {
      await new Promise((ok) => setTimeout(ok, 600));
      const s = await api("/api/ball_recall");
      if ((s.ack || 0) >= want) {
        toast("悬浮球已回到屏幕右侧（闪三下的那个就是它）");
        return;
      }
    }
    toast("悬浮球没有响应，可能已经退出了，请点「开启悬浮窗」重新拉起", { warn: true });
  } catch (e) {
    toast("定位失败：" + (e.message || e), { warn: true });
  }
};

$("#aiMainToggle").onclick = async () => {
  const on = !(aiModeVals.ai_enabled === "1");
  await api("/api/settings", { method: "POST", body: { ai_enabled: on ? "1" : "0" } });
  aiModeVals.ai_enabled = on ? "1" : "0";
  applyAiMainBtn(on);
  toast(on ? "AI 功能已开启" : "AI 功能已关闭：不再消耗 token（聊天也停用）");
  loadFeed(); loadStatus();
};
/* ---------- AI 推荐模式(卡片框 + 详细设置) ---------- */
let aiModeVals = {};   // 最近一次 /api/settings 的快照
function applyAiModeBtn(on) {
  const btn = $("#aiModeToggle");
  btn.textContent = on ? "已开启 · 点击关闭" : "开启 AI 推荐模式";
  btn.classList.toggle("off", !on);
  $("#aiModeState").textContent = on
    ? "状态：开启 · 全部候选经 AI 筛查后推送（消耗 token 较大）"
    : "状态：关闭 · 按热度与机器规则推送（不消耗 AI）";
}
function segState(rowId, val, map) {
  const row = document.getElementById(rowId);
  if (!row) return;
  row.querySelectorAll(".seg").forEach((b) =>
    b.classList.toggle("on", String(b.dataset.v) === String(val)));
}
function segBind(rowId, cb) {
  const row = document.getElementById(rowId);
  if (!row) return;
  row.addEventListener("click", (e) => {
    const b = e.target.closest(".seg");
    if (!b) return;
    row.querySelectorAll(".seg").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    cb(b.dataset.v);
  });
}
$("#aiModeToggle").onclick = async () => {
  const on = !(aiModeVals.ai_full_scan === "1");
  await api("/api/settings", { method: "POST", body: { ai_full_scan: on ? "1" : "0" } });
  aiModeVals.ai_full_scan = on ? "1" : "0";
  applyAiModeBtn(on);
  toast(on ? "已开启 AI 推荐模式：全部视频经 AI 筛查后推送（消耗 token 较大）"
           : "已关闭全量筛查：仅热度前 120 条经 AI 筛查");
  loadFeed(); loadStatus();
};
$("#aiModeDetail").onclick = async () => {
  const s = await api("/api/settings");
  aiModeVals = s;
  segState("aiminRow", s.ai_interest_min || "0");
  segState("aifullRow", s.ai_full_scan || "0");
  segState("aibatchRow", s.ai_batch || "15");
  segState("aicacheRow", s.ai_cache_hours || "24");
  $("#aiDetailModal").classList.remove("hidden");
};
$("#aiDetailClose").onclick = $("#aiDetailCancel").onclick = () =>
  $("#aiDetailModal").classList.add("hidden");
$("#aiDetailModal").addEventListener("click", (e) => {
  if (e.target === $("#aiDetailModal")) $("#aiDetailModal").classList.add("hidden");
});
let aiDetailDraft = {};
segBind("aiminRow", (v) => { aiDetailDraft.ai_interest_min = v; });
segBind("aifullRow", (v) => { aiDetailDraft.ai_full_scan = v; });
segBind("aibatchRow", (v) => { aiDetailDraft.ai_batch = v; });
segBind("aicacheRow", (v) => { aiDetailDraft.ai_cache_hours = v; });
$("#aiDetailSave").onclick = async () => {
  const body = { ...aiDetailDraft };
  if (!Object.keys(body).length) { $("#aiDetailModal").classList.add("hidden"); return; }
  await api("/api/settings", { method: "POST", body });
  aiDetailDraft = {};
  $("#aiDetailModal").classList.add("hidden");
  toast("AI 推荐模式设置已保存，正在按新标准重建内容");
  await loadSettings();
  loadFeed(); loadStatus();
};
$("#aiDetailReset").onclick = async () => {
  await api("/api/settings", { method: "POST", body: {
    ai_interest_min: "0", ai_full_scan: "0", ai_batch: "15", ai_cache_hours: "24" } });
  aiDetailDraft = {};
  await loadSettings();
  segState("aiminRow", "0"); segState("aifullRow", "0");
  segState("aibatchRow", "15"); segState("aicacheRow", "24");
  toast("已恢复默认设置");
};
$("#cfgSave").onclick = async () => {
  await api("/api/settings", { method: "POST", body: {
    ai_base: effectiveBase(),
    ai_key: $("#cfgKey").value.trim(), ai_model: modelValue(),
  }});
  const r = await api("/api/settings");
  $("#cfgStatus").textContent = r.ai_ok ? "已保存 ✓" : "未配置";
  $("#cfgStatus").className = "hint " + (r.ai_ok ? "ok" : "off");
  toast(r.ai_ok ? "AI 配置已保存" : "已保存（未填 Key，仍按热度排序）");
  loadFeed(); loadStatus();
};
$("#prefsSave").onclick = async () => {
  await api("/api/settings", { method: "POST", body: { prefs_note: $("#prefsNote").value } });
  toast("偏好备注已保存，评分时生效");
  loadFeed();
};
$("#dislikeSave").onclick = async () => {
  await api("/api/settings", { method: "POST", body: { dislike_note: $("#dislikeNote").value } });
  toast("厌恶备注已保存，命中关键词的内容将被直接剔除");
  loadFeed();
};

/* ---------- 黑白名单 ---------- */
async function loadBlacklist() {
  const d = await api("/api/blacklist");
  $("#kwList").innerHTML = d.keywords.map((k) =>
    `<span class="chip">${esc(k)}<b data-del="keyword:${esc(k)}"></b></span>`).join("") || "";
  $("#auList").innerHTML = d.authors.map((a) =>
    `<span class="chip">${esc(a)}<b data-del="author:${esc(a)}"></b></span>`).join("") || "";
  document.querySelectorAll(".chip [data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      const [kind, value] = b.dataset.del.split(":");
      await api("/api/blacklist", { method: "POST", body: { kind, value, remove: true } });
      loadBlacklist(); loadFeed();
    }));
}
async function addBlack(kind, val) {
  if (!val) return;
  await api("/api/blacklist", { method: "POST", body: { kind, value: val } });
  loadBlacklist(); loadFeed();
}
$("#kwAdd").onclick = () => { addBlack("keyword", $("#kwInput").value.trim()); $("#kwInput").value = ""; };
$("#auAdd").onclick = () => { addBlack("author", $("#auInput").value.trim()); $("#auInput").value = ""; };
$("#kwInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#kwAdd").click(); });
$("#auInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#auAdd").click(); });

/* ---------- 免责声明 ---------- */
const DISCLAIMER_VERSION = "2026-09-03";
let disclaimerFirstOpen = false;
function openDisclaimerModal({ firstOpen = false } = {}) {
  disclaimerFirstOpen = firstOpen;
  $("#disclaimerModal").classList.toggle("disclaimer-first-open", firstOpen);
  $("#disclaimerModal").classList.remove("hidden");
}
function closeDisclaimerModal() {
  if (disclaimerFirstOpen) return;
  $("#disclaimerModal").classList.add("hidden");
}
$("#btnDisclaimer").onclick = () => openDisclaimerModal();
$("#disclaimerClose").onclick = closeDisclaimerModal;
$("#disclaimerCancel").onclick = closeDisclaimerModal;
$("#disclaimerModal").addEventListener("click", (e) => {
  if (e.target === $("#disclaimerModal")) closeDisclaimerModal();
});
$("#disclaimerAgree").onclick = async () => {
  const r = await api("/api/disclaimer/ack", {
    method: "POST", body: { version: DISCLAIMER_VERSION }
  });
  if (!r.ok) {
    toast(r.error || "免责声明确认失败，请稍后重试", { warn: true });
    return;
  }
  disclaimerFirstOpen = false;
  $("#disclaimerModal").classList.remove("disclaimer-first-open");
  $("#disclaimerModal").classList.add("hidden");
};

$("#modeSelect").onchange = async () => {
  await api("/api/settings", { method: "POST", body: { mode: $("#modeSelect").value } });
  toast("过滤档位：" + $("#modeSelect").value + ( $("#modeSelect").value === "严肃" ? "（严肃：只推硬核教程与知识）" : ""));
  loadFeed(true);   // 强制重建: 立即按新模式出内容, 不再显示旧池内容
};
let searchTimer;
$("#searchInput").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { feedState.q = e.target.value.trim(); loadFeed(); }, 350);
});
$("#btnUser").onclick = (e) => { e.stopPropagation(); $("#userDrop").classList.toggle("hidden"); };
document.addEventListener("click", () => $("#userDrop").classList.add("hidden"));
$("#userDrop").addEventListener("click", async (e) => {
  const act = e.target.closest("button")?.dataset.act;
  if (!act) return;
  $("#userDrop").classList.add("hidden");
  if (act === "account") { openAccountModal(); acctTab("bili"); }
  if (act === "prefs") {
    $("#rail").classList.remove("collapsed");
    $("#prefsNote").scrollIntoView({ behavior: "smooth" });
    $("#prefsNote").focus();
  }
  if (act === "reset") {
    if (confirm("确定清空历史/收藏/聊天/反馈数据？此操作不可恢复。")) {
      await api("/api/reset", { method: "POST", body: {} });
      toast("已清空"); loadConvs(); loadChat(); loadFeed(); loadPanel();
    }
  }
  if (act === "about") openDisclaimerModal();
});
/* 启动预缓存档位: 点击即保存生效 */
document.addEventListener("click", async (e) => {
  const b = e.target.closest("#mColdRow .seg");
  if (!b) return;
  [...b.parentElement.querySelectorAll(".seg")].forEach((x) => x.classList.toggle("on", x === b));
  await api("/api/machine", { method: "POST", body: { m_cold_start_n: b.dataset.v } });
  toast(b.dataset.v === "0" ? "启动预缓存已关闭" : "启动预缓存：" + b.dataset.v + " 条（下次打开直接显示）");
});

$("#btnClearLog").onclick = async () => {
  if (!confirm("清空历史/收藏/聊天记录？")) return;
  await api("/api/reset", { method: "POST", body: {} });
  toast("已清空"); loadConvs(); loadChat(); loadPanel();
};

/* ---------- 设置栏折叠 ---------- */
$("#railToggle").onclick = () => {
  if ($("#rail").classList.contains("hide-txt")) { setRailTxt(false); return; }   // 隐藏文字状态下先恢复
  const willCollapse = !$("#rail").classList.contains("collapsed");
  $("#rail").classList.toggle("collapsed");
  if (!willCollapse) {   // 展开时恢复记忆宽度
    const r = localStorage.getItem("w_rail");
    if (r) $("#rail").style.width = clampSide(parseInt(r, 10) || 230, 230, $("#rail")) + "px";
  }
  localStorage.setItem("rail_collapsed", willCollapse ? "1" : "");
};
if (localStorage.getItem("rail_collapsed")) $("#rail").classList.add("collapsed");
/* 隐藏设置文字: 面板全空但保留宽度/拖拽; 点击空白恢复 */
function setRailTxt(hide) {
  $("#rail").classList.toggle("hide-txt", hide);
  $("#rail").title = hide ? "点击恢复设置" : "";
  localStorage.setItem("rail_hide_txt", hide ? "1" : "");
}
$("#railHide").onclick = (e) => { e.stopPropagation(); setRailTxt(true); };
$("#rail").addEventListener("click", () => { if ($("#rail").classList.contains("hide-txt")) setRailTxt(false); });
if (localStorage.getItem("rail_hide_txt")) setRailTxt(true);

/* ---------- 分隔条(可拖拽调宽) ---------- */
function clampSide(w, min, el, reserve) {
  // 上限 = 容器宽 - 其余栏保留宽度(默认560, 面板场景490), 防止拖出页面
  const max = Math.max(min, el.parentElement.clientWidth - (reserve || 560));
  return Math.min(Math.max(min, w), max);
}
function bindSplitter(id, target, side, saveKey, min, reserve) {
  const sp = document.getElementById(id);
  sp.addEventListener("pointerdown", (e) => {
    sp.classList.add("drag");
    target.classList.add("dragging");
    const startX = e.clientX, startW = target.offsetWidth;
    const move = (ev) => {
      let w = side === "left" ? startW + (ev.clientX - startX) : startW - (ev.clientX - startX);
      w = clampSide(w, min, target, reserve);
      // 同时设 flexBasis: flex item 的 flex-basis 会覆盖 width, 不设则拖不动
      target.style.flexBasis = w + "px";
      target.style.width = w + "px";
    };
    const up = () => {
      sp.classList.remove("drag");
      target.classList.remove("dragging");
      localStorage.setItem(saveKey, target.style.width);
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  });
}
const w2 = localStorage.getItem("w_chat");
const w3 = localStorage.getItem("w_panel");
if (w2) $("#chat").style.width = clampSide(parseInt(w2, 10) || 280, 220, $("#chat")) + "px";
if (w3) {
  const wp = clampSide(parseInt(w3, 10) || 280, 230, $("#panel"), 490);
  $("#panel").style.width = wp + "px";
  $("#panel").style.flexBasis = wp + "px";
}
bindSplitter("sp1", $("#panel"), "right", "w_panel", 230, 490);   // 拖面板左缘: 右拖=变窄
bindSplitter("sp2", $("#chat"), "right", "w_chat", 220);
/* 设置栏: 右侧分割条, 向右拉=变宽 */
const w0 = localStorage.getItem("w_rail");
if (w0 && !$("#rail").classList.contains("collapsed"))
  $("#rail").style.width = clampSide(parseInt(w0, 10) || 230, 230, $("#rail")) + "px";
bindSplitter("sp0", $("#rail"), "left", "w_rail", 230);

/* ---------- 信息流刷新按钮 + 默认宽度70% ---------- */
$("#feedRefresh").onclick = () => { loadFeed(true); loadStatus(); toast("已刷新，正在获取新内容…"); };
$("#feedClear").onclick = async () => {
  if (!confirm("确定清空信息池并重新收录吗？将清空已收录的全部内容，并按当前设置重新抓取/评分。")) return;
  const r = await api("/api/pool/clear", { method: "POST", body: {} });
  if (!r.ok) return toast("清空信息池失败", { warn: true });
  toast("已清空信息池，正在重新收录…");
  loadFeed(true);      // refresh=1 → 后端重新抓取收录
  loadStatus();
};
function defaultFeedWidth() {
  if (localStorage.getItem("w_feed")) return;   // 用户拖过就不覆盖
  const feed = $("#feed"), panel = $("#panel");
  const mid = feed.offsetWidth + panel.offsetWidth;
  feed.style.width = Math.round(mid * 0.7) + "px";
}

/* ---------- B站账号（扫码登录 → 优化推荐） ---------- */
let qrTimer = null;
function qrStop() { if (qrTimer) { clearInterval(qrTimer); qrTimer = null; } }
function isTouchDevice() {
  return window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
}
/* 安卓壳内无法"自己扫自己": 触屏设备在二维码下方提供本机确认入口,
   点击后 window.open → WebView 转交系统浏览器 → App 内确认 */
function addSelfLoginLink(box, url) {
  if (!isTouchDevice()) return;
  const btn = document.createElement("button");
  btn.className = "alt";
  btn.style.cssText = "margin-top:8px;width:100%";
  btn.textContent = "📱 本机登录：在浏览器中打开确认页";
  btn.onclick = () => openExternal(url);
  box.appendChild(btn);
}
async function qrStart() {
  qrStop();
  if ($("#accountModal").classList.contains("hidden")) return;   // 弹窗未打开时不生成二维码
  const box = $("#qrBox"), st = $("#qrState");
  box.innerHTML = "";
  st.textContent = "正在生成二维码…";
  let r;
  try { r = await api("/api/login/qr", { method: "POST", body: {} }); } catch (e) {
    st.textContent = "生成失败：" + e; return;
  }
  if (!r.ok) { st.textContent = "生成失败：" + (r.error || ""); return; }
  new QRCode(box, { text: r.url, width: 200, height: 200, correctLevel: QRCode.CorrectLevel.M });
  addSelfLoginLink(box, r.url);
  st.textContent = "用手机 B站 App 「扫一扫」登录";
  qrTimer = setInterval(async () => {
    let p;
    try { p = await api("/api/login/qr/poll?key=" + encodeURIComponent(r.key)); } catch (e) { return; }
    if (p.status === "ok") {
      qrStop();
      st.textContent = "登录成功！正在生成画像…";
      toast("B站账号登录成功：@" + (p.nickname || ""));
      loadAccountView(); loadFeed(); loadStatus();
      setTimeout(() => $("#accountModal").classList.add("hidden"), 1200);
    } else if (p.status === "confirmed") {
      st.textContent = p.message;
    } else if (p.status === "expired") {
      qrStop(); st.textContent = p.message;
    } else if (p.status === "error") {
      qrStop(); st.textContent = p.message;
    } else {
      st.textContent = p.message || "等待扫码…";
    }
  }, 2500);
}
async function loadAccountView() {
  const d = await api("/api/account");
  const av = $("#userAvatar");
  const box = $("#biliAvatarBox");
  if (d.ok) {
    av.src = "/api/avatar?t=" + Date.now();   // 本地代理, 规避图床防盗链
    av.title = "B站账号：" + d.nickname;
    av.classList.remove("hidden");
    box.classList.remove("hidden");
  } else {
    av.classList.add("hidden");
    box.classList.add("hidden");
  }
  const st = $("#acctStatus");
  st.innerHTML = !d.has_cookie
    ? "尚未登录。用手机 B站 App 扫下方二维码即可登录，读取观看历史/收藏夹后自动优化推荐（无 AI 也生效）。"
    : (d.ok
        ? "<b>已登录：" + esc(d.nickname) + "</b> · 画像同步于 " + ago(d.synced_at) +
          " · 基于历史 " + d.history_n + " 条"
        : "已保存登录凭证但画像未生成（可能已过期），点「同步账号数据」重试。");
  $("#qrZone").style.display = d.ok ? "none" : "";
  if (d.ok) qrStop(); else qrStart();
  const p = $("#acctProfile");
  p.innerHTML = d.ok
    ? `<div class="acct-profile-block">
        <b>常看作者</b>：${(d.authors || []).map((a) => esc(a.name)).join("、") || "暂无"}<br>
        <b>高频关键词</b>：${(d.keywords || []).map(esc).join("、") || "暂无"}<br>
        <b>收藏夹</b>：${(d.favs || []).map((f) => esc(f.title) + " (" + f.count + ")").join("、") || "暂无"}</div>`
    : "";
}
$("#qrRefresh").onclick = qrStart;
$("#acctOpen").onclick = () => openExternal("https://www.bilibili.com");
$("#acctLogin").onclick = async () => {
  const cookie = $("#acctCookie").value.trim();
  if (!cookie) { toast("请先粘贴 Cookie", { warn: true }); return; }
  const r = await api("/api/account", { method: "POST", body: { cookie } });
  if (r.ok) { toast("B站账号登录成功，画像已生成"); loadAccountView(); loadMessages(); loadFeed(); loadStatus(); }
  else toast("登录失败：" + (r.error || "Cookie 无效"), { warn: true });
};
$("#acctSync").onclick = async () => {
  const r = await api("/api/account/sync", { method: "POST", body: {} });
  if (r.ok) { toast("账号数据已同步"); loadAccountView(); loadFeed(); }
  else toast("同步失败：" + (r.error || ""), { warn: true });
};
$("#acctLogout").onclick = async () => {
  await api("/api/account/logout", { method: "POST", body: {} });
  $("#acctCookie").value = "";
  loadAccountView();
  loadMessages();
  toast("已退出账号");
  loadFeed();
};
$("#acctClose").onclick = () => { qrStop(); tbLoginPollStop(); xhhPollStop(); $("#accountModal").classList.add("hidden"); };
$("#accountModal").addEventListener("click", (e) => {
  if (e.target.id === "accountModal") { qrStop(); tbLoginPollStop(); xhhPollStop(); $("#accountModal").classList.add("hidden"); }
});
/* ---------- 账号弹窗标签页(B站/贴吧) ---------- */
function acctTab(name) {
  $("#tabBili").classList.toggle("on", name === "bili");
  $("#tabTieba").classList.toggle("on", name === "tieba");
  $("#tabXhh").classList.toggle("on", name === "xhh");
  $("#acctBili").classList.toggle("hidden", name !== "bili");
  $("#acctTieba").classList.toggle("hidden", name !== "tieba");
  $("#acctXhh").classList.toggle("hidden", name !== "xhh");
}
$("#tabBili").onclick = () => { acctTab("bili"); };
$("#tabTieba").onclick = () => { acctTab("tieba"); };
$("#tabXhh").onclick = () => { acctTab("xhh"); loadXhhView(); };
function openAccountModal() { loadAccountView(); loadTiebaView(); $("#accountModal").classList.remove("hidden"); }
$("#userAvatar").onclick = () => { openAccountModal(); acctTab("bili"); };

/* ---------- 贴吧账号（一键浏览器登录 → CDP 抓 cookie） ---------- */
let tbPollTimer = null;
function tbLoginPollStop() { if (tbPollTimer) { clearInterval(tbPollTimer); tbPollTimer = null; } }
async function loadTiebaView() {
  const d = await api("/api/tieba");
  const st = $("#tbAcctStatus");
  const loginned = d.logged_in || d.cookie_set;
  st.innerHTML = loginned
    ? `<b>已登录${esc(acctName(d.nickname))}</b> · 登录后采集更稳 · 最近抓取 ${ago(d.last_fetch)}`
    : "未登录（可匿名采集，登录后更稳 + 可导入关注吧）";
  $("#tbLogout").classList.toggle("hidden", !d.cookie_set);
  $("#tbLoginZone").style.display = d.cookie_set && d.logged_in ? "none" : "";
  $("#tbLoginState").textContent = d.login_status && d.login_status.message
    ? (d.login_status.status + " · " + d.login_status.message) : "未启动";
  // 顶栏头像: 已登录即显示头像框(有真实头像用头像, 无则字母占位) + 平台标签
  const tbImg = $("#tbAvatar"), tbBox = $("#tbAvatarBox"), tbPh = $("#tbAvatarPh");
  if (d.cookie_set) {
    if (d.avatar) {
      tbImg.src = "/api/tieba/avatar?t=" + Date.now();
      tbImg.title = "贴吧账号：" + (d.nickname || "");
      tbImg.classList.remove("hidden");
      tbPh.classList.add("hidden");
    } else {
      tbImg.classList.add("hidden");
      tbPh.classList.remove("hidden");
    }
    tbBox.classList.remove("hidden");
  } else {
    tbImg.classList.add("hidden");
    tbPh.classList.add("hidden");
    tbBox.classList.add("hidden");
  }
  fillTbBrowseKw();
  return d;
}
$("#tbAvatarBox").onclick = () => { openAccountModal(); acctTab("tieba"); };
if (window.AndroidBridge) {
  const tbSms = document.getElementById("tbLoginNativeSms");
  const tbWeb = document.getElementById("tbLoginNativeWeb");
  if (tbSms) {
    tbSms.classList.remove("hidden");   // 安卓壳内: 手机版短信验证码登录页(wappass)
    tbSms.onclick = () => window.AndroidBridge.openTiebaLogin("sms");
  }
  if (tbWeb) {
    tbWeb.classList.remove("hidden");   // 安卓壳内: 电脑版 passport 页(密码/扫码)
    tbWeb.onclick = () => window.AndroidBridge.openTiebaLogin("web");
  }
}
$("#tbLoginStart").onclick = async () => {
  const r = await api("/api/tieba/login", { method: "POST", body: {} });
  if (!r.ok) { toast("启动失败：" + (r.error || ""), { warn: true }); return; }
  if (r.manual) {
    tbLoginPollStop();
    $("#tbLoginStop").classList.add("hidden");
    $("#tbLoginState").textContent = "已用默认浏览器打开登录；完成后请在下方粘贴 Cookie 保存";
    toast("默认浏览器已打开：登录后粘贴 Cookie 即可保存");
    return;
  }
  $("#tbLoginStop").classList.remove("hidden");
  $("#tbLoginState").textContent = "浏览器窗口已弹出，请在窗口中完成登录…";
  tbLoginPollStop();
  tbPollTimer = setInterval(async () => {
    let p;
    try { p = await api("/api/tieba/login/poll"); } catch (e) { return; }
    $("#tbLoginState").textContent = p.message || p.status || "等待中…";
    if (p.status === "ok") {
      tbLoginPollStop(); $("#tbLoginStop").classList.add("hidden");
      toast("贴吧登录成功，已自动保存凭证");
      loadTiebaView(); loadMessages(); loadStatus(); loadFeed();
    } else if (p.status === "expired" || p.status === "error" || p.status === "closed") {
      tbLoginPollStop(); $("#tbLoginStop").classList.add("hidden");
      if (p.status === "closed") return;   // 用户自己关了窗口, 不打扰
      toast(p.status === "expired" ? "登录超时，请重试" : (p.message || "登录失败"), { warn: true });
    }
  }, 2500);
};
$("#tbLoginStop").onclick = async () => {
  await api("/api/tieba/login/stop", { method: "POST", body: {} });
  tbLoginPollStop(); $("#tbLoginStop").classList.add("hidden");
  $("#tbLoginState").textContent = "窗口已关闭";
};
$("#tbLoginManual").onclick = async () => {
  const cookie = $("#tbCookie").value.trim();
  if (!cookie) { toast("请先粘贴 Cookie", { warn: true }); return; }
  const res = await api("/api/tieba/login/manual", { method: "POST", body: { cookie } });
  if (res.ok) { toast("贴吧登录成功"); loadTiebaView(); loadMessages(); loadStatus(); }
  else toast("登录失败：" + (res.error || "Cookie 无效"), { warn: true });
};
$("#tbLogout").onclick = async () => {
  await api("/api/tieba/logout", { method: "POST", body: {} });
  $("#tbCookie").value = "";
  loadTiebaView(); loadMessages(); loadStatus(); loadFeed();
  toast("已退出贴吧账号");
};
$("#tbImport").onclick = async () => {
  $("#tbImportHint").textContent = "拉取中…";
  const r = await api("/api/tieba/mylike");
  if (!r.ok) { $("#tbImportHint").textContent = ""; toast("导入失败：" + (r.error || ""), { warn: true }); return; }
  const cur = ($("#tbBars").value || "").split(/[\n,，、;；]+/).map((s) => s.trim()).filter(Boolean);
  const merged = [...new Set([...cur, ...r.bars])];
  $("#tbBars").value = merged.join("\n");
  $("#tbImportHint").textContent = "已导入 " + r.bars.length + " 个吧";
  const sv = await api("/api/tieba", { method: "POST", body: { bars: merged } });
  if (sv.ok) toast("已导入关注吧并保存，正在重建信息流");
  else toast("保存失败：" + (sv.error || ""), { warn: true });
};
/* ---------- 浏览器辅助采集(贴吧风控时手动通道) ---------- */
function fillTbBrowseKw() {
  const sel = $("#tbBrowseKw");
  if (!sel) return;
  const bars = ($("#tbBars").value || "").split(/[\n,，、;；]+/).map((s) => s.trim()).filter(Boolean);
  sel.innerHTML = bars.map((b) => `<option value="${esc(b)}">${esc(b)}吧</option>`).join("") ||
    `<option value="">（先在设置里填吧名）</option>`;
}
$("#tbBars").addEventListener("input", fillTbBrowseKw);
$("#tbBrowseOpen").onclick = async () => {
  const kw = $("#tbBrowseKw").value;
  if (!kw) { toast("请先在设置里填吧名", { warn: true }); return; }
  const r = await api("/api/tieba/browse?kw=" + encodeURIComponent(kw));
  $("#tbBrowseHint").textContent = r.ok ? r.message : ("失败：" + (r.error || ""));
  if (!r.ok) { toast("打开失败：" + (r.error || ""), { warn: true }); return; }
  toast("贴吧窗口已打开，等页面加载完点「抓取此页」");
};
$("#tbBrowseGrab").onclick = async () => {
  $("#tbBrowseHint").textContent = "抓取中…";
  const r = await api("/api/tieba/grab", { method: "POST", body: {} });
  $("#tbBrowseHint").textContent = r.ok ? r.message : ("失败：" + (r.error || ""));
  if (r.ok) { toast("已抓取 " + r.items + " 条：" + (r.fname || "")); loadFeed(); loadStatus(); }
  else toast("抓取失败：" + (r.error || ""), { warn: true });
};

/* ---------- 小黑盒账号（扫码登录 → 稳定采集） ---------- */
let xhhPollTimer = null;
function xhhPollStop() { if (xhhPollTimer) { clearInterval(xhhPollTimer); xhhPollTimer = null; } }
async function loadXhhView() {
  const d = await api("/api/xhh");
  const st = $("#xhhAcctStatus");
  st.innerHTML = d.logged_in
    ? `<b>已登录${esc(acctName(d.nickname))}</b> · 采集正常`
    : "未登录（游客采集可用；扫码登录后更稳）";
  $("#xhhLogout").classList.toggle("hidden", !d.logged_in);
  $("#xhhLoginZone").style.display = d.logged_in ? "none" : "";
  // 顶栏头像: 已登录即显示(有真实头像用头像, 无则"盒"占位) + 平台标签
  const img = $("#xhhAvatar"), box = $("#xhhAvatarBox"), ph = $("#xhhAvatarPh");
  if (d.logged_in) {
    if (d.avatar) {
      img.src = "/api/xhh/avatar?t=" + Date.now();
      img.title = "小黑盒账号：" + (d.nickname || "");
      img.classList.remove("hidden");
      ph.classList.add("hidden");
    } else {
      img.classList.add("hidden");
      ph.classList.remove("hidden");
    }
    box.classList.remove("hidden");
  } else {
    img.classList.add("hidden");
    ph.classList.add("hidden");
    box.classList.add("hidden");
  }
  return d;
}
$("#xhhAvatarBox").onclick = () => { openAccountModal(); acctTab("xhh"); };
async function xhhNewQr(autoRenew) {
  const r = await api("/api/xhh/qr");
  if (!r.ok) { toast("二维码生成失败：" + (r.error || ""), { warn: true }); return false; }
  const box = $("#xhhQrBox");
  box.innerHTML = "";
  new QRCode(box, { text: r.url, width: 200, height: 200, correctLevel: QRCode.CorrectLevel.M });
  addSelfLoginLink(box, r.url);
  $("#xhhQrState").textContent = autoRenew
    ? "二维码已自动续期，请重新扫码"
    : "用手机小黑盒 App「扫一扫」登录（二维码 " + r.expire + " 秒有效，请提前打开手机小黑盒做好准备）";
  return true;
}
$("#xhhQrGen").onclick = async () => {
  if (!(await xhhNewQr(false))) return;
  $("#xhhQrStop").classList.remove("hidden");
  xhhPollStop();
  xhhPollTimer = setInterval(async () => {
    let p;
    try { p = await api("/api/xhh/qr/poll"); } catch (e) { return; }
    $("#xhhQrState").textContent = p.message || "等待扫码…";
    if (p.status === "ok") {
      xhhPollStop(); $("#xhhQrStop").classList.add("hidden");
      toast("小黑盒登录成功：" + (p.nickname || ""));
      loadXhhView(); loadMessages(); loadFeed(); loadStatus();
    } else if (p.status === "expired" || p.status === "error") {
      // 二维码过期 → 自动重新生成, 继续轮询(用户无需手动操作)
      if (await xhhNewQr(true)) {
        toast("二维码已过期，已自动续期，请重新扫码");
      } else {
        xhhPollStop(); $("#xhhQrStop").classList.add("hidden");
        $("#xhhQrState").textContent = p.message;
      }
    }
  }, 2500);
};
$("#xhhQrStop").onclick = () => { xhhPollStop(); $("#xhhQrStop").classList.add("hidden"); $("#xhhQrState").textContent = "已停止轮询"; };
$("#xhhLogout").onclick = async () => {
  await api("/api/xhh/logout", { method: "POST", body: {} });
  loadXhhView(); loadMessages(); loadStatus();
  toast("已退出小黑盒账号");
};

/* ---------- 机器筛选（其他设置） ---------- */
let srcOn = {};
function renderSrcChips(groups) {
  const box = $("#srcChips");
  box.innerHTML = groups.map((g) =>
    `<span class="chip ${srcOn[g.id] ? "on2" : ""}" data-id="${g.id}">${esc(g.name)}</span>`).join("");
  box.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", async () => {
    srcOn[c.dataset.id] = !srcOn[c.dataset.id];
    renderSrcChips(groups);
    feedState.gen += 1;
    cancelFeedWork();
    clearFeed();                       // 立即清空列表, 不显示旧tag内容
    feedState.offset = 0;
    $("#feedMore").classList.add("show");
    $("#feedMore").textContent = "正在按新分区重建…";
    try {
      await api("/api/machine", { method: "POST",
        body: { m_sources: Object.keys(srcOn).filter((id) => srcOn[id]) } });
      await feedFetch(false, feedState.gen);          // 新分区世代已就绪, 直接取首批
      toast("分区已切换：已按新分区刷新");
    } catch (e) {
      toast("分区切换失败：" + e, { warn: true });
    }
  }));
}
async function loadMachine() {
  const d = await api("/api/machine");
  const s = d.settings;
  srcOn = {};
  (d.source_groups || []).forEach((g) => { srcOn[g.id] = g.on; });
  renderSrcChips(d.source_groups || []);
  $("#mBlockSync").checked = s.m_blocklist_sync === "1";
  $("#mDedup").checked = s.m_dedup === "1";
  if ($("#mEmotion")) $("#mEmotion").checked = s.m_emotion !== "0";
  if ($("#mNewsFresh")) $("#mNewsFresh").checked = s.m_news_fresh !== "0";
  $("#mMinViews").value = s.m_min_views || "0";
  $("#mMaxViews").value = s.m_max_views || "0";
  $("#mMaxDur").value = Math.round((parseInt(s.m_max_duration, 10) || 0) / 60);
  $("#mMinDur").value = Math.round((parseInt(s.m_min_duration, 10) || 60) / 60);
  $("#mMaxAge").value = s.m_max_age_months || "5";
  const coldV = String(s.m_cold_start_n || "10");
  if ($("#mColdRow")) $("#mColdRow").querySelectorAll(".seg").forEach((b) =>
    b.classList.toggle("on", b.dataset.v === coldV));
  if ($("#mSerMaxAge")) $("#mSerMaxAge").value = s.ser_max_age_months || "30";
  $("#mSeenHours").value = s.m_seen_hours || "6";
  $("#mRepeatLimit").value = s.m_repeat_limit || "4";
  $("#mRepeatCooldown").value = s.m_repeat_cooldown_hours || "72";
  if ($("#mFollowEmph")) $("#mFollowEmph").checked = s.bili_follow_emph !== "0";
  if ($("#mFollowMonths")) { $("#mFollowMonths").value = s.bili_follow_months || "1"; syncMonthsLabel(); }
  if ($("#xhhComm")) $("#xhhComm").value = s.xhh_comm || "";
  if ($("#xhhTopic")) $("#xhhTopic").value = s.xhh_topic || "";
  $("#mBlStatus").textContent = d.account_ok
    ? "✓ 已登录 B站账号，可同步或自动同步你的拉黑名单（登录时自动导入）"
    : "需要先登录 B站账号（个人 → B站账号），登录后会自动同步你的拉黑名单";
  $("#mBlList").innerHTML = d.blocked.length
    ? d.blocked.map((b) => `<span class="chip">${esc(b.name)}<b data-name="${esc(b.name)}"></b></span>`).join("")
    : '<span class="tip">暂无已同步的拉黑名单（B站拉黑过的用户会在登录后自动导入并屏蔽）</span>';
  document.querySelectorAll("#mBlList [data-name]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api("/api/machine/blocklist-remove", { method: "POST", body: { name: b.dataset.name } });
      loadMachine(); loadFeed(); toast("已移除该屏蔽");
    }));
}
$("#btnMachine").onclick = () => { loadMachine(); loadTiebaSettings(); machineTab("general"); $("#machineModal").classList.remove("hidden"); };
$("#mClose").onclick = () => $("#machineModal").classList.add("hidden");
$("#machineModal").addEventListener("click", (e) => {
  if (e.target.id === "machineModal") $("#machineModal").classList.add("hidden");
});
/* 机器筛选弹窗标签页(B站/贴吧/小黑盒) */
function machineTab(name) {
  $("#mTabGeneral").classList.toggle("on", name === "general");
  $("#mTabBili").classList.toggle("on", name === "bili");
  $("#mTabTieba").classList.toggle("on", name === "tieba");
  $("#mTabXhh").classList.toggle("on", name === "xhh");
  $("#mGeneral").classList.toggle("hidden", name !== "general");
  $("#mBili").classList.toggle("hidden", name !== "bili");
  $("#mTieba").classList.toggle("hidden", name !== "tieba");
  $("#mXhh").classList.toggle("hidden", name !== "xhh");
}
$("#mTabGeneral").onclick = () => machineTab("general");
$("#mTabBili").onclick = () => machineTab("bili");
$("#mTabTieba").onclick = () => machineTab("tieba");
$("#mTabXhh").onclick = () => machineTab("xhh");
$("#mGeneralSave").onclick = async () => {
  await api("/api/machine", { method: "POST", body: {
    m_min_views: String(parseInt($("#mMinViews").value, 10) || 0),
    m_max_views: String(parseInt($("#mMaxViews").value, 10) || 0),
    m_max_duration: String((parseInt($("#mMaxDur").value, 10) || 0) * 60),
    m_min_duration: String((parseInt($("#mMinDur").value, 10) || 1) * 60),
    m_max_age_months: String(parseInt($("#mMaxAge").value, 10) || 0),
    ser_max_age_months: String(parseInt($("#mSerMaxAge").value, 10) || 0),
    m_seen_hours: String(parseInt($("#mSeenHours").value, 10) || 0),
    m_repeat_limit: String(Math.max(1, parseInt($("#mRepeatLimit").value, 10) || 4)),
    m_repeat_cooldown_hours: String(Math.max(0, parseInt($("#mRepeatCooldown").value, 10) || 72)),
  }});
  toast("通用筛选设置已保存");
  loadFeed(true); loadMachine();
};
$("#xhhSave").onclick = async () => {
  const r = await api("/api/machine", { method: "POST", body: {
    xhh_comm: $("#xhhComm").value.trim(),
    xhh_topic: $("#xhhTopic").value.trim(),
  }});
  $("#xhhStatus").textContent = r.ok ? "已保存 ✓" : ("失败：" + (r.error || ""));
  if (r.ok) { toast("小黑盒筛选已保存，正在重建信息流"); loadFeed(); loadMachine(); }
  else toast("小黑盒筛选保存失败：" + (r.error || ""), { warn: true });
};
$("#mSave").onclick = async () => {
  await api("/api/machine", { method: "POST", body: {
    m_blocklist_sync: $("#mBlockSync").checked ? "1" : "0",
    m_dedup: $("#mDedup").checked ? "1" : "0",
    m_emotion: $("#mEmotion").checked ? "1" : "0",
    m_news_fresh: $("#mNewsFresh").checked ? "1" : "0",
    bili_follow_emph: $("#mFollowEmph").checked ? "1" : "0",
    bili_follow_months: String(parseInt($("#mFollowMonths").value, 10) || 0),
    m_sources: Object.keys(srcOn).filter((id) => srcOn[id]),
  }});
  toast("机器筛选设置已保存");
  loadFeed(); loadMachine();
};
$("#mBlSync").onclick = async () => {
  const r = await api("/api/machine/blocklist-sync", { method: "POST", body: {} });
  if (r.ok) { toast("已同步 " + r.count + " 位拉黑用户"); loadMachine(); loadFeed(); }
  else toast("同步失败：" + (r.error || ""), { warn: true });
};

/* 关注的人期限滑条: 实时更新显示 */
function syncMonthsLabel() {
  const el = $("#mFollowMonths");
  if (!el) return;
  $("#mFollowMonthsVal").textContent = el.value;
}
if ($("#mFollowMonths")) $("#mFollowMonths").addEventListener("input", syncMonthsLabel);

/* ---------- 初始化 ---------- */
(async function init() {
  const settings = await loadSettings();
  if (settings.disclaimer_version !== DISCLAIMER_VERSION) openDisclaimerModal({ firstOpen: true });
  loadAccountView();
  loadTiebaView();   // 顶栏贴吧头像框: 初始化即显示(不依赖打开个人弹窗)
  loadXhhView();     // 顶栏小黑盒头像框: 初始化即显示
  loadMessages();
  loadBlacklist();
  await loadConvs();
  loadChat();
  loadFeed();
  loadPanel();
  defaultFeedWidth();
})();

/* 安卓壳应用内登录回传: Java 侧已把 Cookie 直连 POST 到 /api/tieba/login/manual,
   这里只负责收到结果通知后刷新界面(页面尚未加载完时跳过, 启动流程会自然读到新登录态) */
window.__nativeTiebaSaved = (ok) => {
  if (ok) {
    toast("贴吧登录成功");
    loadTiebaView(); loadMessages(); loadStatus(); loadFeed();
  } else {
    toast("贴吧登录保存失败，请重试", { warn: true });
  }
};

/* ---------- 移动端底部导航(桌面端由 CSS 隐藏, 此处逻辑自然不触发显示) ---------- */
(function () {
  const bar = document.getElementById("tabbar");
  if (!bar) return;
  const mqMobile = window.matchMedia("(max-width: 1023.9px)");
  function setView(v) {
    document.body.dataset.view = v;
    bar.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("on", b.dataset.view === v));
    if (v === "panel") loadPanel();   // 记录视图打开时刷新内容
  }
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (b) setView(b.dataset.view);
  });
  // 手机上点搜索框时切回首页视图, 键盘输入后能直接看到搜索结果
  $("#searchInput").addEventListener("focus", () => {
    if (mqMobile.matches) setView("feed");
  });
  // 手机上 meta 信息行默认折叠为两行, 点击展开/收起(桌面端完整显示)
  const meta = $("#feedMeta");
  if (mqMobile.matches) {
    meta.classList.add("clamp");
    meta.addEventListener("click", () =>
      meta.classList.toggle("expanded"));
  }
  // 桌面: 来源行可折叠(缩小窗口时省空间), 状态记忆
  const metaToggle = document.getElementById("feedMetaToggle");
  if (metaToggle) {
    const applyMetaFold = () => {
      const folded = localStorage.getItem("feed_meta_fold") === "1";
      meta.classList.toggle("fold", folded && !mqMobile.matches);
      metaToggle.textContent = folded ? "▼" : "▲";
      metaToggle.title = folded ? "展开来源信息" : "收起来源信息";
    };
    metaToggle.addEventListener("click", () => {
      localStorage.setItem("feed_meta_fold",
        localStorage.getItem("feed_meta_fold") === "1" ? "" : "1");
      applyMetaFold();
    });
    applyMetaFold();
  }
  // 聊天视图的返回按钮(移动端)
  const chatBack = document.getElementById("chatBack");
  if (chatBack) chatBack.addEventListener("click", () => setView("feed"));

  /* 边缘手势(跟手版): 右缘向左拖, 聊天面板跟手滑入, 松手过阈值(或快速甩)吸附打开、
     否则弹回右侧; 聊天里左缘向右拖同理滑出回主页。纵向滑动让位给页面滚动。 */
  const chatEl = $("#chat");
  const VW = () => window.innerWidth;
  let sw = null;   // {edge, sx, sy, base, lx, lt, v, from}
  function chatBase() {   // 起拖基准 = 面板当前位移(支持动画中途接住)
    const m = getComputedStyle(chatEl).transform;
    if (!m || m === "none") return document.body.dataset.view === "chat" ? 0 : VW();
    return Math.max(0, Math.min(VW(), new DOMMatrix(m).m41));
  }
  function chatDragTo(offset) {
    chatEl.classList.add("dragging");   // 跟手期间关过渡
    chatEl.style.transform = "translateX(" + Math.max(0, Math.min(VW(), offset)) + "px)";
  }
  function chatSettle(open, fromView) {   // 松手后吸附到目标位(带动画)
    document.body.classList.remove("chat-drag");
    chatEl.classList.remove("dragging");
    chatEl.style.transform = open ? "translateX(0)" : "translateX(" + VW() + "px)";
    document.body.dataset.view = open ? "chat" : (fromView || "feed");
    document.querySelectorAll("#tabbar button").forEach((b) =>
      b.classList.toggle("on", b.dataset.view === document.body.dataset.view));
    setTimeout(() => { chatEl.style.transform = ""; }, 260);   // 动画完交还 CSS 控制
  }
  document.addEventListener("touchstart", (e) => {
    if (!mqMobile.matches) return;
    if (document.querySelector(".modal:not(.hidden),#playerBox,#readerBox")) return;   // 弹窗/播放器/阅读器打开时不识别
    if (e.target.closest("input,textarea,.m-drop,#convList")) return;    // 输入区/下拉不识别
    const t = e.touches[0];
    const W = window.innerWidth;
    const v = document.body.dataset.view;
    let edge = "";
    if (t.clientX > W - 32 && (v === "feed" || v === "panel")) edge = "right";
    else if (t.clientX < 32 && v === "chat") edge = "left";
    if (!edge) return;
    if (edge === "right") document.body.classList.add("chat-drag");   // 拖入期间隐藏底部导航
    sw = { edge, sx: t.clientX, sy: t.clientY, base: chatBase(),
           lx: t.clientX, lt: performance.now(), v: 0, from: v };
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (!sw) return;
    const t = e.touches[0];
    const dx = t.clientX - sw.sx, dy = t.clientY - sw.sy;
    if (Math.abs(dy) > 40 && Math.abs(dy) > Math.abs(dx)) {   // 纵向意图 → 让位滚动, 弹回原位
      chatSettle(sw.edge === "left", sw.from);
      sw = null;
      return;
    }
    const now = performance.now();
    if (now - sw.lt >= 8) {   // 间隔过短(<8ms, 同帧合成事件)不计速度, 防误判"快甩"
      sw.v = (t.clientX - sw.lx) / (now - sw.lt);
      sw.lx = t.clientX; sw.lt = now;
    }
    chatDragTo(sw.base + dx);
  }, { passive: true });
  function swEnd(e) {
    if (!sw) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - sw.sx, dy = t.clientY - sw.sy;
    const v = sw.v || 0, edge = sw.edge, from = sw.from;
    sw = null;
    if (Math.abs(dy) > 80) { chatSettle(edge === "left", from); return; }
    if (edge === "right") {                                   // 拉入聊天
      const dragged = Math.max(0, -dx);
      const commit = dragged > VW() * 0.35 || v < -0.5;       // 过 35% 阈值或快速左甩 → 打开
      chatSettle(commit, from);
    } else {                                                  // 推出聊天
      const dragged = Math.max(0, dx);
      const commit = dragged > VW() * 0.35 || v > 0.5;        // 过阈值或快速右甩 → 关闭
      chatSettle(!commit, "feed");                            // 提交关闭 → 回主页; 弹回 → 留在聊天
    }
  }
  document.addEventListener("touchend", swEnd, { passive: true });
  document.addEventListener("touchcancel", () => {
    if (!sw) return;
    const edge = sw.edge, from = sw.from;
    sw = null;
    chatSettle(edge === "left", from);
  });

  // 暴露给安卓返回键: 智能返回(关浮层 → 切回首页)
  window.__setMobileView = setView;
  /* ---------- 长按卡片菜单: 本地预览 / 跳转软件 / 复制链接 ---------- */
  let lpTimer = null, lpSx = 0, lpSy = 0;
  function closeCardMenu() {
    const m = document.getElementById("cardMenu");
    if (m) m.remove();
  }
  function showCardMenu(card) {
    closeCardMenu();
    const platform = card.dataset.platform || "";
    const url = card.dataset.url || "";
    const bvid = (!isTbCard(platform)) ? ((url.match(/BV[0-9A-Za-z]{8,12}/) || [])[0] || "") : "";
    const tbTid = platform === "贴吧" ? ((url.match(/\/p\/(\d+)/) || [])[1] || "") : "";
    const xhhLid = platform === "小黑盒" ? ((url.match(/bbs\/link\/([0-9a-f]+)/) || [])[1] || "") : "";
    const acts = [];
    acts.push(["🤖 问AI", () => askAiAboutCard(card)]);
    if (bvid) acts.push(["▶ 本地预览", () => openPlayer(bvid, cardTitle(card))]);
    if (tbTid) acts.push(["📖 本地阅读", () => openReader(tbTid, "")]);
    if (xhhLid) acts.push(["📖 本地预览", () => openXhhReader(xhhLid, "")]);
    acts.push(["🚀 跳转软件", () => openItem(card.dataset.key, jumpUrlFor(card))]);
    const descText = (card.querySelector(".desc") || {}).textContent || "";
    if (descText.trim()) acts.push(["📋 复制简介", async () => {
      if (window.AndroidBridge && window.AndroidBridge.copyClipboard) {
        window.AndroidBridge.copyClipboard(descText);
        toast("简介已复制");
      } else {
        try { await navigator.clipboard.writeText(descText); toast("简介已复制"); }
        catch (e) { toast("复制失败", { warn: true }); }
      }
    }]);
    acts.push(["📋 复制链接", async () => {
      if (window.AndroidBridge && window.AndroidBridge.copyClipboard) {
        window.AndroidBridge.copyClipboard(url);
        toast("链接已复制");
      } else {
        try { await navigator.clipboard.writeText(url); toast("链接已复制"); }
        catch (e) { toast("复制失败，请手动复制：" + url, { warn: true }); }
      }
    }]);
    const menu = document.createElement("div");
    menu.id = "cardMenu";
    menu.innerHTML = '<div class="cm-card">' +
      '<div class="cm-title">' + esc((card.querySelector("h3") || {}).textContent || "") + '</div>' +
      acts.map(([label, fn], i) => '<button data-i="' + i + '">' + label + '</button>').join("") +
      '<button class="cm-cancel">取消</button></div>';
    document.body.appendChild(menu);
    menu.addEventListener("click", (e2) => {
      const b = e2.target.closest("button");
      closeCardMenu();
      if (!b || b.classList.contains("cm-cancel")) return;
      const i = parseInt(b.dataset.i, 10);
      if (acts[i]) acts[i][1]();
    });
  }
  function isTbCard(platform) { return platform === "贴吧"; }
  function cardTitle(card) {
    const h = card.querySelector("h3");
    if (!h) return "";
    return [...h.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
  }
  function jumpUrlFor(card) {
    let jumpUrl = card.dataset.url;
    if (card.dataset.platform === "小黑盒" && card.dataset.xlid) {
      if (window.AndroidBridge) {
        const hexId = (card.dataset.url.match(/bbs\/link\/([0-9a-f]+)/) || [])[1] || "";
        return xhhJumpUrl(jumpUrl, card.dataset.xlid || hexId, card.dataset.ltag);
      }
      jumpUrl += "#xlid=" + card.dataset.xlid;
    }
    return jumpUrl;
  }
  $("#feedList").addEventListener("touchstart", (e) => {
    if (!mqMobile.matches) return;
    const card = e.target.closest(".card");
    if (!card || e.target.closest("button,input,textarea,a")) return;
    const t = e.touches[0];
    lpSx = t.clientX; lpSy = t.clientY;
    lpTimer = setTimeout(() => { lpTimer = null; window.__lpSuppress = true; stopPreview(); showCardMenu(card); }, 480);
  }, { passive: true });
  $("#feedList").addEventListener("touchmove", (e) => {
    if (!lpTimer) return;
    const t = e.touches[0];
    if (Math.abs(t.clientX - lpSx) > 12 || Math.abs(t.clientY - lpSy) > 12) {
      clearTimeout(lpTimer); lpTimer = null;
    }
  }, { passive: true });
  const lpCancel = () => { if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; } };
  $("#feedList").addEventListener("touchend", lpCancel, { passive: true });
  $("#feedList").addEventListener("touchcancel", lpCancel, { passive: true });
  $("#feedList").addEventListener("contextmenu", (e) => {
    if (mqMobile.matches && e.target.closest(".card")) e.preventDefault();   // 长按不弹系统复制菜单
  });
  document.addEventListener("click", (e) => {
    // 必须走 window.__lpSuppress: 这里以前写成裸标识符 lpSuppress, 而设值时写的是
    // window.__lpSuppress, 于是这一行每次都抛 ReferenceError —— 结果是"每次点击都报错",
    // 且长按出菜单后那次点击照样会穿透跳转。两处名字必须一致。
    if (window.__lpSuppress) { window.__lpSuppress = false; e.stopPropagation(); e.preventDefault(); return; }
    if (!e.target.closest("#cardMenu") && !e.target.closest(".card")) closeCardMenu();
  }, true);

  // 首次使用给一次手势提示
  if (mqMobile.matches && !localStorage.getItem("hint_swipe")) {
    setTimeout(() => toast("手势提示：从屏幕右缘向左滑打开 AI 聊天，聊天里左缘向右滑返回"), 1500);
    localStorage.setItem("hint_swipe", "1");
  }
  setView("feed");
})();

/* ---------- 设置导入导出(双端通用; 手机端经 AndroidBridge 直读剪贴板) ---------- */
const IO_CRED_KEYS = ["bili_cookie", "tb_cookie", "tb_user", "tb_avatar", "xhh_cookie",
  "xhh_heybox_id", "xhh_pkey", "xhh_user", "xhh_avatar",
  "acct_nickname", "acct_profile", "bili_follow_mids", "bili_follow_names"];
const IO_CATS = [
  ["AI 配置", ["ai_base", "ai_key", "ai_model", "ai_enabled", "ai_full_scan",
               "ai_interest_min", "ai_batch", "ai_cache_hours", "price_in", "price_out"]],
  ["筛选规则", ["m_min_views", "m_max_views", "m_max_duration", "m_min_duration",
                "m_max_age_months", "m_seen_hours", "m_repeat_limit",
                "m_repeat_cooldown_hours", "m_dedup", "m_blocklist_sync", "m_emotion",
                "m_news_fresh", "ser_max_age_months", "m_sources", "tb_allow_help",
                "tb_bars", "xhh_comm", "xhh_topic"]],
  ["平台权重", ["bili_weight", "tb_weight", "xhh_weight"]],
  ["B站关注强调", ["bili_follow_emph", "bili_follow_months"]],
  ["偏好备注", ["prefs_note", "dislike_note"]],
  ["基础", ["mode"]],
];
/* 界面偏好存于 localStorage, 随导出迁移到新设备 */
const IO_PREF_KEYS = ["theme", "accent", "custom_accents", "layout", "cover", "pv_zoom", "reason"];

let ioParsed = null;   // 智能解析结果缓存

async function ioCopy(text) {
  if (window.AndroidBridge && window.AndroidBridge.copyClipboard) {
    window.AndroidBridge.copyClipboard(text);
    return true;
  }
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    const ta = $("#ioExpText");
    ta.removeAttribute("readonly"); ta.select();
    const ok = document.execCommand("copy");
    ta.setAttribute("readonly", "");
    return ok;
  }
}
async function ioReadClip() {
  if (window.AndroidBridge && window.AndroidBridge.readClipboard) {
    try { return window.AndroidBridge.readClipboard() || ""; } catch (e) { /* fallthrough */ }
  }
  try { return (await navigator.clipboard.readText()) || ""; }
  catch (e) { return ""; }
}

$("#btnIoOpen").onclick = () => {
  $("#settingsIoModal").classList.remove("hidden");
  $("#ioExpHint").textContent = "";
  $("#ioImpHint").textContent = "";
  $("#ioImpPreview").classList.add("hidden");
  $("#ioImpPreview").innerHTML = "";
  $("#ioImpConfirm").disabled = true;
  ioParsed = null;
};
$("#ioClose").onclick = () => $("#settingsIoModal").classList.add("hidden");

/* 导出 */
$("#ioExportGen").onclick = async () => {
  const r = await api("/api/settings/export", { method: "POST", body: { credentials: $("#ioExpCred").checked } });
  if (!r.ok) { $("#ioExpHint").textContent = "导出失败：" + (r.error || ""); return; }
  const p = r.payload;
  p.io_prefs = {};
  IO_PREF_KEYS.forEach((k) => { const v = localStorage.getItem(k); if (v !== null) p.io_prefs[k] = v; });
  $("#ioExpText").value = JSON.stringify(p);
  const n = Object.keys(p.settings).length;
  $("#ioExpHint").textContent = "已生成：" + n + " 项设置 · 黑名单 " +
    (p.blacklist.authors.length + p.blacklist.keywords.length) + " 条";
};
$("#ioExpCopy").onclick = async () => {
  if (!$("#ioExpText").value) return toast("请先生成导出内容", { warn: true });
  $("#ioExpHint").textContent = (await ioCopy($("#ioExpText").value)) ? "已复制到剪贴板" : "复制失败，请手动全选复制";
};
$("#ioExpDownload").onclick = () => {
  if (!$("#ioExpText").value) return toast("请先生成导出内容", { warn: true });
  const blob = new Blob([$("#ioExpText").value], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "洞鉴虚明-设置-" + new Date().toISOString().slice(0, 10) + ".json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
};
if (isTouchDevice()) $("#ioExpDownload").classList.add("hidden");   // WebView 无下载监听, 隐藏
if (window.AndroidBridge && window.AndroidBridge.shareText) {
  $("#ioExpShare").classList.remove("hidden");   // 安卓: 调系统分享面板存文件/发给自己
  $("#ioExpShare").onclick = () => {
    if (!$("#ioExpText").value) return toast("请先生成导出内容", { warn: true });
    window.AndroidBridge.shareText($("#ioExpText").value);
  };
}

/* 从文件导入: 电脑端文件选择器/拖拽, 安卓端经原生文件选择器(onShowFileChooser) */
function ioReadFile(file) {
  if (!file) return;
  const fr = new FileReader();
  fr.onload = () => {
    $("#ioImpText").value = String(fr.result || "");
    ioRunParse($("#ioImpText").value);
    $("#ioImpHint").textContent = "已读取文件 " + file.name;
  };
  fr.onerror = () => toast("文件读取失败", { warn: true });
  fr.readAsText(file, "utf-8");
}
$("#ioImpFile").onclick = () => $("#ioImpFileInput").click();
$("#ioImpFileInput").addEventListener("change", () => {
  ioReadFile($("#ioImpFileInput").files && $("#ioImpFileInput").files[0]);
  $("#ioImpFileInput").value = "";   // 允许重复选择同一文件
});
/* 电脑端: 拖 .json 文件进弹窗窗口 */
const ioModal = $("#settingsIoModal");
ioModal.addEventListener("dragover", (e) => { e.preventDefault(); });
ioModal.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) ioReadFile(f);
});

/* 智能解析: 从任意夹杂文本里抠出 JSON, 兼容本工具导出格式 */
function ioExtractJson(text) {
  const s = (text || "").indexOf("{"), e = (text || "").lastIndexOf("}");
  if (s < 0 || e <= s) throw new Error("文本里没找到 JSON 内容");
  const obj = JSON.parse(text.slice(s, e + 1));
  if (obj && obj.kind === "mirror-sorter-settings" && obj.settings) return obj;
  if (obj && obj.settings) return obj;   // 兼容裸 {"settings":{...}}
  throw new Error("不是本工具导出的设置内容");
}
function ioCatOf(key) {
  for (const [name, keys] of IO_CATS) if (keys.includes(key)) return name;
  return IO_CRED_KEYS.includes(key) ? "登录凭证" : null;
}
function ioRunParse(text) {
  let p;
  try { p = ioExtractJson(text); } catch (e) {
    $("#ioImpHint").textContent = "解析失败：" + e.message;
    $("#ioImpPreview").classList.add("hidden");
    $("#ioImpConfirm").disabled = true;
    return;
  }
  ioParsed = p;
  const keys = Object.keys(p.settings || {});
  const cats = [];
  IO_CATS.forEach(([name]) => {
    const n = keys.filter(ioCatOf).filter((k) => ioCatOf(k) === name).length;
    if (n) cats.push([name, n]);
  });
  const credN = keys.filter((k) => IO_CRED_KEYS.includes(k)).length;
  if (credN) cats.push(["登录凭证", credN]);
  const prefsN = Object.keys(p.io_prefs || {}).length;
  if (prefsN) cats.push(["界面偏好", prefsN]);
  const bl = p.blacklist || {};
  const blN = (bl.authors || []).length + (bl.keywords || []).length;
  let html = '<label><b>解析成功，勾选要导入的部分：</b></label>';
  cats.forEach(([name, n]) => {
    const warn = name === "登录凭证" ? ' <span class="tip-inline">⚠ 含登录Cookie，导入后免扫码直接登录</span>' : "";
    html += '<label class="m-row"><input type="checkbox" data-cat="' + esc(name) + '" checked>' +
            '<span>' + esc(name) + '（' + n + ' 项）</span></label>' + warn;
  });
  if (blN) html += '<label class="m-row"><input type="checkbox" data-cat="黑白名单" checked><span>黑白名单（' + blN + ' 条）</span></label>';
  $("#ioImpPreview").innerHTML = html;
  $("#ioImpPreview").classList.remove("hidden");
  $("#ioImpConfirm").disabled = false;
  $("#ioImpHint").textContent = "导出时间 " + (p.ts ? new Date(p.ts * 1000).toLocaleString() : "未知");
}
$("#ioImpParse").onclick = () => ioRunParse($("#ioImpText").value);
$("#ioImpClip").onclick = async () => {
  const text = await ioReadClip();
  if (!text.trim()) {
    $("#ioImpHint").textContent = window.AndroidBridge ? "剪贴板是空的" : "无法读取剪贴板，请手动粘贴到下方输入框";
    return;
  }
  $("#ioImpText").value = text;
  ioRunParse(text);
};
$("#ioImpConfirm").onclick = async () => {
  if (!ioParsed) return;
  const checked = new Set([...$("#ioImpPreview").querySelectorAll("input:checked")].map((c) => c.dataset.cat));
  const settings = {};
  Object.entries(ioParsed.settings || {}).forEach(([k, v]) => {
    const cat = ioCatOf(k);
    if (cat && checked.has(cat)) settings[k] = v;
  });
  const body = { settings };
  if (checked.has("黑白名单")) body.blacklist = ioParsed.blacklist || {};
  const r = await api("/api/settings/import", { method: "POST", body });
  if (!r.ok) { $("#ioImpHint").textContent = "导入失败：" + (r.error || ""); return; }
  const parts = ["设置 " + r.imported + " 项"];
  if (r.bl_authors || r.bl_keywords) parts.push("黑名单 +" + (r.bl_authors + r.bl_keywords));
  if (r.skipped && r.skipped.length) parts.push("跳过 " + r.skipped.length + " 个未知项");
  toast("导入完成：" + parts.join(" · "));
  // 界面偏好直接落在 localStorage 并立即生效
  Object.entries(ioParsed.io_prefs || {}).forEach(([k, v]) => {
    if (IO_PREF_KEYS.includes(k)) localStorage.setItem(k, v);
  });
  applyTheme(); applyView(); applyPvZoom();
  $("#settingsIoModal").classList.add("hidden");
  loadSettings(); loadFeed(); loadStatus(); loadPanel(); loadBlacklist();
};
