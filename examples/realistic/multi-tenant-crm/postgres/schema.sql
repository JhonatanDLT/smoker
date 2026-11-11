-- Realistic multi-tenant CRM schema for PostgreSQL.
-- Exercises: tenant-scoped foreign keys, composite uniqueness, several FKs to
-- the same dimension, audit triggers and application-level segmentation.

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA crm;
SET search_path TO crm, public;

CREATE TABLE tenants (
    id uuid PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    plan text NOT NULL CHECK (plan IN ('free', 'team', 'business', 'enterprise'))
);

CREATE TABLE users (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    email text NOT NULL,
    full_name text NOT NULL,
    CONSTRAINT users_tenant_email UNIQUE (tenant_id, email)
);

CREATE TABLE teams (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    name text NOT NULL,
    CONSTRAINT teams_tenant_name UNIQUE (tenant_id, name)
);

CREATE TABLE team_members (
    team_id uuid NOT NULL REFERENCES teams (id),
    user_id uuid NOT NULL REFERENCES users (id),
    role text NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE companies (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    name text NOT NULL,
    domain text NOT NULL,
    CONSTRAINT companies_tenant_domain UNIQUE (tenant_id, domain)
);

CREATE TABLE contacts (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    company_id uuid NULL REFERENCES companies (id),
    email text NOT NULL,
    full_name text NOT NULL,
    CONSTRAINT contacts_tenant_email UNIQUE (tenant_id, email)
);

CREATE TABLE pipelines (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    name text NOT NULL,
    CONSTRAINT pipelines_tenant_name UNIQUE (tenant_id, name)
);

CREATE TABLE stages (
    id uuid PRIMARY KEY,
    pipeline_id uuid NOT NULL REFERENCES pipelines (id),
    name text NOT NULL,
    position integer NOT NULL CHECK (position > 0),
    CONSTRAINT stages_pipeline_name UNIQUE (pipeline_id, name)
);

CREATE TABLE deals (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    company_id uuid NOT NULL REFERENCES companies (id),
    contact_id uuid NULL REFERENCES contacts (id),
    pipeline_id uuid NOT NULL REFERENCES pipelines (id),
    stage_id uuid NOT NULL REFERENCES stages (id),
    amount numeric(14, 2) NOT NULL CHECK (amount >= 0),
    status text NOT NULL CHECK (status IN ('open', 'won', 'lost')),
    expected_close date NOT NULL,
    description text NULL
);

CREATE TABLE activities (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    deal_id uuid NULL REFERENCES deals (id),
    contact_id uuid NULL REFERENCES contacts (id),
    kind text NOT NULL CHECK (kind IN ('call', 'email', 'meeting', 'task')),
    subject text NOT NULL,
    due_at timestamptz NOT NULL
);

CREATE TABLE notes (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    deal_id uuid NOT NULL REFERENCES deals (id),
    body text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE attachments (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    deal_id uuid NOT NULL REFERENCES deals (id),
    filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0)
);

CREATE TABLE audit.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_deals() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit.audit_log (table_name, action, payload, changed_at)
    VALUES (
        'deals',
        lower(TG_OP),
        jsonb_build_object(
            'id', COALESCE(NEW.id, OLD.id),
            'tenant_id', COALESCE(NEW.tenant_id, OLD.tenant_id),
            'status', COALESCE(NEW.status, OLD.status)
        ),
        now()
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER deals_audit
    AFTER INSERT OR UPDATE OR DELETE ON deals
    FOR EACH ROW EXECUTE FUNCTION audit_deals();

CREATE VIEW pipeline_value AS
    SELECT tenant_id, pipeline_id, status, sum(amount) AS total
    FROM deals
    GROUP BY tenant_id, pipeline_id, status;

CREATE PROCEDURE close_deal(p_deal uuid, p_status text)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE crm.deals
    SET status = p_status
    WHERE id = p_deal;
END;
$$;
