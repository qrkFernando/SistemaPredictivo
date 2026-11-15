import hmac
import io
import json
import math
import os
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import urlparse
from pathlib import Path
from xml.sax.saxutils import escape
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request, Response, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from .db import ROOT, connect, init_db, password_hash, sales_frame, bump_revision, SEDES, PRODUCTS, database_info
from .ingestion import read_file, records, validate, clean
from .forecasting import forecast, explain_prediction
from .planning import plan

PENDING={}
ATTEMPTS={}

@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app=FastAPI(title='American Texas Chicken · Demanda',version='1.0.0',lifespan=lifespan)

@app.middleware('http')
async def origin_guard(request,call_next):
    origin=request.headers.get('origin')
    if request.method in ['POST','PUT','DELETE','PATCH'] and origin and urlparse(origin).netloc!=request.headers.get('host'):
        return JSONResponse({'detail':'Origen de solicitud no permitido.'},status_code=403)
    result=await call_next(request)
    result.headers['X-Content-Type-Options']='nosniff'
    result.headers['Referrer-Policy']='same-origin'
    result.headers['X-Frame-Options']='DENY'
    if request.url.path.startswith('/api/'):
        result.headers['Cache-Control']='no-store'
    return result


def current_user(request:Request):
    token=request.cookies.get('atc_session','')
    digest=__import__('hashlib').sha256(token.encode()).hexdigest()
    with connect() as db:
        row=db.execute('SELECT u.id,u.email,u.name,u.role,u.sede FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(digest,time.time())).fetchone()
    if not row: raise HTTPException(401,'Inicia sesión para continuar.')
    return dict(row)


def admin(user=Depends(current_user)):
    if user['role']!='admin': raise HTTPException(403,'Solo el administrador puede configurar recetas.')
    return user


def check_sede(user,sede):
    if user['role']!='admin' and sede!=user['sede']:
        raise HTTPException(403,'Solo puedes consultar y modificar tu sede asignada.')

class Login(BaseModel):
    email:str
    password:str

@app.post('/api/login')
def login(body:Login,request:Request,response:Response):
    key=(request.client.host,body.email.lower())
    now=time.time()
    if len(ATTEMPTS)>1000: ATTEMPTS.clear()
    ATTEMPTS[key]=[t for t in ATTEMPTS.get(key,[]) if now-t<600]
    if len(ATTEMPTS[key])>=8: raise HTTPException(429,'Demasiados intentos. Vuelve a intentar en 10 minutos.')
    with connect() as db:
        row=db.execute('SELECT * FROM users WHERE email=?',(body.email.lower().strip(),)).fetchone()
        if not row or not hmac.compare_digest(password_hash(body.password,row['password'].split(':')[0]),row['password']):
            ATTEMPTS[key].append(now)
            raise HTTPException(401,'Correo o contraseña incorrectos.')
        ATTEMPTS.pop(key,None)
        token=secrets.token_urlsafe(32)
        digest=__import__('hashlib').sha256(token.encode()).hexdigest()
        db.execute('DELETE FROM sessions WHERE expires<?',(now,))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(digest,row['id'],now+28800))
    response.set_cookie('atc_session',token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=28800)
    return {k:row[k] for k in ['id','name','email','role','sede']}

@app.post('/api/logout')
def logout(request:Request,response:Response,user=Depends(current_user)):
    digest=__import__('hashlib').sha256(request.cookies.get('atc_session','').encode()).hexdigest()
    with connect() as db: db.execute('DELETE FROM sessions WHERE token=?',(digest,))
    response.delete_cookie('atc_session')
    return {'ok':True}

@app.get('/api/me')
def me(user=Depends(current_user)): return user

