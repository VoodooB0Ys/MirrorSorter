/* 行为回归: 弹幕侧「快速文本分析」与「弹幕屏蔽总开关」解耦(v0.3.1.46dsk)
 *
 * 为什么必须做行为级测试: test_shell.py 只能断言源码里"字符串写对了", 但这次的
 * bug 恰恰是**接线**错了 —— `NEED_TEXT = DM_ON && !!CF.ft_dm` 这行字符串本身
 * 完全合法, 静态检查一路绿灯, 实际却让弹幕侧一个文本请求都不发。只有真的把
 * 钩子跑起来、看它到底发没发请求, 才能守住这条。
 *
 * 做法: 造一个最小沙盒(假 window/document/fetch/XMLHttpRequest), 加载真实
 * userscript, 构造真实 wire-format 的 seg.so 字节流喂给 XHR 钩子, 然后看它
 * 往 127.0.0.1 发了哪些请求。
 *
 * 运行: node _test_dm_ft_gate.js
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC_PATH = process.env.MSX_DM_SRC
  || path.join(__dirname, "extensions", "bilibili-dmfilter.user.js");
const SRC = fs.readFileSync(SRC_PATH, "utf8");
const PORT = "8000";

let PASS = 0, FAIL = 0;
function check(name, cond, extra) {
  if (cond) { PASS++; console.log("PASS  " + name); }
  else { FAIL++; console.log("FAIL  " + name + (extra ? "  <<< " + extra : "")); }
}

/* ---------- protobuf 构造: DmSegMobileReply{ repeated elems=1 } ---------- */
function varint(n) {
  const out = [];
  n = Math.floor(n);
  do { let b = n & 0x7f; n = Math.floor(n / 128); if (n) b |= 0x80; out.push(b); } while (n);
  return out;
}
function tag(fno, wt) { return varint((fno << 3) | wt); }
function str(fno, s) {
  const b = Buffer.from(s, "utf8");
  return tag(fno, 2).concat(varint(b.length), Array.from(b));
}
function num(fno, v) { return tag(fno, 0).concat(varint(v)); }

function buildSeg(elems) {
  let out = [];
  for (const e of elems) {
    let body = [];
    body = body.concat(num(1, e.id));
    body = body.concat(num(2, e.t));
    body = body.concat(str(6, e.h));
    body = body.concat(str(7, e.text));
    body = body.concat(num(8, e.ct));
    out = out.concat(tag(1, 2), varint(body.length), body);
  }
  const u8 = new Uint8Array(out);
  // 转成真正的 ArrayBuffer(跨 realm 也能过 instanceof)
  return u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength);
}

const SEG = buildSeg([
  { id: 1, t: 1000, h: "aabbccdd", text: "赢麻了，又是魂游", ct: 1700000000 },
  { id: 2, t: 2000, h: "11223344", text: "前排打卡", ct: 1700000001 },
  { id: 3, t: 3000, h: "deadbeef", text: "典中典，急了急了", ct: 1700000002 },
]);
const SEG_URL = "https://api.bilibili.com/x/v2/dm/wbi/web/seg.so?type=1&oid=12345&segment_index=1";

/* ---------- 沙盒 ---------- */
function run(cfg) {
  const calls = [];          // 记录所有出站请求
  const sandbox = {};
  const win = {};

  function FakeXHR() { this._resp = null; }
  FakeXHR.prototype.open = function (m, u) { this._url = u; };
  Object.defineProperty(FakeXHR.prototype, "response", {
    configurable: true,
    get: function () { return this._resp; },
  });

  const doc = {
    getElementById: () => null,
    createElement: () => ({ setAttribute() {}, style: {}, appendChild() {} }),
    body: { appendChild() {} },
    documentElement: { appendChild() {} },
    querySelector: () => null,
  };

  win.__MSX_CF__ = cfg;
  win.__MSX_PORT__ = PORT;
  win.__INITIAL_STATE__ = { videoData: { duration: 1200, pages: [] } };
  win.fetch = function (url, init) {
    calls.push({ url: String(url), method: (init && init.method) || "GET", body: init && init.body });
    // seg.so 自取 → 永远挂起(本测试只关心"发没发请求")
    if (String(url).indexOf("seg.so") >= 0) return new Promise(() => {});
    if (String(url).indexOf("/api/textfilter") >= 0) {
      const texts = JSON.parse(init.body).texts;
      return Promise.resolve({
        json: () => Promise.resolve({
          ok: true, on: true,
          why: texts.map((t) => (t.indexOf("赢麻了") >= 0 ? "阴阳怪气" : "")),
        }),
      });
    }
    return Promise.resolve({ json: () => Promise.resolve({ ok: true, cookie: true, flags: {} }) });
  };

  Object.assign(sandbox, {
    window: win, document: doc, location: { pathname: "/video/BV1xx411c7mD", search: "" },
    XMLHttpRequest: FakeXHR, fetch: win.fetch,
    setTimeout: () => 0, clearTimeout: () => {}, Promise, Object, Array, JSON,
    TextDecoder, Uint8Array, ArrayBuffer, Response, Math, String, Number, Boolean, Error,
    console: { warn() {}, log() {} }, RegExp, isFinite, Date,
  });
  sandbox.globalThis = sandbox;

  const ctx = vm.createContext(sandbox);
  ctx.window = win;
  ctx.self = win;
  vm.runInContext(SRC, ctx, { filename: "bilibili-dmfilter.user.js" });

  // 触发 XHR 钩子: 播放器就是这么拿响应体的
  const x = new sandbox.XMLHttpRequest();
  x.open("GET", SEG_URL);
  x._resp = SEG;
  const got = x.response;            // ← 这里跑 process() + askFlags/askText

  // 让 microtask 队列排空
  return new Promise((res) => {
    setTimeout_(() => res({ calls, got }), 0);
  });
}
const setTimeout_ = (fn) => require("timers").setTimeout(fn, 0);

