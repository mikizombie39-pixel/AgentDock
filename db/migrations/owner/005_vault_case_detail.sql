-- 帳面の事例を品目と相談で引けるようにする
ALTER TABLE vault_case ADD COLUMN item_id INTEGER REFERENCES item(id);
ALTER TABLE vault_case ADD COLUMN consultation_id INTEGER REFERENCES consultation(id);
ALTER TABLE vault_case ADD COLUMN reason_text TEXT;
CREATE UNIQUE INDEX vault_case_one_per_consultation ON vault_case(consultation_id) WHERE consultation_id IS NOT NULL;
