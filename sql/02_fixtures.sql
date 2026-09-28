TRUNCATE TABLE source_sites RESTART IDENTITY CASCADE;

INSERT INTO source_sites (country_code, site_type, area_sqm, area_unit, source_date, geom, data_completeness_score, uncertainty_notes)
VALUES
('GBR', 'BESS', 5000, 'sqm', '2023-10-01', ST_GeomFromText('POLYGON((-0.1 51.5, -0.1 51.51, -0.09 51.51, -0.09 51.5, -0.1 51.5))', 4326), 0.9, 'Medium grid capacity uncertainty'),
('GBR', 'BESS', 12000, 'sqm', '2023-10-05', ST_GeomFromText('POLYGON((-1.1 52.5, -1.1 52.51, -1.09 52.51, -1.09 52.5, -1.1 52.5))', 4326), 0.8, 'High grid capacity uncertainty'),
('IRL', 'PEATLAND', 150000, 'sqm', '2023-09-15', ST_GeomFromText('POLYGON((-8.1 53.5, -8.1 53.51, -8.09 53.51, -8.09 53.5, -8.1 53.5))', 4326), 0.95, 'Ownership verification pending'),
('IRL', 'PEATLAND', 250000, 'sqm', '2023-09-20', ST_GeomFromText('POLYGON((-7.1 52.5, -7.1 52.51, -7.09 52.51, -7.09 52.5, -7.1 52.5))', 4326), 0.85, 'Environmental eligibility checking');
