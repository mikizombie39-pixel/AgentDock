-- 仲間へのチャット（聞く・頼む・2026-11-04 デイブ）：1回の質問ごとに1行。業務の状態は変えない（チャットが書くのはこの表と費用の記録だけ）
CREATE TABLE chat_turn (
  id INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,    -- 同じキーの再送は前の答え（二重に処理しない）
  item_code TEXT NOT NULL,
  question TEXT NOT NULL,                  -- 担当者の言葉（失敗しても残す）
  status TEXT NOT NULL CHECK (status IN ('running', 'answered', 'failed')),
  asked_at TEXT NOT NULL,
  answered_at TEXT,
  run_kind TEXT NOT NULL,                  -- mock・real
  model TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  context_json TEXT,                       -- AI に渡した文脈（今の仕事・直前の対話の番号）
  fetched_json TEXT,                       -- この回に道具が取得した記録（ref・種類・文・確認の時刻）
  steps_json TEXT,                         -- 道具の呼び出し
  raw_json TEXT,                           -- AI が出した答えそのもの
  answer_json TEXT,                        -- サーバーの検査の後に画面へ出す答え
  dropped_json TEXT,                       -- 捨てた根拠・替えた文と理由
  executed_json TEXT,                      -- 依頼を［この内容で頼む］で実行した時の結果（既存の処理の返り値）
  error TEXT
) STRICT;
