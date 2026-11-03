-- 条件の番号と条件付きの合意（SAL の指示・2026-11-03）
-- 問い合わせに「買いたい・売りたい」の向きを持たせる（返事の条件の売り手と買い手を決めるため）
ALTER TABLE network_message ADD COLUMN side TEXT CHECK (side IS NULL OR side IN ('buy', 'sell'));

-- 返事で示された条件の1つ1つ。示しただけでは在庫の確保ではない（hold_ref は売り手の DB の仮押さえ）
CREATE TABLE offer_term (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES network_message(id),
  seller TEXT NOT NULL,
  buyer TEXT NOT NULL,
  item_code TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  valid_until TEXT NOT NULL,
  hold_ref INTEGER,
  status TEXT NOT NULL CHECK (status IN ('offered', 'confirmed', 'released', 'expired')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (seller != buyer)
) STRICT;

-- AI どうしの条件付きの合意。担当者の承認と、全ての組織の条件の再検証の後にだけ confirmed になる
CREATE TABLE agreement (
  id INTEGER PRIMARY KEY,
  term_id INTEGER NOT NULL UNIQUE REFERENCES offer_term(id),
  seller TEXT NOT NULL,
  buyer TEXT NOT NULL,
  item_code TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  condition TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('conditional', 'needs_staff', 'confirmed', 'cancelled', 'expired')),
  valid_until TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
