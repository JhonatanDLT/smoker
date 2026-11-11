-- Realistic e-commerce schema for SQL Server.
-- Exercises: filtered unique indexes, temporal orders, partitioning, identity,
-- derived amounts, audit triggers and one-to-one foreign keys.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('shop') IS NULL EXEC('CREATE SCHEMA shop');
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

CREATE TABLE shop.customers (
    id uniqueidentifier NOT NULL CONSTRAINT customers_pk PRIMARY KEY,
    email nvarchar(320) NOT NULL CONSTRAINT customers_email UNIQUE,
    full_name nvarchar(200) NOT NULL,
    created_at datetimeoffset(7) NOT NULL
        CONSTRAINT customers_created DEFAULT (SYSDATETIMEOFFSET())
);
GO

CREATE TABLE shop.addresses (
    id uniqueidentifier NOT NULL CONSTRAINT addresses_pk PRIMARY KEY,
    customer_id uniqueidentifier NOT NULL
        CONSTRAINT addresses_customer_fk REFERENCES shop.customers (id),
    line1 nvarchar(300) NOT NULL,
    city nvarchar(120) NOT NULL,
    country nvarchar(120) NOT NULL,
    is_default bit NOT NULL CONSTRAINT addresses_default DEFAULT (0)
);
GO

CREATE UNIQUE INDEX addresses_one_default
    ON shop.addresses (customer_id)
    WHERE is_default = 1;
GO

CREATE TABLE shop.categories (
    id int NOT NULL CONSTRAINT categories_pk PRIMARY KEY,
    name nvarchar(120) NOT NULL CONSTRAINT categories_name UNIQUE,
    parent_id int NULL
);
GO

CREATE TABLE shop.products (
    id uniqueidentifier NOT NULL CONSTRAINT products_pk PRIMARY KEY,
    sku nvarchar(60) NOT NULL CONSTRAINT products_sku UNIQUE,
    name nvarchar(200) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT products_status CHECK (status IN ('draft', 'active', 'discontinued')),
    price decimal(12, 2) NOT NULL CONSTRAINT products_price CHECK (price >= 0),
    attributes nvarchar(max) NOT NULL CONSTRAINT products_attributes DEFAULT (N'{}')
);
GO

CREATE TABLE shop.product_categories (
    product_id uniqueidentifier NOT NULL
        CONSTRAINT product_categories_product_fk REFERENCES shop.products (id),
    category_id int NOT NULL
        CONSTRAINT product_categories_category_fk REFERENCES shop.categories (id),
    CONSTRAINT product_categories_pk PRIMARY KEY (product_id, category_id)
);
GO

CREATE TABLE shop.orders (
    id uniqueidentifier NOT NULL CONSTRAINT orders_pk PRIMARY KEY,
    customer_id uniqueidentifier NOT NULL
        CONSTRAINT orders_customer_fk REFERENCES shop.customers (id),
    status nvarchar(20) NOT NULL
        CONSTRAINT orders_status CHECK (status IN ('pending', 'paid', 'packed', 'shipped', 'delivered', 'cancelled')),
    ordered_at datetimeoffset(7) NOT NULL,
    shipping_address_id uniqueidentifier NOT NULL
        CONSTRAINT orders_address_fk REFERENCES shop.addresses (id),
    total decimal(12, 2) NOT NULL CONSTRAINT orders_total CHECK (total >= 0),
    notes nvarchar(400) NULL,
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = shop.orders_history));
GO

CREATE TABLE shop.order_items (
    id bigint IDENTITY(1, 1) NOT NULL CONSTRAINT order_items_pk PRIMARY KEY,
    order_id uniqueidentifier NOT NULL
        CONSTRAINT order_items_order_fk REFERENCES shop.orders (id),
    product_id uniqueidentifier NOT NULL
        CONSTRAINT order_items_product_fk REFERENCES shop.products (id),
    quantity int NOT NULL CONSTRAINT order_items_quantity CHECK (quantity > 0),
    unit_price decimal(12, 2) NOT NULL
        CONSTRAINT order_items_price CHECK (unit_price >= 0),
    amount decimal(12, 2) NOT NULL,
    CONSTRAINT order_items_amount CHECK (amount = quantity * unit_price)
);
GO

CREATE TABLE shop.payments (
    id uniqueidentifier NOT NULL CONSTRAINT payments_pk PRIMARY KEY,
    order_id uniqueidentifier NOT NULL
        CONSTRAINT payments_order_fk REFERENCES shop.orders (id)
        CONSTRAINT payments_order_unique UNIQUE,
    method nvarchar(20) NOT NULL
        CONSTRAINT payments_method CHECK (method IN ('card', 'transfer', 'cash', 'wallet')),
    amount decimal(12, 2) NOT NULL CONSTRAINT payments_amount CHECK (amount > 0),
    paid_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE shop.shipments (
    id uniqueidentifier NOT NULL CONSTRAINT shipments_pk PRIMARY KEY,
    order_id uniqueidentifier NOT NULL
        CONSTRAINT shipments_order_fk REFERENCES shop.orders (id),
    carrier nvarchar(100) NOT NULL,
    tracking_code nvarchar(80) NOT NULL CONSTRAINT shipments_tracking UNIQUE,
    shipped_at datetimeoffset(7) NOT NULL,
    delivered_at datetimeoffset(7) NULL,
    CONSTRAINT shipments_delivered CHECK (delivered_at >= shipped_at)
);
GO

CREATE TABLE shop.returns (
    id uniqueidentifier NOT NULL CONSTRAINT returns_pk PRIMARY KEY,
    order_id uniqueidentifier NOT NULL
        CONSTRAINT returns_order_fk REFERENCES shop.orders (id),
    reason nvarchar(300) NOT NULL,
    requested_at datetimeoffset(7) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT returns_status CHECK (status IN ('requested', 'approved', 'rejected', 'received'))
);
GO

CREATE PARTITION FUNCTION pf_order_events (datetime2(7))
    AS RANGE RIGHT FOR VALUES
    ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01');
GO
CREATE PARTITION SCHEME ps_order_events
    AS PARTITION pf_order_events ALL TO ([PRIMARY]);
GO

CREATE TABLE shop.order_events (
    id bigint IDENTITY(1, 1) NOT NULL,
    order_id uniqueidentifier NOT NULL
        CONSTRAINT order_events_order_fk REFERENCES shop.orders (id),
    event_name nvarchar(20) NOT NULL
        CONSTRAINT order_events_name CHECK (event_name IN ('created', 'paid', 'packed', 'shipped', 'delivered', 'cancelled')),
    occurred_at datetime2(7) NOT NULL,
    CONSTRAINT order_events_pk PRIMARY KEY (id, occurred_at)
) ON ps_order_events (occurred_at);
GO

CREATE TRIGGER shop.orders_audit
    ON shop.orders
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'orders',
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

CREATE VIEW shop.open_orders AS
    SELECT id, customer_id, status, ordered_at, total
    FROM shop.orders
    WHERE status IN ('pending', 'paid', 'packed');
GO

CREATE PROCEDURE shop.usp_cancel_order
    @order uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE shop.orders
    SET status = 'cancelled'
    WHERE id = @order;
END;
GO
