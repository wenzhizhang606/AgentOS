-- Apply after 001_managed_task.sql on the same MySQL 8.4 primary.
-- Both tables can be created on a live M1 database. Historical runs have no spans.
CREATE TABLE IF NOT EXISTS ag_capability_version (
    kind ENUM('skill','tool') NOT NULL,
    name VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    digest CHAR(64) NOT NULL,
    PRIMARY KEY (kind,name,version)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

-- Add both scoped range indexes atomically. Expand with NULL first so old tasks
-- can be distinguished from new enqueues; a failed migration can be rerun.
SET @ag_monitor_ddl = IF(
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_schema=DATABASE() AND table_name='ag_task' AND column_name='monitor_created_at')=0,
    'ALTER TABLE ag_task ADD COLUMN monitor_created_at DATETIME(6) NULL,
     ADD INDEX ag_monitor_project_time (tenant_id,project_id,monitor_created_at,task_id),
     ADD INDEX ag_monitor_subject_time (tenant_id,project_id,subject_id,monitor_created_at,task_id)',
    'SELECT 1');
PREPARE ag_monitor_expand FROM @ag_monitor_ddl;
EXECUTE ag_monitor_expand;
DEALLOCATE PREPARE ag_monitor_expand;
-- An old M1 writer also receives the UTC enqueue timestamp via this default.
ALTER TABLE ag_task ALTER COLUMN monitor_created_at SET DEFAULT (UTC_TIMESTAMP(6));
UPDATE ag_task t JOIN ag_task_event e ON e.task_id=t.task_id AND e.sequence_no=1
SET t.monitor_created_at=e.occurred_at WHERE t.monitor_created_at IS NULL;

CREATE TABLE IF NOT EXISTS ag_capability_span (
    span_id CHAR(32) PRIMARY KEY,
    parent_span_id CHAR(32) NULL,
    task_id CHAR(32) NOT NULL,
    attempt BIGINT UNSIGNED NOT NULL,
    worker_id VARCHAR(128) NOT NULL,
    lease_epoch BIGINT UNSIGNED NOT NULL,
    kind ENUM('context','skill','tool') NOT NULL,
    name VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    digest CHAR(64) NOT NULL,
    outcome ENUM('started','succeeded','failed') NOT NULL,
    started_at DATETIME(6) NOT NULL,
    ended_at DATETIME(6) NULL,
    error_code VARCHAR(80) NULL,
    snapshot_id VARCHAR(128) NULL,
    FOREIGN KEY (task_id) REFERENCES ag_task(task_id),
    FOREIGN KEY (parent_span_id) REFERENCES ag_capability_span(span_id),
    INDEX ag_span_start (task_id,started_at),
    INDEX ag_span_end (task_id,ended_at),
    UNIQUE KEY ag_span_snapshot (task_id,snapshot_id),
    CHECK ((outcome='started' AND ended_at IS NULL AND error_code IS NULL) OR
           (outcome='succeeded' AND ended_at IS NOT NULL AND error_code IS NULL) OR
           (outcome='failed' AND ended_at IS NOT NULL AND error_code IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;
