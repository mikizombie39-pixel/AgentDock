-- 持ち主の DB（漁協・仲買の各社に1つずつ・同じ形）
-- 書き方の決まり：STRICT・INTEGER PRIMARY KEY・金額は整数の円・時刻は ISO 8601 の文字列・真偽は 0/1・状態は CHECK

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
) STRICT;

-- 1. 蓄積データ -----------------------------------------------------------

CREATE TABLE owner (
  id INTEGER PRIMARY KEY CHECK (id = 1),          -- 1行だけ
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('buyer', 'seller')),
  person_name TEXT NOT NULL,                       -- 持ち主（人）
  companion_name TEXT                              -- 仲間（AI）の呼び名。未定なら NULL
) STRICT;

CREATE TABLE item (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('box', 'fish')),
  unit_weight_g INTEGER NOT NULL CHECK (unit_weight_g > 0)
) STRICT;

CREATE TABLE customer (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('restaurant', 'supermarket', 'processor'))
) STRICT;

CREATE TABLE price_history (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  trade_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('auction', 'tender', 'negotiated')),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  source TEXT NOT NULL CHECK (source IN ('public_result', 'own_trade')),
  lot_no TEXT NOT NULL,
  UNIQUE (item_id, trade_date, source, lot_no)
) STRICT;

CREATE TABLE sales_price (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, customer_id, valid_from)
) STRICT;

CREATE TABLE cost_rule (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  process_cost_yen INTEGER NOT NULL CHECK (process_cost_yen >= 0),
  target_profit_yen INTEGER NOT NULL CHECK (target_profit_yen >= 0),
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, valid_from)
) STRICT;

-- 売り手（漁協）の決まり：下限と「明日の水揚げが多い日は今日の値を下げすぎない」
CREATE TABLE seller_rule (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  base_floor_yen INTEGER NOT NULL CHECK (base_floor_yen > 0),
  high_landing_qty INTEGER NOT NULL CHECK (high_landing_qty > 0),  -- 明日の見込みがこの数以上なら
  hold_up_yen INTEGER NOT NULL CHECK (hold_up_yen >= 0),           -- 下限をこの分だけ上げる
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, valid_from)
) STRICT;

-- 買い手の方針：安い日に注文より多めに押さえる数（C社の「2箱多め」など）
CREATE TABLE buyer_rule (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  extra_qty_on_cheap_day INTEGER NOT NULL DEFAULT 0 CHECK (extra_qty_on_cheap_day >= 0),
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, valid_from)
) STRICT;

CREATE TABLE order_book (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  sell_price_yen INTEGER NOT NULL CHECK (sell_price_yen > 0),
  order_date TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'partly_filled', 'filled', 'cancelled'))
) STRICT;

-- 水揚げ（漁協）：今日の実績と明日の見込み
CREATE TABLE landing (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  landing_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('actual', 'forecast')),
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  size_note TEXT,
  caught_at TEXT,
  area TEXT,
  water_temp_c REAL,
  UNIQUE (item_id, landing_date, kind)
) STRICT;

-- 2. 届ける資料（漁協が作り、仲買の全員に同じものを届ける） ---------------

CREATE TABLE broadcast (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  title TEXT NOT NULL,
  body_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE broadcast_delivery (
  id INTEGER PRIMARY KEY,
  broadcast_id INTEGER NOT NULL REFERENCES broadcast(id),
  recipient TEXT NOT NULL,
  delivered_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE (broadcast_id, recipient)
) STRICT;

-- 仲買が受け取った資料
CREATE TABLE inbox_broadcast (
  id INTEGER PRIMARY KEY,
  broadcast_id INTEGER NOT NULL UNIQUE,            -- 漁協の broadcast.id
  sender TEXT NOT NULL,
  received_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  body_json TEXT NOT NULL
) STRICT;

-- 漁協から届いたせりの結果（他社の名前は持たない）
CREATE TABLE auction_result_in (
  id INTEGER PRIMARY KEY,
  connector TEXT NOT NULL CHECK (connector IN ('reference_market', 'talos_test')),
  result_id TEXT NOT NULL,
  item_id INTEGER NOT NULL REFERENCES item(id),
  lot_no TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  is_own INTEGER NOT NULL CHECK (is_own IN (0, 1)),
  received_at TEXT NOT NULL,
  UNIQUE (connector, result_id)
) STRICT;

-- 3. 判断の基準（コードだけが書く） ---------------------------------------

CREATE TABLE criteria (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  kind TEXT NOT NULL CHECK (kind IN ('ceiling', 'floor')),
  computed_at TEXT NOT NULL,
  limit_yen INTEGER NOT NULL CHECK (limit_yen > 0),  -- 上限（買い手）または下限（売り手）
  market_median_yen INTEGER,
  market_low_yen INTEGER,
  market_high_yen INTEGER,
  last_year_median_yen INTEGER,
  last_year_low_yen INTEGER,
  last_year_high_yen INTEGER,
  sample_days INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  last_year_sample_count INTEGER NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE plan (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  kind TEXT NOT NULL CHECK (kind IN ('tender', 'negotiation', 'purchase')),
  trade_date TEXT NOT NULL,
  max_unit_price_yen INTEGER NOT NULL CHECK (max_unit_price_yen > 0),
  max_quantity INTEGER NOT NULL CHECK (max_quantity > 0),
  criteria_id INTEGER REFERENCES criteria(id),
  status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'superseded')),
  approved_at TEXT
) STRICT;
CREATE UNIQUE INDEX plan_one_approved ON plan(item_id, kind, trade_date) WHERE status = 'approved';

