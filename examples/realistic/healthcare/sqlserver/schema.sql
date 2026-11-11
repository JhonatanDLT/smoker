-- Realistic healthcare schema for SQL Server.
-- Exercises: one-to-one foreign keys, temporal encounters, partitioned lab
-- results, clinical checks, triggers and stored procedures.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('health') IS NULL EXEC('CREATE SCHEMA health');
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

CREATE TABLE health.patients (
    id uniqueidentifier NOT NULL CONSTRAINT patients_pk PRIMARY KEY,
    mrn nvarchar(40) NOT NULL CONSTRAINT patients_mrn UNIQUE,
    full_name nvarchar(200) NOT NULL,
    birth_date date NOT NULL,
    sex nvarchar(10) NOT NULL
        CONSTRAINT patients_sex CHECK (sex IN ('female', 'male', 'other', 'unknown')),
    phone nvarchar(40) NULL
);
GO

CREATE TABLE health.providers (
    id uniqueidentifier NOT NULL CONSTRAINT providers_pk PRIMARY KEY,
    npi nvarchar(20) NOT NULL CONSTRAINT providers_npi UNIQUE,
    full_name nvarchar(200) NOT NULL,
    specialty nvarchar(120) NOT NULL
);
GO

CREATE TABLE health.facilities (
    id int NOT NULL CONSTRAINT facilities_pk PRIMARY KEY,
    name nvarchar(200) NOT NULL CONSTRAINT facilities_name UNIQUE,
    city nvarchar(120) NOT NULL
);
GO

CREATE TABLE health.appointments (
    id uniqueidentifier NOT NULL CONSTRAINT appointments_pk PRIMARY KEY,
    patient_id uniqueidentifier NOT NULL
        CONSTRAINT appointments_patient_fk REFERENCES health.patients (id),
    provider_id uniqueidentifier NOT NULL
        CONSTRAINT appointments_provider_fk REFERENCES health.providers (id),
    facility_id int NOT NULL
        CONSTRAINT appointments_facility_fk REFERENCES health.facilities (id),
    period_start datetime2(7) NOT NULL,
    period_end datetime2(7) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT appointments_status CHECK (status IN ('scheduled', 'checked_in', 'completed', 'cancelled', 'no_show')),
    CONSTRAINT appointments_period CHECK (period_end > period_start)
);
GO

CREATE TABLE health.encounters (
    id uniqueidentifier NOT NULL CONSTRAINT encounters_pk PRIMARY KEY,
    appointment_id uniqueidentifier NOT NULL
        CONSTRAINT encounters_appointment_fk REFERENCES health.appointments (id)
        CONSTRAINT encounters_appointment_unique UNIQUE,
    patient_id uniqueidentifier NOT NULL
        CONSTRAINT encounters_patient_fk REFERENCES health.patients (id),
    provider_id uniqueidentifier NOT NULL
        CONSTRAINT encounters_provider_fk REFERENCES health.providers (id),
    started_at datetimeoffset(7) NOT NULL,
    ended_at datetimeoffset(7) NULL,
    notes nvarchar(600) NULL,
    CONSTRAINT encounters_end CHECK (ended_at >= started_at),
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = health.encounters_history));
GO

CREATE TABLE health.diagnoses (
    id uniqueidentifier NOT NULL CONSTRAINT diagnoses_pk PRIMARY KEY,
    encounter_id uniqueidentifier NOT NULL
        CONSTRAINT diagnoses_encounter_fk REFERENCES health.encounters (id),
    code nvarchar(20) NOT NULL,
    description nvarchar(300) NOT NULL,
    is_primary bit NOT NULL CONSTRAINT diagnoses_primary DEFAULT (0)
);
GO

