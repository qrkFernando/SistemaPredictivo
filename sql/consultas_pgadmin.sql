-- Servidor PostgreSQL 18 · 127.0.0.1:5432 · base atc_db.
-- Abrir con Query Tool de pgAdmin. Consultas de solo lectura.
SELECT current_database() AS base, current_user AS usuario, version();
SELECT table_name FROM information_schema.tables WHERE table_schema='atc' ORDER BY table_name;
SELECT 'ventas' AS tabla, count(*) AS registros FROM atc.sales
UNION ALL SELECT 'archivos', count(*) FROM atc.files
UNION ALL SELECT 'inventario', count(*) FROM atc.inventory
UNION ALL SELECT 'recetas', count(*) FROM atc.recipes
UNION ALL SELECT 'predicciones', count(*) FROM atc.forecasts;
SELECT * FROM atc.branches ORDER BY name;
SELECT * FROM atc.sales ORDER BY fecha DESC,id DESC LIMIT 100;
SELECT * FROM atc.inventory ORDER BY sede,insumo;
SELECT * FROM atc.recipes ORDER BY producto,insumo;
SELECT * FROM atc.ingredients ORDER BY name;
SELECT id,name,email,role,sede FROM atc.users ORDER BY id;
