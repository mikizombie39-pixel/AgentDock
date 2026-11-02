-- 相手に送った文（開示の検査を通ったものだけが入る）
ALTER TABLE message ADD COLUMN body TEXT;
