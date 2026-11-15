# American Texas Chicken · Sistema de demanda

Aplicación operativa conectada a **PostgreSQL 18**, base **atc_db**, esquema **atc**, en `127.0.0.1:5432`. Los módulos consultan y guardan sus registros en esa conexión. El arranque no genera ventas, inventarios ni precios ficticios. Incluye recetas base con medidas aproximadas por porción, solicitadas para iniciar la configuración de cocina.

Las sedes configuradas son Paseo la Breña, San Carlos y Giráldez. Sus nombres y el catálogo de productos provienen de la especificación del negocio. Las existencias deben ingresarse con datos reales; las medidas orientativas de las recetas pueden ajustarse. Los registros y archivos de la versión SQLite anterior se conservan en `data/atc.db`; no se copiaron a PostgreSQL.

## Ejecutar la versión actual

Desde esta carpeta:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

Abre **http://127.0.0.1:8001**. API interactiva: **http://127.0.0.1:8001/docs**. Se usa 8001 porque el puerto 8000 estaba ocupado por otra copia del sistema. Requiere Python 3.9–3.12.

La conexión se carga automáticamente desde `.env`, con permisos `600` y excluido de Git. No compartas ese archivo. Las variables de entorno ya definidas en la terminal tienen prioridad.

| Cuenta inicial de la aplicación | Contraseña inicial | Acceso |
|---|---|---|
| admin@americantexas.pe | Texas2026! | Todas las sedes, configuración y visor de tablas |
| sancarlos@americantexas.pe | Sede2026! | Solo San Carlos |

Estas son credenciales **de la aplicación**; la contraseña de PostgreSQL se configura por separado y no aparece en la interfaz. Para una instalación nueva, `ATC_ADMIN_PASSWORD` y `ATC_MANAGER_PASSWORD` personalizan las contraseñas antes de crear usuarios. El botón de acceso a la demostración fue retirado. Las cuentas usan PBKDF2 y cookies HttpOnly; las sesiones se guardan como hashes en PostgreSQL y duran 8 horas.

## Ver la base de datos

En el sistema, entra como administrador y abre **Base de datos**. Verás motor, servidor, base, esquema, conteos y registros paginados de las tablas. El visor es de solo lectura; no muestra contraseñas ni tokens. Los encargados de sede no tienen acceso a ese visor global.

En **pgAdmin 4**, utiliza esta conexión:

```text
Servidor: PostgreSQL 18
Host: 127.0.0.1
Puerto: 5432
Usuario: postgres
Base: atc_db
Esquema: atc
```

Expande **Servers → PostgreSQL 18 → Databases → atc_db → Schemas → atc → Tables**. Pulsa Refresh si acabas de iniciar el sistema. Para visualizar una tabla: clic derecho → View/Edit Data → All Rows. Puedes ejecutar `sql/consultas_pgadmin.sql` en Query Tool.

Existe otra conexión registrada como `localhost`, puerto `5433`, con PostgreSQL 15. Es un servidor distinto: no es la conexión usada por esta versión.

## Configurar otra conexión

El asistente pide la contraseña sin mostrarla ni incluirla en argumentos de procesos:

```bash
python -B scripts/configure_postgres.py \
  --host 127.0.0.1 --port 5432 \
  --database atc_db --user postgres --schema atc
```

La base de datos debe existir. El asistente comprueba la conexión, guarda `.env` e inicializa las tablas en el esquema indicado. No borra ni reemplaza tablas de otros esquemas. Reinicia Uvicorn cuando cambies la configuración.

Variables disponibles:

```text
ATC_DATABASE_URL=postgresql://usuario:contraseña@host:puerto/base
ATC_DATABASE_SCHEMA=atc
ATC_ADMIN_PASSWORD=contraseña inicial de aplicación
ATC_MANAGER_PASSWORD=contraseña inicial del encargado
```

Si no se define `ATC_DATABASE_URL`, se permite SQLite local en `data/atc_operativo.db`, también sin datos ficticios. Las pruebas usan bases temporales separadas; no usan la base operativa para datos sintéticos.

## Empezar con información real

1. **Datos y archivos:** carga ventas históricas reales en CSV, XLSX o XLS. Selecciona la hoja, revisa las primeras 20 filas, mapea columnas y valida antes de importar.
2. **Recetas:** se incluyen 37 ingredientes asociados a los cuatro productos, con cantidades aproximadas por porción. Se muestran en g, mL y unidades; el almacenamiento usa kg, L y und. Guardar una cantidad la marca como ajustada. Puedes agregar o quitar ingredientes. Los costos se configuran por separado en soles y permanecen pendientes hasta ingresarlos.
3. **Inventario:** registra las existencias reales por sede o carga un archivo. Puedes hacerlo incluso antes de subir la primera venta.
4. **Predicciones:** elige mañana, 7 o 30 días. No se emiten pronósticos si no hay ventas históricas.
5. **Plan de preparación:** revisa insumos, compras y tareas; exporta Excel/PDF. Sin stock registrado, la compra queda pendiente, en lugar de suponer stock cero. Sin precio, el importe queda pendiente, en lugar de mostrar un costo ficticio.