-- 4. 判断と根拠 -------------------------------------------------------------

CREATE TABLE task (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('tender', 'negotiation', 'purchase')),
  item_id INTEGER NOT NULL REFERENCES item(id),
  state TEXT NOT NULL CHECK (state IN (
    'delegated', 'running', 'negotiating', 'awaiting_owner', 'revalidating', 'resumed',
    'awaiting_final_approval', 'registering', 'done', 'no_deal', 'incomplete')),
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE decision (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('bid', 'offer', 'counter', 'accept', 'decline', 'escalate', 'buy')),
  unit_price_yen INTEGER,
  quantity INTEGER,
  criteria_id INTEGER REFERENCES criteria(id),
  within_plan INTEGER NOT NULL CHECK (within_plan IN (0, 1)),   -- コードが判定する
  rationale TEXT NOT NULL,
  model TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE decision_evidence (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decision(id),
  kind TEXT NOT NULL CHECK (kind IN ('price_history', 'order', 'sales_price', 'cost_rule', 'vault_case', 'broadcast', 'auction_result', 'landing')),
  ref_id INTEGER,
  summary TEXT NOT NULL
) STRICT;

CREATE TABLE message (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES task(id),
  seq INTEGER NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('out', 'in')),
  unit_price_yen INTEGER,
  quantity INTEGER,
  sent_at TEXT NOT NULL,
  passed_disclosure_check INTEGER NOT NULL CHECK (passed_disclosure_check IN (0, 1)),
  UNIQUE (task_id, seq)
) STRICT;

-- 5. 相談と承認 -------------------------------------------------------------

CREATE TABLE consultation_group (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'stale'))
) STRICT;

CREATE TABLE consultation (
  id INTEGER PRIMARY KEY,
  group_id INTEGER REFERENCES consultation_group(id),
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'stale')),
  chosen_option_id INTEGER,
  reason_text TEXT,
  answered_at TEXT,
  idempotency_key TEXT UNIQUE,
  UNIQUE (task_id, task_version)
) STRICT;

CREATE TABLE consultation_option (
  id INTEGER PRIMARY KEY,
  consultation_id INTEGER NOT NULL REFERENCES consultation(id),
  code TEXT NOT NULL CHECK (code IN (
    'prefer_restaurant', 'prefer_supermarket', 'split', 'pass', 'hold', 'buy_extra', 'order_only')),
  label TEXT NOT NULL,
  valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
  UNIQUE (consultation_id, code)
) STRICT;

CREATE TABLE approval (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES task(id),
  terms_fingerprint TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  counterparty TEXT NOT NULL,
  connector TEXT NOT NULL CHECK (connector IN ('reference_market', 'talos_test')),
  status TEXT NOT NULL CHECK (status IN ('requested', 'approved', 'rejected', 'stale')),
  decided_at TEXT,
  UNIQUE (task_id, terms_fingerprint)
) STRICT;

-- 6. 登録・照合・記録 -------------------------------------------------------

CREATE TABLE ledger_entry (
  id INTEGER PRIMARY KEY,
  connector TEXT NOT NULL CHECK (connector IN ('reference_market', 'talos_test')),
  external_trade_id TEXT NOT NULL,
  item_id INTEGER NOT NULL REFERENCES item(id),
  unit_price_yen INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  registered_at TEXT NOT NULL,
  UNIQUE (connector, external_trade_id)
) STRICT;

CREATE TABLE reconciliation (
  id INTEGER PRIMARY KEY,
  connector TEXT NOT NULL,
  external_trade_id TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('match', 'mismatch', 'pending')),
  checked_at TEXT NOT NULL,
  detail TEXT
) STRICT;

CREATE TABLE vault_case (
  id INTEGER PRIMARY KEY,
  file_path TEXT NOT NULL UNIQUE,
  task_id INTEGER REFERENCES task(id),
  situation TEXT NOT NULL,
  chosen_option_code TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE audit_event (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor TEXT NOT NULL CHECK (actor IN ('companion', 'owner', 'system', 'hook')),
  event TEXT NOT NULL,
  task_id INTEGER,
  detail TEXT
) STRICT;

CREATE TABLE api_usage (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_micro_usd INTEGER NOT NULL,
  run_kind TEXT NOT NULL CHECK (run_kind IN ('mock', 'scene_check', 'full_run', 'recording'))
) STRICT;
