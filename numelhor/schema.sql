PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    icon   TEXT NOT NULL DEFAULT 'lanche',
    sort   INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES categories(id),
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
    image         TEXT,
    allows_sauces INTEGER NOT NULL DEFAULT 0,
    featured      INTEGER NOT NULL DEFAULT 0,
    old_price_cents INTEGER,
    active        INTEGER NOT NULL DEFAULT 1,
    sort          INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Itens que compõem o produto; os "removable" podem ser retirados pelo cliente.
CREATE TABLE IF NOT EXISTS ingredients (
    id         INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    removable  INTEGER NOT NULL DEFAULT 1,
    sort       INTEGER NOT NULL DEFAULT 0
);

-- Grupos de opções do produto: Tamanho (escolha 1), Sabor, Adicionais (vários)...
CREATE TABLE IF NOT EXISTS option_groups (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    min_choices INTEGER NOT NULL DEFAULT 0,
    max_choices INTEGER NOT NULL DEFAULT 1,
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS options (
    id          INTEGER PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES option_groups(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sauces (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
    active      INTEGER NOT NULL DEFAULT 1,
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id                 INTEGER PRIMARY KEY,
    token              TEXT NOT NULL UNIQUE,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    customer_name      TEXT NOT NULL,
    customer_phone     TEXT NOT NULL,
    customer_email     TEXT,
    fulfillment        TEXT NOT NULL CHECK (fulfillment IN ('retirada', 'entrega')),
    address            TEXT,
    address_number     TEXT,
    complement         TEXT,
    reference          TEXT,
    lat                REAL,
    lng                REAL,
    distance_km        REAL,
    notes              TEXT,
    pay_when           TEXT NOT NULL CHECK (pay_when IN ('online', 'entrega')),
    pay_method         TEXT NOT NULL CHECK (pay_method IN ('pix', 'cartao', 'credito', 'debito', 'dinheiro')),
    change_for_cents   INTEGER,
    subtotal_cents     INTEGER NOT NULL,
    delivery_fee_cents INTEGER NOT NULL DEFAULT 0,
    total_cents        INTEGER NOT NULL,
    status             TEXT NOT NULL,
    payment_status     TEXT NOT NULL,
    payment_provider   TEXT,
    payment_ref        TEXT,
    checkout_url       TEXT,
    pix_copy_paste     TEXT,
    pix_qr_b64         TEXT,
    pix_expires_at     TEXT,
    paid_at            TEXT,
    last_check_at      REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id       INTEGER,
    name             TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    qty              INTEGER NOT NULL,
    removed          TEXT NOT NULL DEFAULT '[]',
    sauces           TEXT NOT NULL DEFAULT '[]',
    options          TEXT NOT NULL DEFAULT '[]',
    notes            TEXT,
    line_total_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id                   INTEGER PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    session_version      INTEGER NOT NULL DEFAULT 1,
    totp_secret          TEXT,
    totp_enabled         INTEGER NOT NULL DEFAULT 0,
    totp_last_counter    INTEGER NOT NULL DEFAULT 0,
    failed_count         INTEGER NOT NULL DEFAULT 0,
    locked_until         REAL NOT NULL DEFAULT 0,
    last_login_at        TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_throttle (
    ip           TEXT PRIMARY KEY,
    fails        INTEGER NOT NULL DEFAULT 0,
    window_start REAL NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY,
    at     TEXT NOT NULL DEFAULT (datetime('now')),
    ip     TEXT,
    actor  TEXT,
    event  TEXT NOT NULL,
    detail TEXT
);
