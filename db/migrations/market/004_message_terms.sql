-- 返事の条件を2つ以上示せるようにする（例：10kg ならこの値・15kg まとめてならこの値）
-- 条件が2つ以上の時だけ 数量と値の組の並び（JSON） を持つ。1つの時は quantity・unit_price_yen の列だけで持つ（agentdock/network.py の terms_of）
ALTER TABLE network_message ADD COLUMN terms_json TEXT;
