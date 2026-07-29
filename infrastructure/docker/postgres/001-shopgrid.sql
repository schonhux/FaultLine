CREATE TABLE IF NOT EXISTS products (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents BIGINT NOT NULL CHECK (price_cents > 0),
    stock BIGINT NOT NULL CHECK (stock >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity BIGINT NOT NULL CHECK (quantity > 0),
    total_cents BIGINT NOT NULL CHECK (total_cents > 0),
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO products (id, name, price_cents, stock) VALUES
    (1, 'Trace Hoodie', 6400, 100000),
    (2, 'Span Notebook', 1800, 100000),
    (3, 'Latency Mug', 2200, 100000),
    (4, 'Runbook Sticker Pack', 900, 100000),
    (5, 'Incident Tote', 3100, 100000)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    price_cents = EXCLUDED.price_cents,
    stock = EXCLUDED.stock;
