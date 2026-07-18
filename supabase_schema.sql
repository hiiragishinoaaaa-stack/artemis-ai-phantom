-- ARTEMIS Phantom Sniper 用のSupabaseスキーマ。
--
-- 使い方: Supabaseでプロジェクトを作成した後、左メニューの「SQL Editor」→
-- 「New query」でこのファイルの中身を全部貼り付けて実行(Run)するだけで、
-- 通知履歴・結果トラッキング・発行者ブラックリストの3テーブルと、
-- 分析用のビューが1回で全部できる。
--
-- ここで作るテーブルはRLS(Row Level Security)を有効化していない。
-- 書き込み・読み取りは常にVPS上のPythonコード(supabase_client.py /
-- dashboard_server.py)がservice_role키(秘密鍵、.envにのみ保存)経由で
-- 行う想定で、ブラウザから直接匿名キーでアクセスする使い方はしないため。

-- --- 通知履歴(HIGH/WATCH通知、および★3つ到達の追い通知) ---
create table if not exists notifications (
    id bigint generated always as identity primary key,
    mint text not null,
    name text not null default '',
    symbol text not null default '',
    notification_type text not null default 'primary', -- 'primary' | 'followup'
    tier text not null,                                  -- 'LOW' | 'WATCH' | 'HIGH'
    score int not null,
    unique_buyers_m5 int not null default 0,
    star_count int not null default 0,                   -- 0〜3
    buys_m5 int not null default 0,
    sells_m5 int not null default 0,
    volume_m5_usd numeric not null default 0,
    liquidity_usd numeric not null default 0,
    price_change_m5_pct numeric not null default 0,
    market_cap_usd numeric not null default 0,
    rugcheck_danger boolean not null default false,
    rugcheck_warn_count int not null default 0,
    creator text not null default '',
    elapsed_seconds int not null default 0,
    notified_at timestamptz not null default now()
);

create index if not exists notifications_mint_idx on notifications (mint);
create index if not exists notifications_notified_at_idx on notifications (notified_at desc);

-- --- 通知後の結果トラッキング(30分/1時間/24時間後の時価総額変化) ---
create table if not exists outcomes (
    id bigint generated always as identity primary key,
    mint text not null,
    name text not null default '',
    symbol text not null default '',
    notified_tier text not null,
    notified_score int not null,
    checkpoint_seconds int not null,
    market_cap_at_notify_usd numeric not null default 0,
    market_cap_now_usd numeric not null default 0,
    change_pct numeric not null default 0,
    recorded_at timestamptz not null default now()
);

create index if not exists outcomes_mint_idx on outcomes (mint);
create index if not exists outcomes_recorded_at_idx on outcomes (recorded_at desc);

-- --- 発行者ブラックリスト(自己学習、creator_blocklist.pyのJSONと同じ内容) ---
create table if not exists creator_blocklist (
    creator text primary key,
    reason text not null default '',
    recorded_at timestamptz not null default now()
);

-- --- 分析用ビュー: 各通知に、そのトークンの最新の結果(あれば)を結合 ---
-- 例:
--   select tier, star_count, avg(change_pct), count(*)
--   from v_notification_latest_outcome
--   group by tier, star_count order by tier, star_count;
create or replace view v_notification_latest_outcome as
select
    n.*,
    o.checkpoint_seconds as latest_checkpoint_seconds,
    o.change_pct as latest_change_pct
from notifications n
left join lateral (
    select checkpoint_seconds, change_pct
    from outcomes o
    where o.mint = n.mint
    order by o.recorded_at desc
    limit 1
) o on true;
