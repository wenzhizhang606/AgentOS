-- Old writers omit worker_id; their claim events remain readable after upgrade.
-- Keep the column and both indexes in the same atomic, restartable MySQL 8.4 DDL.
SET @ag_run_trace_ddl = IF(
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_schema=DATABASE() AND table_name='ag_task_event' AND column_name='worker_id')=0,
    'ALTER TABLE ag_task_event ADD COLUMN worker_id VARCHAR(128) NULL,
     ADD INDEX ag_event_attempt (task_id,attempt,event_type,sequence_no),
     ADD INDEX ag_event_window (event_type,occurred_at,task_id,attempt)',
    'SELECT 1');
PREPARE ag_run_trace_expand FROM @ag_run_trace_ddl;
EXECUTE ag_run_trace_expand;
DEALLOCATE PREPARE ag_run_trace_expand;
