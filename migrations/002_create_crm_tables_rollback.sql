-- ==============================================================================
-- Migration: 002_create_crm_tables_rollback.sql
-- Description: Rollback script for Phase 1 CRM tables.
-- Drops tables in reverse dependency order.
-- ==============================================================================

DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS proposed_actions;
DROP TABLE IF EXISTS follow_ups;
DROP TABLE IF EXISTS risk_assessments;
DROP TABLE IF EXISTS customer_accounts;
