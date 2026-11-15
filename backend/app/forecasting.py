"""Temporal model selection, independent audit periods and forecast provenance."""
import json
import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from fastapi import HTTPException
from .db import connect,sales_frame,database_identity
from .ml_engine import MODEL_VERSION,CANDIDATES,fit_model,feature_vector,recursive_predict,evaluate_window,select_model,score

LOCK=threading.RLock()
ANALYSIS_CACHE={}
MAPE_TARGET=12.0


def revision():
    with connect() as db:return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]


def quality_alerts(frame):
    alerts=[]
    for day,row in frame.iterrows():
        if pd.isna(row['cantidad']):continue
        past=frame.loc[frame.index<day,'cantidad'].dropna()
        peers=past[past.index.dayofweek==day.dayofweek].tail(8)
        if len(peers)<4:continue
        median=float(peers.median());mad=float((peers-median).abs().median())
        if abs(float(row['cantidad'])-median)>max(6*1.4826*mad,median*.6,5):
            alerts.append({'fecha':day.strftime('%Y-%m-%d'),'cantidad':float(row['cantidad']),'referencia':round(median,2),'message':'Registro muy diferente al patrón semanal previo; se conserva en la evaluación.'})
    return alerts[-12:]


def analysis(user):
    with connect() as db:
        data_revision=db.execute("SELECT value FROM meta WHERE key='data_revision'").fetchone()
    key=(MODEL_VERSION,data_revision[0] if data_revision else revision(),user['role'],user['sede'],database_identity())
    if key in ANALYSIS_CACHE:return ANALYSIS_CACHE[key]
    data=sales_frame(user)
    if data.empty:raise HTTPException(422,'Carga ventas históricas antes de generar una predicción.')
    data['fecha']=pd.to_datetime(data['fecha'])
    today=datetime.now(ZoneInfo('America/Lima')).date()
    data=data[data['fecha'].dt.date<=today]
    if data.empty:raise HTTPException(422,'No hay ventas anteriores a hoy para entrenar.')
    bundles=[];metrics=[];backtests=[];warnings=[]
    for (sede,product,shift),group in data.groupby(['sede','producto','turno']):
        series=group.groupby('fecha').agg({'cantidad':'sum','temperatura':'mean','lluvia':'mean','evento':'max'})
        observed=len(series)
        series=series.reindex(pd.date_range(series.index.min(),series.index.max()))
        if (today-series.index.max().date()).days>365:
            raise HTTPException(422,'Los datos tienen más de un año de antigüedad. Sube ventas recientes.')
        missing=int(series['cantidad'].isna().sum())
        selected,candidates=select_model(series)
        points=[];folds=[]
        for offset in [21,14,7]:
            split=len(series)-offset
            if split<28:continue
            period,period_score=evaluate_window(series,selected,split,7)
            points.extend(period)
            folds.append({'train_start':series.index.min().strftime('%Y-%m-%d'),'train_end':series.index[split-1].strftime('%Y-%m-%d'),'test_start':series.index[split].strftime('%Y-%m-%d'),'test_end':series.index[min(split+6,len(series)-1)].strftime('%Y-%m-%d'),**period_score})
        measured=score([p['real'] for p in points],[p['prediccion'] for p in points])
        mape=measured['mape'];density=observed/len(series)
        confidence='Alta' if mape is not None and mape<=MAPE_TARGET and observed>=90 and density>=.9 else ('Media' if mape is not None and mape<30 and observed>=42 else 'Baja')
        alerts=quality_alerts(series)
        identity={'sede':sede,'producto':product,'turno':shift}
        metric={**identity,**measured,'confidence':confidence,'observations':observed,'missing_days':missing,'model':CANDIDATES[selected],'model_kind':selected,'target':MAPE_TARGET,'target_met':mape<=MAPE_TARGET if mape is not None else None,'target_gap':max(0,mape-MAPE_TARGET) if mape is not None else None,'selection':candidates,'folds':folds,'quality_alerts':alerts}
        metrics.append(metric)
        backtests.extend([{**identity,**point} for point in points])
        if missing:warnings.append(f'{sede} · {product} · {shift}: {missing} días sin registro; no se convierten en ventas cero.')
        if observed<21:warnings.append(f'{sede} · {product} · {shift}: solo {observed} días. Se usa promedio estacional con baja confianza.')
        if alerts:warnings.append(f'{sede} · {product} · {shift}: {len(alerts)} registros extremos detectados; revisa el archivo de origen.')
        model,history,temp,rain=fit_model(series,selected)
        if model is None:
            metric['model']='Promedio estacional (evidencia insuficiente)'
            metric['model_kind']='seasonal'
        bundles.append({**identity,'series':series,'model':model,'history':history,'temperature':temp,'rain':rain,'metric':metric,'points':points})
    result={'bundles':bundles,'metrics':metrics,'backtests':backtests,'warnings':warnings,'trained_until':data['fecha'].max().strftime('%Y-%m-%d'),'revision':key[1]}
    if len(ANALYSIS_CACHE)>=8:ANALYSIS_CACHE.clear()
    ANALYSIS_CACHE[key]=result
    return result


def future_impacts(sede):
    with connect() as db:events=[dict(r) for r in db.execute('SELECT * FROM events')]
    impacts={}
    for event in events:
        if not event['sede'] or event['sede']==sede:
            impacts[event['fecha']]=max(impacts.get(event['fecha'],0),event['impact'])
    return impacts


