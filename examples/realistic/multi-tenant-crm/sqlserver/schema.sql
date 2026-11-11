-- Realistic multi-tenant CRM schema for SQL Server.
-- Exercises: tenant-scoped foreign keys, composite keys made of FKs, temporal
-- deals, audit triggers and several references to the same dimensions.

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO
IF SCHEMA_ID('crm') IS NULL EXEC('CREATE SCHEMA crm');
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

CREATE TABLE crm.tenants (
    id uniqueidentifier NOT NULL CONSTRAINT tenants_pk PRIMARY KEY,
    slug nvarchar(80) NOT NULL CONSTRAINT tenants_slug UNIQUE,
    name nvarchar(200) NOT NULL,
    plan_code nvarchar(20) NOT NULL
        CONSTRAINT tenants_plan CHECK (plan_code IN ('free', 'team', 'business', 'enterprise'))
);
GO

CREATE TABLE crm.users (
    id uniqueidentifier NOT NULL CONSTRAINT users_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT users_tenant_fk REFERENCES crm.tenants (id),
    email nvarchar(320) NOT NULL,
    full_name nvarchar(200) NOT NULL,
    CONSTRAINT users_tenant_email UNIQUE (tenant_id, email)
);
GO

CREATE TABLE crm.teams (
    id uniqueidentifier NOT NULL CONSTRAINT teams_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT teams_tenant_fk REFERENCES crm.tenants (id),
    name nvarchar(160) NOT NULL,
    CONSTRAINT teams_tenant_name UNIQUE (tenant_id, name)
);
GO

CREATE TABLE crm.team_members (
    team_id uniqueidentifier NOT NULL
        CONSTRAINT team_members_team_fk REFERENCES crm.teams (id),
    user_id uniqueidentifier NOT NULL
        CONSTRAINT team_members_user_fk REFERENCES crm.users (id),
    role nvarchar(20) NOT NULL
        CONSTRAINT team_members_role CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    CONSTRAINT team_members_pk PRIMARY KEY (team_id, user_id)
);
GO

CREATE TABLE crm.companies (
    id uniqueidentifier NOT NULL CONSTRAINT companies_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT companies_tenant_fk REFERENCES crm.tenants (id),
    name nvarchar(200) NOT NULL,
    domain nvarchar(200) NOT NULL,
    CONSTRAINT companies_tenant_domain UNIQUE (tenant_id, domain)
);
GO

CREATE TABLE crm.contacts (
    id uniqueidentifier NOT NULL CONSTRAINT contacts_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT contacts_tenant_fk REFERENCES crm.tenants (id),
    company_id uniqueidentifier NULL
        CONSTRAINT contacts_company_fk REFERENCES crm.companies (id),
    email nvarchar(320) NOT NULL,
    full_name nvarchar(200) NOT NULL,
    CONSTRAINT contacts_tenant_email UNIQUE (tenant_id, email)
);
GO

CREATE TABLE crm.pipelines (
    id uniqueidentifier NOT NULL CONSTRAINT pipelines_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT pipelines_tenant_fk REFERENCES crm.tenants (id),
    name nvarchar(160) NOT NULL,
    CONSTRAINT pipelines_tenant_name UNIQUE (tenant_id, name)
);
GO

CREATE TABLE crm.stages (
    id uniqueidentifier NOT NULL CONSTRAINT stages_pk PRIMARY KEY,
    pipeline_id uniqueidentifier NOT NULL
        CONSTRAINT stages_pipeline_fk REFERENCES crm.pipelines (id),
    name nvarchar(160) NOT NULL,
    position int NOT NULL CONSTRAINT stages_position CHECK (position > 0),
    CONSTRAINT stages_pipeline_name UNIQUE (pipeline_id, name)
);
GO

CREATE TABLE crm.deals (
    id uniqueidentifier NOT NULL CONSTRAINT deals_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT deals_tenant_fk REFERENCES crm.tenants (id),
    company_id uniqueidentifier NOT NULL
        CONSTRAINT deals_company_fk REFERENCES crm.companies (id),
    contact_id uniqueidentifier NULL
        CONSTRAINT deals_contact_fk REFERENCES crm.contacts (id),
    pipeline_id uniqueidentifier NOT NULL
        CONSTRAINT deals_pipeline_fk REFERENCES crm.pipelines (id),
    stage_id uniqueidentifier NOT NULL
        CONSTRAINT deals_stage_fk REFERENCES crm.stages (id),
    amount decimal(14, 2) NOT NULL CONSTRAINT deals_amount CHECK (amount >= 0),
    status nvarchar(20) NOT NULL
        CONSTRAINT deals_status CHECK (status IN ('open', 'won', 'lost')),
    expected_close date NOT NULL,
    description nvarchar(400) NULL,
    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = crm.deals_history));
GO

CREATE TABLE crm.activities (
    id uniqueidentifier NOT NULL CONSTRAINT activities_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT activities_tenant_fk REFERENCES crm.tenants (id),
    deal_id uniqueidentifier NULL
        CONSTRAINT activities_deal_fk REFERENCES crm.deals (id),
    contact_id uniqueidentifier NULL
        CONSTRAINT activities_contact_fk REFERENCES crm.contacts (id),
    kind nvarchar(20) NOT NULL
        CONSTRAINT activities_kind CHECK (kind IN ('call', 'email', 'meeting', 'task')),
    subject nvarchar(300) NOT NULL,
    due_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE crm.notes (
    id uniqueidentifier NOT NULL CONSTRAINT notes_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT notes_tenant_fk REFERENCES crm.tenants (id),
    deal_id uniqueidentifier NOT NULL
        CONSTRAINT notes_deal_fk REFERENCES crm.deals (id),
    body nvarchar(max) NOT NULL,
    created_at datetimeoffset(7) NOT NULL
);
GO

CREATE TABLE crm.attachments (
    id uniqueidentifier NOT NULL CONSTRAINT attachments_pk PRIMARY KEY,
    tenant_id uniqueidentifier NOT NULL
        CONSTRAINT attachments_tenant_fk REFERENCES crm.tenants (id),
    deal_id uniqueidentifier NOT NULL
        CONSTRAINT attachments_deal_fk REFERENCES crm.deals (id),
    filename nvarchar(300) NOT NULL,
    content_type nvarchar(120) NOT NULL,
    size_bytes bigint NOT NULL
        CONSTRAINT attachments_size CHECK (size_bytes >= 0)
);
GO

CREATE TRIGGER crm.deals_audit
    ON crm.deals
    AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    SELECT
        N'deals',
        CASE
            WHEN d.id IS NULL THEN N'insert'
            WHEN i.id IS NULL THEN N'delete'
            ELSE N'update'
        END,
        CONCAT(
            N'{"id":"',
            CONVERT(nvarchar(36), COALESCE(i.id, d.id)),
            N'","tenant":"',
            CONVERT(nvarchar(36), COALESCE(i.tenant_id, d.tenant_id)),
            N'","status":"',
            COALESCE(i.status, d.status),
            N'"}'
        ),
        SYSUTCDATETIME()
    FROM inserted AS i
    FULL OUTER JOIN deleted AS d ON i.id = d.id;
END;
GO

CREATE VIEW crm.pipeline_value AS
    SELECT tenant_id, pipeline_id, status, SUM(amount) AS total
    FROM crm.deals
    GROUP BY tenant_id, pipeline_id, status;
GO

CREATE PROCEDURE crm.usp_close_deal
    @deal uniqueidentifier,
    @status nvarchar(20)
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE crm.deals
    SET status = @status
    WHERE id = @deal;
END;
GO