La plantilla de ventas contiene los encabezados y ninguna venta inventada:

```csv
fecha,sede,producto,cantidad,turno,hora,temperatura,lluvia,evento
```

Formato:

- `fecha`: `AAAA-MM-DD`, `DD/MM/AAAA` o fecha nativa/serial Excel. Las fechas con zona horaria se convierten al día comercial de Lima. No se admiten ventas futuras.
- `sede`: nombre del local. El encargado solo puede importar su sede.
- `producto`: usa nombres consistentes con la receta; se admiten productos nuevos.
- `cantidad`: número finito no negativo. Usa totales por fecha, sede, producto y turno, no tickets individuales sin agregar.
- `turno`: Almuerzo, Noche o Sin turno. Si falta, se usa Sin turno.
- `hora`: entero 0–23; opcional, para mapa de calor.
- `temperatura`: °C de -30 a 55; `lluvia`: mm de 0 a 500; opcionales.
- `evento`: incremento histórico estimado entre 0 y 3; opcional. `0.15` significa +15 %.

Inventario: `sede,insumo,cantidad,unidad`. Las unidades deben coincidir con el catálogo: kg, L y und. La plantilla enumera insumos y deja las cantidades vacías para completarlas con existencias reales.

Las cargas detectan separador y codificación (UTF-8, UTF-16 con BOM, Windows-1252/Latin-1), y reportan vacíos, fechas/cantidades inválidas, negativos, duplicados, outliers y variables fuera de rango. Las correcciones permiten eliminar, rellenar cantidad con mediana o conservar observaciones admisibles. No se inventan fechas ni identidades. Los outliers usan Q3 + 3×IQR por sede/producto/turno con al menos 8 observaciones.

Reimportar reemplaza la venta con la misma clave fecha+sede+producto+turno. No suma de nuevo el mismo total. Un total Sin turno sustituye sus turnos del mismo día y viceversa; un mismo archivo no puede mezclar ambos niveles. Los archivos de la tabla se descargan como datos normalizados vigentes. Límites: 15 MB, 100 000 filas y 80 MB descomprimidos para XLSX. Revisiones temporales: 30 minutos, privadas del usuario.

## Arquitectura y módulos

Frontend HTML/CSS/JavaScript ES Modules, servido desde FastAPI. Backend con pandas, scikit-learn y persistencia transaccional PostgreSQL usando psycopg 3. Un adaptador mantiene las consultas parametrizadas y permite ejecutar las mismas pruebas con SQLite.

```text
backend/app/
  db.py                 Conexión, esquema, cuentas y migraciones de catálogo
  recipe_catalog.py     37 medidas orientativas; migración que conserva ajustes
  database_browser.py   Consultas de solo lectura y tablas/columnas permitidas
  ingestion.py          Lectura, mapeo, validación y limpieza
  ml_engine.py          Variables causales, candidatos y trazabilidad del cálculo
  forecasting.py        Selección temporal, backtesting y predicciones persistidas
  savings.py            Comparación valorizada y cobertura de precios/inventario
  planning.py           Recetas, inventario y planificación secuencial
  main.py               API, autenticación, permisos, costos y exportaciones
  demo.py               Fixtures sintéticas, exclusivamente para pruebas aisladas
frontend/
  index.html, styles.css, ui.js, app.js, ml-view.js, evaluation-view.js
scripts/configure_postgres.py
sql/consultas_pgadmin.sql
tests/
```

Tablas de PostgreSQL: `atc.users`, `sessions`, `files`, `branches`, `sales`, `ingredients`, `recipes`, `inventory`, `events`, `forecasts` y `meta`. El esquema `public` permanece separado. Los estimadores se reconstruyen cuando es necesario; las predicciones se guardan y cachean en `forecasts`. Nuevas ventas y eventos invalidan esa caché.

## Modelo e interpretación

