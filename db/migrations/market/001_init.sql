-- 参照用の市場の DB（会期中に作成・試験はすべてここで通す）
-- 売り手の帳簿と買い手の帳簿を別の表で持ち、片側だけの失敗を試せるようにする

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
) STRICT;

CREATE TABLE lot (
  id INTEGER PRIMARY KEY,
  lot_no TEXT NOT NULL UNIQUE,
  item_code TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  seller TEXT NOT NULL,
  seller_kind TEXT NOT NULL CHECK (seller_kind IN ('coop', 'buyer')),  -- 'buyer' は仲買どうしの相対
  method TEXT NOT NULL CHECK (method IN ('auction', 'tender', 'negotiation')),
  trade_date TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE tender (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL UNIQUE REFERENCES lot(id),
  status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'confirmed')),
  opens_at TEXT NOT NULL,
  closes_at TEXT NOT NULL
) STRICT;

CREATE TABLE tender_bid (
  id INTEGER PRIMARY KEY,
  tender_id INTEGER NOT NULL REFERENCES tender(id),
  bidder TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  submitted_at TEXT NOT NULL,
  UNIQUE (tender_id, bidder)
) STRICT;

CREATE TABLE tender_award (
  id INTEGER PRIMARY KEY,
  tender_id INTEGER NOT NULL REFERENCES tender(id),
  bidder TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  UNIQUE (tender_id, bidder)
) STRICT;

CREATE TABLE negotiation (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES lot(id),
  buyer TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  rounds INTEGER NOT NULL DEFAULT 0 CHECK (rounds BETWEEN 0 AND 6),
  status TEXT NOT NULL CHECK (status IN ('open', 'agreed', 'no_deal')),
  updated_at TEXT NOT NULL,
  UNIQUE (lot_id, buyer)
) STRICT;

CREATE TABLE trade (
  id INTEGER PRIMARY KEY,
  trade_id TEXT NOT NULL UNIQUE,
  lot_id INTEGER NOT NULL REFERENCES lot(id),
  seller TEXT NOT NULL,
  buyer TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  version INTEGER NOT NULL,
  terms_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('awaiting_approval', 'registered', 'incomplete')),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE trade_approval (
  id INTEGER PRIMARY KEY,
  trade_id TEXT NOT NULL REFERENCES trade(trade_id),
  party TEXT NOT NULL,
  terms_fingerprint TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  UNIQUE (trade_id, party)
) STRICT;

CREATE TABLE seller_ledger (
  id INTEGER PRIMARY KEY,
  trade_id TEXT NOT NULL UNIQUE REFERENCES trade(trade_id),
  seller TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE buyer_ledger (
  id INTEGER PRIMARY KEY,
  trade_id TEXT NOT NULL UNIQUE REFERENCES trade(trade_id),
  buyer TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
) STRICT;

-- せりの結果（漁協が仲買へ送る）。人が行ったせりの結果を、公開の値として全員へ
CREATE TABLE auction_result (
  id INTEGER PRIMARY KEY,
  result_id TEXT NOT NULL UNIQUE,
  lot_no TEXT NOT NULL,
  item_code TEXT NOT NULL,
  buyer TEXT NOT NULL,
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  trade_date TEXT NOT NULL
) STRICT;
