-- Realistic fintech / banking schema for SQL Server.
-- Exercises: temporal accounts, partitioned ledger, multiple FKs to one table,
-- money checks, filtered uniqueness and audit triggers.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('fintech') IS NULL EXEC('CREATE SCHEMA fintech');
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

CREATE TABLE fintech.customers (
    id uniqueidentifier NOT NULL CONSTRAINT customers_pk PRIMARY KEY,
    email nvarchar(320) NOT NULL CONSTRAINT customers_email UNIQUE,
    full_name nvarchar(200) NOT NULL,
    risk_score int NOT NULL
        CONSTRAINT customers_risk CHECK (risk_score >= 0 AND risk_score <= 100)
);
GO

CREATE TABLE fintech.accounts (
    id uniqueidentifier NOT NULL CONSTRAINT accounts_pk PRIMARY KEY,
    customer_id uniqueidentifier NOT NULL
        CONSTRAINT accounts_customer_fk REFERENCES fintech.customers (id),
    account_number nvarchar(40) NOT NULL CONSTRAINT accounts_number UNIQUE,
    kind nvarchar(20) NOT NULL
        CONSTRAINT accounts_kind CHECK (kind IN ('checking', 'savings', 'credit')),
    currency nchar(3) NOT NULL
        CONSTRAINT accounts_currency CHECK (currency IN ('MXN', 'USD', 'EUR')),
    balance decimal(16, 2) NOT NULL CONSTRAINT accounts_balance CHECK (balance >= 0),
    opened_at datetimeoffset(7) NOT NULL,
    nickname nvarchar(80) NULL,
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = fintech.accounts_history));
GO

CREATE TABLE fintech.holds (
    id uniqueidentifier NOT NULL CONSTRAINT holds_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT holds_account_fk REFERENCES fintech.accounts (id),
    amount decimal(14, 2) NOT NULL CONSTRAINT holds_amount CHECK (amount > 0),
    period_start datetime2(7) NOT NULL,
    period_end datetime2(7) NOT NULL,
    CONSTRAINT holds_period CHECK (period_end > period_start)
);
GO

CREATE TABLE fintech.transactions (
    id uniqueidentifier NOT NULL CONSTRAINT transactions_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT transactions_account_fk REFERENCES fintech.accounts (id),
    kind nvarchar(20) NOT NULL
        CONSTRAINT transactions_kind CHECK (kind IN ('debit', 'credit', 'fee', 'interest')),
    amount decimal(14, 2) NOT NULL CONSTRAINT transactions_amount CHECK (amount > 0),
    posted_at datetimeoffset(7) NOT NULL,
    reference nvarchar(80) NOT NULL CONSTRAINT transactions_reference UNIQUE
);
GO

CREATE TABLE fintech.transfers (
    id uniqueidentifier NOT NULL CONSTRAINT transfers_pk PRIMARY KEY,
    from_account_id uniqueidentifier NOT NULL
        CONSTRAINT transfers_from_fk REFERENCES fintech.accounts (id),
    to_account_id uniqueidentifier NOT NULL
        CONSTRAINT transfers_to_fk REFERENCES fintech.accounts (id),
    amount decimal(14, 2) NOT NULL CONSTRAINT transfers_amount CHECK (amount > 0),
    status nvarchar(20) NOT NULL
        CONSTRAINT transfers_status CHECK (status IN ('pending', 'posted', 'failed', 'reversed')),
    created_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE fintech.cards (
    id uniqueidentifier NOT NULL CONSTRAINT cards_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT cards_account_fk REFERENCES fintech.accounts (id),
    last4 nchar(4) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT cards_status CHECK (status IN ('active', 'blocked', 'expired')),
    expires_on date NOT NULL
);
GO

CREATE TABLE fintech.card_authorizations (
    id uniqueidentifier NOT NULL CONSTRAINT authorizations_pk PRIMARY KEY,
    card_id uniqueidentifier NOT NULL
        CONSTRAINT authorizations_card_fk REFERENCES fintech.cards (id),
    amount decimal(14, 2) NOT NULL
        CONSTRAINT authorizations_amount CHECK (amount > 0),
    authorized_at datetimeoffset(7) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT authorizations_status CHECK (status IN ('pending', 'captured', 'declined', 'reversed'))
);
GO

CREATE TABLE fintech.statements (
    id uniqueidentifier NOT NULL CONSTRAINT statements_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT statements_account_fk REFERENCES fintech.accounts (id),
    period_start date NOT NULL,
    period_end date NOT NULL,
    opening_balance decimal(16, 2) NOT NULL
        CONSTRAINT statements_opening CHECK (opening_balance >= 0),
    closing_balance decimal(16, 2) NOT NULL
        CONSTRAINT statements_closing CHECK (closing_balance >= 0),
    CONSTRAINT statements_period CHECK (period_end > period_start)
);
GO

CREATE TABLE fintech.fees (
    id uniqueidentifier NOT NULL CONSTRAINT fees_pk PRIMARY KEY,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT fees_account_fk REFERENCES fintech.accounts (id),
    kind nvarchar(20) NOT NULL
        CONSTRAINT fees_kind CHECK (kind IN ('monthly', 'overdraft', 'atm', 'wire')),
    amount decimal(12, 2) NOT NULL CONSTRAINT fees_amount CHECK (amount >= 0),
    charged_at datetimeoffset(7) NOT NULL
);
GO

CREATE PARTITION FUNCTION pf_ledger_entries (datetime2(7))
    AS RANGE RIGHT FOR VALUES
    ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01');
GO
CREATE PARTITION SCHEME ps_ledger_entries
    AS PARTITION pf_ledger_entries ALL TO ([PRIMARY]);
GO

CREATE TABLE fintech.ledger_entries (
    id bigint IDENTITY(1, 1) NOT NULL,
    account_id uniqueidentifier NOT NULL
        CONSTRAINT ledger_account_fk REFERENCES fintech.accounts (id),
    direction nvarchar(10) NOT NULL
        CONSTRAINT ledger_direction CHECK (direction IN ('debit', 'credit')),
    amount decimal(16, 2) NOT NULL CONSTRAINT ledger_amount CHECK (amount > 0),
    posted_at datetime2(7) NOT NULL,
    CONSTRAINT ledger_pk PRIMARY KEY (id, posted_at)
) ON ps_ledger_entries (posted_at);
GO

CREATE TRIGGER fintech.transactions_audit
    ON fintech.transactions
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'transactions',
        CASE
            WHEN d.id IS NULL THEN N'insert'
            WHEN i.id IS NULL THEN N'delete'
            ELSE N'update'
        END,
        CONCAT(
            N'{"id":"',
            CONVERT(nvarchar(36), COALESCE(i.id, d.id)),
            N'","amount":',
            COALESCE(i.amount, d.amount),
            N'}'
        ),
        SYSUTCDATETIME()
    FROM inserted AS i
    FULL OUTER JOIN deleted AS d ON i.id = d.id;
END;
GO

CREATE VIEW fintech.account_activity AS
    SELECT account_id, kind, amount, posted_at
    FROM fintech.transactions;
GO

CREATE PROCEDURE fintech.usp_freeze_account
    @account uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE fintech.cards
    SET status = 'blocked'
    WHERE account_id = @account;
END;
GO
