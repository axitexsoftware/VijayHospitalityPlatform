CREATE TABLE IF NOT EXISTS guest_enquiries(
 id INTEGER PRIMARY KEY,
 property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK(kind IN('room','event','general')),
 name TEXT NOT NULL,
 phone TEXT NOT NULL,
 email TEXT,
 preferred_date TEXT,
 guest_count INTEGER CHECK(guest_count IS NULL OR guest_count>0),
 message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN('new','contacted','closed')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_guest_enquiries_status_date ON guest_enquiries(status,created_at);