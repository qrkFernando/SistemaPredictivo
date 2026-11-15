"""Authenticated, read-only inspection. Table and column names are allowlisted."""
from .db import connect, database_info

TABLES = {
    'sales': ('Ventas históricas', ['id','file_id','fecha','sede','producto','cantidad','turno','hora','temperatura','lluvia','evento'], 'fecha DESC, id DESC'),
    'files': ('Archivos cargados', ['id','name','owner','created','row_count','quality'], 'created DESC'),
    'inventory': ('Inventario', ['sede','insumo','amount'], 'sede, insumo'),
    'recipes': ('Recetas', ['producto','insumo','amount','approximate'], 'producto, insumo'),
    'ingredients': ('Insumos y costos', ['name','unit','cost','action'], 'name'),
    'branches': ('Sedes', ['name'], 'name'),
    'events': ('Eventos', ['id','name','fecha','sede','impact'], 'fecha DESC'),
    'forecasts': ('Predicciones guardadas', ['created'], 'created DESC'),
    'users': ('Usuarios', ['id','name','email','role','sede'], 'id'),
}


def overview():
    with connect() as db:
        db.execute('SELECT 1')
        tables=[{'name':name,'label':label,'count':db.execute('SELECT count(*) FROM '+name).fetchone()[0]} for name,(label,_,_) in TABLES.items()]
    return {**database_info(),'connected':True,'tables':tables}


def table_rows(name,limit=50,offset=0):
    label,columns,order=TABLES[name]
    with connect() as db:
        total=db.execute('SELECT count(*) FROM '+name).fetchone()[0]
        rows=[dict(row) for row in db.execute('SELECT '+','.join(columns)+' FROM '+name+' ORDER BY '+order+' LIMIT ? OFFSET ?',(limit,offset))]
    return {'name':name,'label':label,'columns':columns,'rows':rows,'total':total,'limit':limit,'offset':offset}
