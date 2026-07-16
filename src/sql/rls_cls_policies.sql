-- =====================================================================================
-- rls_cls_policies.sql — defense-in-depth access controls for the Fabric Warehouse /
-- SQL analytics endpoint over the de-identified Gold layer.
--
-- SYNTHETIC DATA ONLY. Adapt object names + principals to your tenant.
--
-- Purpose in the demo
-- -------------------
-- De-identification (notebooks 02b/03b) is the PRIMARY control: the bytes in gold_safe_*
-- are already PHI-free. RLS/CLS here are the SECOND layer — they show how to further
-- scope even the de-identified data by role (the "WITH user / WITHOUT user" contrast),
-- and how you would protect any still-sensitive column if you chose to keep one.
--
-- Two mechanisms:
--   * CLS (Column-Level Security) : GRANT/DENY at column granularity.
--   * RLS (Row-Level Security)    : a security predicate function + policy filters rows.
--
-- NOTE: In Fabric, the data-plane source of truth is OneLake security (data access roles).
-- Warehouse RLS/CLS applies to the T-SQL endpoint. Use both consciously; see
-- docs/enforcement_models.md for where each layer stops.
--
-- LAKEHOUSE SQL ANALYTICS ENDPOINT CONSTRAINT: this endpoint is READ-ONLY over the Delta
-- tables — you CANNOT CREATE TABLE here. So the analyst->region mapping is kept INLINE
-- (a VALUES list inside the predicate) instead of a physical sec_user_region table.
-- Roles, functions, security policies, and GRANT/DENY are all supported on the endpoint.
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- Roles (map these to Entra security groups in your tenant)
-- -------------------------------------------------------------------------------------
-- Analysts see de-identified data only. Stewards may see a bit more context.
-- CREATE ROLE analyst_deid;
-- CREATE ROLE data_steward;
-- ALTER ROLE analyst_deid  ADD MEMBER [analytics-analysts@contoso.com];
-- ALTER ROLE data_steward  ADD MEMBER [data-stewards@contoso.com];


-- =====================================================================================
-- COLUMN-LEVEL SECURITY (CLS)
-- Even though gold_safe_dim_patient holds a TOKEN in MRN (not the real MRN), we DENY
-- the analyst role from selecting the token column to demonstrate column scoping.
-- =====================================================================================
DENY SELECT ON dbo.gold_safe_dim_patient (MRN)        TO analyst_deid;
DENY SELECT ON dbo.gold_safe_dim_provider (NPI)       TO analyst_deid;

-- Stewards may see the tokens (for join/debugging) but never a re-identified value
-- (re-identification only exists in the Vault workspace crosswalk, not here).
GRANT SELECT ON dbo.gold_safe_dim_patient  TO data_steward;
GRANT SELECT ON dbo.gold_safe_dim_provider TO data_steward;


-- =====================================================================================
-- ROW-LEVEL SECURITY (RLS)
-- Example: scope analysts to a single region/facility. The predicate reads the caller's
-- identity (USER_NAME()/SESSION_CONTEXT) and filters rows. Here we scope facilities by a
-- mapping table so the same view serves every analyst, each seeing only their region.
-- =====================================================================================

-- Principal -> allowed region mapping is INLINE (no physical table on a Lakehouse
-- SQL analytics endpoint). Replace the placeholder UPNs with your real Entra users/groups.

CREATE SCHEMA IF NOT EXISTS sec;
GO

-- Predicate: a row is visible if the caller is mapped to that Region, or is a steward.
CREATE FUNCTION sec.fn_region_predicate(@Region NVARCHAR(50))
    RETURNS TABLE
AS
    RETURN
        SELECT 1 AS is_visible
        WHERE
            IS_MEMBER('data_steward') = 1
            OR EXISTS (
                SELECT 1
                FROM (VALUES
                    ('northeast-analyst@contoso.com', 'Northeast'),
                    ('west-analyst@contoso.com',      'West')
                ) AS m(UserName, Region)
                WHERE m.UserName = USER_NAME()
                  AND m.Region   = @Region
            );
GO

-- Apply the predicate as a FILTER on the facility dimension (drives fact visibility
-- through the model relationships).
CREATE SECURITY POLICY sec.RegionFilter
    ADD FILTER PREDICATE sec.fn_region_predicate(Region)
        ON dbo.gold_safe_dim_facility
    WITH (STATE = ON);
GO

-- =====================================================================================
-- Demo script
-- -------------------------------------------------------------------------------------
-- 1. As a steward:   SELECT * FROM dbo.gold_safe_dim_facility;  -- sees all regions
-- 2. As NE analyst:  SELECT * FROM dbo.gold_safe_dim_facility;  -- sees Northeast only
-- 3. As analyst:     SELECT MRN FROM dbo.gold_safe_dim_patient; -- DENIED by CLS
-- The point: even AFTER de-identification, access is still least-privilege and role-scoped.
-- =====================================================================================
