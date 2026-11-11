-- Realistic fintech / banking schema for PostgreSQL.
-- Exercises: EXCLUDE ranges, enums, money checks, partitioned ledger,
-- multiple FKs to one table and audit triggers.

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA fintech;
SET search_path TO fintech, public;

CREATE TYPE account_kind AS ENUM ('checking', 'savings', 'credit');
CREATE TYPE transaction_kind AS ENUM ('debit', 'credit', 'fee', 'interest');
CREATE TYPE transfer_status AS ENUM ('pending', 'posted', 'failed', 'reversed');
CREATE TYPE card_status AS ENUM ('active', 'blocked', 'expired');
CREATE TYPE authorization_status AS ENUM ('pending', 'captured', 'declined', 'reversed');
CREATE TYPE fee_kind AS ENUM ('monthly', 'overdraft', 'atm', 'wire');

CREATE TABLE customers (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    full_name text NOT NULL,
    risk_score integer NOT NULL CHECK (risk_score >= 0 AND risk_score <= 100)
);

CREATE TABLE accounts (
    id uuid PRIMARY KEY,
    customer_id uuid NOT NULL REFERENCES customers (id),
    account_number text NOT NULL UNIQUE,
    kind account_kind NOT NULL,
    currency char(3) NOT NULL CHECK (currency IN ('MXN', 'USD', 'EUR')),
    balance numeric(16, 2) NOT NULL CHECK (balance >= 0),
    opened_at timestamptz NOT NULL
);

CREATE TABLE holds (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    amount numeric(14, 2) NOT NULL CHECK (amount > 0),
    period tstzrange NOT NULL,
    CONSTRAINT holds_period_excl
        EXCLUDE USING gist (account_id WITH =, period WITH &&)
);

CREATE TABLE transactions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    kind transaction_kind NOT NULL,
    amount numeric(14, 2) NOT NULL CHECK (amount > 0),
    posted_at timestamptz NOT NULL,
    reference text NOT NULL UNIQUE
);

CREATE TABLE transfers (
    id uuid PRIMARY KEY,
    from_account_id uuid NOT NULL REFERENCES accounts (id),
    to_account_id uuid NOT NULL REFERENCES accounts (id),
    amount numeric(14, 2) NOT NULL CHECK (amount > 0),
    status transfer_status NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE cards (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    last4 char(4) NOT NULL,
    status card_status NOT NULL,
    expires_on date NOT NULL
);

CREATE TABLE card_authorizations (
    id uuid PRIMARY KEY,
    card_id uuid NOT NULL REFERENCES cards (id),
    amount numeric(14, 2) NOT NULL CHECK (amount > 0),
    authorized_at timestamptz NOT NULL,
    status authorization_status NOT NULL
);

CREATE TABLE statements (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    period_start date NOT NULL,
    period_end date NOT NULL,
    opening_balance numeric(16, 2) NOT NULL CHECK (opening_balance >= 0),
    closing_balance numeric(16, 2) NOT NULL CHECK (closing_balance >= 0),
    CHECK (period_end > period_start)
);

CREATE TABLE fees (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts (id),
    kind fee_kind NOT NULL,
    amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
    charged_at timestamptz NOT NULL
);

CREATE TABLE ledger_entries (
    id bigint NOT NULL,
    account_id uuid NOT NULL REFERENCES accounts (id),
    direction text NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount numeric(16, 2) NOT NULL CHECK (amount > 0),
    posted_at timestamptz NOT NULL,
    PRIMARY KEY (id, posted_at)
) PARTITION BY RANGE (posted_at);

CREATE TABLE ledger_entries_2023 PARTITION OF ledger_entries
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE ledger_entries_2024 PARTITION OF ledger_entries
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ledger_entries_2025 PARTITION OF ledger_entries
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ledger_entries_default PARTITION OF ledger_entries DEFAULT;

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_transactions() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'transactions',
        lower(TG_OP),
        jsonb_build_object(
            'id', COALESCE(NEW.id, OLD.id),
            'amount', COALESCE(NEW.amount, OLD.amount)
        ),
        now()
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER transactions_audit
    AFTER INSERT OR UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION audit_transactions();

CREATE VIEW account_activity AS
    SELECT account_id, kind, amount, posted_at
    FROM transactions
    WHERE posted_at IS NOT NULL;

CREATE PROCEDURE freeze_account(p_account uuid)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE fintech.cards
    SET status = 'blocked'
    WHERE account_id = p_account;
END;
$$;