@app.get('/api/summary')
def summary(user=Depends(current_user)):
    df=sales_frame(user)
    with connect() as db:
        files=[dict(r) for r in db.execute('SELECT f.*,u.name AS uploader,u.email FROM files f JOIN users u ON f.owner=u.id ORDER BY f.created DESC')]
        events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY fecha') if user['role']=='admin' or not r['sede'] or r['sede']==user['sede']]
        ingredients=[dict(r) for r in db.execute('SELECT * FROM ingredients')]
    visible=[]
    for f in files:
        sub=df[df['file_id']==f['id']]
        if sub.empty: continue
        created=datetime.fromisoformat(f['created']) if isinstance(f['created'],str) else f['created']
        f['created']=created.replace(tzinfo=created.tzinfo or ZoneInfo('UTC')).astimezone(ZoneInfo('America/Lima')).isoformat()
        f['row_count']=len(sub); f['sedes']=list(sub['sede'].unique()); visible.append(f)
    sedes=[]
    for sede,g in df.groupby('sede'):
        sedes.append({'name':sede,'files':g['file_id'].nunique(),'products':g['producto'].nunique(),'sales':float(g['cantidad'].sum()),'rows':len(g)})
    with connect() as db:
        configured=[r['name'] for r in db.execute('SELECT name FROM branches ORDER BY name') if user['role']=='admin' or r['name']==user['sede']]
    existing={s['name'] for s in sedes}
    sedes.extend({'name':name,'files':0,'products':0,'sales':0,'rows':0} for name in configured if name not in existing)
    sedes.sort(key=lambda s:s['name'])
    return {'database':database_info(),'sedes':sedes,'files':visible,'events':events,'ingredients':ingredients,'rows':len(df),'products':list(dict.fromkeys(PRODUCTS+(list(df['producto'].unique()) if len(df) else []))),'date_start':df['fecha'].min() if len(df) else None,'date_end':df['fecha'].max() if len(df) else None,'demo':False,'quality':round(sum(f['quality']*f['row_count'] for f in visible)/max(1,len(df)),1)}

@app.get('/api/history')
def history(user=Depends(current_user)):
    df=sales_frame(user)
    if df.empty:return {'daily':[],'heatmap':[],'branches':[]}
    daily=df.groupby(['fecha','sede','producto'],as_index=False)['cantidad'].sum().tail(2400)
    df['weekday']=pd.to_datetime(df['fecha']).dt.dayofweek
    heat=df.dropna(subset=['hora']).groupby(['fecha','sede','producto','weekday','hora'])['cantidad'].sum().reset_index().groupby(['sede','producto','weekday','hora'])['cantidad'].mean().reset_index()
    branches=df.groupby(['sede','producto'],as_index=False)['cantidad'].sum()
    return {'daily':records(daily),'heatmap':records(heat),'branches':records(branches)}

async def safe_upload(file):
    if not file.filename: raise HTTPException(422,'Selecciona un archivo.')
    content=await file.read(15*1024*1024+1)
    if len(content)>15*1024*1024: raise HTTPException(413,'El archivo supera los 15 MB.')
    if Path(file.filename).suffix.lower()=='.xlsx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if sum(f.file_size for f in z.infolist())>80*1024*1024:
                    raise HTTPException(413,'El Excel descomprimido supera los 80 MB.')
        except zipfile.BadZipFile: raise HTTPException(422,'El archivo Excel está dañado.')
    return content

@app.post('/api/uploads/preview')
async def preview(file:UploadFile=File(...),sheet:Optional[str]=Form(None),user=Depends(current_user)):
    content=await safe_upload(file)
    now=time.time()
    for key in list(PENDING):
        if now-PENDING[key]['created']>1800: PENDING.pop(key,None)
    if sum(len(x['content']) for x in PENDING.values())+len(content)>100*1024*1024:
        raise HTTPException(429,'Hay demasiados archivos en revisión. Espera o reinicia la revisión.')
    frame,meta,mapping=read_file(content,file.filename,sheet)
    token=secrets.token_urlsafe(24)
    PENDING[token]={'owner':user['id'],'content':content,'name':Path(file.filename).name,'sheet':meta.get('sheet'),'created':now}
    return {'token':token,'columns':list(frame.columns),'preview':json.loads(frame.head(20).to_json(orient='records',date_format='iso')),'total':len(frame),'mapping':mapping,**meta}

class Inspect(BaseModel):
    token:str
    mapping:dict[str,str]
    policies:dict[str,str]=Field(default_factory=dict)


def pending_frame(body,user):
    pending=PENDING.get(body.token)
    if not pending or pending['owner']!=user['id'] or time.time()-pending['created']>1800:
        raise HTTPException(404,'La revisión del archivo venció. Vuelve a subirlo.')
    frame,_,_=read_file(pending['content'],pending['name'],pending['sheet'])
    return frame,pending

@app.post('/api/uploads/validate')
def inspect(body:Inspect,user=Depends(current_user)):
    frame,_=pending_frame(body,user)
    _,report,_=validate(frame,body.mapping)
    return report

