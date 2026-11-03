-- 取引の整合性と磁石ボード（SAL の指示・2026-11-03）
-- 007 で作った磁石ボードと提案の表は、どの DB でもまだ使っていないので作り直す（参照する表の側から外す）
DROP TABLE IF EXISTS board_option;
DROP TABLE IF EXISTS board_card;
DROP TABLE IF EXISTS proposal;
DROP TABLE IF EXISTS quote_in;

-- 加工の工程と費用（販売先ではなく工程に結びつける）
CREATE TABLE process_step (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  name TEXT NOT NULL,
  cost_yen_per_unit INTEGER NOT NULL CHECK (cost_yen_per_unit >= 0),
  valid_from TEXT NOT NULL
) STRICT;

-- 売り先ごとに、どの工程を通るか。status が unconfirmed の行は、費用を確定値として扱わない
CREATE TABLE route_process (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  destination TEXT NOT NULL,                 -- 売り先の名前（自社の得意先）か、組織のコード（仲買どうし）
  destination_kind TEXT NOT NULL CHECK (destination_kind IN ('customer', 'broker')),
  process_step_id INTEGER REFERENCES process_step(id),
  status TEXT NOT NULL CHECK (status IN ('confirmed', 'unconfirmed')),
  note TEXT,
  UNIQUE (item_id, destination)
) STRICT;

-- 担当者が事前に承認した売買の条件。AI はこの範囲で交渉し、条件付きの合意ができる。超えれば担当者の承認待ち
CREATE TABLE preapproval (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  max_unit_price_yen INTEGER CHECK (max_unit_price_yen IS NULL OR max_unit_price_yen > 0),
  min_unit_price_yen INTEGER CHECK (min_unit_price_yen IS NULL OR min_unit_price_yen > 0),
  spec TEXT NOT NULL,
  deliver_by TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'used', 'cancelled'))
) STRICT;

-- 1つの注文から始まる案件（磁石ボードの単位）。版は、ボードを出し直すたびに上がる
CREATE TABLE deal_case (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES order_book(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  trade_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'presented', 'registered', 'incomplete', 'rejected', 'held', 'stale', 'needs_president')),
  version INTEGER NOT NULL DEFAULT 0,
  last_run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (order_id)
) STRICT;

-- 磁石ボードのカード（決められた種類だけ。中身の数字はコードが DB から埋める）
CREATE TABLE board_card (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  version INTEGER NOT NULL,
  position INTEGER NOT NULL,
  card_type TEXT NOT NULL CHECK (card_type IN ('order', 'quote', 'agreement', 'vault', 'customer', 'post', 'market', 'risk', 'note')),
  ref_id INTEGER,
  note TEXT,
  data_json TEXT NOT NULL
) STRICT;

-- 磁石ボードの案。plan は Claude が選んだ条件の番号と配分、numbers はコードの計算、fingerprint は表示した時の状態
CREATE TABLE board_option (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  version INTEGER NOT NULL,
  code TEXT NOT NULL,
  label TEXT NOT NULL,
  reason TEXT NOT NULL,
  risk TEXT NOT NULL,
  plan_json TEXT NOT NULL,
  numbers_json TEXT NOT NULL,
  recommended INTEGER NOT NULL CHECK (recommended IN (0, 1)),
  executable INTEGER NOT NULL CHECK (executable IN (0, 1)),
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('offered', 'chosen', 'withdrawn')),
  UNIQUE (case_id, version, code)
) STRICT;

-- 担当者の判断（承認・拒否・保留）。同じ操作の二重の実行は idempotency_key で止める
CREATE TABLE case_decision (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  option_id INTEGER REFERENCES board_option(id),
  action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'hold')),
  actor TEXT NOT NULL,
  at TEXT NOT NULL,
  result TEXT NOT NULL,
  detail TEXT,
  idempotency_key TEXT NOT NULL UNIQUE
) STRICT;

-- 確定した（または確定の途中の）売買。権限の「1件」の合計はここの買いから数える（agentdock/authority.py）
CREATE TABLE proposal (
  id INTEGER PRIMARY KEY,
  case_id INTEGER REFERENCES deal_case(id),
  option_id INTEGER REFERENCES board_option(id),
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  item_id INTEGER NOT NULL REFERENCES item(id),
  counterparty TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  trade_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('approved', 'registered', 'incomplete', 'cancelled')),
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
) STRICT;