CREATE TABLE health.prescriptions (
    id uniqueidentifier NOT NULL CONSTRAINT prescriptions_pk PRIMARY KEY,
    encounter_id uniqueidentifier NOT NULL
        CONSTRAINT prescriptions_encounter_fk REFERENCES health.encounters (id),
    patient_id uniqueidentifier NOT NULL
        CONSTRAINT prescriptions_patient_fk REFERENCES health.patients (id),
    medication nvarchar(200) NOT NULL,
    dosage nvarchar(120) NOT NULL,
    refills int NOT NULL CONSTRAINT prescriptions_refills CHECK (refills >= 0),
    prescribed_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE health.medication_orders (
    id uniqueidentifier NOT NULL CONSTRAINT medication_orders_pk PRIMARY KEY,
    prescription_id uniqueidentifier NOT NULL
        CONSTRAINT medication_orders_prescription_fk REFERENCES health.prescriptions (id),
    status nvarchar(20) NOT NULL
        CONSTRAINT medication_orders_status CHECK (status IN ('ordered', 'dispensed', 'cancelled')),
    ordered_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE health.lab_orders (
    id uniqueidentifier NOT NULL CONSTRAINT lab_orders_pk PRIMARY KEY,
    encounter_id uniqueidentifier NOT NULL
        CONSTRAINT lab_orders_encounter_fk REFERENCES health.encounters (id),
    panel nvarchar(120) NOT NULL,
    ordered_at datetimeoffset(7) NOT NULL,
    status nvarchar(20) NOT NULL
        CONSTRAINT lab_orders_status CHECK (status IN ('ordered', 'collected', 'resulted', 'cancelled'))
);
GO

CREATE PARTITION FUNCTION pf_lab_results (datetime2(7))
    AS RANGE RIGHT FOR VALUES
    ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01');
GO
CREATE PARTITION SCHEME ps_lab_results
    AS PARTITION pf_lab_results ALL TO ([PRIMARY]);
GO

CREATE TABLE health.lab_results (
    id uniqueidentifier NOT NULL,
    lab_order_id uniqueidentifier NOT NULL
        CONSTRAINT lab_results_order_fk REFERENCES health.lab_orders (id),
    analyte nvarchar(120) NOT NULL,
    value decimal(12, 4) NOT NULL,
    unit nvarchar(30) NOT NULL,
    collected_at datetime2(7) NOT NULL,
    CONSTRAINT lab_results_pk PRIMARY KEY (id, collected_at)
) ON ps_lab_results (collected_at);
GO

CREATE TABLE health.insurance_plans (
    id uniqueidentifier NOT NULL CONSTRAINT insurance_plans_pk PRIMARY KEY,
    payer nvarchar(160) NOT NULL,
    plan_name nvarchar(160) NOT NULL,
    CONSTRAINT insurance_payer_plan UNIQUE (payer, plan_name)
);
GO

CREATE TABLE health.claims (
    id uniqueidentifier NOT NULL CONSTRAINT claims_pk PRIMARY KEY,
    encounter_id uniqueidentifier NOT NULL
        CONSTRAINT claims_encounter_fk REFERENCES health.encounters (id),
    insurance_plan_id uniqueidentifier NOT NULL
        CONSTRAINT claims_plan_fk REFERENCES health.insurance_plans (id),
    amount decimal(14, 2) NOT NULL CONSTRAINT claims_amount CHECK (amount >= 0),
    status nvarchar(20) NOT NULL
        CONSTRAINT claims_status CHECK (status IN ('draft', 'submitted', 'paid', 'denied')),
    submitted_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE health.claim_lines (
    claim_id uniqueidentifier NOT NULL
        CONSTRAINT claim_lines_claim_fk REFERENCES health.claims (id),
    line_no int NOT NULL CONSTRAINT claim_lines_line CHECK (line_no > 0),
    code nvarchar(30) NOT NULL,
    amount decimal(12, 2) NOT NULL CONSTRAINT claim_lines_amount CHECK (amount >= 0),
    CONSTRAINT claim_lines_pk PRIMARY KEY (claim_id, line_no)
);
GO

CREATE TABLE health.vitals (
    id uniqueidentifier NOT NULL CONSTRAINT vitals_pk PRIMARY KEY,
    encounter_id uniqueidentifier NOT NULL
        CONSTRAINT vitals_encounter_fk REFERENCES health.encounters (id),
    recorded_at datetimeoffset(7) NOT NULL,
    systolic int NOT NULL
        CONSTRAINT vitals_systolic CHECK (systolic >= 0 AND systolic <= 300),
    diastolic int NOT NULL
        CONSTRAINT vitals_diastolic CHECK (diastolic >= 0 AND diastolic <= 200),
    temperature decimal(5, 2) NOT NULL
        CONSTRAINT vitals_temperature CHECK (temperature >= 30 AND temperature <= 45)
);
GO

CREATE TRIGGER health.encounters_audit
    ON health.encounters
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'encounters',
        CASE
            WHEN d.id IS NULL THEN N'insert'
            WHEN i.id IS NULL THEN N'delete'
            ELSE N'update'
        END,
        CONCAT(
            N'{"id":"',
            CONVERT(nvarchar(36), COALESCE(i.id, d.id)),
            N'","patient":"',
            CONVERT(nvarchar(36), COALESCE(i.patient_id, d.patient_id)),
            N'"}'
        ),
        SYSUTCDATETIME()
    FROM inserted AS i
    FULL OUTER JOIN deleted AS d ON i.id = d.id;
END;
GO

CREATE VIEW health.active_prescriptions AS
    SELECT id, patient_id, medication, prescribed_at
    FROM health.prescriptions;
GO

CREATE PROCEDURE health.usp_cancel_appointment
    @appointment uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE health.appointments
    SET status = 'cancelled'
    WHERE id = @appointment;
END;
GO
