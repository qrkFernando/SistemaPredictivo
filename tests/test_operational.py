import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import db
from backend.app.main import app

class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.saved=(db.DATABASE_URL,db.DB_PATH,db.DEMO_DATA)
        self.tmp=tempfile.TemporaryDirectory()
        db.DATABASE_URL='';db.DB_PATH=Path(self.tmp.name)/'operational.db';db.DEMO_DATA=False
        self.client=TestClient(app);self.client.__enter__()
        self.client.post('/api/login',json={'email':'admin@americantexas.pe','password':'Texas2026!'})
    def tearDown(self):
        self.client.__exit__(None,None,None)
        db.DATABASE_URL,db.DB_PATH,db.DEMO_DATA=self.saved
        self.tmp.cleanup()
    def test_no_invented_business_data(self):
        summary=self.client.get('/api/summary').json()
        self.assertEqual(summary['rows'],0)
        self.assertEqual(summary['files'],[])
        self.assertFalse(summary['demo'])
        self.assertEqual(len(summary['sedes']),3)
        self.assertTrue(all(i['cost'] is None for i in summary['ingredients']))
        self.assertEqual(self.client.get('/api/inventory').json(),[])
        recipes=self.client.get('/api/recipes').json()
        self.assertEqual(len(recipes),37)
        self.assertTrue(all(r['approximate']==1 for r in recipes))
        self.assertEqual(self.client.get('/api/forecast').status_code,422)
    def test_inventory_before_first_sale_and_real_cost(self):
        r=self.client.put('/api/inventory',json={'sede':'San Carlos','insumo':'Pollo','amount':12})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.put('/api/ingredients/cost',json={'insumo':'Pollo','cost':14.5}).status_code,200)
        self.assertEqual(self.client.get('/api/inventory').json()[0]['cost'],14.5)
    def test_database_view_hides_secrets_and_disallows_arbitrary_sql(self):
        overview=self.client.get('/api/database')
        self.assertEqual(overview.status_code,200)
        users=self.client.get('/api/database/tables/users').json()
        self.assertNotIn('password',users['columns'])
        self.assertTrue(all('password' not in row for row in users['rows']))
        self.assertEqual(self.client.get('/api/database/tables/sessions').status_code,404)
        manager=TestClient(app)
        manager.post('/api/login',json={'email':'sancarlos@americantexas.pe','password':'Sede2026!'})
        self.assertEqual(manager.get('/api/database').status_code,403)
    def test_template_contains_headers_without_fake_sales(self):
        import io
        import pandas as pd
        response=self.client.get('/api/template')
        frame=pd.read_excel(io.BytesIO(response.content))
        self.assertEqual(list(frame.columns)[:4],['fecha','sede','producto','cantidad'])
        self.assertEqual(len(frame),0)

    def test_unknown_stock_and_price_are_not_reported_as_zero(self):
        from backend.app.planning import plan
        self.client.put('/api/recipes',json={'producto':'Pollo broaster','insumo':'Pollo','amount':.3})
        prediction={'rows':[{'fecha':'2026-10-21','sede':'San Carlos','producto':'Pollo broaster','turno':'Noche','esperado':100,'minimo':90,'maximo':110}]}
        user={'role':'admin','sede':None}
        result=plan(prediction,user)
        chicken=next(r for r in result['rows'] if r['insumo']=='Pollo')
        self.assertEqual(chicken['estado'],'pendiente')
        self.assertIsNone(chicken['inventario'])
        self.assertIsNone(chicken['comprar'])
        self.assertIsNone(result['kpis']['compras_estimadas'])
        self.client.put('/api/inventory',json={'sede':'San Carlos','insumo':'Pollo','amount':0})
        result=plan(prediction,user)
        chicken=next(r for r in result['rows'] if r['insumo']=='Pollo')
        self.assertEqual(chicken['comprar'],30)
        self.assertIsNone(chicken['costo'])
        self.client.put('/api/ingredients/cost',json={'insumo':'Pollo','cost':12})
        self.assertEqual(next(r for r in plan(prediction,user)['rows'] if r['insumo']=='Pollo')['costo'],360)

    def test_recipe_units_and_preservation_after_restart(self):
        rows=self.client.get('/api/recipes').json()
        pepper=next(r for r in rows if r['producto']=='Hamburguesa' and r['insumo']=='Pimienta')
        self.assertEqual(pepper['amount'],.0003)
        self.assertEqual(pepper['unit'],'kg')
        egg=next(r for r in rows if r['insumo']=='Huevo')
        self.assertEqual(egg['amount'],.25)
        self.assertEqual(egg['unit'],'und')
        body={'producto':'Pollo broaster','insumo':'Pollo','amount':.275}
        self.assertEqual(self.client.put('/api/recipes',json=body).status_code,200)
        self.assertEqual(self.client.request('DELETE','/api/recipes',json={'producto':'Hamburguesa','insumo':'Queso'}).status_code,200)
        db.init_db()
        rows=self.client.get('/api/recipes').json()
        chicken=next(r for r in rows if r['producto']=='Pollo broaster' and r['insumo']=='Pollo')
        self.assertEqual(chicken['amount'],.275)
        self.assertEqual(chicken['approximate'],0)
        self.assertFalse(any(r['producto']=='Hamburguesa' and r['insumo']=='Queso' for r in rows))

    def test_recipe_approximation_is_reported_in_plan(self):
        from backend.app.planning import plan
        prediction={'rows':[{'fecha':'2026-10-21','sede':'San Carlos','producto':'Salchipapa','turno':'Noche','esperado':10,'minimo':9,'maximo':11}]}
        result=plan(prediction,{'role':'admin','sede':None})
        self.assertEqual(result['approximate_recipes'],['Salchipapa'])
        self.assertIn('medidas aproximadas',result['note'])
        self.assertEqual(next(r for r in result['rows'] if r['insumo']=='Papa')['requerido'],3)

    def test_existing_recipe_schema_and_quantities_are_migrated(self):
        import sqlite3
        original=db.DB_PATH
        try:
            db.DB_PATH=Path(self.tmp.name)/'legacy.db'
            with sqlite3.connect(db.DB_PATH) as raw:
                raw.execute('CREATE TABLE recipes(producto TEXT,insumo TEXT,amount REAL,PRIMARY KEY(producto,insumo))')
                raw.execute("INSERT INTO recipes VALUES('Pollo broaster','Pollo',.425)")
            db.init_db()
            with db.connect() as raw:
                row=raw.execute("SELECT amount,approximate FROM recipes WHERE producto='Pollo broaster' AND insumo='Pollo'").fetchone()
            self.assertEqual(row['amount'],.425)
            self.assertEqual(row['approximate'],0)
        finally:
            db.DB_PATH=original
