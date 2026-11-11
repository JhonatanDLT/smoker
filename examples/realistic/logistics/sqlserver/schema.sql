-- Realistic logistics schema for SQL Server.
-- Exercises: composite keys, temporal shipments, partitioned package events,
-- many foreign keys and audit triggers.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('logistics') IS NULL EXEC('CREATE SCHEMA logistics');
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

CREATE TABLE logistics.warehouses (
    id int NOT NULL CONSTRAINT warehouses_pk PRIMARY KEY,
    code nvarchar(30) NOT NULL CONSTRAINT warehouses_code UNIQUE,
    name nvarchar(200) NOT NULL,
    city nvarchar(120) NOT NULL
);
GO

CREATE TABLE logistics.zones (
    warehouse_id int NOT NULL
        CONSTRAINT zones_warehouse_fk REFERENCES logistics.warehouses (id),
    zone_code nvarchar(20) NOT NULL,
    name nvarchar(120) NOT NULL,
    CONSTRAINT zones_pk PRIMARY KEY (warehouse_id, zone_code)
);
GO

CREATE TABLE logistics.carriers (
    id uniqueidentifier NOT NULL CONSTRAINT carriers_pk PRIMARY KEY,
    name nvarchar(200) NOT NULL CONSTRAINT carriers_name UNIQUE,
    scac nchar(4) NOT NULL CONSTRAINT carriers_scac UNIQUE
);
GO

CREATE TABLE logistics.vehicles (
    id uniqueidentifier NOT NULL CONSTRAINT vehicles_pk PRIMARY KEY,
    carrier_id uniqueidentifier NOT NULL
        CONSTRAINT vehicles_carrier_fk REFERENCES logistics.carriers (id),
    plate nvarchar(20) NOT NULL CONSTRAINT vehicles_plate UNIQUE,
    capacity_kg decimal(10, 2) NOT NULL
        CONSTRAINT vehicles_capacity CHECK (capacity_kg > 0),
    active bit NOT NULL CONSTRAINT vehicles_active DEFAULT (1)
);
GO

CREATE TABLE logistics.drivers (
    id uniqueidentifier NOT NULL CONSTRAINT drivers_pk PRIMARY KEY,
    carrier_id uniqueidentifier NOT NULL
        CONSTRAINT drivers_carrier_fk REFERENCES logistics.carriers (id),
    license_number nvarchar(40) NOT NULL CONSTRAINT drivers_license UNIQUE,
    full_name nvarchar(200) NOT NULL,
    phone nvarchar(40) NULL
);
GO

CREATE TABLE logistics.routes (
    id uniqueidentifier NOT NULL CONSTRAINT routes_pk PRIMARY KEY,
    carrier_id uniqueidentifier NOT NULL
        CONSTRAINT routes_carrier_fk REFERENCES logistics.carriers (id),
    origin_warehouse_id int NOT NULL
        CONSTRAINT routes_origin_fk REFERENCES logistics.warehouses (id),
    destination_warehouse_id int NOT NULL
        CONSTRAINT routes_destination_fk REFERENCES logistics.warehouses (id),
    distance_km decimal(10, 2) NOT NULL
        CONSTRAINT routes_distance CHECK (distance_km > 0)
);
GO

CREATE TABLE logistics.shipments (
    id uniqueidentifier NOT NULL CONSTRAINT shipments_pk PRIMARY KEY,
    reference nvarchar(60) NOT NULL CONSTRAINT shipments_reference UNIQUE,
    origin_warehouse_id int NOT NULL
        CONSTRAINT shipments_origin_fk REFERENCES logistics.warehouses (id),
    destination_warehouse_id int NOT NULL
        CONSTRAINT shipments_destination_fk REFERENCES logistics.warehouses (id),
    carrier_id uniqueidentifier NOT NULL
        CONSTRAINT shipments_carrier_fk REFERENCES logistics.carriers (id),
    status nvarchar(20) NOT NULL
        CONSTRAINT shipments_status CHECK (status IN ('created', 'in_transit', 'delivered', 'returned', 'cancelled')),
    weight_kg decimal(10, 2) NOT NULL
        CONSTRAINT shipments_weight CHECK (weight_kg > 0),
    created_at datetimeoffset(7) NOT NULL,
    notes nvarchar(400) NULL,
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = logistics.shipments_history));
GO

