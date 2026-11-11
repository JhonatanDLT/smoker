-- Realistic e-commerce schema for PostgreSQL.
-- Exercises: enums, partial unique indexes, self-references, derived amounts,
-- JSON attributes, partitioned events and audit triggers.

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA shop;
SET search_path TO shop, public;

CREATE TYPE order_status AS ENUM (
    'pending', 'paid', 'packed', 'shipped', 'delivered', 'cancelled'
);
CREATE TYPE product_status AS ENUM ('draft', 'active', 'discontinued');

CREATE TABLE customers (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    full_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE addresses (
    id uuid PRIMARY KEY,
    customer_id uuid NOT NULL REFERENCES customers (id),
    line1 text NOT NULL,
    city text NOT NULL,
    country text NOT NULL,
    is_default boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX addresses_one_default
    ON addresses (customer_id)
    WHERE is_default;

CREATE TABLE categories (
    id integer PRIMARY KEY,
    name text NOT NULL UNIQUE,
    parent_id integer NULL REFERENCES categories (id)
);

CREATE TABLE products (
    id uuid PRIMARY KEY,
    sku text NOT NULL UNIQUE,
    name text NOT NULL,
    status product_status NOT NULL,
    price numeric(12, 2) NOT NULL CHECK (price >= 0),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE product_categories (
    product_id uuid NOT NULL REFERENCES products (id),
    category_id integer NOT NULL REFERENCES categories (id),
    PRIMARY KEY (product_id, category_id)
);

CREATE TABLE orders (
    id uuid PRIMARY KEY,
    customer_id uuid NOT NULL REFERENCES customers (id),
    status order_status NOT NULL,
    ordered_at timestamptz NOT NULL,
    shipping_address_id uuid NOT NULL REFERENCES addresses (id),
    total numeric(12, 2) NOT NULL CHECK (total >= 0),
    notes text NULL
);

CREATE TABLE order_items (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES orders (id),
    product_id uuid NOT NULL REFERENCES products (id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12, 2) NOT NULL CHECK (unit_price >= 0),
    amount numeric(12, 2) NOT NULL CHECK (amount = quantity * unit_price)
);

CREATE TABLE payments (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL UNIQUE REFERENCES orders (id),
    method text NOT NULL CHECK (method IN ('card', 'transfer', 'cash', 'wallet')),
    amount numeric(12, 2) NOT NULL CHECK (amount > 0),
    paid_at timestamptz NOT NULL
);

CREATE TABLE shipments (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES orders (id),
    carrier text NOT NULL,
    tracking_code text NOT NULL UNIQUE,
    shipped_at timestamptz NOT NULL,
    delivered_at timestamptz NULL,
    CHECK (delivered_at >= shipped_at)
);

CREATE TABLE returns (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES orders (id),
    reason text NOT NULL,
    requested_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('requested', 'approved', 'rejected', 'received'))
);

CREATE TABLE order_events (
    id bigint NOT NULL,
    order_id uuid NOT NULL REFERENCES orders (id),
    event_name text NOT NULL CHECK (event_name IN ('created', 'paid', 'packed', 'shipped', 'delivered', 'cancelled')),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE order_events_2023 PARTITION OF order_events
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE order_events_2024 PARTITION OF order_events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE order_events_2025 PARTITION OF order_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE order_events_default PARTITION OF order_events DEFAULT;

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_orders() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'orders',
        lower(TG_OP),
        jsonb_build_object(
            'id', COALESCE(NEW.id, OLD.id),
            'status', COALESCE(NEW.status, OLD.status)
        ),
        now()
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER orders_audit
    AFTER INSERT OR UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION audit_orders();

CREATE VIEW open_orders AS
    SELECT id, customer_id, status, ordered_at, total
    FROM orders
    WHERE status IN ('pending', 'paid', 'packed');

CREATE PROCEDURE cancel_order(p_order uuid)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE shop.orders
    SET status = 'cancelled'
    WHERE id = p_order;
END;
$$;
