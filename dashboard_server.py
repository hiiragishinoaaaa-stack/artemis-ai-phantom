"""ARTEMIS Phantom Sniper 用の読み取り専用ダッシュボードサーバー。

Supabase(supabase_client.py)に溜まった通知履歴・結果トラッキング・
発行者ブラックリストを、スマホのブラウザから見れる形にするだけの、
依存パッケージを追加しない最小限のHTTPサーバー(settings_server.py
[mt5-ai-trader]と同じ設計方針)。phantom-sniper.service(本体のbot)とは
完全に別プロセスで、このサーバーが止まっていてもボット自体の動作(検知・
スコアリング・Discord通知)には一切影響しない。

SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEYが未設定の場合、/api/*は
「Supabase未設定」というエラーメッセージ入りのJSONを返す(500ではなく
200で返し、フロント側がそのまま表示できるようにしている)。

## セキュリティに関する重要な注意

読み取り専用(POSTエンドポイントは無い)だが、ウォレット関連の情報
(発行者アドレス等)を含むため、settings_server.pyと同様に**信頼できる
ローカルネットワーク内でのみ使用し、インターネットへ公開しないこと**。
簡易的な追加防御として.envで`DASHBOARD_API_TOKEN`を設定すると、
/api/*へのアクセスに`Authorization: Bearer <token>`が必須になる
(トップページ自体は常に見れる。ページ内のトークン入力欄で設定する)。

## エンドポイント

    GET  /              ダッシュボードのHTML(単一ファイル、外部CDN不使用)
    GET  /api/summary    通知数・ティア別・★分布・チェックポイント別勝率・
                         ブロックリスト件数の集計(dashboard_analytics.py参照)
    GET  /api/notifications  直近の通知一覧(?limit=で件数変更、既定は
                         config.DASHBOARD_RECENT_NOTIFICATIONS_LIMIT)
    GET  /token/<mint>       1銘柄の詳細ページ(Discord通知の「詳細」ボタン
                         の遷移先、HTML)
    GET  /api/token/<mint>   1銘柄の通知履歴・結果トラッキングをまとめて
                         返すJSON(/token/<mint>のデータソース)

## 実行方法

    python dashboard_server.py
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import dashboard_analytics
import supabase_client
from logger import setup_logger

logger = logging.getLogger("phantom_sniper")

_INDEX_PATH = "/"
_SUMMARY_PATH = "/api/summary"
_NOTIFICATIONS_PATH = "/api/notifications"
_TOKEN_PAGE_PREFIX = "/token/"
_TOKEN_API_PREFIX = "/api/token/"

_NOT_CONFIGURED_MESSAGE = (
    "SupabaseがSUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY未設定のため、"
    "データがありません(.envを設定してphantom-sniperを再起動してください)"
)


def _fetch_summary() -> dict:
    if not supabase_client.is_configured():
        return {"supabase_configured": False, "error": _NOT_CONFIGURED_MESSAGE}

    notifications = supabase_client.fetch("notifications?select=notification_type,tier,star_count&limit=2000")
    outcomes = supabase_client.fetch("outcomes?select=checkpoint_seconds,change_pct&limit=5000")
    blocklist = supabase_client.fetch("creator_blocklist?select=creator")

    return {
        "supabase_configured": True,
        "notifications": dashboard_analytics.summarize_notifications(notifications or []),
        "win_rate_by_checkpoint": dashboard_analytics.summarize_outcomes(outcomes or []),
        "blocklist_count": len(blocklist) if blocklist is not None else 0,
    }


def _fetch_recent_notifications(limit: int) -> dict:
    if not supabase_client.is_configured():
        return {"supabase_configured": False, "error": _NOT_CONFIGURED_MESSAGE, "notifications": []}

    query = (
        "notifications?select=mint,name,symbol,notification_type,tier,score,star_count,"
        f"elapsed_seconds,notified_at&order=notified_at.desc&limit={limit}"
    )
    rows = supabase_client.fetch(query)
    return {"supabase_configured": True, "notifications": rows if rows is not None else []}


def _fetch_token_detail(mint: str) -> dict:
    if not supabase_client.is_configured():
        return {"supabase_configured": False, "error": _NOT_CONFIGURED_MESSAGE, "notifications": [], "outcomes": []}

    encoded_mint = urllib.parse.quote(mint, safe="")
    notifications = supabase_client.fetch(
        f"notifications?select=*&mint=eq.{encoded_mint}&order=notified_at.desc"
    )
    outcomes = supabase_client.fetch(
        f"outcomes?select=*&mint=eq.{encoded_mint}&order=checkpoint_seconds.asc"
    )
    return {
        "supabase_configured": True,
        "notifications": notifications if notifications is not None else [],
        "outcomes": outcomes if outcomes is not None else [],
    }


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "ARTEMISPhantomDashboard/1.0"

    def _set_common_headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Type", content_type)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._set_common_headers(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self._set_common_headers(status, content_type="text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        if not config.DASHBOARD_API_TOKEN:
            return True
        expected = f"Bearer {config.DASHBOARD_API_TOKEN}"
        return self.headers.get("Authorization") == expected

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._set_common_headers(204)
        self.end_headers()

    def _path_without_query(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def _query_param(self, name: str) -> str | None:
        query = urllib.parse.urlsplit(self.path).query
        values = urllib.parse.parse_qs(query).get(name)
        return values[0] if values else None

    def do_GET(self) -> None:  # noqa: N802
        path = self._path_without_query()
        if path == _INDEX_PATH:
            self._send_html(200, _DASHBOARD_HTML)
        elif path == _SUMMARY_PATH:
            self._handle_get_summary()
        elif path == _NOTIFICATIONS_PATH:
            self._handle_get_notifications()
        elif path.startswith(_TOKEN_API_PREFIX):
            self._handle_get_token_detail_api(path[len(_TOKEN_API_PREFIX):])
        elif path.startswith(_TOKEN_PAGE_PREFIX):
            self._handle_get_token_detail_page()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_get_summary(self) -> None:
        if not self._is_authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        self._send_json(200, _fetch_summary())

    def _handle_get_notifications(self) -> None:
        if not self._is_authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        raw_limit = self._query_param("limit")
        try:
            limit = int(raw_limit) if raw_limit else config.DASHBOARD_RECENT_NOTIFICATIONS_LIMIT
        except ValueError:
            limit = config.DASHBOARD_RECENT_NOTIFICATIONS_LIMIT
        limit = max(1, min(limit, 500))
        self._send_json(200, _fetch_recent_notifications(limit))

    def _handle_get_token_detail_api(self, encoded_mint: str) -> None:
        if not self._is_authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        mint = urllib.parse.unquote(encoded_mint)
        if not mint:
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, _fetch_token_detail(mint))

    def _handle_get_token_detail_page(self) -> None:
        # データ自体はJS側が/api/token/<mint>を認証ヘッダー付きで取得するため、
        # ページのHTMLそのものはトップページ同様に認証不要で返す。
        self._send_html(200, _TOKEN_DETAIL_HTML)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug("dashboard_server: %s - %s", self.address_string(), format % args)


_DASHBOARD_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PHANTOM SNIPER Dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px 12px 40px;
    background: #0b0d12; color: #e6e8ee;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  h1 { font-size: 1.15rem; margin: 0 0 4px; }
  .sub { color: #8a90a0; font-size: 0.8rem; margin-bottom: 16px; }
  .token-row { display: flex; gap: 6px; margin-bottom: 16px; }
  .token-row input {
    flex: 1; background: #171a22; border: 1px solid #2a2f3d; color: #e6e8ee;
    border-radius: 8px; padding: 8px 10px; font-size: 0.85rem;
  }
  .token-row button {
    background: #2b6cff; color: white; border: none; border-radius: 8px;
    padding: 8px 14px; font-size: 0.85rem;
  }
  .banner {
    background: #3a2a12; border: 1px solid #6b4a1a; color: #ffcf8a;
    border-radius: 10px; padding: 10px 12px; margin-bottom: 16px; font-size: 0.85rem;
  }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 18px; }
  .card {
    background: #12151d; border: 1px solid #232838; border-radius: 12px; padding: 12px;
  }
  .card .label { font-size: 0.72rem; color: #8a90a0; margin-bottom: 4px; }
  .card .value { font-size: 1.4rem; font-weight: 700; }
  .card .value.small { font-size: 1.05rem; }
  section { margin-bottom: 22px; }
  section h2 { font-size: 0.95rem; margin: 0 0 8px; color: #c7cbd6; }
  .bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 0.8rem; }
  .bar-row .bar-label { width: 74px; flex-shrink: 0; color: #a7abba; }
  .bar-track { flex: 1; background: #1b1f2a; border-radius: 6px; overflow: hidden; height: 14px; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, #2b6cff, #7aa7ff); }
  .bar-count { width: 34px; text-align: right; color: #a7abba; flex-shrink: 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #1e222e; white-space: nowrap; }
  th { color: #8a90a0; font-weight: 500; }
  .tier-HIGH { color: #ff6b6b; }
  .tier-WATCH { color: #ffcf5c; }
  .tier-tag.followup { color: #7ad0ff; }
  .table-wrap { overflow-x: auto; }
  .empty { color: #6b7080; font-size: 0.82rem; padding: 8px 0; }
</style>
</head>
<body>
  <h1>🔫 PHANTOM SNIPER Dashboard</h1>
  <div class="sub" id="updated-at">読み込み中...</div>

  <div class="token-row">
    <input id="token-input" type="password" placeholder="APIトークン(設定している場合のみ)">
    <button id="token-save">保存</button>
  </div>

  <div id="banner"></div>

  <div class="grid" id="stat-cards"></div>

  <section>
    <h2>★分布(初動通知時点、直近2000件)</h2>
    <div id="star-bars"></div>
  </section>

  <section>
    <h2>通知後の勝率(change_pct &gt; 0 の割合)</h2>
    <div id="win-rate-cards" class="grid"></div>
  </section>

  <section>
    <h2>直近の通知</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>時刻</th><th>種別</th><th>銘柄</th><th>Tier</th><th>Score</th><th>★</th><th>経過</th></tr>
        </thead>
        <tbody id="notifications-body"></tbody>
      </table>
    </div>
  </section>

<script>
function getToken() { return localStorage.getItem('phantom_dashboard_token') || ''; }

document.getElementById('token-input').value = getToken();
document.getElementById('token-save').addEventListener('click', function () {
  localStorage.setItem('phantom_dashboard_token', document.getElementById('token-input').value.trim());
  loadAll();
});

function authHeaders() {
  var token = getToken();
  return token ? { 'Authorization': 'Bearer ' + token } : {};
}

function fmtPct(n) { return (n > 0 ? '+' : '') + n.toFixed(1) + '%'; }

function renderBanner(message) {
  var el = document.getElementById('banner');
  el.innerHTML = message ? '<div class="banner">' + message + '</div>' : '';
}

function renderStatCards(summary) {
  var n = summary.notifications || {};
  var cards = [
    ['総通知数', n.total_notifications || 0],
    ['うち追い通知', n.followup_count || 0],
    ['HIGH', (n.tier_counts && n.tier_counts.HIGH) || 0],
    ['WATCH', (n.tier_counts && n.tier_counts.WATCH) || 0],
    ['発行者ブラックリスト', summary.blocklist_count || 0],
  ];
  var html = '';
  cards.forEach(function (c) {
    html += '<div class="card"><div class="label">' + c[0] + '</div><div class="value">' + c[1] + '</div></div>';
  });
  document.getElementById('stat-cards').innerHTML = html;
}

function renderStarBars(summary) {
  var stars = (summary.notifications && summary.notifications.star_counts) || { '0': 0, '1': 0, '2': 0, '3': 0 };
  var total = Object.values(stars).reduce(function (a, b) { return a + b; }, 0) || 1;
  var labels = { '0': '★0', '1': '★1', '2': '★2', '3': '★3' };
  var html = '';
  ['0', '1', '2', '3'].forEach(function (key) {
    var count = stars[key] || 0;
    var pct = Math.round((count / total) * 100);
    html += '<div class="bar-row">' +
      '<div class="bar-label">' + labels[key] + '</div>' +
      '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
      '<div class="bar-count">' + count + '</div>' +
      '</div>';
  });
  document.getElementById('star-bars').innerHTML = html;
}

function renderWinRateCards(summary) {
  var checkpoints = { '1800': '30分後', '3600': '1時間後', '86400': '24時間後' };
  var data = summary.win_rate_by_checkpoint || {};
  var html = '';
  Object.keys(checkpoints).forEach(function (key) {
    var stat = data[key];
    if (!stat) {
      html += '<div class="card"><div class="label">' + checkpoints[key] + '</div><div class="value small">データなし</div></div>';
      return;
    }
    html += '<div class="card">' +
      '<div class="label">' + checkpoints[key] + '(n=' + stat.count + ')</div>' +
      '<div class="value">' + stat.win_rate_pct + '%</div>' +
      '<div class="label">平均 ' + fmtPct(stat.avg_change_pct) + '</div>' +
      '</div>';
  });
  document.getElementById('win-rate-cards').innerHTML = html;
}

function renderNotifications(payload) {
  var rows = payload.notifications || [];
  var body = document.getElementById('notifications-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">まだ通知がありません</td></tr>';
    return;
  }
  var html = '';
  rows.forEach(function (r) {
    var time = r.notified_at ? new Date(r.notified_at).toLocaleString('ja-JP') : '-';
    var typeLabel = r.notification_type === 'followup' ? '<span class="tier-tag followup">追い通知</span>' : '通常';
    var tierClass = r.tier === 'HIGH' ? 'tier-HIGH' : (r.tier === 'WATCH' ? 'tier-WATCH' : '');
    var name = r.name || '(名称不明)';
    var stars = '⭐'.repeat(r.star_count || 0);
    html += '<tr>' +
      '<td>' + time + '</td>' +
      '<td>' + typeLabel + '</td>' +
      '<td>' + name + (r.symbol ? ' ($' + r.symbol + ')' : '') + '</td>' +
      '<td class="' + tierClass + '">' + (r.tier || '-') + '</td>' +
      '<td>' + (r.score != null ? r.score : '-') + '</td>' +
      '<td>' + stars + '</td>' +
      '<td>' + (r.elapsed_seconds != null ? r.elapsed_seconds + '秒' : '-') + '</td>' +
      '</tr>';
  });
  body.innerHTML = html;
}

function loadAll() {
  fetch('/api/summary', { headers: authHeaders() })
    .then(function (r) { return r.json(); })
    .then(function (summary) {
      if (summary.supabase_configured === false) {
        renderBanner(summary.error);
      } else {
        renderBanner('');
      }
      renderStatCards(summary);
      renderStarBars(summary);
      renderWinRateCards(summary);
    })
    .catch(function (e) { renderBanner('summary取得に失敗しました: ' + e); });

  fetch('/api/notifications', { headers: authHeaders() })
    .then(function (r) { return r.json(); })
    .then(renderNotifications)
    .catch(function (e) { console.error(e); });

  document.getElementById('updated-at').textContent = '最終更新: ' + new Date().toLocaleTimeString('ja-JP');
}

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>
"""


