"""File inspection and auditable cleaning before a transactional import."""
import csv
import io
import re
import unicodedata
from pathlib import Path
import numpy as np
import pandas as pd
from charset_normalizer import from_bytes
from fastapi import HTTPException

REQUIRED = ['fecha','sede','producto','cantidad']
OPTIONAL = ['turno','hora','temperatura','lluvia','evento']
ALIASES = {'fecha':['fecha','date','dia','fecha venta'], 'sede':['sede','local','tienda','sucursal'], 'producto':['producto','product','articulo'], 'cantidad':['cantidad','cantidad vendida','unidades','ventas','quantity'], 'turno':['turno','shift'],'hora':['hora','hour'],'temperatura':['temperatura','temperature'],'lluvia':['lluvia','rain'],'evento':['evento','event']}


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFD',str(value).lower().strip()) if unicodedata.category(c)!='Mn').replace('_',' ')


def read_file(content, filename, sheet=None):
    ext=Path(filename).suffix.lower()
    meta={'encoding':None,'separator':None,'sheets':[]}
    try:
        if ext=='.csv':
            try:
                if content.startswith((b'\xff\xfe', b'\xfe\xff')):
                    text=content.decode('utf-16'); encoding='UTF-16'
                else:
                    text=content.decode('utf-8-sig'); encoding='UTF-8'
            except UnicodeDecodeError:
                best=from_bytes(content, cp_isolation=['cp1252','latin_1']).best()
                if best is None:
                    raise ValueError('No se pudo detectar la codificación.')
                text=str(best); encoding=best.encoding
            try:
                sep=csv.Sniffer().sniff(text[:64000],delimiters=',;\t|').delimiter
            except csv.Error:
                sep=','
            frame=pd.read_csv(io.StringIO(text),sep=sep,dtype=object)
            meta.update(encoding=encoding,separator=sep)
        elif ext in ['.xlsx','.xls']:
            book=pd.ExcelFile(io.BytesIO(content),engine='xlrd' if ext=='.xls' else 'openpyxl')
            meta['sheets']=book.sheet_names
            selected=sheet or book.sheet_names[0]
            if selected not in book.sheet_names:
                raise ValueError('La hoja seleccionada no existe.')
            frame=book.parse(selected,dtype=object)
            meta['sheet']=selected
        else:
            raise ValueError('Formato no compatible. Usa .xlsx, .xls o .csv.')
    except Exception as exc:
        raise HTTPException(422, 'No se pudo leer el archivo. ' + str(exc)[:200])
    if frame.empty:
        raise HTTPException(422,'El archivo no contiene filas de datos.')
    if len(frame)>100000:
        raise HTTPException(422,'El límite es 100 000 filas por archivo.')
    frame.columns=[str(x).strip() for x in frame.columns]
    if len(set(frame.columns))!=len(frame.columns):
        raise HTTPException(422,'Hay nombres de columnas repetidos.')
    mapping={k:next((c for c in frame if normalized(c) in aliases),'') for k,aliases in ALIASES.items()}
    return frame,meta,mapping


def records(frame):
    return frame.replace([np.inf,-np.inf],np.nan).astype(object).where(pd.notna(frame),None).to_dict('records')


def validate(frame,mapping):
    for key in REQUIRED:
        if not mapping.get(key) or mapping[key] not in frame:
            raise HTTPException(422, 'El archivo no tiene la columna ' + key + '. Selecciónala en el mapeo.')
    selected=[v for v in mapping.values() if v]
    if len(set(selected))!=len(selected):
        raise HTTPException(422,'Cada columna debe mapearse a un solo campo.')
    df=pd.DataFrame({k:frame[v] for k,v in mapping.items() if v and v in frame}).copy()
    df['fecha']=df['fecha'].map(parse_date)
    df['cantidad']=pd.to_numeric(df['cantidad'].astype(str).str.replace(',','.',regex=False),errors='coerce')
    for c in ['sede','producto']:
        df[c]=df[c].astype('string').str.strip().replace('',pd.NA)
    df['turno']=df.get('turno',pd.Series('Sin turno',index=df.index)).fillna('Sin turno').astype(str).str.strip()
    allowed_shifts={'almuerzo':'Almuerzo','noche':'Noche','sin turno':'Sin turno'}
    df['turno']=df['turno'].map(lambda x:allowed_shifts.get(normalized(x),x))
    for key,default in [('hora',None),('temperatura',None),('lluvia',None),('evento',0)]:
        df[key]=pd.to_numeric(df.get(key,pd.Series(default,index=df.index,dtype=object)),errors='coerce')
    missing=frame[[mapping[k] for k in REQUIRED]].apply(lambda x:x.isna() | x.astype(str).str.strip().eq('')).any(axis=1)
    invalid_date=df['fecha'].isna()
    raw_quantity=frame[mapping['cantidad']]
    empty_quantity=raw_quantity.isna() | raw_quantity.astype(str).str.strip().eq('')
    invalid_quantity=(df['cantidad'].isna() | ~np.isfinite(df['cantidad'])) & ~empty_quantity
    negative=df['cantidad'].lt(0)
    duplicate=df.duplicated(['fecha','sede','producto','turno'],keep='first')
    outlier=pd.Series(False,index=df.index)
    for _,group in df.groupby(['sede','producto','turno'],dropna=False):
        values=group.loc[np.isfinite(group['cantidad']) & group['cantidad'].ge(0),'cantidad']
        if len(values)<8: continue
        q1,q3=values.quantile([.25,.75]); spread=max(q3-q1,1)
        outlier.loc[group.index]=group['cantidad'].gt(q3+3*spread)
    invalid_optional=(df['hora'].notna() & (~df['hora'].between(0,23) | df['hora'].mod(1).ne(0))) | (df['temperatura'].notna() & ~df['temperatura'].between(-30,55)) | (df['lluvia'].notna() & ~df['lluvia'].between(0,500)) | (df['evento'].notna() & ~df['evento'].between(0,3)) | ~df['turno'].isin(['Almuerzo','Noche','Sin turno'])
    masks={'vacios':missing,'fechas_invalidas':invalid_date,'cantidades_invalidas':invalid_quantity,'negativos':negative,'duplicados':duplicate,'outliers':outlier,'variables_invalidas':invalid_optional}
    any_issue=pd.concat(masks.values(),axis=1).any(axis=1)
    valid=df.loc[~invalid_date,'fecha']
    report={'rows':len(df),'valid_rows':int((~any_issue).sum()),'quality':round(100*(~any_issue).mean(),1),'issues':{k:int(v.sum()) for k,v in masks.items()},'samples':{k:[int(i)+2 for i in v[v].index[:12]] for k,v in masks.items()},'date_start':str(valid.min().date()) if len(valid) else None,'date_end':str(valid.max().date()) if len(valid) else None,'sedes':int(df['sede'].nunique()),'products':int(df['producto'].nunique())}
    return df,report,masks


