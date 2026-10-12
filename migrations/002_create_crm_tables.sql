-- ==============================================================================
-- Migration: 002_create_crm_tables.sql
-- Description: Idempotent Phase 1 schema creation for RevOps_Astra CRM tables.
-- Tables created:
--   1. customer_accounts
--   2. risk_assessments
--   3. follow_ups
--   4. proposed_actions
--   5. audit_logs
-- Notes:
--   - Integrates cleanly with existing approval_actions and approval_action_audit.
--   - Designed for compatibility with local MCP server tools and mock CRM fields.
-- ==============================================================================

-- 1. Customer Accounts Table
CREATE TABLE IF NOT EXISTS customer_accounts (
    account_id VARCHAR(64) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    industry VARCHAR(100) NULL,
    region VARCHAR(100) NULL,
    account_owner VARCHAR(120) NULL,
    owner_email VARCHAR(255) NULL,
    customer_status VARCHAR(50) NOT NULL DEFAULT 'Active',
    plan_tier VARCHAR(100) NULL,
    arr_usd DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
    health_score INT NOT NULL DEFAULT 50,
    notes TEXT NULL,
    last_contact_date DATE NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (account_id),
    KEY idx_customer_accounts_status (customer_status),
    KEY idx_customer_accounts_owner (account_owner),
    KEY idx_customer_accounts_health (health_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 2. Risk Assessments Table (Telemetry & Risk Evaluation Snapshot)
CREATE TABLE IF NOT EXISTS risk_assessments (
    assessment_id BIGINT NOT NULL AUTO_INCREMENT,
    account_id VARCHAR(64) NOT NULL,
    risk_index INT NOT NULL DEFAULT 0,
    risk_level VARCHAR(50) NOT NULL DEFAULT 'Unknown',
    evidence_confidence INT NOT NULL DEFAULT 100,
    confidence_label VARCHAR(50) NOT NULL DEFAULT 'High',
    usage_change_pct DECIMAL(8, 4) NULL,
    recorded_revenue_90d DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
    open_tickets INT NOT NULL DEFAULT 0,
    overdue_followups INT NOT NULL DEFAULT 0,
    risk_drivers TEXT NULL,
    counter_evidence TEXT NULL,
    analysis_date DATE NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (assessment_id),
    KEY idx_risk_assessments_account_date (account_id, analysis_date),
    KEY idx_risk_assessments_level (risk_level),
    CONSTRAINT fk_risk_assessments_account
        FOREIGN KEY (account_id) REFERENCES customer_accounts(account_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 3. Follow-ups Table (Account Interactions & Scheduled Contacts)
CREATE TABLE IF NOT EXISTS follow_ups (
    followup_id VARCHAR(64) NOT NULL,
    account_id VARCHAR(64) NOT NULL,
    assigned_owner VARCHAR(120) NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'Pending',
    scheduled_date DATE NULL,
    completed_date DATE NULL,
    notes TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (followup_id),
    KEY idx_follow_ups_account (account_id),
    KEY idx_follow_ups_status_date (status, scheduled_date),
    CONSTRAINT fk_follow_ups_account
        FOREIGN KEY (account_id) REFERENCES customer_accounts(account_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 4. Proposed Actions Table
-- Bridges AI / LLM generated proposals to human review in approval_actions.
-- If an action enters the approval queue, approval_action_id references approval_actions(action_id).
CREATE TABLE IF NOT EXISTS proposed_actions (
    proposal_id BIGINT NOT NULL AUTO_INCREMENT,
    account_id VARCHAR(64) NOT NULL,
    approval_action_id CHAR(36) NULL,
    action_type VARCHAR(50) NOT NULL,
    recommended_action VARCHAR(500) NOT NULL,
    issue_summary TEXT NOT NULL,
    email_subject VARCHAR(300) NULL,
    email_body TEXT NULL,
    evidence_refs JSON NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PROPOSED',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (proposal_id),
    KEY idx_proposed_actions_account (account_id),
    KEY idx_proposed_actions_status (status),
    KEY idx_proposed_actions_approval_action (approval_action_id),
    CONSTRAINT fk_proposed_actions_account
        FOREIGN KEY (account_id) REFERENCES customer_accounts(account_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_proposed_actions_approval
        FOREIGN KEY (approval_action_id) REFERENCES approval_actions(action_id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 5. General CRM Audit Logs Table
-- Tracks operational lifecycle changes across customer accounts, CRM records, and external syncs.
-- Complements existing approval_action_audit (which specifically logs human approval queue transitions).
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id BIGINT NOT NULL AUTO_INCREMENT,
    account_id VARCHAR(64) NULL,
    action_id CHAR(36) NULL,
    entity_type VARCHAR(50) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    actor VARCHAR(120) NOT NULL DEFAULT 'SYSTEM',
    changes JSON NULL,
    message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (log_id),
    KEY idx_audit_logs_account_created (account_id, created_at),
    KEY idx_audit_logs_action (action_id),
    KEY idx_audit_logs_entity (entity_type, event_type),
    CONSTRAINT fk_audit_logs_account
        FOREIGN KEY (account_id) REFERENCES customer_accounts(account_id)
        ON DELETE SET NULL,
    CONSTRAINT fk_audit_logs_action
        FOREIGN KEY (action_id) REFERENCES approval_actions(action_id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
