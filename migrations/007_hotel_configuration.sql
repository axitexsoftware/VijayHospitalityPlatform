CREATE TABLE IF NOT EXISTS hotel_configuration(
 id INTEGER PRIMARY KEY CHECK(id=1),
 config_json TEXT NOT NULL,
 setup_complete INTEGER NOT NULL DEFAULT 0 CHECK(setup_complete IN(0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_by INTEGER REFERENCES users(id)
);
