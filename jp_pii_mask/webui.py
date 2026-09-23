"""ブラウザから操作できる制御パネル (プロキシが配信する単一 HTML)。

デスクトップ GUI (Tauri) と同じ管理 API を使うため、
GUI が未ビルドでも製品の操作系を先に検証できる。
外部 CDN を使わない (オフライン動作・監査容易性のため)。
"""

from __future__ import annotations

__all__ = ["PANEL_HTML"]

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jp-pii-mask 制御パネル</title>
<style>
  :root{
    --bg:#0e1116; --panel:#161b22; --line:#242c38; --fg:#e6edf3; --dim:#8b949e;
    --ok:#3fb950; --warn:#d29922; --bad:#f85149; --accent:#2f81f7;
  }
  *{box-sizing:border-box}
  body{
    margin:0;background:var(--bg);color:var(--fg);
    font-family:-apple-system,"Segoe UI","Yu Gothic UI","Hiragino Sans",Meiryo,sans-serif;
    font-size:14px;line-height:1.5;
  }
  .wrap{max-width:760px;margin:0 auto;padding:24px 20px 64px}
  h1{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:8px}
  .sub{color:var(--dim);font-size:12px;margin-bottom:20px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
  .row{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .label{font-size:12px;color:var(--dim);margin-bottom:6px;display:block}
  .toggle{display:flex;align-items:center;gap:14px}
  .switch{position:relative;width:88px;height:44px;flex:0 0 auto}
  .switch input{opacity:0;width:0;height:0}
  .slider{
    position:absolute;inset:0;cursor:pointer;background:#3a4250;border-radius:44px;
    transition:.18s;
  }
  .slider:before{
    content:"";position:absolute;height:36px;width:36px;left:4px;top:4px;background:#fff;
    border-radius:50%;transition:.18s;
  }
  .switch input:checked + .slider{background:var(--ok)}
  .switch input:checked + .slider:before{transform:translateX(44px)}
  .switch input:disabled + .slider{opacity:.5;cursor:not-allowed}
  .state{font-size:16px;font-weight:600}
  .state.on{color:var(--ok)} .state.off{color:var(--bad)}
  input[type=text],input[type=password],input[type=number],select{
    width:100%;background:#0d1117;border:1px solid var(--line);color:var(--fg);
    border-radius:8px;padding:9px 11px;font-size:13px;font-family:inherit;
  }
  input:focus,select:focus{outline:none;border-color:var(--accent)}
  button{
    background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 16px;
    font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
  }
  button.ghost{background:#21262d;border:1px solid var(--line)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
  .metric{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:10px}
  .metric .n{font-size:20px;font-weight:700}
  .metric .k{font-size:11px;color:var(--dim)}
  .pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;border:1px solid var(--line);color:var(--dim)}
  .pill.ok{color:var(--ok);border-color:#1f6f36}
  .pill.bad{color:var(--bad);border-color:#6e2422}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--dim);font-weight:500}
  .toast{
    position:fixed;left:50%;bottom:20px;transform:translateX(-50%);
    background:#21262d;border:1px solid var(--line);border-radius:8px;
    padding:10px 16px;font-size:13px;opacity:0;transition:.2s;pointer-events:none;
  }
  .toast.show{opacity:1}
  code{background:#0d1117;padding:1px 5px;border-radius:4px;font-size:12px}
  .hint{color:var(--dim);font-size:11px;margin-top:6px}
  details summary{cursor:pointer;color:var(--dim);font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <h1>🌙 jp-pii-mask <span class="pill" id="ver">-</span></h1>
  <div class="sub">日本語の個人情報を AI API へ送る前に自動マスクし、返答で自動復元します。</div>

  <div class="card">
    <div class="toggle">
      <label class="switch">
        <input type="checkbox" id="enabled">
        <span class="slider"></span>
      </label>
      <div>
        <div class="state" id="enabledLabel">読み込み中…</div>
        <div class="hint" id="listenHint">接続先: <code id="listenUrl">-</code></div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom:12px">
      <div class="label" style="margin:0">保護の動作</div>
      <span class="pill" id="nerPill">NER: -</span>
    </div>
    <div class="grid">
      <label style="display:block">
        <span class="label">リクエストをマスク</span>
        <select id="mask_requests">
          <option value="true">する</option>
          <option value="false">しない</option>
        </select>
      </label>
      <label style="display:block">
        <span class="label">応答を復元</span>
        <select id="restore_responses">
          <option value="true">する</option>
          <option value="false">しない</option>
        </select>
      </label>
    </div>
    <div class="hint">両方 ON が通常運用です。OFF にすると該当処理だけを素通しします。</div>
  </div>

  <div class="card">
    <span class="label">転送先 API</span>
    <div class="grid">
      <select id="upstream_preset"></select>
      <input type="text" id="upstream" placeholder="https://api.openai.com">
    </div>
    <div style="margin-top:12px">
      <span class="label">API キー <span id="keyState" class="pill">未設定</span></span>
      <input type="password" id="api_key" placeholder="sk-… （空欄なら環境変数を使用）" autocomplete="off">
      <div class="hint">入力したキーはこの端末内でのみ使われ、外部へは送信されません。</div>
    </div>
    <div style="margin-top:14px;display:flex;gap:8px">
      <button id="save">設定を保存</button>
      <button class="ghost" id="reload">再読み込み</button>
    </div>
  </div>

  <div class="card">
    <span class="label">検出テスト (マスクはしません)</span>
    <input type="text" id="probe" placeholder="例: 田中太郎の携帯は090-1234-5678、〒100-0001 東京都千代田区千代田1-1">
    <div style="margin-top:10px"><button class="ghost" id="probeBtn">検出する</button></div>
    <div id="probeOut" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <span class="label">統計 (原文は保存していません)</span>
    <div class="metrics">
      <div class="metric"><div class="n" id="m_req">0</div><div class="k">受信リクエスト</div></div>
      <div class="metric"><div class="n" id="m_ent">0</div><div class="k">マスク件数</div></div>
      <div class="metric"><div class="n" id="m_res">0</div><div class="k">復元件数</div></div>
    </div>
    <div style="margin-top:12px">
      <details>
        <summary>種別の内訳</summary>
        <table id="typeTable"><tbody></tbody></table>
      </details>
    </div>
    <div style="margin-top:12px"><button class="ghost" id="resetStats">統計をリセット</button></div>
  </div>

  <div class="card">
    <span class="label">このアプリの使い方</span>
    <div class="hint" style="font-size:12px;line-height:1.7">
      お使いの AI アプリ (Claude Desktop / Cursor / 自作スクリプトなど) の接続先を
      <code id="baseUrlHint">http://127.0.0.1:8787</code> に設定してください。<br>
      OpenAI 互換なら <code>OPENAI_BASE_URL</code> を同じ値に、Anthropic なら
      <code>ANTHROPIC_BASE_URL</code> に向けるだけで動きます。
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
(function(){
  const $ = (id) => document.getElementById(id);
  let state = null;

  function toast(msg){
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    setTimeout(()=>t.classList.remove("show"), 1800);
  }

  async function api(path, opts){
    const res = await fetch(path, Object.assign({headers:{"Content-Type":"application/json"}}, opts||{}));
    if(!res.ok){
      let detail = res.statusText;
      try { detail = (await res.json()).error || detail; } catch(e){}
      throw new Error(detail);
    }
    return res.json();
  }

  function render(s){
    state = s;
    $("enabled").checked = !!s.enabled;
    $("enabled").disabled = false;
    const lbl = $("enabledLabel");
    lbl.textContent = s.enabled ? "マスキング保護: ON" : "マスキング保護: OFF";
    lbl.className = "state " + (s.enabled ? "on" : "off");

    $("mask_requests").value = String(!!s.mask_requests);
    $("restore_responses").value = String(!!s.restore_responses);
    $("upstream_preset").value = s.upstream_preset;
    if(document.activeElement !== $("upstream")) $("upstream").value = s.upstream;

    $("ver").textContent = "v" + s.version;
    $("keyState").textContent = s.api_key_set ? "設定済み" : "未設定";
    $("keyState").className = "pill " + (s.api_key_set ? "ok" : "bad");
    $("nerPill").textContent = "NER: " + (s.ner_available ? "利用可" : "未導入(ルールのみ)");
    $("nerPill").className = "pill " + (s.ner_available ? "ok" : "");

    const url = "http://" + (s.host === "0.0.0.0" ? "127.0.0.1" : s.host) + ":" + s.port;
    $("listenUrl").textContent = url;
    $("baseUrlHint").textContent = url;

    const st = s.stats || {};
    $("m_req").textContent = st.requests_total || 0;
    $("m_ent").textContent = st.entities_masked || 0;
    $("m_res").textContent = st.placeholders_restored || 0;

    const tb = $("typeTable").querySelector("tbody");
    const by = st.by_type || {};
    const keys = Object.keys(by);
    tb.innerHTML = keys.length
      ? keys.sort((a,b)=>by[b]-by[a]).map(k=>`<tr><td>${k}</td><td>${by[k]}</td></tr>`).join("")
      : '<tr><td colspan="2" style="color:var(--dim)">まだ検出がありません</td></tr>';
  }

  async function refresh(){
    try{
      render(await api("/admin/api/state"));
    }catch(e){
      $("enabledLabel").textContent = "接続できません";
      $("enabledLabel").className = "state off";
      $("enabled").disabled = true;
    }
  }

  async function presets(){
    try{
      const p = await api("/admin/api/presets");
      $("upstream_preset").innerHTML = Object.entries(p)
        .map(([k,v])=>`<option value="${k}">${v.label}</option>`).join("");
    }catch(e){}
  }

  async function post(payload){
    return api("/admin/api/state", {method:"POST", body: JSON.stringify(payload)});
  }

  $("enabled").addEventListener("change", async (e)=>{
    const want = e.target.checked;
    e.target.disabled = true;
    try{
      await post({enabled: want});
      toast(want ? "保護を ON にしました" : "保護を OFF にしました");
    }catch(err){ toast("失敗: " + err.message); }
    await refresh();
  });

  $("save").addEventListener("click", async ()=>{
    const payload = {
      mask_requests: $("mask_requests").value === "true",
      restore_responses: $("restore_responses").value === "true",
      upstream_preset: $("upstream_preset").value,
      upstream: $("upstream").value.trim(),
    };
    const key = $("api_key").value.trim();
    if(key){ payload.api_key = key; payload.store_api_key = true; }
    try{
      await post(payload);
      $("api_key").value = "";
      toast("設定を保存しました");
    }catch(err){ toast("失敗: " + err.message); }
    await refresh();
  });

  $("upstream_preset").addEventListener("change", async ()=>{
    try{
      const p = await api("/admin/api/presets");
      const sel = $("upstream_preset").value;
      if(p[sel] && p[sel].base_url) $("upstream").value = p[sel].base_url;
    }catch(e){}
  });

  $("reload").addEventListener("click", refresh);

  $("probeBtn").addEventListener("click", async ()=>{
    const text = $("probe").value;
    if(!text){ return; }
    try{
      const r = await api("/admin/api/detect?text=" + encodeURIComponent(text));
      if(!r.count){
        $("probeOut").innerHTML = '<span style="color:var(--dim)">検出なし</span>';
        return;
      }
      $("probeOut").innerHTML = "<table><thead><tr><th>種別</th><th>文字列</th><th>信頼度</th></tr></thead><tbody>"
        + r.entities.map(e=>`<tr><td>${e.type}</td><td>${escapeHtml(e.text)}</td><td>${e.confidence}</td></tr>`).join("")
        + "</tbody></table>";
    }catch(err){ toast("失敗: " + err.message); }
  });

  $("resetStats").addEventListener("click", async ()=>{
    try{ await api("/admin/api/stats/reset", {method:"POST"}); toast("統計をリセットしました"); }
    catch(err){ toast("失敗: " + err.message); }
    await refresh();
  });

  function escapeHtml(s){
    return String(s).replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
  }

  presets().then(refresh);
  setInterval(refresh, 3000);
})();
</script>
</body>
</html>
"""
