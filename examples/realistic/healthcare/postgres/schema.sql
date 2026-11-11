-- Realistic healthcare schema for PostgreSQL.
-- Exercises: EXCLUDE ranges, one-to-one foreign keys, composite keys,
-- partitioned lab results, clinical range checks and audit triggers.

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA health;
SET search_path TO health, public;

CREATE TABLE patients (
    id uuid PRIMARY KEY,
    mrn text NOT NULL UNIQUE,
    full_name text NOT NULL,
    birth_date date NOT NULL,
    sex text NOT NULL CHECK (sex IN ('female', 'male', 'other', 'unknown')),
    phone text NULL
);

CREATE TABLE providers (
    id uuid PRIMARY KEY,
    npi text NOT NULL UNIQUE,
    full_name text NOT NULL,
    specialty text NOT NULL
);

CREATE TABLE facilities (
    id integer PRIMARY KEY,
    name text NOT NULL UNIQUE,
    city text NOT NULL
);

CREATE TABLE appointments (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patients (id),
    provider_id uuid NOT NULL REFERENCES providers (id),
    facility_id integer NOT NULL REFERENCES facilities (id),
    period tstzrange NOT NULL,
    status text NOT NULL CHECK (status IN ('scheduled', 'checked_in', 'completed', 'cancelled', 'no_show')),
    CONSTRAINT appointments_provider_excl
        EXCLUDE USING gist (provider_id WITH =, period WITH &&)
);

CREATE TABLE encounters (
    id uuid PRIMARY KEY,
    appointment_id uuid NOT NULL UNIQUE REFERENCES appointments (id),
    patient_id uuid NOT NULL REFERENCES patients (id),
    provider_id uuid NOT NULL REFERENCES providers (id),
    started_at timestamptz NOT NULL,
    ended_at timestamptz NULL,
    notes text NULL,
    CHECK (ended_at >= started_at)
);

CREATE TABLE diagnoses (
    id uuid PRIMARY KEY,
    encounter_id uuid NOT NULL REFERENCES encounters (id),
    code text NOT NULL,
    description text NOT NULL,
    is_primary boolean NOT NULL DEFAULT false
);

CREATE TABLE prescriptions (
    id uuid PRIMARY KEY,
    encounter_id uuid NOT NULL REFERENCES encounters (id),
    patient_id uuid NOT NULL REFERENCES patients (id),
    medication text NOT NULL,
    dosage text NOT NULL,
    refills integer NOT NULL CHECK (refills >= 0),
    prescribed_at timestamptz NOT NULL
);

CREATE TABLE medication_orders (
    id uuid PRIMARY KEY,
    prescription_id uuid NOT NULL REFERENCES prescriptions (id),
    status text NOT NULL CHECK (status IN ('ordered', 'dispensed', 'cancelled')),
    ordered_at timestamptz NOT NULL
);

CREATE TABLE lab_orders (
    id uuid PRIMARY KEY,
    encounter_id uuid NOT NULL REFERENCES encounters (id),
    panel text NOT NULL,
    ordered_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('ordered', 'collected', 'resulted', 'cancelled'))
);

CREATE TABLE lab_results (
    id uuid NOT NULL,
    lab_order_id uuid NOT NULL REFERENCES lab_orders (id),
    analyte text NOT NULL,
    value numeric(12, 4) NOT NULL,
    unit text NOT NULL,
    collected_at timestamptz NOT NULL,
    PRIMARY KEY (id, collected_at)
) PARTITION BY RANGE (collected_at);

CREATE TABLE lab_results_2023 PARTITION OF lab_results
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE lab_results_2024 PARTITION OF lab_results
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE lab_results_2025 PARTITION OF lab_results
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE lab_results_default PARTITION OF lab_results DEFAULT;

CREATE TABLE insurance_plans (
    id uuid PRIMARY KEY,
    payer text NOT NULL,
    plan_name text NOT NULL,
    CONSTRAINT insurance_payer_plan UNIQUE (payer, plan_name)
);

CREATE TABLE claims (
    id uuid PRIMARY KEY,
    encounter_id uuid NOT NULL REFERENCES encounters (id),
    insurance_plan_id uuid NOT NULL REFERENCES insurance_plans (id),
    amount numeric(14, 2) NOT NULL CHECK (amount >= 0),
    status text NOT NULL CHECK (status IN ('draft', 'submitted', 'paid', 'denied')),
    submitted_at timestamptz NOT NULL
);

CREATE TABLE claim_lines (
    claim_id uuid NOT NULL REFERENCES claims (id),
    line_no integer NOT NULL CHECK (line_no > 0),
    code text NOT NULL,
    amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (claim_id, line_no)
);

CREATE TABLE vitals (
    id uuid PRIMARY KEY,
    encounter_id uuid NOT NULL REFERENCES encounters (id),
    recorded_at timestamptz NOT NULL,
    systolic integer NOT NULL CHECK (systolic >= 0 AND systolic <= 300),
    diastolic integer NOT NULL CHECK (diastolic >= 0 AND diastolic <= 200),
    temperature numeric(5, 2) NOT NULL CHECK (temperature >= 30 AND temperature <= 45)
);

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_encounters() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'encounters',
        lower(TG_OP),
        jsonb_build_object(
            'id', COALESCE(NEW.id, OLD.id),
            'patient_id', COALESCE(NEW.patient_id, OLD.patient_id)
        ),
        now()
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER encounters_audit
    AFTER INSERT OR UPDATE OR DELETE ON encounters
    FOR EACH ROW EXECUTE FUNCTION audit_encounters();

CREATE VIEW active_prescriptions AS
    SELECT id, patient_id, medication, prescribed_at
    FROM prescriptions
    WHERE prescribed_at IS NOT NULL;

CREATE PROCEDURE cancel_appointment(p_appointment uuid)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE health.appointments
    SET status = 'cancelled'
    WHERE id = p_appointment;
END;
$$;
