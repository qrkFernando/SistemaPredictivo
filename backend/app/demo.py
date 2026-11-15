"""Synthetic fixtures for isolated automated tests only."""
from datetime import date,timedelta
import numpy as np
from .db import connect,SEDES,PRODUCTS

def seed_demo_data():
    with connect() as db:
        if db.execute('SELECT count(*) FROM sales').fetchone()[0]:
            return
        ingredients = [('Pollo','kg',12.8,'Marinar'),('Papa','kg',2.6,'Prefreír'),('Pan de hamburguesa','und',0.65,'Preparar'),('Carne de hamburguesa','kg',24,'Preparar'),('Alitas','kg',14.5,'Marinar'),('Salsa BBQ','kg',13,'Preparar'),('Salchicha','kg',11,'Preparar'),('Aceite','L',7.8,'Preparar')]
        db.executemany('UPDATE ingredients SET cost=?,action=? WHERE name=?',[(cost,action,name) for name,unit,cost,action in ingredients])
        recipes = [('Pollo broaster','Pollo',0.3),('Pollo broaster','Papa',0.18),('Pollo broaster','Aceite',0.025),('Hamburguesa','Pan de hamburguesa',1),('Hamburguesa','Carne de hamburguesa',0.12),('Hamburguesa','Papa',0.1),('Alitas BBQ','Alitas',0.25),('Alitas BBQ','Salsa BBQ',0.06),('Alitas BBQ','Papa',0.12),('Salchipapa','Papa',0.3),('Salchipapa','Salchicha',0.13),('Salchipapa','Aceite',0.025)]
        db.executemany('INSERT INTO recipes(producto,insumo,amount) VALUES(?,?,?)', recipes)
        rng = np.random.default_rng(42)
        end = date.today()
        # Use Lima's date independent of the server timezone.
        from zoneinfo import ZoneInfo
        from datetime import datetime
        end = datetime.now(ZoneInfo('America/Lima')).date()
        rows = []
        for si, sede in enumerate(SEDES):
            fid = 'demo-' + str(si)
            db.execute('INSERT INTO files(id,name,owner,row_count,quality,demo) VALUES(?,?,1,?,100,1)', (fid,'Ventas · ' + sede + '.xlsx',180*8))
            for di in range(180):
                day = end - timedelta(days=179-di)
                temperature = round(13 + 3*np.sin(di/20) + rng.normal(0,1),1)
                rain = round(max(0,rng.normal(2,3)),1)
                for pi, product in enumerate(PRODUCTS):
                    for shift in ['Almuerzo','Noche']:
                        base = [62,38,31,45][pi] * [1.15,0.9,1][si]
                        expected = base * (1.4 if day.weekday()>=4 else 0.91) * (0.65 if shift=='Almuerzo' else 1) * (1+di/1500)
                        quantity = max(0,round(expected + rng.normal(0,base*0.12)))
                        rows.append((fid,day.isoformat(),sede,product,quantity,shift,13 if shift=='Almuerzo' else 20,temperature,rain,0))
            for name, unit, cost, action in ingredients:
                db.execute('INSERT INTO inventory VALUES(?,?,?)',(sede,name,round(float(rng.uniform(12,75)) if unit!='und' else float(rng.integers(60,160)),2)))
        db.executemany('INSERT INTO sales(file_id,fecha,sede,producto,cantidad,turno,hora,temperatura,lluvia,evento) VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
