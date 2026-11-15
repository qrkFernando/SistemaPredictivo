"""Opt-in integration tests in a new, temporary PostgreSQL schema only."""
import os
import unittest
import uuid
from fastapi.testclient import TestClient
from backend.app import db
from backend.app.main import app

@unittest.skipUnless(os.environ.get('ATC_TEST_POSTGRES')=='1','PostgreSQL integration opt-in')
class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.DATABASE_URL:raise unittest.SkipTest('ATC_DATABASE_URL not configured')
        cls.saved=(db.SCHEMA,db.DEMO_DATA)
        cls.schema='atc_test_'+uuid.uuid4().hex
        db.SCHEMA=cls.schema;db.DEMO_DATA=True
        cls.client=TestClient(app);cls.client.__enter__()
        cls.client.post('/api/login',json={'email':'admin@americantexas.pe','password':'Texas2026!'})
    @classmethod
    def tearDownClass(cls):
        import psycopg
        cls.client.__exit__(None,None,None)
        with psycopg.connect(db.DATABASE_URL) as raw:
            raw.execute('DROP SCHEMA "'+cls.schema+'" CASCADE')
        db.SCHEMA,db.DEMO_DATA=cls.saved
    def test_postgresql_round_trip_and_metadata(self):
        summary=self.client.get('/api/summary')
        self.assertEqual(summary.status_code,200,summary.text)
        self.assertEqual(summary.json()['database']['engine'],'PostgreSQL')
        self.assertEqual(summary.json()['rows'],4320)
        for path in ['/api/history','/api/recipes','/api/inventory','/api/database','/api/database/tables/users']:
            r=self.client.get(path);self.assertEqual(r.status_code,200,r.text)
    def test_import_inventory_recipe_event_and_forecast_persistence(self):
        csv='fecha,sede,producto,cantidad,turno\n2026-10-01,San Carlos,Pollo broaster,123,Noche'
        p=self.client.post('/api/uploads/preview',files={'file':('venta.csv',csv.encode(),'text/csv')}).json()
        body={'token':p['token'],'mapping':p['mapping']}
        self.assertEqual(self.client.post('/api/uploads/import',json=body).status_code,200)
        with db.connect() as raw:
            self.assertEqual(raw.execute("SELECT cantidad FROM sales WHERE sede='San Carlos' AND producto='Pollo broaster' AND fecha='2026-10-01' AND turno='Noche'").fetchone()[0],123)
        self.assertEqual(self.client.put('/api/inventory',json={'sede':'San Carlos','insumo':'Pollo','amount':12.34}).status_code,200)
        self.assertEqual(self.client.put('/api/recipes',json={'producto':'Pollo broaster','insumo':'Pollo','amount':.31}).status_code,200)
        event=self.client.post('/api/events',json={'name':'Evento de prueba','fecha':'2026-11-01','sede':'San Carlos','impact':.2})
        self.assertEqual(event.status_code,200,event.text)
        self.assertEqual(self.client.delete('/api/events/'+str(event.json()['id'])).status_code,200)
        result=self.client.get('/api/forecast?horizon=1')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(result.json()['rows']),24)
        self.assertEqual(self.client.get('/api/plan?horizon=1').status_code,200)
        self.assertEqual(self.client.get('/api/forecast?horizon=1').json(),result.json())
        with db.connect() as raw:
            self.assertGreater(raw.execute('SELECT count(*) FROM forecasts').fetchone()[0],0)
