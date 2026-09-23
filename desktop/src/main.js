// =============================================================================
// JP PII Mask — 設定ウィンドウのフロントエンド
// =============================================================================
//
// 役割:
//   - /admin/api/state を 3 秒ごとに取得して UI に反映
//   - 大きな ON/OFF トグル・プロバイダ選択・API キー入力の送信
//   - トレイからの "tray-request-toggle" イベントを処理
//
// 依存: なし (バンドラ無し / CDN 無し / すべてこのファイル内)
//       Tauri API は `withGlobalTauri: true` により window.__TAURI__ 経由で使う。
//
// ⚠️ 未検証:
//   このファイルは Tauri アプリ上で一度も読み込まれていません。
//   `window.__TAURI__` の有無 / invoke・listen の名前空間は
//   Tauri 2 を前提に書いていますが未確認です。VERIFY.md を参照してください。

'use strict';

// -----------------------------------------------------------------------------
// 定数
// -----------------------------------------------------------------------------
//
// NOTE: ポートは src-tauri/src/main.rs の DEFAULT_PORT と一致させること。
//       設定ファイルからの読み込みは未実装 (変更時は 2 箇所を直す)。
const PORT = 8787;
const BASE = 'http://127.0.0.1:' + PORT;
const ADMIN_STATE = BASE + '/admin/api/state';
const HEALTHZ = BASE + '/healthz';
const POLL_MS = 3000;

/** プロバイダ選択値 → 実際の upstream URL。'__custom__' は入力欄を使う。 */
const CUSTOM = '__custom__';

/** 二重送信を防ぐためのフラグ。 */
let busy = false;
/** 直近に取得したサーバ状態 (null = 未接続)。 */
let lastState = null;
/** ポーリングタイマ ID。 */
let pollTimer = null;

// -----------------------------------------------------------------------------
// DOM
// -----------------------------------------------------------------------------
const el = {
  toggleBtn: document.getElementById('toggleBtn'),
  toggleEmoji: document.getElementById('toggleEmoji'),
  toggleSub: document.getElementById('toggleSub'),
  upstreamSelect: document.getElementById('upstreamSelect'),
  customWrap: document.getElementById('customWrap'),
  customUpstream: document.getElementById('customUpstream'),
  apiKey: document.getElementById('apiKey'),
  saveBtn: document.getElementById('saveBtn'),
  clearKeyBtn: document.getElementById('clearKeyBtn'),
  statPort: document.getElementById('statPort'),
  statEnabled: document.getElementById('statEnabled'),
  statMasking: document.getElementById('statMasking'),
  statKey: document.getElementById('statKey'),
  statDetected: document.getElementById('statDetected'),
  statReplaced: document.getElementById('statReplaced'),
  statsExtra: document.getElementById('statsExtra'),
  statusDot: document.getElementById('statusDot'),
  statusText: document.getElementById('statusText'),
  statusPort: document.getElementById('statusPort'),
};

// -----------------------------------------------------------------------------
// Tauri ブリッジ (未定義でも落ちないように防御する)
// -----------------------------------------------------------------------------
const tauri = (typeof window !== 'undefined' && window.__TAURI__) ? window.__TAURI__ : null;

/**
 * Rust コマンドを呼ぶ。Tauri が無い環境 (素のブラウザで index.html を開いた場合)
 * でも動作確認できるよう、失敗は握りつぶして console に出すだけにする。
 */
function invoke(cmd, args) {
  if (!tauri || !tauri.core || typeof tauri.core.invoke !== 'function') {
    console.warn('[jp-pii-mask] Tauri コマンドを利用できません (window.__TAURI__ 未定義):', cmd);
    return Promise.resolve(null);
  }
  return tauri.core.invoke(cmd, args || {}).catch(function (err) {
    console.error('[jp-pii-mask] invoke 失敗:', cmd, err);
    return null;
  });
}

/**
 * トレイ → webview のイベントを購読する。
 * NOTE: Tauri 2 の `listen` は window.__TAURI__.event.listen を想定。
 *       名前空間が違う場合は VERIFY.md の確認項目に従って修正すること。
 */
function listenTrayEvents() {
  if (!tauri || !tauri.event || typeof tauri.event.listen !== 'function') {
    console.warn('[jp-pii-mask] トレイイベントを購読できません (window.__TAURI__.event 未定義)');
    return;
  }
  tauri.event.listen('tray-request-toggle', function (event) {
    // payload は bool。トレイが「こうしたい」と言っている値。
    var desired = !!(event && event.payload);
    applyMasking(desired, 'トレイ');
  }).catch(function (err) {
    console.error('[jp-pii-mask] tray-request-toggle の購読に失敗:', err);
  });
}

// -----------------------------------------------------------------------------
// 表示ヘルパ
// -----------------------------------------------------------------------------
function setStatus(kind, text) {
  el.statusDot.className = 'dot' + (kind ? ' ' + kind : '');
  el.statusText.textContent = text;
}