@app.post('/api/uploads/import')
def import_file(body:Inspect,user=Depends(current_user)):
    frame,pending=pending_frame(body,user)
    cleaned,report=clean(frame,body.mapping,body.policies)
    for sede in cleaned['sede'].unique():check_sede(user,sede)
    for _, group in cleaned.groupby(['fecha','sede','producto']):
        if 'Sin turno' in set(group['turno']) and group['turno'].nunique()>1:
            raise HTTPException(422,'No mezcles un total diario (Sin turno) con sus turnos para el mismo producto, sede y fecha.')
    today=datetime.now(ZoneInfo('America/Lima')).date().isoformat()
    if cleaned['fecha'].gt(today).any():
        raise HTTPException(422,'Las ventas históricas no pueden tener fechas futuras.')
    fid=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO files(id,name,owner,row_count,quality) VALUES(?,?,?,?,?)',(fid,pending['name'],user['id'],len(cleaned),report['quality']))
        new_rows=0; updated_rows=0
        db.executemany('INSERT INTO branches(name) VALUES(?) ON CONFLICT(name) DO NOTHING',[(s,) for s in cleaned['sede'].unique()])
        for r in records(cleaned):
            if r['turno']=='Sin turno':
                db.execute('DELETE FROM sales WHERE fecha=? AND sede=? AND producto=? AND turno<>?',(r['fecha'],r['sede'],r['producto'],'Sin turno'))
            else:
                db.execute('DELETE FROM sales WHERE fecha=? AND sede=? AND producto=? AND turno=?',(r['fecha'],r['sede'],r['producto'],'Sin turno'))
            # Reupload replaces an existing daily total; it never doubles it.
            exists=db.execute('SELECT 1 FROM sales WHERE fecha=? AND sede=? AND producto=? AND turno=?',(r['fecha'],r['sede'],r['producto'],r['turno'])).fetchone()
            if exists: updated_rows+=1
            else: new_rows+=1
            db.execute('''INSERT INTO sales(file_id,fecha,sede,producto,cantidad,turno,hora,temperatura,lluvia,evento) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fecha,sede,producto,turno) DO UPDATE SET file_id=excluded.file_id,cantidad=excluded.cantidad,hora=excluded.hora,temperatura=excluded.temperatura,lluvia=excluded.lluvia,evento=excluded.evento''', (fid,r['fecha'],r['sede'],r['producto'],r['cantidad'],r['turno'],r['hora'],r['temperatura'],r['lluvia'],r['evento'] or 0))
        bump_revision(db)
    PENDING.pop(body.token,None)
    return {'id':fid,'imported':len(cleaned),'new_rows':new_rows,'updated_rows':updated_rows,'removed':len(frame)-len(cleaned),'message':'Archivo importado. Las próximas predicciones se reentrenarán con estos datos.'}


def excel_response(sheets,filename):
    buffer=io.BytesIO()
    with pd.ExcelWriter(buffer,engine='openpyxl') as writer:
        for name,frame in sheets.items():
            safe=frame.copy()
            for col in safe.select_dtypes(include='object').columns:
                safe[col]=safe[col].map(lambda v:"'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v)
            safe.to_excel(writer,sheet_name=name[:31],index=False)
            ws=writer.sheets[name[:31]]
            ws.freeze_panes='A2'
            for column in ws.columns:
                ws.column_dimensions[column[0].column_letter].width=min(38,max(14,max(len(str(c.value or '')) for c in column)+2))
    buffer.seek(0)
    return StreamingResponse(buffer,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})

@app.get('/api/template')
def template(kind:str='ventas',user=Depends(current_user)):
    if kind=='inventario':
        with connect() as db:names=[dict(r) for r in db.execute('SELECT * FROM ingredients')]
        frame=pd.DataFrame([{'sede':user['sede'] or SEDES[0],'insumo':r['name'],'cantidad':None,'unidad':r['unit']} for r in names])
    else:
        frame=pd.DataFrame(columns=['fecha','sede','producto','cantidad','turno','hora','temperatura','lluvia','evento'])
    return excel_response({'Datos':frame},'plantilla_'+kind+'.xlsx')