def forecast(user,horizon=7,temperature=None,rain=None):
    today=datetime.now(ZoneInfo('America/Lima')).date()
    key=json.dumps([MODEL_VERSION,revision(),user['role'],user['sede'],today.isoformat(),horizon,temperature,rain])
    with LOCK:
        with connect() as db:
            cached=db.execute('SELECT payload FROM forecasts WHERE cache_key=?',(key,)).fetchone()
            if cached:return json.loads(cached[0])
        fitted=analysis(user);rows=[]
        for bundle in fitted['bundles']:
            metric=bundle['metric'];last=bundle['series'].index.max()
            dates=pd.date_range(last+pd.Timedelta(days=1),pd.Timestamp(today)+pd.Timedelta(days=horizon))
            impacts=future_impacts(bundle['sede'])
            predicted,_=recursive_predict(bundle['model'],bundle['history'],dates,bundle['temperature'] if temperature is None else temperature,bundle['rain'] if rain is None else rain,impacts)
            errors=[abs(p['real']-p['prediccion']) for p in bundle['points']]
            residual=float(np.quantile(errors,.9)) if errors else max(float(bundle['history'].std()) if len(bundle['history'].dropna())>1 else 1,float(bundle['history'].mean())*.4,1)
            for day,pred in zip(dates,predicted):
                if day.date()<=today:continue
                steps=(day-last).days
                width=residual*math.sqrt(1+steps/7)*(1+impacts.get(day.strftime('%Y-%m-%d'),0))
                expected=pred['esperado']
                rows.append({'fecha':day.strftime('%Y-%m-%d'),'sede':bundle['sede'],'producto':bundle['producto'],'turno':bundle['turno'],'minimo':max(0,expected-width),'esperado':expected,'maximo':expected+width,'baseline_expected':pred['baseline_expected'],'model':metric['model'],'confidence':metric['confidence'] if steps<=7 else ('Baja' if metric['confidence']=='Baja' else 'Media'),'evento':impacts.get(day.strftime('%Y-%m-%d'),0)})
        assessed=[m for m in fitted['metrics'] if m['mape'] is not None]
        payload={'rows':rows,'metrics':fitted['metrics'],'backtests':fitted['backtests'],'warnings':fitted['warnings'],'horizon':horizon,'trained_until':fitted['trained_until'],'generated':datetime.now(ZoneInfo('America/Lima')).isoformat(),'model_version':MODEL_VERSION,'method':'Selección temporal por serie: Ridge, Random Forest y Extra Trees','target':{'mape_max':MAPE_TARGET,'met':sum(m['target_met'] for m in assessed),'evaluated':len(assessed),'total':len(fitted['metrics'])},'evaluation_note':'El modelo se elige con 21 días anteriores al backtesting. Se reportan tres ventanas posteriores de hasta 7 días, sin ajustar ni recortar sus errores. MAPE excluye reales iguales a cero.','interval_note':'Rango orientativo con percentil 90 del error de backtesting; se amplía con el horizonte. No garantiza cobertura estadística. Eventos son incrementos definidos por el usuario.','weather_note':'Clima manual del horizonte; si se omite, se utiliza la mediana del entrenamiento. No hay conexión meteorológica en vivo.'}
        with connect() as db:
            db.execute('INSERT INTO forecasts(cache_key,payload) VALUES(?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,created=CURRENT_TIMESTAMP',(key,json.dumps(payload,allow_nan=False)))
        return payload


def explain_prediction(user,sede,product,shift,horizon=7,temperature=None,rain=None):
    with LOCK:
        fitted=analysis(user)
        bundle=next((b for b in fitted['bundles'] if (b['sede'],b['producto'],b['turno'])==(sede,product,shift)),None)
        if not bundle:raise HTTPException(404,'No hay una serie disponible para esa sede, producto y turno.')
        today=datetime.now(ZoneInfo('America/Lima')).date();last=bundle['series'].index.max()
        dates=pd.date_range(last+pd.Timedelta(days=1),pd.Timestamp(today)+pd.Timedelta(days=horizon))
        temp=bundle['temperature'] if temperature is None else temperature
        rainfall=bundle['rain'] if rain is None else rain
        _,traces=recursive_predict(bundle['model'],bundle['history'],dates,temp,rainfall,future_impacts(sede),explain=True)
        series=bundle['series']
        training=[{'fecha':day.strftime('%Y-%m-%d'),'cantidad':float(row['cantidad']) if pd.notna(row['cantidad']) else None,'temperatura':float(row['temperatura']) if pd.notna(row['temperatura']) else None,'lluvia':float(row['lluvia']) if pd.notna(row['lluvia']) else None} for day,row in series.tail(35).iterrows()]
        return {'sede':sede,'producto':product,'turno':shift,'model_version':MODEL_VERSION,'metric':bundle['metric'],'training':training,'training_range':{'start':series.index.min().strftime('%Y-%m-%d'),'end':series.index.max().strftime('%Y-%m-%d'),'observations':int(series['cantidad'].notna().sum()),'samples':bundle['model'].samples if bundle['model'] else 0},'predictions':[t for t in traces if pd.Timestamp(t['fecha']).date()>today],'weather_source':'Escenario manual' if temperature is not None or rain is not None else 'Medianas históricas del entrenamiento','note':'Los valores y recorridos se calculan con el mismo modelo y las mismas variables usados para emitir la predicción. Los aportes y pesos describen el cálculo del modelo; no prueban causalidad.'}