function yesNo(v) {
  return v ? 'はい' : 'いいえ';
}

function render(state) {
  lastState = state;

  // ---- トグル ----
  var on = !!state.masking_enabled;
  el.toggleBtn.dataset.state = on ? 'on' : 'off';
  el.toggleBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  el.toggleEmoji.textContent = on ? '🛡️' : '⚠️';
  el.toggleSub.textContent = on ? '保護中 — 送信前に個人情報をマスクします' : '停止中 — 原文がそのまま送信されます';

  // ---- 稼働状況 ----
  el.statPort.textContent = state.port != null ? String(state.port) : '—';
  el.statusPort.textContent = ':' + (state.port != null ? state.port : PORT);
  el.statEnabled.textContent = yesNo(state.enabled);
  el.statMasking.textContent = yesNo(state.masking_enabled);
  el.statKey.textContent = state.api_key_set ? '設定済み' : '未設定';

  // ---- stats ----
  // Python 側 (/admin/api/state) の統計キー名:
  //   entities_masked        送信前にマスクした件数
  //   placeholders_restored  応答で原文に戻した件数
  var s = state.stats || {};
  el.statDetected.textContent = fmtNum(s.entities_masked);
  el.statReplaced.textContent = fmtNum(s.placeholders_restored);

  var extras = [];
  ['requests_total', 'requests_masked', 'restore_misses', 'uptime_seconds'].forEach(function (k) {
    if (s[k] !== undefined && s[k] !== null) extras.push(k + ': ' + s[k]);
  });
  if (s.by_type && typeof s.by_type === 'object') {
    var inner = Object.keys(s.by_type).map(function (k) { return k + '=' + s.by_type[k]; });
    if (inner.length) extras.push('by_type: ' + inner.join(', '));
  }
  el.statsExtra.textContent = extras.length ? extras.join('  /  ') : '';

  // ---- 接続先 (ユーザーが編集中でなければ反映) ----
  if (document.activeElement !== el.upstreamSelect &&
      document.activeElement !== el.customUpstream) {
    syncUpstreamField(state.upstream);
  }

  // ---- API キー欄 (サーバが持っている値は取得できないので、状態だけ示す) ----
  el.apiKey.placeholder = state.api_key_set
    ? '設定済み (変更する場合のみ入力)'
    : 'sk-...';

  setStatus('ok', '接続中: 127.0.0.1:' + (state.port != null ? state.port : PORT));
}

function fmtNum(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'object') {
    // e.g. {"氏名": 3, "住所": 1} のような内訳辞書だった場合は合計を出す。
    var total = 0, keys = Object.keys(v), numeric = false;
    keys.forEach(function (k) {
      if (typeof v[k] === 'number') { total += v[k]; numeric = true; }
    });
    return numeric ? String(total) : '—';
  }
  return String(v);
}

/** state.upstream の値から select の状態を復元する。 */
function syncUpstreamField(upstream) {
  if (!upstream) return;
  var opts = el.upstreamSelect.options;
  var matched = false;
  for (var i = 0; i < opts.length; i++) {
    if (opts[i].value === upstream && opts[i].value !== CUSTOM) {
      el.upstreamSelect.value = upstream;
      matched = true;
      break;
    }
  }
  if (!matched) {
    el.upstreamSelect.value = CUSTOM;
    el.customUpstream.value = upstream;
  }
  updateCustomVisibility();
}

function updateCustomVisibility() {
  el.customWrap.classList.toggle('hidden', el.upstreamSelect.value !== CUSTOM);
}

/** select / custom 入力から実際の upstream URL を作る。 */
function currentUpstream() {
  if (el.upstreamSelect.value === CUSTOM) {
    return (el.customUpstream.value || '').trim();
  }
  return el.upstreamSelect.value;
}

// -----------------------------------------------------------------------------
// 通信
// -----------------------------------------------------------------------------

