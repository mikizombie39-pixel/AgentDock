-- 売り先の当てのない在庫を仕事として扱う（2026-11-04 デイブ：料亭 2本を注文なしで登録した後、AI が何もしなかった）
-- 1. 在庫の仕入れ値（1単位）：引当のない在庫を譲る時の最低の値と、売れ残りの損の元
-- 2. 注文の無い売り先に回すつもりの数量：人が承認した案の行き先（未打診の売り先）を在庫に残す
-- 3. 引当のない在庫になった時刻と、AI が注文に充てた起動：今の状態をある時刻の形に組み直す（記録の再生）
-- 4. 引当のない在庫をほかの仲買に譲る時にかかる費用（項目ごと・1単位）：最低の値 ＝ 仕入れ値 ＋ 費用の合計
-- 5. 引当のない在庫の扱いを、AI が人に問う（売り先に注文を確かめる・ほかの仲買に譲る・そのまま持つ）
ALTER TABLE stock ADD COLUMN unit_cost_yen INTEGER CHECK (unit_cost_yen IS NULL OR unit_cost_yen > 0);
ALTER TABLE stock ADD COLUMN intended_customer_id INTEGER REFERENCES customer(id);
ALTER TABLE stock ADD COLUMN unallocated_at TEXT;
ALTER TABLE stock ADD COLUMN allocated_by_run INTEGER REFERENCES agent_run(id);

CREATE TABLE resale_cost (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  name TEXT NOT NULL,
  cost_yen_per_unit INTEGER NOT NULL CHECK (cost_yen_per_unit >= 0),
  valid_from TEXT NOT NULL,
  UNIQUE (item_id, name, valid_from)
) STRICT;

CREATE TABLE stock_question (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),      -- 問うた時の引当のない数（コードが数えた）
  question TEXT NOT NULL,
  choices_json TEXT NOT NULL,                          -- [{code, label}]（code と label はコードが決める）
  recommended TEXT,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'stale', 'closed')),
  answer TEXT,
  answered_by TEXT,
  answered_at TEXT,
  idempotency_key TEXT UNIQUE,
  run_id INTEGER REFERENCES agent_run(id),
  created_at TEXT NOT NULL,
  closed_at TEXT
) STRICT;
