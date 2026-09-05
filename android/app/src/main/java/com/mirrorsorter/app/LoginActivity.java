package com.mirrorsorter.app;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** 应用内登录WebView: 打开平台登录页(短信/密码), 轮询捕获目标Cookie后回传主界面。
 *  贴吧登录在手机上的唯一可行方案(桌面版依赖CDP本机浏览器, 安卓不存在)。
 *  每次打开都清空WebView Cookie: 否则上次登录的BDUSS残留会让轮询立即命中旧凭证,
 *  直接"登录成功", 用户无法更换账号。 */
public class LoginActivity extends Activity {

    private WebView wv;
    private String cookieProbeUrl;   // 从该URL读Cookie(目标登录态域名)
    private String cookieName;       // 目标Cookie名(出现即视为登录成功, 如 BDUSS)
    private boolean reported = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private int pollsLeft = 120;     // 最多轮询 ~3 分钟

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String url = getIntent().getStringExtra("url");
        cookieProbeUrl = getIntent().getStringExtra("cookieProbeUrl");
        cookieName = getIntent().getStringExtra("cookieName");
        String title = getIntent().getStringExtra("title");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF161920);

        TextView head = new TextView(this);
        head.setText(title == null ? "登录" : title);
        head.setTextColor(0xFFE8EAF0);
        head.setTextSize(15);
        head.setPadding(30, 34, 30, 26);
        root.addView(head);

        Button done = new Button(this);
        done.setText("登录完成后点此保存");
        done.setOnClickListener(v -> saveAndClose());
        root.addView(done);

        wv = new WebView(this);
        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        wv.setBackgroundColor(0xFFFFFFFF);
        wv.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView v, String u) {
                checkCookie();
                super.onPageFinished(v, u);
            }
        });
        root.addView(wv, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);

        // 每次打开登录页先清掉WebView里的旧Cookie(含上次登录的BDUSS),
        // 清完再加载登录页并启动轮询 —— 否则轮询立即命中上次凭证, 无法换号。
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.removeAllCookies(v -> startLoading());
        // 保险: 个别机型回调可能不触发, 800ms后兜底启动(started标志防双启)
        new Handler(Looper.getMainLooper()).postDelayed(this::startLoading, 800);
    }

    private boolean started = false;

    private void startLoading() {
        if (started || isFinishing()) return;
        started = true;
        CookieManager cm = CookieManager.getInstance();
        cm.flush();
        cm.setAcceptThirdPartyCookies(wv, true);
        wv.loadUrl(getIntent().getStringExtra("url"));
        pollCookie();
    }

    private void pollCookie() {
        if (reported || isFinishing()) return;
        checkCookie();
        if (pollsLeft-- > 0) {
            handler.postDelayed(this::pollCookie, 1500);
        }
    }

    private void checkCookie() {
        if (reported) return;
        try {
            CookieManager cm = CookieManager.getInstance();
            // 多域探针: BDUSS 通常挂在 .baidu.com, 任一命中等价; 兜底防个别机型域匹配差异
            String[] probes = {cookieProbeUrl, "https://wappass.baidu.com/", "https://www.baidu.com/"};
            for (String u : probes) {
                if (u == null || u.isEmpty()) continue;
                String ck = cm.getCookie(u);
                if (ck != null && ck.contains(cookieName + "=")) {
                    reported = true;
                    getIntent().putExtra("cookie", ck);
                    setResult(RESULT_OK, getIntent());
                    finish();
                    return;
                }
            }
        } catch (Exception ignored) {}
    }

    private void saveAndClose() {
        if (!reported) checkCookie();
        if (!reported) {
            // 点保存时还没抓到Cookie: 给出反馈并留在登录页(可继续等自动保存或重试)
            Toast.makeText(this, "未检测到登录Cookie：请确认页面已显示登录成功，稍候再点一次；返回键可取消", Toast.LENGTH_LONG).show();
            return;
        }
        finish();
    }

    @Override
    public void onBackPressed() {
        if (wv != null && wv.canGoBack()) wv.goBack();
        else finish();   // 取消登录: 直接退出, 不弹提示
    }
}
