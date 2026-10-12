-- leads_enriched: LLM classification and score (Job 3), producer review, Momentum push state (Job 4).
BEGIN;

CREATE TABLE leads_enriched (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_entity_id            uuid NOT NULL REFERENCES raw_entities (id),
    contacts_enriched_id     uuid NOT NULL UNIQUE REFERENCES contacts_enriched (id),
    industry                 lead_industry NOT NULL,
    needs                    text[] NOT NULL DEFAULT '{}'
        CHECK (needs <@ ARRAY['bop', 'gl', 'wc', 'auto', 'umbrella', 'prof', 'inland_marine', 'liquor']),
    score                    integer NOT NULL CHECK (score BETWEEN 0 AND 10),
    rationale                text NOT NULL,
    outreach_line            text NOT NULL,
    momentum_prospect_id     text,
    status                   text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'pushed_to_momentum', 'converted', 'discarded')),
    -- Which model and prompt produced this score.
    llm_model                text NOT NULL,
    prompt_version           text NOT NULL,
    -- Deviation: producer review step between Job 3 and Job 4. Null = not yet reviewed.
    review_verdict           text CHECK (review_verdict IN ('approved', 'rejected')),
    reviewed_by              text,
    reviewed_at              timestamptz,
    review_notes             text,
    -- Per-step push state, so Job 4 can resume after a partial failure without
    -- creating a duplicate prospect. pushed_at is set once all steps are done.
    momentum_tags_applied_at timestamptz,
    momentum_task_id         text,
    pushed_at                timestamptz,
    push_error               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX leads_enriched_raw_entity_id_idx ON leads_enriched (raw_entity_id);
-- Job 4's work queue.
CREATE INDEX leads_enriched_push_queue_idx ON leads_enriched (review_verdict, score)
    WHERE pushed_at IS NULL;

CREATE TRIGGER leads_enriched_set_updated_at
    BEFORE UPDATE ON leads_enriched
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- No policies: only the workers' direct connection may read or write.
ALTER TABLE leads_enriched ENABLE ROW LEVEL SECURITY;

COMMIT;
