-- The success number as named queries on the live database (read-only SELECTs). Same rules as jobpilot/api/success_core.py:
-- run one with   axiom psql -XAt -F '|' -v cutoff=2026-10-26T00:48:09Z -f scripts/metrics/success.sql
-- `cutoff` is an ISO timestamp (runs.created_at is text): the cohort is the jobs whose first real run is at or after it.
--
-- One statement (no temp views: nothing is written), printing section|key|value rows:
--   funnel    attempted, confirmed, failed, eligible and the five rates
--   excluded  the jobs left out of eligible, one row per structured reason
--   failures  the failed jobs by "last state:blocker type"
--   evidence  the confirmed jobs by the grade of their best receipt (strong|medium|weak, then the kind)
--   platform  per ATS: attempted|confirmed|eligible|filtered_pct
-- The rules are success_core.py's, line for line; scripts/metrics/check_success.py compares the two on a saved dump.

WITH ev AS (
  SELECT r.id, r.job_id, j.ats, r.created_at, r.state AS raw_state,
         r.reason = 'Worker interrupted before submission; draft recovery scheduled' AS interrupted,
         r.packet->>'blocker_type' AS blocker,
         (COALESCE(r.receipt->>'existing_application_notice', 'false') NOT IN ('false', 'null', '0', '')
          OR COALESCE(r.receipt->>'reconciled_existing_submission', 'false') NOT IN ('false', 'null', '0', '')
          OR COALESCE(r.receipt->>'matched_existing_application', 'false') NOT IN ('false', 'null', '0', '')
          OR r.receipt->>'type' = 'employer_already_applied') AS existing,
         CASE
           WHEN COALESCE(r.receipt->>'existing_application_notice', 'false') NOT IN ('false', 'null', '0', '')
             OR COALESCE(r.receipt->>'reconciled_existing_submission', 'false') NOT IN ('false', 'null', '0', '')
             OR COALESCE(r.receipt->>'matched_existing_application', 'false') NOT IN ('false', 'null', '0', '')
             OR r.receipt->>'type' = 'employer_already_applied' THEN 'weak|existing_application_view'
           WHEN r.receipt->>'type' = 'invalid_page_text'
             OR r.receipt->>'evidence_selector' = '[data-automation-id=similar-jobs-heading]' THEN 'weak|page_text_rejected'
           WHEN r.receipt->>'type' IN ('user_reconciled', 'owner_submitted', 'unsent_reconciled',
                                       'reviewed_employer_confirmation_capture') THEN 'weak|applicant_recorded'
           WHEN r.receipt->>'type' = 'final_page_confirmation' THEN 'weak|final_page_quote'
           WHEN r.receipt->>'type' = 'employer_dashboard_status' THEN 'weak|dashboard_status'
           WHEN r.receipt->>'type' = 'explicit_confirmation_page'
             AND COALESCE(r.receipt->>'website_confirmed', 'false') NOT IN ('false', 'null', '0', '')
             THEN CASE WHEN m.ok THEN 'strong|page_and_email' ELSE 'strong|page_only' END
           WHEN r.receipt->>'type' = 'application_created'
             THEN CASE WHEN m.ok THEN 'strong|platform_and_email' ELSE 'medium|platform_response' END
           WHEN COALESCE(r.receipt->>'type', '') = '' AND m.present
             THEN CASE WHEN m.ok THEN 'strong|email_only' ELSE 'weak|email_untrusted_sender' END
           ELSE 'weak|unrecognised'
         END AS grade
  FROM runs r
  JOIN jobs j ON j.id = r.job_id AND NOT j.demo
  CROSS JOIN LATERAL (
    SELECT r.receipt->'email'->>'message_id' IS NOT NULL AS present,
           r.receipt->'email'->>'message_id' IS NOT NULL
             AND COALESCE(r.receipt->'email'->>'sender_trusted', 'true') IN ('true', 'True', '1')
             AND COALESCE(r.receipt->'email'->>'authenticated', 'true') IN ('true', 'True', '1') AS ok
  ) m
  WHERE r.job_id IN (SELECT job_id FROM runs WHERE created_at >= :'cutoff')
), commit_at AS (  -- AXIOM's own earliest run that began a commit, per job
  SELECT r.job_id, min(r.created_at) AS at
  FROM runs r JOIN events e ON e.run_id = r.id
  WHERE e.kind IN ('submitting', 'submission', 'submission_requests', 'step_submit')
  GROUP BY r.job_id
), adj AS (  -- a confirmation resting on the employer's own record of an application AXIOM never began is "already applied"
  SELECT ev.*,
         CASE WHEN ev.raw_state = 'confirmed' AND ev.grade IN ('weak|existing_application_view', 'weak|final_page_quote', 'weak|dashboard_status',
                                    'weak|applicant_recorded', 'weak|page_text_rejected')
                   AND NOT (c.at IS NOT NULL AND c.at <= ev.created_at)
              THEN 'already_applied' ELSE ev.raw_state END AS st
  FROM ev LEFT JOIN commit_at c ON c.job_id = ev.job_id
), jobs_scored AS (
  SELECT job_id, max(ats) AS ats,
         bool_or(st = 'confirmed') AS ok, bool_or(st = 'already_applied') AS dup,
         (array_agg(grade ORDER BY CASE split_part(grade, '|', 1) WHEN 'strong' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at, id)
            FILTER (WHERE st = 'confirmed'))[1] AS best_grade,
         (array_agg(st ORDER BY created_at DESC, id DESC))[1] AS last_state,
         (array_agg(COALESCE(blocker, '') ORDER BY created_at DESC, id DESC))[1] AS last_blocker,
         (array_agg(interrupted ORDER BY created_at DESC, id DESC))[1] AS last_interrupted,
         COALESCE((array_agg(st ORDER BY created_at, id) FILTER (WHERE st <> 'dry_run_passed' AND NOT interrupted))[1],
                  CASE WHEN (array_agg(st ORDER BY created_at DESC, id DESC))[1] <> 'dry_run_passed'
                       THEN (array_agg(st ORDER BY created_at DESC, id DESC))[1] END) AS first_state,
         COALESCE((array_agg(created_at ORDER BY created_at, id) FILTER (WHERE st <> 'dry_run_passed' AND NOT interrupted))[1],
                  max(created_at)) AS started
  FROM adj GROUP BY job_id
), cohort AS (
  SELECT *, CASE
      WHEN ok THEN 'confirmed'
      WHEN dup THEN 'already_on_file'
      WHEN last_state IN ('queued', 'running', 'submitting', 'waiting_email') OR last_interrupted THEN 'in_progress'
      WHEN last_blocker = 'employer_limit' THEN 'employer_limit'
      WHEN last_blocker = 'sensitive_id' THEN 'sensitive_id_required'
      WHEN last_state = 'closed' THEN 'closed'
      WHEN last_state = 'cancelled' AND last_blocker = '' THEN 'cancelled'
      WHEN last_state = 'paused' THEN 'paused_by_owner'
      WHEN last_state = 'waiting_answer' OR last_blocker = 'missing_fact' THEN 'waiting_on_owner'
      ELSE 'failed' END AS outcome
  FROM jobs_scored WHERE first_state IS NOT NULL AND started >= :'cutoff'
), n AS (
  SELECT count(*) AS attempted,
         count(*) FILTER (WHERE outcome = 'confirmed') AS confirmed,
         count(*) FILTER (WHERE outcome = 'failed') AS failed,
         count(*) FILTER (WHERE outcome = 'waiting_on_owner') AS waiting_on_owner,
         count(*) FILTER (WHERE outcome = 'in_progress') AS in_progress,
         count(*) FILTER (WHERE first_state = 'confirmed') AS first_try,
         count(*) FILTER (WHERE outcome = 'confirmed' AND split_part(best_grade, '|', 1) = 'strong') AS strong,
         count(*) FILTER (WHERE outcome = 'confirmed' AND split_part(best_grade, '|', 1) = 'medium') AS medium,
         count(*) FILTER (WHERE outcome = 'confirmed' AND split_part(best_grade, '|', 1) = 'weak') AS weak
  FROM cohort
)
SELECT section, key, value FROM (
  SELECT 'funnel' AS section, 'attempted' AS key, attempted::text AS value, 1 AS o FROM n
  UNION ALL SELECT 'funnel', 'confirmed', confirmed::text, 2 FROM n
  UNION ALL SELECT 'funnel', 'failed', failed::text, 3 FROM n
  UNION ALL SELECT 'funnel', 'eligible', (confirmed + failed)::text, 4 FROM n
  UNION ALL SELECT 'funnel', 'raw_pct', (floor(1000.0 * confirmed / attempted) / 10)::numeric(6,1)::text, 5 FROM n
  UNION ALL SELECT 'funnel', 'filtered_pct', (floor(1000.0 * confirmed / (confirmed + failed)) / 10)::numeric(6,1)::text, 6 FROM n
  UNION ALL SELECT 'funnel', 'first_try_pct', (floor(1000.0 * first_try / (confirmed + failed)) / 10)::numeric(6,1)::text, 7 FROM n
  UNION ALL SELECT 'funnel', 'strict_pct',
         (floor(1000.0 * confirmed / (confirmed + failed + waiting_on_owner + in_progress)) / 10)::numeric(6,1)::text, 8 FROM n
  UNION ALL SELECT 'funnel', 'strong_pct', (floor(1000.0 * strong / (confirmed + failed)) / 10)::numeric(6,1)::text, 9 FROM n
  UNION ALL SELECT 'funnel', 'evidence_strong', strong::text, 10 FROM n
  UNION ALL SELECT 'funnel', 'evidence_medium', medium::text, 11 FROM n
  UNION ALL SELECT 'funnel', 'evidence_weak', weak::text, 12 FROM n
  UNION ALL SELECT 'excluded', outcome, count(*)::text, 20 FROM cohort WHERE outcome NOT IN ('confirmed', 'failed') GROUP BY outcome
  UNION ALL SELECT 'failures', last_state || ':' || COALESCE(NULLIF(last_blocker, ''), 'unclassified'), count(*)::text, 30
         FROM cohort WHERE outcome = 'failed' GROUP BY 2
  UNION ALL SELECT 'evidence', best_grade, count(*)::text, 40 FROM cohort WHERE outcome = 'confirmed' GROUP BY best_grade
  UNION ALL SELECT 'platform', ats, count(*) || '|' || count(*) FILTER (WHERE outcome = 'confirmed') || '|'
         || count(*) FILTER (WHERE outcome IN ('confirmed', 'failed')) || '|'
         || COALESCE((floor(1000.0 * count(*) FILTER (WHERE outcome = 'confirmed')
                      / NULLIF(count(*) FILTER (WHERE outcome IN ('confirmed', 'failed')), 0)) / 10)::numeric(6,1)::text, ''), 50
         FROM cohort GROUP BY ats
) all_rows ORDER BY o, key;
