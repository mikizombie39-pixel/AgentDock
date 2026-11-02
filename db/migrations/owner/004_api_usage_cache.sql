-- 呼び出しの費用の計算に、キャッシュの読み出しと書き込みのトークン数を足す
ALTER TABLE api_usage ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_usage ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0;
