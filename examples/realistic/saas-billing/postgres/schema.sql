-- Realistic SaaS billing schema for PostgreSQL.
-- Exercises: enums, composite/cyclic FKs, partial unique indexes, EXCLUDE
-- ranges, generated columns, arrays, JSON, partitioned tables and triggers.

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA billing;
SET search_path TO billing, public;

CREATE TYPE account_status AS ENUM ('trial', 'active', 'past_due', 'cancelled');
CREATE TYPE plan_tier AS ENUM ('free', 'pro', 'business', 'enterprise');
CREATE TYPE subscription_status AS ENUM ('trialing', 'active', 'past_due', 'cancelled');
CREATE TYPE invoice_status AS ENUM ('draft', 'open', 'paid', 'void', 'uncollectible');
CREATE TYPE payment_method AS ENUM ('card', 'bank_transfer', 'cash', 'credit');

CREATE TABLE accounts (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    status account_status NOT NULL DEFAULT 'trial',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE contacts (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    email text NOT NULL,
    full_name text NOT NULL,
    is_billing boolean NOT NULL DEFAULT false,
    reports_to_id uuid NULL REFERENCES contacts (id),
    CONSTRAINT contacts_account_email_key UNIQUE (account_id, email)
);

CREATE TABLE plans (
    id smallint PRIMARY KEY,
    code text NOT NULL UNIQUE,
    tier plan_tier NOT NULL,
    monthly_price numeric(12, 2) NOT NULL CHECK (monthly_price >= 0),
    seat_limit integer NOT NULL CHECK (seat_limit > 0),
    features jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE coupons (
    id integer PRIMARY KEY,
    code text NOT NULL UNIQUE,
    percent_off numeric(5, 2) NOT NULL
        CHECK (percent_off > 0 AND percent_off <= 100),
    expires_on date NULL
);

CREATE TABLE subscriptions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    plan_id smallint NOT NULL REFERENCES plans (id),
    status subscription_status NOT NULL,
    seats integer NOT NULL CHECK (seats > 0),
    period daterange NOT NULL,
    CONSTRAINT subscriptions_period_excl
        EXCLUDE USING gist (account_id WITH =, period WITH &&)
);

CREATE UNIQUE INDEX subscriptions_one_active
    ON subscriptions (account_id)
    WHERE status = 'active';

CREATE TABLE invoices (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    subscription_id uuid NOT NULL REFERENCES subscriptions (id),
    number text NOT NULL UNIQUE,
    status invoice_status NOT NULL,
    issued_on date NOT NULL,
    due_on date NOT NULL,
    subtotal numeric(12, 2) NOT NULL CHECK (subtotal >= 0),
    tax numeric(12, 2) NOT NULL CHECK (tax >= 0),
    total numeric(12, 2) NOT NULL CHECK (total = subtotal + tax),
    memo text NULL,
    CHECK (due_on >= issued_on)
);

CREATE TABLE invoice_lines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id uuid NOT NULL REFERENCES invoices (id),
    description text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12, 2) NOT NULL CHECK (unit_price >= 0),
    amount numeric(12, 2) NOT NULL CHECK (amount = quantity * unit_price)
);

CREATE TABLE payments (
    id uuid PRIMARY KEY,
    invoice_id uuid NOT NULL REFERENCES invoices (id),
    amount numeric(12, 2) NOT NULL CHECK (amount > 0),
    method payment_method NOT NULL,
    paid_at timestamptz NOT NULL,
    external_ref text NOT NULL UNIQUE
);

CREATE TABLE refunds (
    id uuid PRIMARY KEY,
    payment_id uuid NOT NULL REFERENCES payments (id),
    amount numeric(12, 2) NOT NULL CHECK (amount > 0),
    refunded_at timestamptz NOT NULL,
    reason text NULL
);

CREATE TABLE usage_events (
    id bigint NOT NULL,
    account_id uuid NOT NULL REFERENCES accounts (id),
    metric text NOT NULL
        CHECK (metric IN ('api_calls', 'storage_gb', 'seats', 'emails')),
    quantity bigint NOT NULL CHECK (quantity >= 0),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (id, recorded_at)
) PARTITION BY RANGE (recorded_at);

CREATE TABLE usage_events_2023 PARTITION OF usage_events
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE usage_events_2024 PARTITION OF usage_events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE usage_events_2025 PARTITION OF usage_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE usage_events_default PARTITION OF usage_events DEFAULT;

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    prefix text NOT NULL UNIQUE,
    secret_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz NULL
);

CREATE TABLE webhook_endpoints (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    url text NOT NULL UNIQUE,
    events text[] NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TYPE webhook_delivery_status AS ENUM ('pending', 'delivered', 'failed');

CREATE TABLE webhook_deliveries (
    id uuid PRIMARY KEY,
    endpoint_id uuid NOT NULL REFERENCES webhook_endpoints (id),
    event_name text NOT NULL,
    status webhook_delivery_status NOT NULL,
    attempts integer NOT NULL CHECK (attempts >= 0),
    last_error text NULL,
    delivered_at timestamptz NULL
);

CREATE FUNCTION audit_invoices() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'invoices',
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

CREATE TRIGGER invoices_audit
    AFTER INSERT OR UPDATE OR DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION audit_invoices();

CREATE VIEW active_subscriptions AS
    SELECT s.id, s.account_id, s.plan_id, s.seats, s.period
    FROM subscriptions s
    WHERE s.status = 'active';

CREATE PROCEDURE mark_invoice_paid(p_invoice uuid)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE billing.invoices
    SET status = 'paid'
    WHERE id = p_invoice;
END;
$$;
