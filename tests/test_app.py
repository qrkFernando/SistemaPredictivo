import io
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import db
from backend.app.main import app, PENDING
from backend.app.planning import plan

class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.old=db.DB_PATH;cls.old_url=db.DATABASE_URL;cls.old_demo=db.DEMO_DATA
        db.DATABASE_URL='';db.DEMO_DATA=True;db.DB_PATH=Path(cls.tmp.name)/'test.db'
        cls.client=TestClient(app);cls.client.__enter__()
        cls.client.post('/api/login',json={'email':'admin@americantexas.pe','password':'Texas2026!'})
        cls.manager=TestClient(app)
        cls.manager.post('/api/login',json={'email':'sancarlos@americantexas.pe','password':'Sede2026!'})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None);db.DB_PATH=cls.old;db.DATABASE_URL=cls.old_url;db.DEMO_DATA=cls.old_demo;cls.tmp.cleanup()

    def test_session_and_scoping(self):
        self.assertEqual(TestClient(app).get('/api/summary').status_code,401)
        data=self.manager.get('/api/summary').json()
        self.assertEqual([s['name'] for s in data['sedes']],['San Carlos'])
        self.assertTrue(all(r['sede']=='San Carlos' for r in self.manager.get('/api/inventory').json()))
        self.assertEqual(self.manager.get('/api/files/demo-0/download').status_code,404)
        self.assertEqual(self.manager.put('/api/recipes',json={'producto':'X','insumo':'Pollo','amount':1}).status_code,403)
        self.assertEqual(self.manager.put('/api/inventory',json={'sede':'Giráldez','insumo':'Pollo','amount':1}).status_code,403)

    def upload(self,client=None,sede='San Carlos',product='Producto de prueba'):
        client=client or self.client
        csv=f'fecha,sede,producto,cantidad\n2026-10-01,{sede},{product},15\n2026-10-02,{sede},{product},25'
        response=client.post('/api/uploads/preview',files={'file':('ventas.csv',csv.encode(),'text/csv')})
        self.assertEqual(response.status_code,200,response.text)
        p=response.json();return {'token':p['token'],'mapping':p['mapping']}

    def test_import_validation_idempotency_and_ownership(self):
        body=self.upload()
        self.assertEqual(self.client.post('/api/uploads/validate',json=body).json()['quality'],100)
        self.assertEqual(self.manager.post('/api/uploads/validate',json=body).status_code,404)
        one=self.client.post('/api/uploads/import',json=body)
        self.assertEqual(one.status_code,200,one.text)
        self.assertEqual(one.json()['new_rows'],2)
        two=self.client.post('/api/uploads/import',json=self.upload())
        self.assertEqual(two.json()['new_rows'],0)
        self.assertEqual(two.json()['updated_rows'],2)
        wrong=self.upload(self.manager,'Giráldez')
        self.assertEqual(self.manager.post('/api/uploads/import',json=wrong).status_code,403)

    def test_forecast_backtesting_and_exports(self):
        response=self.client.get('/api/forecast?horizon=7')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertGreater(len(data['rows']),100);self.assertGreater(len(data['backtests']),100)
        for row in data['rows']:
            self.assertLessEqual(row['minimo'],row['esperado']);self.assertLessEqual(row['esperado'],row['maximo'])
        self.assertTrue(all(m['test_days']<=21 for m in data['metrics']))
        for kind,prefix in [('xlsx',b'PK'),('pdf',b'%PDF')]:
            res=self.client.get('/api/export/'+kind+'?horizon=7&sede=San%20Carlos')
            self.assertEqual(res.status_code,200,res.text[:150] if res.status_code!=200 else '')
            self.assertTrue(res.content.startswith(prefix))
        with db.connect() as database:
            saved=database.execute('SELECT count(*) FROM forecasts').fetchone()[0]
        self.assertGreater(saved,0)

    def test_inventory_allocation_is_not_reused(self):
        admin={'role':'admin','sede':None}
        self.client.put('/api/inventory',json={'sede':'San Carlos','insumo':'Pollo','amount':45})
        result=plan({'rows':[{'fecha':f'2026-10-0{day}','sede':'San Carlos','producto':'Pollo broaster','turno':'Noche','esperado':100,'minimo':90,'maximo':110} for day in [1,2]]},admin)
        chicken=[r for r in result['rows'] if r['insumo']=='Pollo']
        self.assertEqual(chicken[0]['comprar'],0);self.assertEqual(chicken[1]['comprar'],15)
        self.assertEqual(chicken[1]['inventario'],15)

    def test_model_explanation_matches_forecast_and_respects_scope(self):
        prediction=self.manager.get('/api/forecast?horizon=1').json()
        row=prediction['rows'][0]
        params={key:row[key] for key in ['sede','producto','turno']}
        params['horizon']=1
        response=self.manager.get('/api/ml/explain',params=params)
        self.assertEqual(response.status_code,200,response.text)
        explanation=response.json()
        self.assertAlmostEqual(explanation['predictions'][-1]['prediction'],row['esperado'],places=6)
        params['sede']='Giráldez'
        self.assertEqual(self.manager.get('/api/ml/explain',params=params).status_code,403)

    def test_savings_assumptions_and_validation(self):
        response=self.manager.get('/api/plan?horizon=1&margin=0.25')
        self.assertEqual(response.status_code,200,response.text)
        savings=response.json()['savings']
        self.assertEqual(savings['margin'],.25)
        self.assertFalse(savings['measured'])
        self.assertTrue(all(r['sede']=='San Carlos' for r in savings['rows']))
        self.assertIn(savings['status'],['completo','parcial','pendiente'])
        self.assertEqual(self.manager.get('/api/plan?margin=-0.1').status_code,422)
        self.assertEqual(self.manager.get('/api/export/xlsx?margin=1.1').status_code,422)

    def test_invalid_inventory_is_atomic(self):
        before=self.client.get('/api/inventory').json()
        csv='sede,insumo,cantidad,unidad\nSan Carlos,Pollo,99,kg\nSan Carlos,Papa,-3,kg'
        res=self.client.post('/api/inventory/upload',files={'file':('stock.csv',csv.encode(),'text/csv')})
        self.assertEqual(res.status_code,422)
        self.assertEqual(before,self.client.get('/api/inventory').json())

    def test_events_weather_and_role_scope(self):
        self.assertEqual(self.manager.post('/api/events',json={'name':'Concierto','fecha':'2026-11-01','sede':None,'impact':.2}).status_code,403)
        r=self.manager.post('/api/events',json={'name':'Concierto','fecha':'2026-11-01','sede':'San Carlos','impact':.2})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.manager.delete('/api/events/'+str(r.json()['id'])).status_code,200)
        self.assertEqual(self.client.get('/api/forecast?horizon=7&temperature=100').status_code,422)
        self.assertEqual(self.client.get('/api/forecast?horizon=5').status_code,422)

    def test_csrf_origin(self):
        res=self.client.put('/api/inventory',headers={'Origin':'https://attacker.example'},json={'sede':'San Carlos','insumo':'Pollo','amount':1})
        self.assertEqual(res.status_code,403)

    def test_daily_totals_replace_shifts_without_double_counting(self):
        product='Cambio de granularidad'
        def import_csv(text):
            p=self.client.post('/api/uploads/preview',files={'file':('grain.csv',text.encode(),'text/csv')}).json()
            return self.client.post('/api/uploads/import',json={'token':p['token'],'mapping':p['mapping']})
        header='fecha,sede,producto,cantidad,turno\n'
        prefix='2026-10-01,San Carlos,'+product
        self.assertEqual(import_csv(header+prefix+',10,Almuerzo\n'+prefix+',20,Noche').status_code,200)
        self.assertEqual(import_csv(header+prefix+',40,Sin turno').status_code,200)
        with db.connect() as database:
            rows=database.execute('SELECT cantidad,turno FROM sales WHERE producto=?',(product,)).fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['cantidad'],40)
        self.assertEqual(import_csv(header+prefix+',40,Sin turno\n'+prefix+',20,Noche').status_code,422)

    def test_manager_horizons_and_future_event_scenario(self):
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        tomorrow=(datetime.now(ZoneInfo('America/Lima')).date()+timedelta(days=1)).isoformat()
        base=self.manager.get('/api/forecast?horizon=1').json()
        self.assertTrue(all(r['sede']=='San Carlos' and r['fecha']==tomorrow for r in base['rows']))
        event=self.manager.post('/api/events',json={'name':'Evento de prueba','fecha':tomorrow,'sede':'San Carlos','impact':.25}).json()
        try:
            scenario=self.manager.get('/api/forecast?horizon=1').json()
            for before,after in zip(base['rows'],scenario['rows']):
                self.assertAlmostEqual(after['esperado'],before['esperado']*1.25,delta=.2)
            month=self.manager.get('/api/forecast?horizon=30').json()
            self.assertEqual(len(set(r['fecha'] for r in month['rows'])),30)
            self.assertTrue(all(r['sede']=='San Carlos' for r in month['backtests']))
        finally:
            self.manager.delete('/api/events/'+str(event['id']))

    def test_formula_injection_export(self):
        from backend.app.main import excel_response
        import pandas as pd
        from openpyxl import load_workbook
        response=excel_response({'Datos':pd.DataFrame({'producto':['=HYPERLINK("malicious")']})},'test.xlsx')
        import asyncio
        async def collect():
            parts=[]
            async for part in response.body_iterator:parts.append(part)
            return b''.join(parts)
        workbook=load_workbook(io.BytesIO(asyncio.run(collect())))
        self.assertTrue(workbook.active['A2'].value.startswith("'="))

if __name__=='__main__':unittest.main()
