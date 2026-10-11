CREATE TABLE approval_actions (
    action_id CHAR(36) NOT NULL,
    account_id VARCHAR(64) NOT NULL,
    action_type VARCHAR(40) NOT NULL,
    proposed_payload JSON NOT NULL,
    evidence_refs JSON NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reviewer_id INT NULL,
    decision_reason TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    decided_at DATETIME(6) NULL,
    execution_result JSON NULL,
    execution_started_at DATETIME(6) NULL,
    executed_at DATETIME(6) NULL,
    PRIMARY KEY (action_id),
    KEY idx_approval_actions_status_created (status, created_at),
    KEY idx_approval_actions_account (account_id),
    CONSTRAINT fk_approval_actions_reviewer
        FOREIGN KEY (reviewer_id) REFERENCES admins(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE approval_action_audit (
    event_id BIGINT NOT NULL AUTO_INCREMENT,
    action_id CHAR(36) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    actor_admin_id INT NULL,
    from_status VARCHAR(20) NULL,
    to_status VARCHAR(20) NULL,
    reason TEXT NULL,
    details JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (event_id),
    KEY idx_approval_action_audit_action_created (action_id, created_at),
    CONSTRAINT fk_approval_action_audit_action
        FOREIGN KEY (action_id) REFERENCES approval_actions(action_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_approval_action_audit_actor
        FOREIGN KEY (actor_admin_id) REFERENCES admins(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;
