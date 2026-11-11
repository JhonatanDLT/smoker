-- Realistic SaaS billing schema for SQL Server.
-- Exercises: filtered unique indexes, temporal history, table partitioning,
-- identity columns, triggers, procedures, JSON-ish payloads and composite keys.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('billing') IS NULL EXEC('CREATE SCHEMA billing');
GO

CREATE TABLE audit.audit_log (
    id bigint IDENTITY(1, 1) NOT NULL CONSTRAINT audit_log_pk PRIMARY KEY,
    table_name nvarchar(128) NOT NULL,
    action nvarchar(16) NOT NULL
        CONSTRAINT audit_log_action CHECK (action IN ('insert', 'update', 'delete')),
    payload nvarchar(max) NOT NULL CONSTRAINT audit_log_payload DEFAULT (N'{}'),
    changed_at datetime2(7) NOT NULL CONSTRAINT audit_log_changed DEFAULT (SYSUTCDATETIME())
);
GO

CREATE TABLE billing.accounts (
    id uniqueidentifier NOT NULL CONSTRAINT accounts_pk PRIMARY KEY,
    name nvarchar(200) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT accounts_status CHECK (status IN ('trial', 'active', 'past_due', 'cancelled')),
    created_at datetimeoffset(7) NOT NULL CONSTRAINT accounts_created DEFAULT (SYSDATETIMEOFFSET())
);
GO

CREATE TABLE billing.contacts (
    id uniqueidentifier NOT NULL CONSTRAINT contacts_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT contacts_account_fk REFERENCES billing.accounts (id),
    email nvarchar(320) NOT NULL,
    full_name nvarchar(200) NOT NULL,
    is_billing bit NOT NULL CONSTRAINT contacts_is_billing DEFAULT (0),
    CONSTRAINT contacts_account_email UNIQUE (account_id, email)
);
GO

CREATE TABLE billing.plans (
    id smallint NOT NULL CONSTRAINT plans_pk PRIMARY KEY,
    code nvarchar(40) NOT NULL CONSTRAINT plans_code UNIQUE,
    tier nvarchar(20) NOT NULL
        CONSTRAINT plans_tier CHECK (tier IN ('free', 'pro', 'business', 'enterprise')),
    monthly_price decimal(12, 2) NOT NULL
        CONSTRAINT plans_price CHECK (monthly_price >= 0),
    seat_limit int NOT NULL CONSTRAINT plans_seats CHECK (seat_limit > 0),
    features nvarchar(max) NOT NULL CONSTRAINT plans_features DEFAULT (N'{}')
);
GO

CREATE TABLE billing.coupons (
    id int NOT NULL CONSTRAINT coupons_pk PRIMARY KEY,
    code nvarchar(40) NOT NULL CONSTRAINT coupons_code UNIQUE,
    percent_off decimal(5, 2) NOT NULL
        CONSTRAINT coupons_percent CHECK (percent_off > 0 AND percent_off <= 100),
    expires_on date NULL
);
GO

CREATE TABLE billing.subscriptions (
    id uniqueidentifier NOT NULL CONSTRAINT subscriptions_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT subscriptions_account_fk REFERENCES billing.accounts (id),
    plan_id smallint NOT NULL
        CONSTRAINT subscriptions_plan_fk REFERENCES billing.plans (id),
    status nvarchar(20) NOT NULL
        CONSTRAINT subscriptions_status CHECK (status IN ('trialing', 'active', 'past_due', 'cancelled')),
    seats int NOT NULL CONSTRAINT subscriptions_seats CHECK (seats > 0),
    period_start date NOT NULL,
    period_end date NOT NULL,
    CONSTRAINT subscriptions_period CHECK (period_end > period_start)
);
GO

CREATE UNIQUE INDEX subscriptions_one_active
    ON billing.subscriptions (account_id)
    WHERE status = 'active';
GO

CREATE TABLE billing.invoices (
    id uniqueidentifier NOT NULL CONSTRAINT invoices_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT invoices_account_fk REFERENCES billing.accounts (id),
    subscription_id uniqueidentifier NOT NULL
        CONSTRAINT invoices_subscription_fk REFERENCES billing.subscriptions (id),
    number nvarchar(40) NOT NULL CONSTRAINT invoices_number UNIQUE,
    status nvarchar(20) NOT NULL
        CONSTRAINT invoices_status CHECK (status IN ('draft', 'open', 'paid', 'void', 'uncollectible')),
    issued_on date NOT NULL,
    due_on date NOT NULL,
    subtotal decimal(12, 2) NOT NULL CONSTRAINT invoices_subtotal CHECK (subtotal >= 0),
    tax decimal(12, 2) NOT NULL CONSTRAINT invoices_tax CHECK (tax >= 0),
    total decimal(12, 2) NOT NULL,
    memo nvarchar(400) NULL,
    CONSTRAINT invoices_total CHECK (total = subtotal + tax),
    CONSTRAINT invoices_due CHECK (due_on >= issued_on),
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = billing.invoices_history));
GO

CREATE TABLE billing.invoice_lines (
    id bigint IDENTITY(1, 1) NOT NULL CONSTRAINT invoice_lines_pk PRIMARY KEY,
    invoice_id uniqueidentifier NOT NULL
        CONSTRAINT invoice_lines_invoice_fk REFERENCES billing.invoices (id),
    description nvarchar(300) NOT NULL,
    quantity int NOT NULL CONSTRAINT invoice_lines_quantity CHECK (quantity > 0),
    unit_price decimal(12, 2) NOT NULL CONSTRAINT invoice_lines_price CHECK (unit_price >= 0),
    amount decimal(12, 2) NOT NULL,
    CONSTRAINT invoice_lines_amount CHECK (amount = quantity * unit_price)
);
GO