CREATE TABLE logistics.shipment_legs (
    id uniqueidentifier NOT NULL CONSTRAINT shipment_legs_pk PRIMARY KEY,
    shipment_id uniqueidentifier NOT NULL
        CONSTRAINT shipment_legs_shipment_fk REFERENCES logistics.shipments (id),
    vehicle_id uniqueidentifier NOT NULL
        CONSTRAINT shipment_legs_vehicle_fk REFERENCES logistics.vehicles (id),
    driver_id uniqueidentifier NOT NULL
        CONSTRAINT shipment_legs_driver_fk REFERENCES logistics.drivers (id),
    sequence int NOT NULL
        CONSTRAINT shipment_legs_sequence CHECK (sequence > 0),
    started_at datetimeoffset(7) NOT NULL,
    ended_at datetimeoffset(7) NULL,
    CONSTRAINT shipment_legs_order UNIQUE (shipment_id, sequence),
    CONSTRAINT shipment_legs_end CHECK (ended_at >= started_at)
);
GO

CREATE TABLE logistics.packages (
    id uniqueidentifier NOT NULL CONSTRAINT packages_pk PRIMARY KEY,
    shipment_id uniqueidentifier NOT NULL
        CONSTRAINT packages_shipment_fk REFERENCES logistics.shipments (id),
    barcode nvarchar(80) NOT NULL CONSTRAINT packages_barcode UNIQUE,
    weight_kg decimal(10, 2) NOT NULL CONSTRAINT packages_weight CHECK (weight_kg > 0),
    length_cm decimal(8, 2) NOT NULL CONSTRAINT packages_length CHECK (length_cm > 0),
    width_cm decimal(8, 2) NOT NULL CONSTRAINT packages_width CHECK (width_cm > 0),
    height_cm decimal(8, 2) NOT NULL CONSTRAINT packages_height CHECK (height_cm > 0)
);
GO

CREATE PARTITION FUNCTION pf_package_events (datetime2(7))
    AS RANGE RIGHT FOR VALUES
    ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01');
GO
CREATE PARTITION SCHEME ps_package_events
    AS PARTITION pf_package_events ALL TO ([PRIMARY]);
GO

CREATE TABLE logistics.package_events (
    id bigint IDENTITY(1, 1) NOT NULL,
    package_id uniqueidentifier NOT NULL
        CONSTRAINT package_events_package_fk REFERENCES logistics.packages (id),
    shipment_id uniqueidentifier NOT NULL
        CONSTRAINT package_events_shipment_fk REFERENCES logistics.shipments (id),
    status nvarchar(30) NOT NULL
        CONSTRAINT package_events_status CHECK (status IN ('picked_up', 'in_transit', 'out_for_delivery', 'delivered', 'exception')),
    occurred_at datetime2(7) NOT NULL,
    CONSTRAINT package_events_pk PRIMARY KEY (id, occurred_at)
) ON ps_package_events (occurred_at);
GO

CREATE TABLE logistics.delivery_attempts (
    id uniqueidentifier NOT NULL CONSTRAINT delivery_attempts_pk PRIMARY KEY,
    shipment_id uniqueidentifier NOT NULL
        CONSTRAINT delivery_attempts_shipment_fk REFERENCES logistics.shipments (id),
    attempted_at datetimeoffset(7) NOT NULL,
    outcome nvarchar(30) NOT NULL
        CONSTRAINT delivery_attempts_outcome CHECK (outcome IN ('delivered', 'absent', 'refused', 'wrong_address')),
    notes nvarchar(400) NULL
);
GO

CREATE TABLE logistics.incidents (
    id uniqueidentifier NOT NULL CONSTRAINT incidents_pk PRIMARY KEY,
    shipment_id uniqueidentifier NOT NULL
        CONSTRAINT incidents_shipment_fk REFERENCES logistics.shipments (id),
    severity nvarchar(20) NOT NULL
        CONSTRAINT incidents_severity CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    opened_at datetimeoffset(7) NOT NULL,
    closed_at datetimeoffset(7) NULL,
    description nvarchar(400) NOT NULL,
    CONSTRAINT incidents_closed CHECK (closed_at >= opened_at)
);
GO

CREATE TRIGGER logistics.shipments_audit
    ON logistics.shipments
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'shipments',
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

CREATE VIEW logistics.open_incidents AS
    SELECT id, shipment_id, severity, opened_at
    FROM logistics.incidents
    WHERE closed_at IS NULL;
GO

CREATE PROCEDURE logistics.usp_close_incident
    @incident uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE logistics.incidents
    SET closed_at = SYSUTCDATETIME()
    WHERE id = @incident;
END;
GO