async function fetchState() {
  var res = await fetch(ADMIN_STATE, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return await res.json();
}

/** POST /admin/api/state。body は部分更新でよい。
 *
 * Python 側は GET /admin/api/state と同じ形 (＋ ok / applied) を返すため、
 * そのまま render() に渡せる。
 */
async function postState(patch) {
  var res = await fetch(ADMIN_STATE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  var text = await res.text();
  return text ? JSON.parse(text) : null;
}

async function poll() {
  try {
    var state = await fetchState();
    render(state);
  } catch (err) {
    lastState = null;
    setStatus('err', 'プロキシに接続できません (jp-pii-mask serve --port ' + PORT + ' が起動していますか？)');
    el.statPort.textContent = '—';
    el.statEnabled.textContent = '—';
    el.statMasking.textContent = '—';
    el.statKey.textContent = '—';
  }
}

// -----------------------------------------------------------------------------
// 操作
// -----------------------------------------------------------------------------

/** マスキング保護の ON/OFF を適用し、結果をトレイに同期する。 */
async function applyMasking(desired, source) {
  if (busy) return;
  busy = true;
  el.toggleBtn.disabled = true;
  setStatus('wait', (desired ? 'ON' : 'OFF') + ' に切り替えています… (' + source + ')');
  try {
    var updated = await postState({ masking_enabled: desired });
    // 応答が無い場合は GET で確定値を取得する。
    var state = updated || await fetchState();
    render(state);
    // 実際の確定値をトレイのチェック状態に書き戻す (乖離防止)。
    await invoke('sync_tray_checked', { checked: !!state.masking_enabled });
  } catch (err) {
    console.error('[jp-pii-mask] 切り替えに失敗:', err);
    setStatus('err', '切り替えに失敗しました: ' + err.message);
    // 失敗時はサーバの現在値に戻す。
    await poll();
    if (lastState) {
      await invoke('sync_tray_checked', { checked: !!lastState.masking_enabled });
    }
  } finally {
    busy = false;
    el.toggleBtn.disabled = false;
  }
}

/** 上流 URL と API キーを保存する。 */
async function saveSettings() {
  var upstream = currentUpstream();
  if (!upstream) {
    setStatus('err', '接続先 URL を入力してください');
    return;
  }
  if (!/^https?:\/\//i.test(upstream)) {
    setStatus('err', '接続先 URL は http(s):// で始まる必要があります');
    return;
  }

  var patch = { upstream: upstream };
  // 空欄なら「変更しない」。キー消去は専用ボタンで行う。
  if (el.apiKey.value) patch.api_key = el.apiKey.value;

  busy = true;
  el.saveBtn.disabled = true;
  setStatus('wait', '設定を保存しています…');
  try {
    var updated = await postState(patch);
    el.apiKey.value = ''; // 送信後はメモリ上からも消す
    var state = updated || await fetchState();
    render(state);
    setStatus('ok', '設定を保存しました');
  } catch (err) {
    console.error('[jp-pii-mask] 保存に失敗:', err);
    setStatus('err', '保存に失敗しました: ' + err.message);
  } finally {
    busy = false;
    el.saveBtn.disabled = false;
  }
}

// -----------------------------------------------------------------------------
// イベント登録
// -----------------------------------------------------------------------------
el.toggleBtn.addEventListener('click', function () {
  var desired = !(lastState && lastState.masking_enabled);
  applyMasking(desired, 'ウィンドウ');
});

el.upstreamSelect.addEventListener('change', updateCustomVisibility);
el.saveBtn.addEventListener('click', saveSettings);

el.clearKeyBtn.addEventListener('click', async function () {
  // api_key に空文字を POST すると Python 側は「消去」として扱う
  // (ConfigStore.update は None だけを無視し、空文字は値として設定する)。
  if (!confirm('保存済みの API キーを消去します。よろしいですか？')) return;
  busy = true;
  setStatus('wait', 'API キーを消去しています…');
  try {
    var updated = await postState({ api_key: '' });
    var state = updated || await fetchState();
    render(state);
    setStatus('ok', 'API キーを消去しました');
  } catch (err) {
    setStatus('err', '消去に失敗しました: ' + err.message);
  } finally {
    busy = false;
  }
});

// 入力中に Enter で保存
[el.apiKey, el.customUpstream].forEach(function (input) {
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') saveSettings();
  });
});

// ポーリング (タブ非表示中は無駄打ちを避ける)
function startPolling() {
  if (pollTimer !== null) return;
  poll();
  pollTimer = setInterval(poll, POLL_MS);
}
function stopPolling() {
  if (pollTimer === null) return;
  clearInterval(pollTimer);
  pollTimer = null;
}
document.addEventListener('visibilitychange', function () {
  if (document.hidden) stopPolling(); else startPolling();
});

// -----------------------------------------------------------------------------
// 起動
// -----------------------------------------------------------------------------
(async function init() {
  // Rust コマンド名は snake_case だが webview 側からは camelCase になる
  // (Tauri の慣習)。念のため両方の綴りでログを出しておく。
  console.log('[jp-pii-mask] 設定ウィンドウを初期化します');

  // サイドカーが立ち上がるまで少し待ってから初回取得する。
  // (起動直後は 8787 がまだ listen していない可能性がある)
  el.statusText.textContent = 'プロキシの起動を待っています…';
  el.statusDot.className = 'dot wait';

  listenTrayEvents();
  startPolling();

  // ヘルスチェックで早期に接続可否を判定 (失敗してもポーリングが再試行する)。
  try {
    await fetch(HEALTHZ, { cache: 'no-store' });
  } catch (_) {
    /* ポーリング側で扱う */
  }
})();