def parse_date(value):
    if pd.isna(value): return pd.NaT
    if isinstance(value,(int,float,np.number)):
        # Excel serial dates; not Unix nanoseconds.
        if 20000<float(value)<80000:
            return pd.Timestamp('1899-12-30')+pd.Timedelta(days=float(value))
        return pd.NaT
    try:
        s=str(value).strip()
        iso=bool(re.match(r'^\d{4}-\d{2}-\d{2}',s))
        stamp=pd.to_datetime(s,dayfirst=not iso,errors='raise')
        if stamp.tzinfo is not None:
            stamp=stamp.tz_convert('America/Lima').tz_localize(None)
        return stamp.normalize()
    except Exception:
        return pd.NaT


def clean(frame,mapping,policies):
    df,report,masks=validate(frame,mapping)
    allowed={'vacios':['eliminar','rellenar'],'fechas_invalidas':['eliminar'],'cantidades_invalidas':['eliminar','rellenar'],'negativos':['eliminar','rellenar'],'duplicados':['eliminar','ignorar'],'outliers':['eliminar','rellenar','ignorar'],'variables_invalidas':['eliminar','rellenar']}
    defaults={k:'eliminar' for k in masks}; defaults['outliers']='ignorar'
    # Apply masks from original data; a union prevents one correction hiding another issue.
    drop=pd.Series(False,index=df.index)
    actions={**defaults,**policies}
    for issue,mask in masks.items():
        action=actions[issue]
        if action not in allowed[issue]:
            raise HTTPException(422,f'La acción {action} no es válida para {issue}.')
        if action=='eliminar': drop |= mask
        elif action=='rellenar':
            if issue in ['cantidades_invalidas','negativos','vacios']:
                good=df['cantidad'].where(df['cantidad'].ge(0) & np.isfinite(df['cantidad']))
                median=good.groupby([df['sede'],df['producto'],df['turno']]).transform('median').fillna(good.median()).fillna(0)
                bad=df['cantidad'].isna() | df['cantidad'].lt(0) | ~np.isfinite(df['cantidad'])
                df.loc[bad,'cantidad']=median[bad]
                # Identities/dates are never fabricated.
                drop |= df['fecha'].isna() | df['sede'].isna() | df['producto'].isna()
            elif issue=='outliers':
                med=df.groupby(['sede','producto','turno'])['cantidad'].transform('median')
                df.loc[mask,'cantidad']=med[mask]
            elif issue=='variables_invalidas':
                df.loc[~df['hora'].between(0,23) | df['hora'].mod(1).ne(0),'hora']=np.nan
                df.loc[~df['temperatura'].between(-30,55),'temperatura']=np.nan
                df.loc[~df['lluvia'].between(0,500),'lluvia']=np.nan
                df.loc[~df['evento'].between(0,3),'evento']=0
                df.loc[~df['turno'].isin(['Almuerzo','Noche','Sin turno']),'turno']='Sin turno'
    df=df.loc[~drop].copy()
    # Ignoring duplicate warnings means aggregate quantities, not count silently twice.
    if actions['duplicados']=='ignorar':
        df=df.groupby(['fecha','sede','producto','turno'],as_index=False).agg({'cantidad':'sum','hora':'first','temperatura':'mean','lluvia':'mean','evento':'max'})
    else:
        df=df.drop_duplicates(['fecha','sede','producto','turno'],keep='first')
    df['fecha']=df['fecha'].dt.strftime('%Y-%m-%d')
    if df.empty: raise HTTPException(422,'No quedaron filas válidas. Revisa las correcciones.')
    return df,report