@app.get('/api/files/{fid}/download')
def download(fid:str,user=Depends(current_user)):
    df=sales_frame(user); df=df[df['file_id']==fid]
    if df.empty:raise HTTPException(404,'Archivo no disponible para tu sede.')
    return excel_response({'Ventas':df.drop(columns=['id','file_id'])},'ventas_normalizadas.xlsx')

@app.get('/api/forecast')
def predict(horizon:int=Query(7,ge=1,le=30),temperature:Optional[float]=Query(None,ge=-30,le=55),rain:Optional[float]=Query(None,ge=0,le=500),user=Depends(current_user)):
    if horizon not in [1,7,30]:raise HTTPException(422,'El horizonte debe ser 1, 7 o 30 días.')
    return forecast(user,horizon,temperature,rain)

@app.get('/api/plan')
def recommendations(margin:float=Query(.20,ge=0,le=1),horizon:int=Query(7,ge=1,le=30),temperature:Optional[float]=Query(None,ge=-30,le=55),rain:Optional[float]=Query(None,ge=0,le=500),user=Depends(current_user)):
    if horizon not in [1,7,30]:raise HTTPException(422,'El horizonte debe ser 1, 7 o 30 días.')
    return plan(forecast(user,horizon,temperature,rain),user,margin)

@app.get('/api/ml/explain')
def explain(sede:str,producto:str,turno:str,horizon:int=Query(7,ge=1,le=30),temperature:Optional[float]=Query(None,ge=-30,le=55),rain:Optional[float]=Query(None,ge=0,le=500),user=Depends(current_user)):
    check_sede(user,sede)
    if horizon not in [1,7,30]:raise HTTPException(422,'El horizonte debe ser 1, 7 o 30 días.')
    return explain_prediction(user,sede,producto,turno,horizon,temperature,rain)

