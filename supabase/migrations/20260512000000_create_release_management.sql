-- Release Management tables for automated deployment workflows

-- Create release status enum type
DO $$ BEGIN
    CREATE TYPE release_status AS ENUM (
        'draft',
        'planning',
        'executing',
        'awaiting_review',
        'deploying',
        'verifying',
        'completed',
        'failed',
        'cancelled'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create release step type enum
DO $$ BEGIN
    CREATE TYPE release_step_type AS ENUM (
        'jira_fetch',
        'confluence_parse',
        'plan_changes',
        'approve_plan',
        'apply_yaml',
        'sops_commands',
        'create_pr',
        'approve_pr',
        'approve_sync',
        'argocd_sync',
        'health_check',
        'approve_deploy'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create release step status enum
DO $$ BEGIN
    CREATE TYPE release_step_status AS ENUM (
        'pending',
        'in_progress',
        'completed',
        'failed',
        'skipped',
        'awaiting_approval'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create approval decision enum
DO $$ BEGIN
    CREATE TYPE approval_decision AS ENUM (
        'approved',
        'rejected'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Releases table: top-level release workflow records
CREATE TABLE IF NOT EXISTS releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jira_ticket_id TEXT NOT NULL,
    version TEXT NOT NULL,
    region TEXT NOT NULL,
    status release_status NOT NULL DEFAULT 'draft',
    confluence_page_url TEXT,
    release_notes JSONB DEFAULT '{}',
    planned_changes JSONB DEFAULT '{}',
    pr_url TEXT,
    pr_number INTEGER,
    created_by UUID REFERENCES auth.users(id),
    organization_id UUID NOT NULL,
    project_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Release steps table: individual workflow steps
CREATE TABLE IF NOT EXISTS release_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    step_type release_step_type NOT NULL,
    status release_step_status NOT NULL DEFAULT 'pending',
    output JSONB DEFAULT '{}',
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE(release_id, step_type)
);

-- Release approvals table: approval records for gated steps
CREATE TABLE IF NOT EXISTS release_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES release_steps(id) ON DELETE CASCADE,
    approved_by UUID REFERENCES auth.users(id),
    decision approval_decision NOT NULL,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);
CREATE INDEX IF NOT EXISTS idx_releases_org ON releases(organization_id);
CREATE INDEX IF NOT EXISTS idx_releases_jira ON releases(jira_ticket_id);
CREATE INDEX IF NOT EXISTS idx_releases_created ON releases(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_release_steps_release ON release_steps(release_id);
CREATE INDEX IF NOT EXISTS idx_release_steps_status ON release_steps(status);
CREATE INDEX IF NOT EXISTS idx_release_approvals_release ON release_approvals(release_id);
CREATE INDEX IF NOT EXISTS idx_release_approvals_step ON release_approvals(step_id);

-- Auto-update updated_at on releases
CREATE OR REPLACE FUNCTION update_releases_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS releases_updated_at ON releases;
CREATE TRIGGER releases_updated_at
    BEFORE UPDATE ON releases
    FOR EACH ROW
    EXECUTE FUNCTION update_releases_updated_at();

-- RLS policies for tenant isolation
ALTER TABLE releases ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_approvals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "releases_org_isolation" ON releases;
CREATE POLICY "releases_org_isolation" ON releases
    FOR ALL
    USING (organization_id = (current_setting('app.current_org_id', true))::uuid);

DROP POLICY IF EXISTS "release_steps_via_release" ON release_steps;
CREATE POLICY "release_steps_via_release" ON release_steps
    FOR ALL
    USING (
        release_id IN (
            SELECT id FROM releases
            WHERE organization_id = (current_setting('app.current_org_id', true))::uuid
        )
    );

DROP POLICY IF EXISTS "release_approvals_via_release" ON release_approvals;
CREATE POLICY "release_approvals_via_release" ON release_approvals
    FOR ALL
    USING (
        release_id IN (
            SELECT id FROM releases
            WHERE organization_id = (current_setting('app.current_org_id', true))::uuid
        )
    );

-- Create PGMQ queue for release notifications
SELECT pgmq.create('release_notifications');
