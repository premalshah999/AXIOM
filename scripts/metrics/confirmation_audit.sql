-- Audit of the stored confirmations (read-only): every job with a settled confirmed run, graded by its best receipt, and whether
-- AXIOM's own commit came first. Same grading as success.sql / success_core.py.
--   axiom psql -XAt -F '|' -f scripts/metrics/confirmation_audit.sql
-- Output: grade|kind|commit_first|jobs. "commit_first" = f means nothing of AXIOM's began a send on that job before the run:
-- for a weak receipt that is the ASSA ABLOY kind (an employer's record, a quote or a note credited to AXIOM).

WITH ev AS (
  SELECT r.id, r.job_id, j.ats, r.created_at, r.state AS raw_state,
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
  WHERE r.state = 'confirmed' AND COALESCE(r.receipt->>'evidence_reconciliation', '') <> 'pending'
), commit_at AS (
  SELECT r.job_id, min(r.created_at) AS at
  FROM runs r JOIN events e ON e.run_id = r.id
  WHERE e.kind = 'submitting'  -- success_core.COMMIT_EVENT
  GROUP BY r.job_id
), best AS (
  SELECT DISTINCT ON (ev.job_id) ev.job_id, ev.grade, (c.at IS NOT NULL AND c.at <= ev.created_at) AS commit_first
  FROM ev LEFT JOIN commit_at c ON c.job_id = ev.job_id
  ORDER BY ev.job_id, CASE split_part(ev.grade, '|', 1) WHEN 'strong' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, ev.created_at, ev.id
)
SELECT split_part(grade, '|', 1) AS grade, split_part(grade, '|', 2) AS kind, commit_first, count(*) AS jobs
FROM best GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;
