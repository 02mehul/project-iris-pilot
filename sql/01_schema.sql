CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Source tables (mocking upstream data)
CREATE TABLE IF NOT EXISTS source_sites (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(3) NOT NULL CHECK (country_code ~ '^[A-Z]{3}$'),
    site_type VARCHAR(50) NOT NULL, -- e.g., 'BESS' or 'PEATLAND'
    area_sqm NUMERIC NOT NULL,
    area_unit VARCHAR(10) NOT NULL DEFAULT 'sqm', -- explicit units data contract; only 'sqm' is accepted downstream
    source_date DATE NOT NULL,
    geom GEOMETRY(Polygon, 4326) NOT NULL,
    data_completeness_score NUMERIC CHECK (data_completeness_score BETWEEN 0 AND 1),
    uncertainty_notes TEXT,
    -- country-scoped identifier: lookups/joins on this table are expected to
    -- go through (country_code, id), never a bare cross-country id.
    UNIQUE (country_code, id)
);

CREATE INDEX IF NOT EXISTS idx_source_sites_country_code ON source_sites (country_code);

-- 2. Core tables (promoted data)
CREATE TABLE IF NOT EXISTS core_candidate_sites (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(3) NOT NULL CHECK (country_code ~ '^[A-Z]{3}$'),
    site_type VARCHAR(50) NOT NULL,
    area_sqm NUMERIC NOT NULL,
    area_unit VARCHAR(10) NOT NULL DEFAULT 'sqm',
    eco_points NUMERIC NOT NULL, -- Computed based on area * 8
    promoted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    source_date DATE NOT NULL,
    geom GEOMETRY(Polygon, 4326) NOT NULL,
    uncertainty_notes TEXT,
    UNIQUE (country_code, id)
);

CREATE INDEX IF NOT EXISTS idx_core_candidate_sites_country_code ON core_candidate_sites (country_code);

-- 3. Materialized views for BESS and PEATLAND
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_bess_candidates AS
SELECT
    id,
    country_code,
    area_sqm,
    area_unit,
    eco_points,
    geom,
    promoted_at,
    'Preliminary prospecting material. Figures, eco-point estimates and site suitability are indicative and based on available source data and commercial screening assumptions. The 8 eco-points/m2 factor is the current commercial baseline, not certified compensation. Ownership, planning, grid capacity, environmental eligibility and transferability remain subject to project-specific verification. No permit, reservation or construction readiness is represented.' AS reference_wording
FROM core_candidate_sites
WHERE site_type = 'BESS';

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_peatland_candidates AS
SELECT
    id,
    country_code,
    area_sqm,
    area_unit,
    eco_points,
    geom,
    promoted_at,
    'Preliminary prospecting material. Figures, eco-point estimates and site suitability are indicative and based on available source data and commercial screening assumptions. The 8 eco-points/m2 factor is the current commercial baseline, not certified compensation. Ownership, planning, grid capacity, environmental eligibility and transferability remain subject to project-specific verification. No permit, reservation or construction readiness is represented.' AS reference_wording
FROM core_candidate_sites
WHERE site_type = 'PEATLAND';