_TOKEN_DETAIL_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>トークン詳細 - PHANTOM SNIPER</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px 12px 40px;
    background: #0b0d12; color: #e6e8ee;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  a { color: #7aa7ff; }
  h1 { font-size: 1.05rem; margin: 0 0 4px; word-break: break-all; }
  .sub { color: #8a90a0; font-size: 0.8rem; margin-bottom: 16px; word-break: break-all; }
  .back { display: inline-block; margin-bottom: 14px; font-size: 0.82rem; }
  .token-row { display: flex; gap: 6px; margin-bottom: 16px; }
  .token-row input {
    flex: 1; background: #171a22; border: 1px solid #2a2f3d; color: #e6e8ee;
    border-radius: 8px; padding: 8px 10px; font-size: 0.85rem;
  }
  .token-row button {
    background: #2b6cff; color: white; border: none; border-radius: 8px;
    padding: 8px 14px; font-size: 0.85rem;
  }
  .banner {
    background: #3a2a12; border: 1px solid #6b4a1a; color: #ffcf8a;
    border-radius: 10px; padding: 10px 12px; margin-bottom: 16px; font-size: 0.85rem;
  }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 18px; }
  .card {
    background: #12151d; border: 1px solid #232838; border-radius: 12px; padding: 12px;
  }
  .card .label { font-size: 0.72rem; color: #8a90a0; margin-bottom: 4px; }
  .card .value { font-size: 1.3rem; font-weight: 700; }
  .card .value.small { font-size: 1.0rem; }
  section { margin-bottom: 22px; }
  section h2 { font-size: 0.95rem; margin: 0 0 8px; color: #c7cbd6; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #1e222e; white-space: nowrap; }
  th { color: #8a90a0; font-weight: 500; }
  .tier-HIGH { color: #ff6b6b; }
  .tier-WATCH { color: #ffcf5c; }
  .tier-tag.followup { color: #7ad0ff; }
  .table-wrap { overflow-x: auto; }
  .empty { color: #6b7080; font-size: 0.82rem; padding: 8px 0; }
  .pct-up { color: #6bffa0; }
  .pct-down { color: #ff6b6b; }
</style>
</head>
<body>
  <a class="back" href="/">← ダッシュボードに戻る</a>
  <h1 id="token-title">読み込み中...</h1>
  <div class="sub" id="token-mint"></div>

  <div class="token-row">
    <input id="token-input" type="password" placeholder="APIトークン(設定している場合のみ)">
    <button id="token-save">保存</button>
  </div>

  <div id="banner"></div>

  <div class="grid" id="stat-cards"></div>

  <section>
    <h2>通知履歴(この銘柄)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>時刻</th><th>種別</th><th>Tier</th><th>Score</th><th>★</th><th>経過</th></tr>
        </thead>
        <tbody id="notifications-body"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>通知後の時価総額推移</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>経過</th><th>通知時</th><th>その時点</th><th>増減</th></tr>
        </thead>
        <tbody id="outcomes-body"></tbody>
      </table>
    </div>
  </section>

<script>
function getToken() { return localStorage.getItem('phantom_dashboard_token') || ''; }

document.getElementById('token-input').value = getToken();
document.getElementById('token-save').addEventListener('click', function () {
  localStorage.setItem('phantom_dashboard_token', document.getElementById('token-input').value.trim());
  loadAll();
});

function authHeaders() {
  var token = getToken();
  return token ? { 'Authorization': 'Bearer ' + token } : {};
}

function mintFromUrl() {
  var parts = location.pathname.split('/');
  return decodeURIComponent(parts[parts.length - 1] || '');
}

function fmtUsd(n) {
  if (n == null) return '-';
  return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function fmtPct(n) {
  if (n == null) return '-';
  return (n > 0 ? '+' : '') + n.toFixed(1) + '%';
}

function fmtCheckpoint(seconds) {
  var labels = { 1800: '30分後', 3600: '1時間後', 86400: '24時間後' };
  return labels[seconds] || (seconds + '秒後');
}

function renderBanner(message) {
  var el = document.getElementById('banner');
  el.innerHTML = message ? '<div class="banner">' + message + '</div>' : '';
}

function renderHeader(latest, mint) {
  document.getElementById('token-mint').textContent = mint;
  if (!latest) {
    document.getElementById('token-title').textContent = '(通知履歴が見つかりません)';
    return;
  }
  var name = latest.name || '(名称不明)';
  var symbol = latest.symbol ? ' ($' + latest.symbol + ')' : '';
  document.getElementById('token-title').textContent = name + symbol;
}

function renderStatCards(latest) {
  var el = document.getElementById('stat-cards');
  if (!latest) { el.innerHTML = ''; return; }
  var stars = '⭐'.repeat(latest.star_count || 0) || '-';
  var cards = [
    ['スコア', (latest.score != null ? latest.score + '/100' : '-')],
    ['ユニーク買い手★', stars],
    ['Tier', latest.tier || '-'],
    ['出来高(5分)', fmtUsd(latest.volume_m5_usd)],
    ['流動性', fmtUsd(latest.liquidity_usd)],
    ['時価総額(通知時)', fmtUsd(latest.market_cap_usd)],
    ['上位10保有者集中度', latest.top10_holders_pct != null ? latest.top10_holders_pct.toFixed(1) + '%' : '-'],
    ['RugCheck警告数', latest.rugcheck_warn_count != null ? latest.rugcheck_warn_count : '-'],
    ['X連携', latest.has_twitter ? 'あり' : 'なし'],
    ['Telegram連携', latest.has_telegram ? 'あり' : 'なし'],
    ['発行者(creator)', latest.creator || '-'],
    ['なりすまし注意', latest.duplicate_name_reason || 'なし'],
  ];
  var html = '';
  cards.forEach(function (c) {
    html += '<div class="card"><div class="label">' + c[0] + '</div><div class="value small">' + c[1] + '</div></div>';
  });
  el.innerHTML = html;
}

function renderNotifications(rows) {
  var body = document.getElementById('notifications-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">まだ通知がありません</td></tr>';
    return;
  }
  var html = '';
  rows.forEach(function (r) {
    var time = r.notified_at ? new Date(r.notified_at).toLocaleString('ja-JP') : '-';
    var typeLabel = r.notification_type === 'followup' ? '<span class="tier-tag followup">追い通知</span>' : '通常';
    var tierClass = r.tier === 'HIGH' ? 'tier-HIGH' : (r.tier === 'WATCH' ? 'tier-WATCH' : '');
    var stars = '⭐'.repeat(r.star_count || 0);
    html += '<tr>' +
      '<td>' + time + '</td>' +
      '<td>' + typeLabel + '</td>' +
      '<td class="' + tierClass + '">' + (r.tier || '-') + '</td>' +
      '<td>' + (r.score != null ? r.score : '-') + '</td>' +
      '<td>' + stars + '</td>' +
      '<td>' + (r.elapsed_seconds != null ? r.elapsed_seconds + '秒' : '-') + '</td>' +
      '</tr>';
  });
  body.innerHTML = html;
}

function renderOutcomes(rows) {
  var body = document.getElementById('outcomes-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">まだ結果データがありません</td></tr>';
    return;
  }
  var html = '';
  rows.forEach(function (r) {
    var pctClass = r.change_pct == null ? '' : (r.change_pct >= 0 ? 'pct-up' : 'pct-down');
    html += '<tr>' +
      '<td>' + fmtCheckpoint(r.checkpoint_seconds) + '</td>' +
      '<td>' + fmtUsd(r.market_cap_at_notify_usd) + '</td>' +
      '<td>' + fmtUsd(r.market_cap_now_usd) + '</td>' +
      '<td class="' + pctClass + '">' + fmtPct(r.change_pct) + '</td>' +
      '</tr>';
  });
  body.innerHTML = html;
}

function loadAll() {
  var mint = mintFromUrl();
  fetch('/api/token/' + encodeURIComponent(mint), { headers: authHeaders() })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.supabase_configured === false) {
        renderBanner(data.error);
      } else {
        renderBanner('');
      }
      var notifications = data.notifications || [];
      var latest = notifications[0] || null;
      renderHeader(latest, mint);
      renderStatCards(latest);
      renderNotifications(notifications);
      renderOutcomes(data.outcomes || []);
    })
    .catch(function (e) { renderBanner('取得に失敗しました: ' + e); });
}

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>
"""


def main() -> None:
    setup_logger()

    if not config.DASHBOARD_API_TOKEN:
        logger.warning(
            "dashboard_server: DASHBOARD_API_TOKEN未設定のため認証なしで待受します。"
            "信頼できるローカルネットワーク以外では絶対に使用しないでください。"
        )
    if not supabase_client.is_configured():
        logger.warning(
            "dashboard_server: SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY未設定のため、"
            "データは空のまま表示されます(Supabaseセットアップ手順はREADME.md参照)"
        )

    server = ThreadingHTTPServer((config.DASHBOARD_SERVER_HOST, config.DASHBOARD_SERVER_PORT), DashboardRequestHandler)
    logger.info(
        "dashboard_server: http://%s:%s/ で待受を開始します (Ctrl+Cで終了)",
        config.DASHBOARD_SERVER_HOST,
        config.DASHBOARD_SERVER_PORT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("dashboard_server: ユーザーにより停止されました")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
