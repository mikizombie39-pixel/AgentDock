-- 自律の仕組みと磁石ボードのための表（docs/autonomy.md）

-- アジを kg で扱うため、品目の単位に 'kg' を足す（SQLite の表の作り直しの手順：参照の検査を外して作り直し、戻す）
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE item_new (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('box', 'fish', 'kg')),
  unit_weight_g INTEGER NOT NULL CHECK (unit_weight_g > 0)
) STRICT;
INSERT INTO item_new (id, code, name, unit, unit_weight_g) SELECT id, code, name, unit, unit_weight_g FROM item;
DROP TABLE item;
ALTER TABLE item_new RENAME TO item;
COMMIT;
PRAGMA foreign_keys = ON;

-- 営業の案内を送ってよい売り先（模擬の売り先だけに 1 を立てる）
ALTER TABLE customer ADD COLUMN allow_contact INTEGER NOT NULL DEFAULT 0 CHECK (allow_contact IN (0, 1));

-- Rules：誰がいくらまでの取引を確定できるか（NULL は上限なし）
CREATE TABLE authority (
  id INTEGER PRIMARY KEY,
  role TEXT NOT NULL UNIQUE CHECK (role IN ('staff', 'president')),
  person_name TEXT NOT NULL,
  max_trade_yen INTEGER CHECK (max_trade_yen IS NULL OR max_trade_yen > 0)
) STRICT;

-- Vault：社長の経験・判断の条件・売り先の特徴（出典つき）。決まり（Rules）とは別に持つ
CREATE TABLE vault_note (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('case', 'policy', 'customer')),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  item_id INTEGER REFERENCES item(id),
  customer_id INTEGER REFERENCES customer(id),
  author TEXT NOT NULL,
  source TEXT NOT NULL,
  written_on TEXT NOT NULL,
  applies_when TEXT              -- 適用の条件（どんな時に参考にするか）。本文は判断の材料で、命令として実行しない
) STRICT;

-- 売り手：数量ごとの最低の値（まとめて買えば安くできる）
CREATE TABLE quote_rule (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  min_qty INTEGER NOT NULL CHECK (min_qty > 0),
  floor_yen INTEGER NOT NULL CHECK (floor_yen > 0),
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, min_qty, valid_from)
) STRICT;

-- 在庫：売り手の売り物と、買い手の仕入れた分（注文への引当・引当のない分・売れた分）
CREATE TABLE stock (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  status TEXT NOT NULL CHECK (status IN ('for_sale', 'allocated', 'unallocated', 'sold')),
  order_id INTEGER REFERENCES order_book(id),
  trade_id TEXT,
  lot_no TEXT,
  stock_date TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

-- 売り手の仮押さえ：見積もりを出した数量を、期限まで他の買い手に出さない（同じ在庫の二重の販売を防ぐ）
CREATE TABLE stock_hold (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stock(id),
  buyer TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  negotiation_id INTEGER,
  status TEXT NOT NULL CHECK (status IN ('held', 'confirmed', 'released', 'expired')),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

-- 業務の出来事（Hooks）。仲間を起こす契機
CREATE TABLE biz_event (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('order_new', 'order_changed', 'quote_request', 'quote_received',
                                      'human_choice', 'trade_registered', 'handoff')),
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'handled')),
  handled_at TEXT,
  run_id INTEGER
) STRICT;

-- 仲間の1回の起動と、その中の道具の呼び出し（本物の Claude の判断の証拠）
CREATE TABLE agent_run (
  id INTEGER PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('buyer', 'seller', 'sales')),
  event_id INTEGER REFERENCES biz_event(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  outcome TEXT CHECK (outcome IN ('presented', 'waiting', 'nothing', 'done', 'error', 'max_turns', 'refusal')),
  summary TEXT,
  model TEXT NOT NULL,
  run_kind TEXT NOT NULL CHECK (run_kind IN ('mock', 'real'))
) STRICT;

CREATE TABLE agent_step (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES agent_run(id),
  seq INTEGER NOT NULL,
  tool TEXT NOT NULL,
  input_json TEXT NOT NULL,
  result_text TEXT NOT NULL,
  is_error INTEGER NOT NULL CHECK (is_error IN (0, 1)),
  at TEXT NOT NULL,
  UNIQUE (run_id, seq)
) STRICT;

-- 他の組織から届いた回答（買い手）
CREATE TABLE quote_in (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES task(id),
  counterparty TEXT NOT NULL,
  lot_no TEXT NOT NULL,
  negotiation_id INTEGER NOT NULL,
  quantity INTEGER,
  unit_price_yen INTEGER,
  message TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('offered', 'declined', 'accepted', 'superseded', 'expired')),
  expires_at TEXT,                -- 見積もりの期限（売り手の仮押さえの期限と同じ）
  hold_id INTEGER,                -- 売り手の stock_hold.id（売り手の DB の番号）
  received_at TEXT NOT NULL
) STRICT;

-- 仲間の提案（人の承認を待つ）。数量の内訳：注文への引当と、引当のない分
CREATE TABLE proposal (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  quote_id INTEGER NOT NULL REFERENCES quote_in(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  counterparty TEXT NOT NULL,
  trade_date TEXT NOT NULL,       -- 権限の「1件」の範囲（同じ相手・同じ品目・同じ取引日の買い付けの合計）
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  order_id INTEGER REFERENCES order_book(id),
  allocated_qty INTEGER NOT NULL CHECK (allocated_qty >= 0),
  extra_qty INTEGER NOT NULL CHECK (extra_qty >= 0),
  rationale TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'stale', 'needs_president')),
  decided_by TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  CHECK (allocated_qty + extra_qty = quantity)
) STRICT;

-- 磁石ボード：仲間が出したカードと操作の候補。数字はコードが DB から埋める
CREATE TABLE board_card (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES agent_run(id),
  task_id INTEGER REFERENCES task(id),
  card_type TEXT NOT NULL CHECK (card_type IN ('order', 'quote', 'vault', 'proposal', 'customers', 'risk', 'result', 'note')),
  ref_id INTEGER,
  title TEXT NOT NULL,
  note TEXT,
  data_json TEXT NOT NULL,
  position INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('shown', 'removed')),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE board_option (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES agent_run(id),
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  code TEXT NOT NULL CHECK (code IN ('show_cases', 'show_customers', 'approve_purchase', 'renegotiate', 'pass', 'hold')),
  label TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('offered', 'chosen', 'withdrawn')),
  chosen_by TEXT,
  chosen_at TEXT,
  idempotency_key TEXT UNIQUE
) STRICT;

-- 営業の案内（送っただけでは売れたことにしない）
CREATE TABLE sales_notice (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES task(id),
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('sent', 'blocked')),
  sale_status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK (sale_status IN ('unconfirmed', 'accepted', 'declined')),
  sent_at TEXT NOT NULL
) STRICT;