- Selección por sede, producto y turno entre Ridge estacional (sobre logaritmo de demanda), Random Forest y Extra Trees de 64 árboles con criterio robusto. Variables: calendario, feriado peruano, temperatura, lluvia, rezagos de 1/7/14/21/28 días, estadísticas móviles y demanda del mismo día de semana durante hasta 8 semanas. Todas las variables utilizan exclusivamente datos anteriores a la fecha predicha.
- Horizontes de 1, 7 o 30 días desde la fecha actual en Lima. Si falta un tramo reciente, se predice recursivamente; se rechazan historiales con más de un año de antigüedad.
- Días ausentes son desconocidos y no se convierten en ventas cero. Series cortas usan promedio estacional y baja confianza.
- Selección con validación temporal de 21 días anteriores al backtesting final. El modelo elegido se evalúa después en tres ventanas consecutivas de hasta 7 días, con entrenamiento expansivo y predicción recursiva. MAE/RMSE en porciones; MAPE excluye reales iguales a cero, que sí cuentan para MAE/RMSE. Series cortas muestran evidencia insuficiente; no se usa la prueba final para elegir al ganador.
- Objetivo MAPE ≤12 %, calculado con errores reales sin recorte ni alteración de ventas. La interfaz indica por serie si cumple y la diferencia respecto al objetivo. No se garantiza alcanzar 12 % con cualquier historial; las observaciones atípicas se señalan para revisión y permanecen en la evaluación.
- Bandas orientativas con percentil 90 del error absoluto, ampliadas con `sqrt(1+pasos/7)`. No son extremos garantizados ni un intervalo con cobertura validada del 90 %. Sumar bandas por series tampoco produce un intervalo conjunto calibrado.
- Confianza alta: MAPE ≤12 %, 90 días y 90 % de cobertura; media: MAPE <30 % y al menos 42 días; baja: el resto. Se reduce al extender el horizonte.
- Clima futuro manual o mediana del entrenamiento; no hay API meteorológica en vivo. Eventos: escenario multiplicativo definido por el usuario, sin afirmar causalidad; si coinciden, se usa el mayor incremento. Las festividades locales requieren fechas confirmadas; el calendario nacional procede de `holidays` con PE.
- Insumos a partir de porciones completas, redondeadas hacia arriba. El stock se consume día a día sin reutilizarlo. Compras en und se redondean a enteros; kg/L a centésimas, conservando sobrantes por redondeo.
- **Ahorro potencial:** compara compras valorizadas de una preparación de referencia (promedio del mismo día de semana más un margen editable, inicialmente 20 %) con las compras recomendadas. Ambas consumen, independientemente y día a día, el mismo inventario inicial. Las compras adicionales reducen el saldo. La vista muestra importes por fecha y sede, primera fecha con saldo acumulado positivo, fórmula y supuestos. El margen es un supuesto, no una práctica observada; solo se incluyen insumos con precio y stock registrados. Los resultados parciales indican cobertura y faltantes y no representan el total del negocio. Excel y PDF incluyen el cálculo y sus supuestos.
- Ahorro realizado, desperdicio y quiebres efectivamente evitados requieren registros de merma, caducidad, costos y resultados; no se confunden con la estimación potencial.
- **Cómo funciona la predicción:** recorrido interactivo de seis etapas con el historial, variables, selección, operaciones del modelo elegido, evaluación y resultado diario. Muestra contribuciones reales de Ridge o predicciones de los árboles y recorrido del primer árbol. Reproduce el cálculo ya ejecutado; al cambiar clima y pulsar ejecutar se recalcula la predicción. Usa el mismo modelo e información del pronóstico, sin resultados preparados aparte.

Referencias primarias: [psycopg](https://www.psycopg.org/psycopg3/docs/basic/usage.html), [esquemas PostgreSQL](https://www.postgresql.org/docs/current/ddl-schemas.html), [Random Forest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html), [evaluación temporal](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html).

## Pruebas

```bash
python -B -m unittest discover -s tests -v
node --check frontend/app.js
node --check frontend/ui.js
```

Para ejecutar integración real en PostgreSQL:

```bash
ATC_TEST_POSTGRES=1 python -B -m unittest discover -s tests -p test_postgres.py -v
```

Esta prueba crea un esquema aleatorio `atc_test_*`, prueba importación, sesiones, roles, recetas, inventario, eventos, pronósticos y caché, y elimina únicamente ese esquema al terminar. No inserta fixtures en `atc`. Las pruebas normales aíslan sus datos en SQLite temporal. Los archivos CSV de ejemplo anteriores se conservan como material de prueba manual y no se cargan al arrancar.

## Recetas base por porción

| Producto | Ingredientes asociados | Referencias principales |
|---|---:|---|
| Pollo broaster | 10 | 300 g de pollo, 180 g de papa, 40 g de harina |
| Hamburguesa | 13 | 1 pan, 120 g de carne, 20 g de queso y 100 g de papa |
| Alitas BBQ | 7 | 250 g de alitas, 60 g de salsa BBQ y 120 g de papa |
| Salchipapa | 7 | 300 g de papa, 130 g de salchicha y salsas |

Las guarniciones de papa están incluidas y pueden quitarse si el producto se vende sin ellas. El aceite indica consumo aproximado por porción, no el volumen de llenado de una freidora. Las medidas no son la formulación oficial del negocio. El plan y los reportes advierten cuando utilizan recetas aproximadas.

La migración `default_recipes_v1` agrega estas asociaciones una sola vez, sin reemplazar cantidades existentes. Los ajustes y las eliminaciones se conservan después de reiniciar. No se asignan precios, existencias ni ventas.