CREATE TABLE billing.payments (
    id uniqueidentifier NOT NULL CONSTRAINT payments_pk PRIMARY KEY,
    invoice_id uniqueidentifier NOT NULL
        CONSTRAINT payments_invoice_fk REFERENCES billing.invoices (id),
    amount decimal(12, 2) NOT NULL CONSTRAINT payments_amount CHECK (amount > 0),
    method nvarchar(20) NOT NULL
        CONSTRAINT payments_method CHECK (method IN ('card', 'bank_transfer', 'cash', 'credit')),
    paid_at datetimeoffset(7) NOT NULL,
    external_ref nvarchar(80) NOT NULL CONSTRAINT payments_ref UNIQUE
);
GO

CREATE TABLE billing.refunds (
    id uniqueidentifier NOT NULL CONSTRAINT refunds_pk PRIMARY KEY,
    payment_id uniqueidentifier NOT NULL
        CONSTRAINT refunds_payment_fk REFERENCES billing.payments (id),
    amount decimal(12, 2) NOT NULL CONSTRAINT refunds_amount CHECK (amount > 0),
    refunded_at datetimeoffset(7) NOT NULL,
    reason nvarchar(300) NULL
);
GO

CREATE PARTITION FUNCTION pf_usage_events (datetime2(7))
    AS RANGE RIGHT FOR VALUES
    ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01');
GO
CREATE PARTITION SCHEME ps_usage_events
    AS PARTITION pf_usage_events ALL TO ([PRIMARY]);
GO

CREATE TABLE billing.usage_events (
    id bigint IDENTITY(1, 1) NOT NULL,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT usage_events_account_fk REFERENCES billing.accounts (id),
    metric nvarchar(20) NOT NULL
        CONSTRAINT usage_events_metric CHECK (metric IN ('api_calls', 'storage_gb', 'seats', 'emails')),
    quantity bigint NOT NULL CONSTRAINT usage_events_quantity CHECK (quantity >= 0),
    recorded_at datetime2(7) NOT NULL,
    CONSTRAINT usage_events_pk PRIMARY KEY (id, recorded_at)
) ON ps_usage_events (recorded_at);
GO

CREATE TABLE billing.api_keys (
    id uniqueidentifier NOT NULL CONSTRAINT api_keys_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT api_keys_account_fk REFERENCES billing.accounts (id),
    prefix nvarchar(40) NOT NULL CONSTRAINT api_keys_prefix UNIQUE,
    secret_hash nvarchar(200) NOT NULL,
    created_at datetimeoffset(7) NOT NULL CONSTRAINT api_keys_created DEFAULT (SYSDATETIMEOFFSET()),
    revoked_at datetimeoffset(7) NULL
);
GO

CREATE TABLE billing.webhook_endpoints (
    id uniqueidentifier NOT NULL CONSTRAINT webhook_endpoints_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT webhook_account_fk REFERENCES billing.accounts (id),
    url nvarchar(400) NOT NULL CONSTRAINT webhook_url UNIQUE,
    active bit NOT NULL CONSTRAINT webhook_active DEFAULT (1),
    created_at datetimeoffset(7) NOT NULL CONSTRAINT webhook_created DEFAULT (SYSDATETIMEOFFSET())
);
GO

CREATE TABLE billing.webhook_endpoint_events (
    endpoint_id uniqueidentifier NOT NULL
        CONSTRAINT webhook_events_endpoint_fk REFERENCES billing.webhook_endpoints (id),
    event_name nvarchar(60) NOT NULL,
    CONSTRAINT webhook_events_pk PRIMARY KEY (endpoint_id, event_name)
);
GO

CREATE TABLE billing.webhook_deliveries (
    id uniqueidentifier NOT NULL CONSTRAINT webhook_deliveries_pk PRIMARY KEY,
    endpoint_id uniqueidentifier NOT NULL
        CONSTRAINT webhook_deliveries_endpoint_fk REFERENCES billing.webhook_endpoints (id),
    event_name nvarchar(60) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT webhook_deliveries_status CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts int NOT NULL CONSTRAINT webhook_deliveries_attempts CHECK (attempts >= 0),
    last_error nvarchar(400) NULL,
    delivered_at datetimeoffset(7) NULL
);
GO

CREATE TRIGGER billing.invoices_audit
    ON billing.invoices
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'invoices',
        CASE
            WHEN d.id IS NULL THEN N'insert'
            WHEN i.id IS NULL THEN N'delete'
            ELSE N'update'
        END,
        CONCAT(
            N'{"id":"',
            CONVERT(nvarchar(36), COALESCE(i.id, d.id)),
            N'","status":"',
            COALESCE(i.status, d.status),
            N'"}'
        ),
        SYSUTCDATETIME()
    FROM inserted AS i
    FULL OUTER JOIN deleted AS d ON i.id = d.id;
END;
GO

CREATE VIEW billing.active_subscriptions AS
    SELECT s.id, s.account_id, s.plan_id, s.seats, s.period_start, s.period_end
    FROM billing.subscriptions AS s
    WHERE s.status = 'active';
GO

CREATE PROCEDURE billing.usp_mark_invoice_paid
    @invoice uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE billing.invoices
    SET status = 'paid'
    WHERE id = @invoice;
END;
GO
