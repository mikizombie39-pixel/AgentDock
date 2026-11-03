-- 組織の間の問い合わせと返事（Agent Network）。送る前に、送る側の DB で開示の検査を通す（agentdock/network.py）
-- 届いた時に、受け手の DB に出来事（問い合わせ＝quote_request・返事＝quote_received）を書く
CREATE TABLE network_message (
  id INTEGER PRIMARY KEY,
  from_owner TEXT NOT NULL,
  to_owner TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('inquiry', 'offer', 'decline')),   -- 問い合わせ・条件を示す返事・断る返事
  item_code TEXT NOT NULL,
  quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
  unit_price_yen INTEGER CHECK (unit_price_yen IS NULL OR unit_price_yen > 0),
  body TEXT NOT NULL,
  in_reply_to INTEGER REFERENCES network_message(id),
  request_id INTEGER REFERENCES deal_request(id),   -- 掲示への問い合わせなら、その掲示
  trade_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK (from_owner != to_owner),
  CHECK ((kind = 'inquiry') = (in_reply_to IS NULL))
) STRICT;