/* ---------- 用例 ---------- */
(async function () {
  console.log("== 弹幕侧 fastText 门控行为回归 (v0.3.1.46dsk) ==\n");

  // T1: 用户的实际配置 —— 总开关关、fastText 开。文本请求**必须**发出去。
  {
    const { calls } = await run({ dm_on: false, ft_dm: true, ft_cf: true, blk_on: true, dmhash: [] });
    const txt = calls.filter((c) => c.url.indexOf("/api/textfilter") >= 0);
    const flags = calls.filter((c) => c.url.indexOf("/api/bili/dmflags") >= 0);
    check("T1 总开关关 + fastText 开 → 弹幕侧照发文本请求(本次修复的核心)", txt.length === 1,
          "实际 textfilter 请求数=" + txt.length + " 全部出站=" + JSON.stringify(calls.map((c) => c.url)));
    if (txt.length === 1) {
      const body = JSON.parse(txt[0].body);
      check("T1b 请求体 kind=dm 且带上本段文本", body.kind === "dm" && body.texts.length === 3,
            JSON.stringify(body));
    }
    check("T1c 总开关关 → 仍不查发送者画像(零 dmflags 请求)", flags.length === 0,
          "dmflags 请求数=" + flags.length);
  }

  // T2: fastText 的弹幕通道关掉 → 一个文本请求都不许发(省流量、零打扰)
  {
    const { calls } = await run({ dm_on: true, ft_dm: false, ft_cf: true, blk_on: true, dmhash: [] });
    const txt = calls.filter((c) => c.url.indexOf("/api/textfilter") >= 0);
    check("T2 总开关开但 fastText 弹幕通道关 → 零文本请求", txt.length === 0,
          "textfilter 请求数=" + txt.length);
  }

  // T3: 总开关开 + 抽奖规则开 → 该查画像的照查(解耦不能把身份规则一起放开/掐死)
  {
    const { calls } = await run({ dm_on: true, ft_dm: false, lottery: true, blk_on: true, dmhash: [] });
    const flags = calls.filter((c) => c.url.indexOf("/api/bili/dmflags") >= 0);
    check("T3 总开关开 + 抽奖规则开 → 正常发画像请求(身份规则没被误伤)", flags.length === 1,
          "dmflags 请求数=" + flags.length);
  }

  // T4: 总开关关 + 抽奖规则开 → 身份规则必须仍然被掐死(不能因为解耦就放开)
  {
    const { calls } = await run({ dm_on: false, ft_dm: false, lottery: true, blk_on: true, dmhash: [] });
    const flags = calls.filter((c) => c.url.indexOf("/api/bili/dmflags") >= 0);
    check("T4 总开关关 → 身份规则(抽奖)依旧不发请求", flags.length === 0,
          "dmflags 请求数=" + flags.length);
  }

  // T5: 黑名单哈希表 —— 总开关关时不该建表(壳也不注入), 命不中
  {
    const { calls } = await run({ dm_on: false, ft_dm: true, blk_on: true, dmhash: ["aabbccdd"] });
    const txt = calls.filter((c) => c.url.indexOf("/api/textfilter") >= 0);
    check("T5 总开关关 + 黑名单表注入 → 黑名单不生效, 但文本通道照常", txt.length === 1,
          "textfilter=" + txt.length);
  }

  console.log("\nRESULT: " + (FAIL ? "FAIL(" + FAIL + ")" : "ALL_PASS (" + PASS + " checks)"));
  process.exit(FAIL ? 1 : 0);
})();