@app.get('/api/recipes')
def recipes(user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT r.*,i.unit,i.cost,i.action FROM recipes r JOIN ingredients i ON i.name=r.insumo ORDER BY r.producto,r.insumo')]

class RecipeKey(BaseModel):
    producto:str=Field(min_length=1,max_length=100)
    insumo:str=Field(min_length=1,max_length=100)

class Recipe(RecipeKey):
    amount:float=Field(gt=0,le=10000,allow_inf_nan=False)

@app.put('/api/recipes')
def update_recipe(body:Recipe,user=Depends(admin)):
    with connect() as db:
        if not db.execute('SELECT 1 FROM ingredients WHERE name=?',(body.insumo,)).fetchone():raise HTTPException(422,'El insumo no existe.')
        db.execute('INSERT INTO recipes(producto,insumo,amount,approximate) VALUES(?,?,?,0) ON CONFLICT(producto,insumo) DO UPDATE SET amount=excluded.amount,approximate=0',(body.producto.strip(),body.insumo,body.amount))
    return {'ok':True}

@app.delete('/api/recipes')
def remove_recipe(body:RecipeKey,user=Depends(admin)):
    with connect() as db:
        db.execute('DELETE FROM recipes WHERE producto=? AND insumo=?',(body.producto,body.insumo))
    return {'ok':True}

class InventoryItem(BaseModel):
    sede:str
    insumo:str
    amount:float=Field(ge=0,le=10000000,allow_inf_nan=False)

@app.get('/api/inventory')
def inventory(user=Depends(current_user)):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT v.*,i.unit,i.cost FROM inventory v JOIN ingredients i ON i.name=v.insumo ORDER BY v.sede,v.insumo') if user['role']=='admin' or r['sede']==user['sede']]

@app.put('/api/inventory')
def save_inventory(body:InventoryItem,user=Depends(current_user)):
    check_sede(user,body.sede)
    with connect() as db:
        if not db.execute('SELECT 1 FROM ingredients WHERE name=?',(body.insumo,)).fetchone():raise HTTPException(422,'El insumo no existe.')
        if not db.execute('SELECT 1 FROM branches WHERE name=?',(body.sede,)).fetchone():raise HTTPException(422,'La sede no existe.')
        db.execute('INSERT INTO inventory VALUES(?,?,?) ON CONFLICT(sede,insumo) DO UPDATE SET amount=excluded.amount',(body.sede,body.insumo,body.amount))
    return {'ok':True}

@app.post('/api/inventory/upload')
async def upload_inventory(file:UploadFile=File(...),user=Depends(current_user)):
    content=await safe_upload(file)
    frame,_,_=read_file(content,file.filename)
    if not {'sede','insumo','cantidad','unidad'}.issubset(frame.columns):raise HTTPException(422,'El inventario requiere columnas sede, insumo, cantidad y unidad.')
    values=[]
    with connect() as db:
        known={r['name']:r['unit'] for r in db.execute('SELECT * FROM ingredients')}
        branches={r[0] for r in db.execute('SELECT name FROM branches')}
        for i,r in frame.iterrows():
            sede=str(r['sede']).strip(); insumo=str(r['insumo']).strip()
            check_sede(user,sede)
            try:amount=float(str(r['cantidad']).replace(',','.'))
            except (ValueError,TypeError):raise HTTPException(422,f'Fila {i+2}: cantidad inválida.')
            if not math.isfinite(amount) or amount<0:raise HTTPException(422,f'Fila {i+2}: cantidad negativa o inválida.')
            if sede not in branches:raise HTTPException(422,f'Fila {i+2}: sede desconocida.')
            if insumo not in known or str(r['unidad']).strip()!=known[insumo]:raise HTTPException(422,f'Fila {i+2}: insumo o unidad no reconocidos.')
            values.append((sede,insumo,amount))
        if len(set((s,i) for s,i,a in values))!=len(values):raise HTTPException(422,'Hay insumos duplicados por sede en el inventario.')
        db.executemany('INSERT INTO inventory VALUES(?,?,?) ON CONFLICT(sede,insumo) DO UPDATE SET amount=excluded.amount',values)
    return {'updated':len(values)}

class Event(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    fecha:date
    sede:Optional[str]=None
    impact:float=Field(ge=0,le=3,allow_inf_nan=False)

@app.post('/api/events')
def add_event(body:Event,user=Depends(current_user)):
    check_sede(user,body.sede)
    with connect() as db:
        if body.sede and not db.execute('SELECT 1 FROM branches WHERE name=?',(body.sede,)).fetchone():raise HTTPException(422,'La sede no existe.')
        cursor=db.execute('INSERT INTO events(name,fecha,sede,impact) VALUES(?,?,?,?) RETURNING id',(body.name.strip(),body.fecha.isoformat(),body.sede,body.impact))
        eid=cursor.fetchone()[0]
        bump_revision(db,data_changed=False)
        return {'id':eid}

@app.delete('/api/events/{eid}')
def delete_event(eid:int,user=Depends(current_user)):
    with connect() as db:
        event=db.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not event:raise HTTPException(404,'El evento no existe.')
        check_sede(user,event['sede'])
        db.execute('DELETE FROM events WHERE id=?',(eid,)); bump_revision(db,data_changed=False)
    return {'ok':True}

@app.get('/api/export/{kind}')
def export(kind:str,margin:float=Query(.20,ge=0,le=1),horizon:int=Query(7,ge=1,le=30),temperature:Optional[float]=Query(None,ge=-30,le=55),rain:Optional[float]=Query(None,ge=0,le=500),sede:Optional[str]=None,day:Optional[date]=None,user=Depends(current_user)):
    if kind not in ['xlsx','pdf']:raise HTTPException(422,'El formato debe ser xlsx o pdf.')
    if horizon not in [1,7,30]:raise HTTPException(422,'Horizonte inválido.')
    if sede:check_sede(user,sede)
    prediction=forecast(user,horizon,temperature,rain); result=plan(prediction,user,margin)
    choose=lambda rows:[r for r in rows if (not sede or r['sede']==sede) and (not day or r['fecha']==day.isoformat())]
    result['rows']=choose(result['rows']); result['preparation']=choose(result['preparation'])
    from .savings import summarize_savings
    savings=summarize_savings(choose(result['savings']['rows']))
    savings_notes=[result['savings']['baseline'],result['savings']['method'],result['savings']['note'],f'Margen de comparación: {margin*100:.1f} %',f"Resultado: {savings['status']}; cobertura: {savings['coverage_percent']} %"]
    if kind=='xlsx':
        return excel_response({'Ahorro por día':pd.DataFrame(savings['daily']),'Detalle ahorro':pd.DataFrame(choose(result['savings']['rows'])),'Supuestos ahorro':pd.DataFrame({'nota':savings_notes}),'Compras e insumos':pd.DataFrame(result['rows']),'Preparación':pd.DataFrame(result['preparation']),'Predicciones':pd.DataFrame(choose(prediction['rows'])),'Evaluación':pd.DataFrame([r for r in prediction['metrics'] if not sede or r['sede']==sede]),'Notas':pd.DataFrame({'nota':[result['note'],prediction['interval_note'],prediction['weather_note']]})},'recomendaciones_atc.xlsx')
    buffer=io.BytesIO(); styles=getSampleStyleSheet()
    document=SimpleDocTemplate(buffer,pagesize=landscape(A4),rightMargin=28,leftMargin=28)
    content=[Paragraph('American Texas Chicken | Plan de compras y preparación',styles['Title']),Paragraph(escape(f"Horizonte: {horizon} días. Datos hasta {prediction['trained_until']}."),styles['Normal']),Spacer(1,12)]
    savings_amount=f"S/ {savings['amount']:.2f}" if savings['amount'] is not None else 'Pendiente de precios y stock'
    content.extend([Paragraph('Ahorro potencial estimado: '+escape(savings_amount)+' ('+escape(savings['status'])+')',styles['Heading2']),Paragraph(escape(' '.join(savings_notes)),styles['Normal']),Spacer(1,12)])
    savings_data=[['Fecha','Sede','Referencia S/','Recomendación S/','Diferencia S/','Cobertura']]
    for row in savings['daily']:
        amounts=[f"{row[key]:.2f}" if row['valorizados'] else 'Pendiente' for key in ['costo_referencia','costo_recomendado','neto']]
        savings_data.append([row['fecha'],row['sede'],*amounts,f"{row['valorizados']}/{row['total']}"])
    savings_table=Table(savings_data,repeatRows=1)
    savings_table.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    content.extend([savings_table,Spacer(1,12),Paragraph('Compras e insumos',styles['Heading2'])])
    data=[['Fecha','Sede','Insumo','Unidad','Preparar','Stock','Comprar','Costo S/']]
    for r in result['rows']:
        data.append([r['fecha'],r['sede'],r['insumo'],r['unidad'],str(r['requerido']),str(r['inventario']) if r['inventario'] is not None else 'Pendiente',str(r['comprar']) if r['comprar'] is not None else 'Pendiente',f"{r['costo']:.2f}" if r['costo'] is not None else 'Pendiente'])
    table=Table(data,repeatRows=1,colWidths=[64,95,130,40,65,60,65,75]); table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dddddd'))]))
    content.extend([table,Spacer(1,14),Paragraph('Preparación por producto y turno',styles['Heading2'])])
    prep=[['Fecha','Sede','Producto','Turno','Porciones']]+[[r['fecha'],r['sede'],r['producto'],r['turno'],str(r['porciones'])] for r in result['preparation']]
    pt=Table(prep,repeatRows=1);pt.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    content.extend([pt,Spacer(1,12),Paragraph(escape(result['note']),styles['Normal']),Paragraph(escape(prediction['interval_note']),styles['Normal'])])
    document.build(content);buffer.seek(0)
    return StreamingResponse(buffer,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="recomendaciones_atc.pdf"'})

class IngredientCost(BaseModel):
    insumo:str
    cost:Optional[float]=Field(None,ge=0,le=10000000,allow_inf_nan=False)

@app.put('/api/ingredients/cost')
def update_ingredient_cost(body:IngredientCost,user=Depends(admin)):
    with connect() as db:
        if not db.execute('SELECT 1 FROM ingredients WHERE name=?',(body.insumo,)).fetchone():
            raise HTTPException(404,'El insumo no existe.')
        db.execute('UPDATE ingredients SET cost=? WHERE name=?',(body.cost,body.insumo))
    return {'ok':True}

@app.get('/api/database')
def inspect_database(user=Depends(admin)):
    from .database_browser import overview
    return overview()

@app.get('/api/database/tables/{name}')
def inspect_table(name:str,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user=Depends(admin)):
    from .database_browser import TABLES,table_rows
    if name not in TABLES:
        raise HTTPException(404,'Tabla no disponible para consulta.')
    return table_rows(name,limit,offset)

app.mount('/assets',StaticFiles(directory=ROOT/'frontend'),name='assets')

@app.get('/')
def index():return FileResponse(ROOT/'frontend'/'index.html')

@app.get('/health')
def health():return {'status':'ok'}
