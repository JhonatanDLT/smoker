-- Realistic logistics schema for PostgreSQL.
-- Exercises: composite keys, many foreign keys, range partitions, date
-- ordering checks, audit triggers and derived-free check constraints.

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA logistics;
SET search_path TO logistics, public;

CREATE TABLE warehouses (
    id integer PRIMARY KEY,
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    city text NOT NULL
);

CREATE TABLE zones (
    warehouse_id integer NOT NULL REFERENCES warehouses (id),
    zone_code text NOT NULL,
    name text NOT NULL,
    PRIMARY KEY (warehouse_id, zone_code)
);

CREATE TABLE carriers (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    scac char(4) NOT NULL UNIQUE
);

CREATE TABLE vehicles (
    id uuid PRIMARY KEY,
    carrier_id uuid NOT NULL REFERENCES carriers (id),
    plate text NOT NULL UNIQUE,
    capacity_kg numeric(10, 2) NOT NULL CHECK (capacity_kg > 0),
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE drivers (
    id uuid PRIMARY KEY,
    carrier_id uuid NOT NULL REFERENCES carriers (id),
    license_number text NOT NULL UNIQUE,
    full_name text NOT NULL,
    phone text NULL
);

CREATE TABLE routes (
    id uuid PRIMARY KEY,
    carrier_id uuid NOT NULL REFERENCES carriers (id),
    origin_warehouse_id integer NOT NULL REFERENCES warehouses (id),
    destination_warehouse_id integer NOT NULL REFERENCES warehouses (id),
    distance_km numeric(10, 2) NOT NULL CHECK (distance_km > 0)
);

CREATE TABLE shipments (
    id uuid PRIMARY KEY,
    reference text NOT NULL UNIQUE,
    origin_warehouse_id integer NOT NULL REFERENCES warehouses (id),
    destination_warehouse_id integer NOT NULL REFERENCES warehouses (id),
    carrier_id uuid NOT NULL REFERENCES carriers (id),
    status text NOT NULL CHECK (status IN ('created', 'in_transit', 'delivered', 'returned', 'cancelled')),
    weight_kg numeric(10, 2) NOT NULL CHECK (weight_kg > 0),
    created_at timestamptz NOT NULL,
    notes text NULL
);

CREATE TABLE shipment_legs (
    id uuid PRIMARY KEY,
    shipment_id uuid NOT NULL REFERENCES shipments (id),
    vehicle_id uuid NOT NULL REFERENCES vehicles (id),
    driver_id uuid NOT NULL REFERENCES drivers (id),
    sequence integer NOT NULL CHECK (sequence > 0),
    started_at timestamptz NOT NULL,
    ended_at timestamptz NULL,
    CONSTRAINT shipment_legs_sequence UNIQUE (shipment_id, sequence),
    CHECK (ended_at >= started_at)
);

CREATE TABLE packages (
    id uuid PRIMARY KEY,
    shipment_id uuid NOT NULL REFERENCES shipments (id),
    barcode text NOT NULL UNIQUE,
    weight_kg numeric(10, 2) NOT NULL CHECK (weight_kg > 0),
    length_cm numeric(8, 2) NOT NULL CHECK (length_cm > 0),
    width_cm numeric(8, 2) NOT NULL CHECK (width_cm > 0),
    height_cm numeric(8, 2) NOT NULL CHECK (height_cm > 0)
);

CREATE TABLE package_events (
    id bigint NOT NULL,
    package_id uuid NOT NULL REFERENCES packages (id),
    shipment_id uuid NOT NULL REFERENCES shipments (id),
    status text NOT NULL CHECK (status IN ('picked_up', 'in_transit', 'out_for_delivery', 'delivered', 'exception')),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE package_events_2023 PARTITION OF package_events
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE package_events_2024 PARTITION OF package_events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE package_events_2025 PARTITION OF package_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE package_events_default PARTITION OF package_events DEFAULT;

CREATE TABLE delivery_attempts (
    id uuid PRIMARY KEY,
    shipment_id uuid NOT NULL REFERENCES shipments (id),
    attempted_at timestamptz NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('delivered', 'absent', 'refused', 'wrong_address')),
    notes text NULL
);

CREATE TABLE incidents (
    id uuid PRIMARY KEY,
    shipment_id uuid NOT NULL REFERENCES shipments (id),
    severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    opened_at timestamptz NOT NULL,
    closed_at timestamptz NULL,
    description text NOT NULL,
    CHECK (closed_at >= opened_at)
);

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_shipments() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'shipments',
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

CREATE TRIGGER shipments_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipments
    FOR EACH ROW EXECUTE FUNCTION audit_shipments();

CREATE VIEW open_incidents AS
    SELECT id, shipment_id, severity, opened_at
    FROM incidents
    WHERE closed_at IS NULL;

CREATE PROCEDURE close_incident(p_incident uuid)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE logistics.incidents
    SET closed_at = now()
    WHERE id = p_incident;
END;
$$;
