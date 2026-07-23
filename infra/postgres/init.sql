CREATE TABLE IF NOT EXISTS service_bootstrap_log (
    id SERIAL PRIMARY KEY,
    service_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
