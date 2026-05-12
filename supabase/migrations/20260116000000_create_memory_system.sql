-- Migration: Create persistent memory system tables
-- This enables claude-mem style observations and session summaries

-- ============================================
-- Observations table (granular memories)
-- ============================================
CREATE TABLE IF NOT EXISTS claude_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    observation_type TEXT NOT NULL,  -- 'fact', 'preference', 'context', 'tool_result', 'insight'
    content TEXT NOT NULL,
    importance FLOAT DEFAULT 0.5 CHECK (importance >= 0.0 AND importance <= 1.0),
    metadata JSONB DEFAULT '{}',
    embedding_id TEXT,  -- Optional: reference to vector store
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_observations_user_id ON claude_observations(user_id);
CREATE INDEX IF NOT EXISTS idx_observations_session_id ON claude_observations(session_id);
CREATE INDEX IF NOT EXISTS idx_observations_user_session ON claude_observations(user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_observations_user_created ON claude_observations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_type ON claude_observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_importance ON claude_observations(user_id, importance DESC);

-- Full-text search index for content
CREATE INDEX IF NOT EXISTS idx_observations_fts ON claude_observations 
    USING GIN (to_tsvector('english', content));

-- Enable RLS
ALTER TABLE claude_observations ENABLE ROW LEVEL SECURITY;

-- Policies for observations
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_observations' AND policyname = 'Users can view own observations') THEN
        CREATE POLICY "Users can view own observations" ON claude_observations FOR SELECT USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_observations' AND policyname = 'Users can insert own observations') THEN
        CREATE POLICY "Users can insert own observations" ON claude_observations FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_observations' AND policyname = 'Users can delete own observations') THEN
        CREATE POLICY "Users can delete own observations" ON claude_observations FOR DELETE USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_observations' AND policyname = 'Service role bypass observations') THEN
        CREATE POLICY "Service role bypass observations" ON claude_observations FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ============================================
-- Session summaries table
-- ============================================
CREATE TABLE IF NOT EXISTS claude_session_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    key_topics TEXT[] DEFAULT '{}',
    tools_used TEXT[] DEFAULT '{}',
    message_count INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    duration_seconds INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for session summaries
CREATE INDEX IF NOT EXISTS idx_session_summaries_user_id ON claude_session_summaries(user_id);
CREATE INDEX IF NOT EXISTS idx_session_summaries_user_created ON claude_session_summaries(user_id, created_at DESC);

-- Full-text search on summaries
CREATE INDEX IF NOT EXISTS idx_session_summaries_fts ON claude_session_summaries 
    USING GIN (to_tsvector('english', summary));

-- Enable RLS
ALTER TABLE claude_session_summaries ENABLE ROW LEVEL SECURITY;

-- Policies for session summaries
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_session_summaries' AND policyname = 'Users can view own summaries') THEN
        CREATE POLICY "Users can view own summaries" ON claude_session_summaries FOR SELECT USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_session_summaries' AND policyname = 'Users can insert own summaries') THEN
        CREATE POLICY "Users can insert own summaries" ON claude_session_summaries FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_session_summaries' AND policyname = 'Users can update own summaries') THEN
        CREATE POLICY "Users can update own summaries" ON claude_session_summaries FOR UPDATE USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_session_summaries' AND policyname = 'Users can delete own summaries') THEN
        CREATE POLICY "Users can delete own summaries" ON claude_session_summaries FOR DELETE USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'claude_session_summaries' AND policyname = 'Service role bypass summaries') THEN
        CREATE POLICY "Service role bypass summaries" ON claude_session_summaries FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ============================================
-- Helper function for full-text search on observations
-- ============================================
CREATE OR REPLACE FUNCTION search_observations(
    p_user_id UUID,
    p_query TEXT,
    p_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    id UUID,
    session_id TEXT,
    observation_type TEXT,
    content TEXT,
    importance FLOAT,
    created_at TIMESTAMPTZ,
    rank REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        o.id,
        o.session_id,
        o.observation_type,
        o.content,
        o.importance,
        o.created_at,
        ts_rank(to_tsvector('english', o.content), plainto_tsquery('english', p_query)) AS rank
    FROM claude_observations o
    WHERE o.user_id = p_user_id
      AND to_tsvector('english', o.content) @@ plainto_tsquery('english', p_query)
    ORDER BY rank DESC, o.importance DESC, o.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Helper function for full-text search on summaries
-- ============================================
CREATE OR REPLACE FUNCTION search_session_summaries(
    p_user_id UUID,
    p_query TEXT,
    p_limit INTEGER DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    session_id TEXT,
    summary TEXT,
    key_topics TEXT[],
    created_at TIMESTAMPTZ,
    rank REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        s.id,
        s.session_id,
        s.summary,
        s.key_topics,
        s.created_at,
        ts_rank(to_tsvector('english', s.summary), plainto_tsquery('english', p_query)) AS rank
    FROM claude_session_summaries s
    WHERE s.user_id = p_user_id
      AND to_tsvector('english', s.summary) @@ plainto_tsquery('english', p_query)
    ORDER BY rank DESC, s.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Comments for documentation
-- ============================================
COMMENT ON TABLE claude_observations IS 'Stores granular observations/memories extracted from AI conversations';
COMMENT ON COLUMN claude_observations.observation_type IS 'Type: fact, preference, context, tool_result, insight';
COMMENT ON COLUMN claude_observations.importance IS 'Importance score from 0.0 to 1.0 for prioritization';
COMMENT ON COLUMN claude_observations.embedding_id IS 'Optional reference to vector embedding in ChromaDB';

COMMENT ON TABLE claude_session_summaries IS 'Stores summaries of completed AI sessions for context in future sessions';
COMMENT ON COLUMN claude_session_summaries.key_topics IS 'Array of main topics discussed in the session';
COMMENT ON COLUMN claude_session_summaries.tools_used IS 'Array of tools that were used during the session';
