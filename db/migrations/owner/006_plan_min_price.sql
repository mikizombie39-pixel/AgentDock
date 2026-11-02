-- 計画に「この値より下では売らない」（売り手の下限）を持たせる。上限と下限のどちらかは必ず持つ
CREATE TABLE plan_new (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  kind TEXT NOT NULL CHECK (kind IN ('tender', 'negotiation', 'purchase')),
  trade_date TEXT NOT NULL,
  max_unit_price_yen INTEGER CHECK (max_unit_price_yen IS NULL OR max_unit_price_yen > 0),
  max_quantity INTEGER NOT NULL CHECK (max_quantity > 0),
  criteria_id INTEGER REFERENCES criteria(id),
  status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'superseded')),
  approved_at TEXT,
  min_unit_price_yen INTEGER CHECK (min_unit_price_yen IS NULL OR min_unit_price_yen > 0),
  CHECK (max_unit_price_yen IS NOT NULL OR min_unit_price_yen IS NOT NULL)
) STRICT;
INSERT INTO plan_new (id, item_id, kind, trade_date, max_unit_price_yen, max_quantity, criteria_id, status, approved_at)
  SELECT id, item_id, kind, trade_date, max_unit_price_yen, max_quantity, criteria_id, status, approved_at FROM plan;
DROP TABLE plan;
ALTER TABLE plan_new RENAME TO plan;
CREATE UNIQUE INDEX plan_one_approved ON plan(item_id, kind, trade_date) WHERE status = 'approved';
