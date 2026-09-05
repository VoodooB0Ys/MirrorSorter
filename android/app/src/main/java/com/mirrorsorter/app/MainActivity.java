package com.mirrorsorter.app;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {

    // app.py 的 PORTS=(8000, 8010, 8020), 启动后实际端口需轮询探测
    private static final int[] PORTS = {8000, 8010, 8020};
    private static final int FILE_CHOOSER_REQ = 1001;
    private static final int TIEBA_LOGIN_REQ = 2002;

    private WebView web;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private ValueCallback<Uri[]> fileCallback;   // 设置导入的文件选择回调
    private boolean historyCleared = false;      // 主页面首次加载后清掉启动页历史(防返回键白屏)
    private volatile int activePort = 8000;      // 探测成功的本地服务端口(登录回传直连用)

    // 安卓返回键的智能返回: 关浮层/弹窗 → 切回首页视图, 都没有才走网页回退/退出
    private static final String BACK_JS =
        "(function(){try{" +
        "var pb=document.getElementById('playerBox');if(pb){pb.remove();return 'handled';}" +
        "var rb=document.getElementById('readerBox');if(rb){rb.remove();return 'handled';}" +
        "var m=document.querySelector('.modal:not(.hidden)');" +
        "if(m){var x=m.querySelector('.modal-card header .x');if(x){x.click();return 'handled';}}" +
        "if(window.__setMobileView&&document.body.dataset.view&&document.body.dataset.view!=='feed'){" +
        "window.__setMobileView('feed');return 'handled';}" +
        "return 'unhandled';}catch(e){return 'unhandled';}})()";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        File base = getFilesDir();
        File appDir = new File(base, "app");
        File dataDir = new File(base, "data");
        try {
            copyAssetDir("python_src", appDir);
        } catch (IOException e) {
            Toast.makeText(this, "内置资源复制失败: " + e, Toast.LENGTH_LONG).show();
        }

        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
        Python.getInstance().getModule("launcher")
              .callAttr("start", appDir.getAbsolutePath(), dataDir.getAbsolutePath());

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // 前端 localStorage 存 UI 状态
        s.setSupportMultipleWindows(true);     // window.open(_blank) → 系统浏览器
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        web.setBackgroundColor(Color.rgb(245, 245, 247));
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                String host = u.getHost();
                if (host == null || "127.0.0.1".equals(host) || "localhost".equals(host)) {
                    return false;
                }
                openExternal(u);
                return true;
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                // 主页面就绪后清掉启动页(data URL)历史: 返回键回退到它会导致白屏
                if (!historyCleared && url != null && url.startsWith("http://127.0.0.1")) {
                    historyCleared = true;
                    v.clearHistory();
                }
                super.onPageFinished(v, url);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView v, boolean dialog, boolean userGesture, Message resultMsg) {
                // window.open 新窗口: 用临时 WebView 接住首次跳转, 再转交系统浏览器
                WebView tmp = new WebView(MainActivity.this);
                tmp.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView w, WebResourceRequest r) {
                        openExternal(r.getUrl());
                        return true;
                    }
                });
                ((WebView.WebViewTransport) resultMsg.obj).setWebView(tmp);
                resultMsg.sendToTarget();
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                // 设置导入: 让 <input type=file> 弹出系统文件选择器(默认 WebView 不支持)
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    Intent intent = params.createIntent();
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(intent, FILE_CHOOSER_REQ);
                } catch (Exception e) {
                    fileCallback = null;
                    Toast.makeText(MainActivity.this, "无法打开文件选择器: " + e, Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }
        });
        web.addJavascriptInterface(new JsBridge(), "AndroidBridge");
        setContentView(web);

        web.loadDataWithBaseURL("http://127.0.0.1:8000/", SPLASH_HTML, "text/html", "utf-8", null);
        pollPort(0);
    }

    private void pollPort(final int idx) {
        if (idx >= PORTS.length) {
            ui.postDelayed(() -> pollPort(0), 800);
            return;
        }
        final int port = PORTS[idx];
        new Thread(() -> {
            boolean ok = false;
            try {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                        new java.net.URL("http://127.0.0.1:" + port + "/api/ui_version").openConnection();
                c.setConnectTimeout(700);
                c.setReadTimeout(700);
                ok = c.getResponseCode() == 200;
                c.disconnect();
            } catch (Exception ignored) {}
            if (ok) {
                activePort = port;
                ui.post(() -> web.loadUrl("http://127.0.0.1:" + port + "/"));
            } else {
                ui.post(() -> pollPort(idx + 1));
            }
        }).start();
    }

    private void openExternal(Uri u) {
        // 小黑盒官方唤起协议(前端构造 heybox:// + JSON); App未装时回落 fragment 里的网页地址
        if ("heybox".equals(u.getScheme())) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                return;
            } catch (Exception ignored) {
            }
            String frag = u.getFragment() == null ? "" : u.getFragment();
            if (frag.startsWith("web=")) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW,
                            Uri.parse(Uri.decode(frag.substring(4)))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    return;
                } catch (Exception ignored) {}
            }
            return;
        }
        // 域名/路径 → 候选Intent列表: 依次尝试, 全失败才落回系统浏览器
        for (Intent cand : deepLinkCandidates(u)) {
            try {
                startActivity(cand);
                return;
            } catch (Exception ignored) {
            }
        }
        try {
            // 系统自行解析: 目标App注册了对应URL时可能弹出选择或直接进App
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (Exception ignored) {}
    }

    private Intent pkgIntent(Uri u, String pkg) {
        Intent i = new Intent(Intent.ACTION_VIEW, u);
        i.setPackage(pkg);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return i;
    }

    private java.util.List<Intent> deepLinkCandidates(Uri u) {
        java.util.List<Intent> out = new java.util.ArrayList<>();
        String host = u.getHost() == null ? "" : u.getHost();
        String path = u.getPath() == null ? "" : u.getPath();
        if (host.equals("b23.tv") || host.equals("bilibili.com") || host.endsWith(".bilibili.com")) {
            out.add(pkgIntent(u, "tv.danmaku.bili"));
        } else if (host.equals("tieba.baidu.com") || host.endsWith(".tieba.baidu.com")) {
            // 帖子页: 先试官方客户端的 unidispatch 分发scheme, 再试 https AppLinks, 最后系统解析
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("^/p/(\\d+)").matcher(path);
            if (m.find()) {
                out.add(new Intent(Intent.ACTION_VIEW,
                        Uri.parse("com.baidu.tieba://unidispatch/pb?tid=" + m.group(1)))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            }
            out.add(pkgIntent(u, "com.baidu.tieba"));
        } else if (host.equals("xiaoheihe.cn") || host.endsWith(".xiaoheihe.cn")) {
            // 实测: 小黑盒App对登录确认页(/account/qr_login/)只会闪退, 让浏览器处理确认流程
            if (host.equals("api.xiaoheihe.cn") && path.startsWith("/account/qr_login/")) {
                return out;   // 空候选 → 落回浏览器打开确认页
            }
            // 帖子跳转(反编译官方APK的RouterActivity实测): 对 heybox://<host> 的URI按host分发,
            // host=link → 读 link_id/link_tag 查询参数构造BBSLinkObj → 直接进帖子详情页。
            // www/api 域名的https链接未注册App Link, 直接开App只会落主页。
            // 帖子链接格式 = https://www.xiaoheihe.cn/app/bbs/link/<16进制link_id>
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("^/app/bbs/link/([0-9a-fA-F]+)").matcher(path);
            if (m.find()) {
                String hex = m.group(1);
                // ① 官方唤起协议: heybox://link?link_id=<hex>(App自身分享链接同款, host=link)
                out.add(pkgIntent(Uri.parse("heybox://link?link_id=" + hex), "com.max.xiaoheihe"));
                // ② App未装的兜底: 浏览器打开官方分享H5(能看正文, 并引导装App)
                out.add(new Intent(Intent.ACTION_VIEW,
                        Uri.parse("https://www.xiaoheihe.cn/bbs/post_share?link_id=" + hex))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                return out;
            }
            out.add(pkgIntent(u, "com.max.xiaoheihe"));   // 注意: 小黑盒真实包名, 非网页域名
        } else if (host.equals("zhihu.com") || host.endsWith(".zhihu.com")) {
            out.add(pkgIntent(u, "com.zhihu.android"));
        }
        return out;
    }

    @Override
    public void onBackPressed() {
        if (web == null) {
            super.onBackPressed();
            return;
        }
        // 智能返回: 前端先处理(关浮层/弹窗/回首页), 没有可关的才回退网页或退出。
        // evaluateJavascript 异步回调, 不能在 onBackPressed 里同步等(会死锁UI线程)。
        web.evaluateJavascript(BACK_JS, value -> {
            if (value != null && value.contains("handled")) return;
            if (web.canGoBack()) {
                web.goBack();
            } else {
                finish();
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == TIEBA_LOGIN_REQ) {
            String cookie = data == null ? null : data.getStringExtra("cookie");
            if (cookie != null && !cookie.isEmpty()) {
                saveTiebaCookie(cookie);
            }
            return;
        }
        if (requestCode == FILE_CHOOSER_REQ && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    /* 登录Cookie保存: Java 直连本地后端 /api/tieba/login/manual。
       不再经页面 JS 回传 —— 旧路径要求主页已加载完(定义了 __nativeTiebaCookie),
       页面没就绪时调用是空操作, 表现为"点了保存却没登录"。后端保存成功与否
       都会 Toast 反馈, 并通知前端刷新(页面未就绪时由启动流程自然读到新状态)。 */
    private void saveTiebaCookie(String cookie) {
        final String body = "{\"cookie\":" + org.json.JSONObject.quote(cookie) + "}";
        new Thread(() -> {
            boolean ok = false;
            String detail = "";
            try {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                        new java.net.URL("http://127.0.0.1:" + activePort + "/api/tieba/login/manual")
                                .openConnection();
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setConnectTimeout(5000);
                c.setReadTimeout(30000);   // 后端要向百度验证Cookie并抓头像, 可能较慢
                c.setRequestProperty("Content-Type", "application/json");
                try (java.io.OutputStream os = c.getOutputStream()) {
                    os.write(body.getBytes("UTF-8"));
                }
                java.io.InputStream in = c.getResponseCode() < 400 ? c.getInputStream() : c.getErrorStream();
                java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                int n;
                while (in != null && (n = in.read(buf)) > 0) bos.write(buf, 0, n);
                String resp = bos.toString("UTF-8");
                ok = resp.contains("\"ok\":true") || resp.contains("\"ok\": true");
                if (!ok) detail = resp;
            } catch (Exception e) {
                detail = String.valueOf(e);
            }
            final boolean fok = ok;
            final String fdetail = detail;
            ui.post(() -> {
                if (fok) {
                    Toast.makeText(MainActivity.this, "贴吧登录成功，已保存", Toast.LENGTH_SHORT).show();
                } else {
                    Toast.makeText(MainActivity.this, "贴吧登录保存失败：" +
                            (fdetail.isEmpty() ? "本地服务未响应" : fdetail.substring(0, Math.min(120, fdetail.length()))),
                            Toast.LENGTH_LONG).show();
                }
                String js = "window.__nativeTiebaSaved && window.__nativeTiebaSaved(" + fok + ")";
                web.evaluateJavascript(js, null);
            });
        }).start();
    }

    private void copyAssetDir(String assetDir, File target) throws IOException {
        String[] list = getAssets().list(assetDir);
        if (list == null || list.length == 0) return;
        if (!target.exists()) target.mkdirs();
        for (String name : list) {
            String child = assetDir + "/" + name;
            File dst = new File(target, name);
            if (getAssets().list(child).length > 0) {
                copyAssetDir(child, dst);
            } else {
                try (InputStream in = getAssets().open(child);
                     OutputStream out = new FileOutputStream(dst)) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                }
            }
        }
    }

    private static final String SPLASH_HTML =
        "<html><body style='background:#f5f5f7;color:#666;font-family:sans-serif;"
        + "display:flex;align-items:center;justify-content:center;height:96%'>"
        + "<h3>正在启动本地服务…</h3></body></html>";

    /* 剪贴板桥: 设置导入导出的"一键读/写剪贴板"用。
       ClipboardManager 必须在主线程访问, JS桥线程调用时用 Handler + 闩锁同步等待。 */
    private class JsBridge {
        @JavascriptInterface
        public String readClipboard() {
            final String[] out = {""};
            final CountDownLatch latch = new CountDownLatch(1);
            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                    ClipData cd = cm == null ? null : cm.getPrimaryClip();
                    if (cd != null && cd.getItemCount() > 0) {
                        CharSequence s = cd.getItemAt(0).coerceToText(MainActivity.this);
                        out[0] = s == null ? "" : s.toString();
                    }
                } catch (Exception ignored) {
                } finally {
                    latch.countDown();
                }
            });
            try {
                latch.await(2, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {}
            return out[0];
        }

        @JavascriptInterface
        public void copyClipboard(final String text) {
            new Handler(Looper.getMainLooper()).post(() -> {
                try {
                    ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                    if (cm != null) {
                        cm.setPrimaryClip(ClipData.newPlainText("mirror-sorter", text == null ? "" : text));
                    }
                } catch (Exception ignored) {}
            });
        }

        /* 手机贴吧登录: mode=sms → 手机版wappass页(短信验证码为主);
           mode=web → 电脑版passport页(密码/扫码)。两入口按钮分开(用户要求)。
           抓到BDUSS后由 onActivityResult 直连后端保存。 */
        @JavascriptInterface
        public void openTiebaLogin(final String mode) {
            runOnUiThread(() -> {
                Intent i = new Intent(MainActivity.this, LoginActivity.class);
                if ("sms".equals(mode)) {
                    i.putExtra("url", "https://wappass.baidu.com/passport/?login&u=https%3A%2F%2Ftieba.baidu.com%2F");
                    i.putExtra("title", "短信验证码登录：输手机号 → 收验证码，成功后自动保存");
                } else {
                    i.putExtra("url", "https://passport.baidu.com/v2/?login&tpl=tb&u=https%3A%2F%2Ftieba.baidu.com%2F");
                    i.putExtra("title", "电脑版页面登录（密码/扫码），成功后自动保存");
                }
                i.putExtra("cookieProbeUrl", "https://tieba.baidu.com/");
                i.putExtra("cookieName", "BDUSS");
                startActivityForResult(i, TIEBA_LOGIN_REQ);
            });
        }

        /* 导出分享: 调系统分享面板, 可存到微信/网盘/本地备忘等, 规避 WebView 不支持下载 */
        @JavascriptInterface
        public void shareText(final String text) {
            runOnUiThread(() -> {
                try {
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType("text/plain");
                    send.putExtra(Intent.EXTRA_TEXT, text == null ? "" : text);
                    startActivity(Intent.createChooser(send, "导出设置"));
                } catch (Exception ignored) {}
            });
        }
    }
}
