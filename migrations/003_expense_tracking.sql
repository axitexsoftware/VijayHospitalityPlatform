CREATE TABLE IF NOT EXISTS expense_entries(
 id INTEGER PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN('operating','capital')),
 category TEXT NOT NULL,
 description TEXT NOT NULL,
 amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
 incurred_on TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_expense_entries_date ON expense_entries(incurred_on,kind);