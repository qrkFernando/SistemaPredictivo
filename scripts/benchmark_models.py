"""Read-only model evaluation against the connected history; never alters sales."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.db import sales_frame
from backend.app.ml_engine import select_model,evaluate_window,score
import pandas as pd

frame=sales_frame({'role':'admin','sede':None})
frame['fecha']=pd.to_datetime(frame['fecha'])
results=[]
for key,group in frame.groupby(['sede','producto','turno']):
    series=group.groupby('fecha').agg({'cantidad':'sum','temperatura':'mean','lluvia':'mean','evento':'max'})
    series=series.reindex(pd.date_range(series.index.min(),series.index.max()))
    kind,candidates=select_model(series)
    points=[]
    for offset in [21,14,7]:
        if len(series)-offset>=28:
            p,_=evaluate_window(series,kind,len(series)-offset);points.extend(p)
    metrics=score([p['real'] for p in points],[p['prediccion'] for p in points])
    print(' | '.join(key),kind,round(metrics['mape'],3) if metrics['mape'] is not None else None,flush=True)
    results.append(metrics['mape'])
print('Cumplen <=12:',sum(x is not None and x<=12 for x in results),'/',len(results),flush=True)
