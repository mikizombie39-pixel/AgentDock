-- 他の仲買からの相対の申し込み（Agent Network の掲示）。買いたい・譲りたいの条件を、出した組織の名で掲げる
-- 申し込みの文は、出す前に出した組織の DB で開示の検査を通す（agentdock/market.py の post_request）
CREATE TABLE deal_request (
  id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  item_code TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  min_qty INTEGER NOT NULL CHECK (min_qty > 0),   -- これより少ない数量では応じない（まとめての条件）
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  message TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'withdrawn', 'filled')),
  created_at TEXT NOT NULL,
  CHECK (min_qty <= quantity)
) STRICT;
