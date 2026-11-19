package com.comfyphone.t2i;

import android.annotation.SuppressLint;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.text.TextUtils;
import android.view.View;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

import android.net.http.SslError;

/**
 * 安卓壳：打开电脑上已经跑着的出图页面。
 * 樱花隧道的证书是 frpc 自动生成的，只对用户填写的那台服务器放行。
 */
public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "comfyphone";
    private static final String KEY_URL = "server_url";
    private static final String KEY_TRUST = "trusted_host";

    private SharedPreferences prefs;
    private WebView webView;
    private ProgressBar progress;
    private String serverUrl;
    private SslErrorHandler pendingSsl;
    private boolean handlingSsl;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverUrl = prefs.getString(KEY_URL, getString(R.string.default_server_url));
        webView = findViewById(R.id.webview);
        progress = findViewById(R.id.progress);

        TextView refresh = findViewById(R.id.refresh);
        TextView server = findViewById(R.id.server);
        refresh.setOnClickListener(v -> webView.loadUrl(serverUrl));
        server.setOnClickListener(v -> showServerDialog());

        setupWebView();
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(serverUrl);
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(settings, false);
        }
        webView.setBackgroundColor(Color.parseColor("#0F172A"));
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                handlingSsl = false;
                progress.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (!request.isForMainFrame() || handlingSsl) {
                    return;
                }
                CharSequence description = error.getDescription();
                showOffline(explain(description == null ? "" : description.toString()));
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                String errorHost = hostOf(error.getUrl());
                String want = hostOf(serverUrl);
                if (want.isEmpty() || !want.equalsIgnoreCase(errorHost)) {
                    handler.cancel();
                    return;
                }
                handlingSsl = true;
                String trusted = prefs.getString(KEY_TRUST, "");
                if (want.equalsIgnoreCase(trusted)) {
                    handler.proceed();
                    return;
                }
                askToTrust(want, handler);
            }
        });
        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
                progress.setProgress(newProgress);
            }
        });
    }

    private void askToTrust(String host, SslErrorHandler handler) {
        if (pendingSsl != null) {
            pendingSsl.cancel();
        }
        pendingSsl = handler;
        new AlertDialog.Builder(this)
                .setTitle("证书不受信任")
                .setMessage("这条隧道的证书是自动生成的，系统不会信任它。只继续连接 " + host + " ？")
                .setPositiveButton("继续", (dialog, which) -> {
                    prefs.edit().putString(KEY_TRUST, host).apply();
                    if (pendingSsl != null) {
                        pendingSsl.proceed();
                        pendingSsl = null;
                    }
                })
                .setNegativeButton("取消", (dialog, which) -> cancelSsl("已取消连接"))
                .setOnCancelListener(dialog -> cancelSsl("已取消连接"))
                .show();
    }

    private void cancelSsl(String reason) {
        if (pendingSsl != null) {
            pendingSsl.cancel();
            pendingSsl = null;
        }
        handlingSsl = false;
        showOffline(reason);
    }

    private void showServerDialog() {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setText(serverUrl);
        input.setSelection(input.getText().length());
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        FrameLayout box = new FrameLayout(this);
        box.setPadding(pad, pad / 3, pad, 0);
        box.addView(input, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT));
        new AlertDialog.Builder(this)
                .setTitle("服务器地址")
                .setMessage("填樱花隧道地址。电脑上的网关要开着。")
                .setView(box)
                .setPositiveButton("保存", (dialog, which) -> applyServer(input.getText().toString().trim()))
                .setNegativeButton("取消", null)
                .show();
    }

    private void applyServer(String raw) {
        Uri uri = Uri.parse(raw);
        String scheme = uri.getScheme() == null ? "" : uri.getScheme();
        if ((!scheme.equals("http") && !scheme.equals("https")) || TextUtils.isEmpty(uri.getHost())) {
            Toast.makeText(this, "地址要以 http:// 或 https:// 开头", Toast.LENGTH_LONG).show();
            return;
        }
        String previous = hostOf(serverUrl);
        serverUrl = raw;
        SharedPreferences.Editor editor = prefs.edit().putString(KEY_URL, serverUrl);
        if (!previous.equalsIgnoreCase(uri.getHost())) {
            editor.remove(KEY_TRUST);
        }
        editor.apply();
        webView.loadUrl(serverUrl);
    }

    private void showOffline(String reason) {
        String safe = TextUtils.htmlEncode(reason);
        String html = "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>body{margin:0;background:#0F172A;color:#E2E8F0;font-family:sans-serif;"
                + "display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}"
                + "div{max-width:360px}h1{font-size:22px;font-weight:600}p{color:#94A3B8;line-height:1.6}</style>"
                + "</head><body><div><h1>连不上出图服务</h1><p>" + safe + "</p>"
                + "<p>确认电脑上的网关和樱花隧道都开着，再点右上角的刷新。</p></div></body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private String explain(String raw) {
        if (raw.contains("ERR_CERT") || raw.contains("SSL")) {
            return "证书被系统拒绝";
        }
        if (raw.contains("ERR_CONNECTION_REFUSED")) {
            return "隧道没有响应";
        }
        if (raw.contains("ERR_NAME_NOT_RESOLVED")) {
            return "找不到这个地址";
        }
        if (raw.contains("ERR_INTERNET_DISCONNECTED") || raw.contains("ERR_NETWORK_CHANGED")) {
            return "手机没有网络";
        }
        if (raw.contains("ERR_TIMED_OUT") || raw.contains("ERR_CONNECTION_TIMED_OUT")) {
            return "连接超时";
        }
        if (raw.contains("ERR_CONNECTION_CLOSED") || raw.contains("ERR_CONNECTION_RESET")) {
            return "连接被中断";
        }
        if (raw.isEmpty()) {
            return "未知原因";
        }
        return raw;
    }

    private String hostOf(String url) {
        if (url == null) {
            return "";
        }
        String host = Uri.parse(url).getHost();
        return host == null ? "" : host;
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
    }

    @Override
    protected void onPause() {
        webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (pendingSsl != null) {
            pendingSsl.cancel();
            pendingSsl = null;
        }
        webView.destroy();
        super.onDestroy();
    }
}
