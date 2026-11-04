-- Vault をリポジトリのファイル（vault/<会社>/<分類>/<識別子>.md）へ移す（2026-11-04 デイブ）
-- 1. vault_note はファイルから作る索引にする（本文の正本はファイル）。識別子・分類・適用期間・状態を持つ
-- 2. 社長の指示（directive）は Vault の社長の指示の記録から作る。元の記録の識別子を持つ
ALTER TABLE vault_note ADD COLUMN vault_id TEXT;
ALTER TABLE vault_note ADD COLUMN category TEXT;
ALTER TABLE vault_note ADD COLUMN valid_from TEXT;
ALTER TABLE vault_note ADD COLUMN valid_to TEXT;
ALTER TABLE vault_note ADD COLUMN status TEXT;
CREATE UNIQUE INDEX vault_note_vault_id ON vault_note(vault_id) WHERE vault_id IS NOT NULL;
ALTER TABLE directive ADD COLUMN vault_id TEXT;
