-- 問い合わせと返事に、相手に伝えてよい短い理由を持たせる（Claude が書き、送る前に開示の検査を通す。サーバーは理由を作らない）
ALTER TABLE network_message ADD COLUMN reason TEXT;
