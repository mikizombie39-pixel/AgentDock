-- 売れ方の記録：売り先ごと・日ごとの「注文の数」と「実際に売れた数」
-- 仲間は「安い日に、注文の何倍売れたか」を根拠に、注文より多めに買う案を出す
CREATE TABLE sales_history (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  sale_date TEXT NOT NULL,
  ordered_qty INTEGER NOT NULL CHECK (ordered_qty > 0),
  sold_qty INTEGER NOT NULL CHECK (sold_qty >= 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  UNIQUE (customer_id, item_id, sale_date)
) STRICT;
