-- M1 / MySQL 8.4 InnoDB. Apply to the selected AgentOS database.
CREATE TABLE IF NOT EXISTS ag_release (
    version_id CHAR(64) NOT NULL PRIMARY KEY,
    manifest JSON NOT NULL,
    registered_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_policy_head (
    tenant_id VARCHAR(128) NOT NULL,
    project_id VARCHAR(128) NOT NULL,
    epoch BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, project_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_project_member (
    tenant_id VARCHAR(128) NOT NULL,
    project_id VARCHAR(128) NOT NULL,
    subject_id VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL,
    PRIMARY KEY (tenant_id, project_id, subject_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES ag_policy_head (tenant_id, project_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_source (
    tenant_id VARCHAR(128) NOT NULL,
    project_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    version BIGINT UNSIGNED NOT NULL,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    title TEXT NOT NULL,
    body MEDIUMTEXT NOT NULL,
    location TEXT NOT NULL,
    PRIMARY KEY (tenant_id, project_id, source_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES ag_policy_head (tenant_id, project_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_task (
    task_id CHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    project_id VARCHAR(128) NOT NULL,
    subject_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    agent_version_id VARCHAR(128) NOT NULL,
    goal TEXT NOT NULL,
    state VARCHAR(16) NOT NULL,
    claimable TINYINT AS (state IN ('queued','running')) STORED,
    version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    attempt BIGINT UNSIGNED NOT NULL DEFAULT 0,
    lease_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
    worker_id VARCHAR(128) NULL,
    lease_until DATETIME(6) NULL,
    ready_at DATETIME(6) NOT NULL,
    deadline_at DATETIME(6) NOT NULL,
    limit_micros BIGINT NOT NULL,
    reserved_micros BIGINT NOT NULL DEFAULT 0,
    settled_micros BIGINT NOT NULL DEFAULT 0,
    result JSON NULL,
    error_code VARCHAR(80) NULL,
    UNIQUE KEY ag_create_idem (tenant_id, subject_id, idempotency_key),
    INDEX ag_queue (agent_version_id, claimable, ready_at, task_id),
    INDEX ag_queue_all (claimable, ready_at, task_id),
    CHECK (limit_micros >= 0 AND reserved_micros >= 0 AND settled_micros >= 0)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_dispatch (
    dispatch_attempt_id CHAR(32) PRIMARY KEY,
    task_id CHAR(32) NOT NULL UNIQUE,
    worker_id VARCHAR(128) NOT NULL,
    lease_epoch BIGINT UNSIGNED NOT NULL,
    snapshot_id VARCHAR(128) NOT NULL UNIQUE,
    snapshot JSON NOT NULL,
    retained_payload MEDIUMBLOB NULL,
    reservation BIGINT NOT NULL,
    admitted_at DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NOT NULL,
    receipt_id VARCHAR(256) NULL,
    actual_micros BIGINT NULL,
    input_tokens BIGINT NULL,
    output_tokens BIGINT NULL,
    FOREIGN KEY (task_id) REFERENCES ag_task(task_id),
    CHECK (reservation >= 0 AND (actual_micros IS NULL OR actual_micros >= 0))
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_cancel_request (
    tenant_id VARCHAR(128) NOT NULL,
    subject_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    task_id CHAR(32) NOT NULL,
    expected_version BIGINT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, idempotency_key),
    FOREIGN KEY (task_id) REFERENCES ag_task(task_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ag_task_event (
    event_id CHAR(32) PRIMARY KEY,
    task_id CHAR(32) NOT NULL,
    sequence_no BIGINT UNSIGNED NOT NULL,
    attempt BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    UNIQUE KEY ag_event_sequence (task_id, sequence_no),
    FOREIGN KEY (task_id) REFERENCES ag_task(task_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;
