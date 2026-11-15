"""Recipe conversions and sequential inventory allocation over the full horizon."""
from collections import defaultdict
import math
from .db import connect


def plan(prediction,user,margin=.20):
    with connect() as db:
        recipes=[dict(r) for r in db.execute('SELECT r.*,i.unit,i.cost,i.action FROM recipes r JOIN ingredients i ON i.name=r.insumo')]
        inventory={(r['sede'],r['insumo']):r['amount'] for r in db.execute('SELECT * FROM inventory')}
    starting_inventory=inventory.copy()
    requirements=defaultdict(lambda:{'esperado':0,'minimo':0,'maximo':0,'unit':'','cost':None,'action':''})
    preparation=[]; missing=set(); approximate=set()
    for row in prediction['rows']:
        matching=[r for r in recipes if r['producto']==row['producto']]
        if not matching: missing.add(row['producto'])
        if any(r['approximate'] for r in matching): approximate.add(row['producto'])
        preparation.append({**row,'porciones':math.ceil(row['esperado'])})
        for recipe in matching:
            rec=requirements[(row['fecha'],row['sede'],recipe['insumo'])]
            for scenario in ['esperado','minimo','maximo']:
                rec[scenario]+=math.ceil(row[scenario])*recipe['amount']
            rec.update(unit=recipe['unit'],cost=recipe['cost'],action=recipe['action'])
    rows=[]
    for (day,sede,ingredient),needed in sorted(requirements.items()):
        key=(sede,ingredient)
        if key not in inventory:
            rows.append({'fecha':day,'sede':sede,'insumo':ingredient,'unidad':needed['unit'],'requerido':round(needed['esperado'],2),'minimo':round(needed['minimo'],2),'maximo':round(needed['maximo'],2),'inventario':None,'comprar':None,'excedente':None,'costo':None,'accion':needed['action'],'estado':'pendiente'})
            continue
        stock=inventory[key]
        raw_deficit=max(0,needed['esperado']-stock)
        deficit=math.ceil(raw_deficit) if needed['unit']=='und' else math.ceil(raw_deficit*100-1e-8)/100
        surplus=max(0,stock-needed['maximo'])
        # Stock is consumed once, day by day; purchases cover the expected deficit.
        inventory[key]=max(0,stock+deficit-needed['esperado'])
        rows.append({'fecha':day,'sede':sede,'insumo':ingredient,'unidad':needed['unit'],'requerido':round(needed['esperado'],2),'minimo':round(needed['minimo'],2),'maximo':round(needed['maximo'],2),'inventario':round(stock,2),'comprar':round(deficit,2),'excedente':round(surplus,2),'costo':round(deficit*needed['cost'],2) if needed['cost'] is not None else (0 if deficit==0 else None),'accion':needed['action'],'estado':'quiebre' if deficit>0 else ('excedente' if surplus>needed['maximo']*.5 else 'optimo')})
    from .savings import compare_savings
    savings=compare_savings(prediction,recipes,starting_inventory,margin)
    approximation_note=(' Recetas con medidas aproximadas: '+', '.join(sorted(approximate))+'. Ajusta las cantidades a tu preparación real.') if approximate else ''
    return {'savings':savings,'rows':rows,'preparation':preparation,'missing_recipes':sorted(missing),'approximate_recipes':sorted(approximate),'kpis':{'compras_estimadas':None if any(r['costo'] is None for r in rows) else round(sum(r['costo'] for r in rows),2),'riesgos_quiebre':sum(r['estado']=='quiebre' for r in rows),'riesgos_excedente':sum(r['estado']=='excedente' for r in rows),'ahorro_potencial':savings['amount'],'desperdicio_estimado':None,'quiebres_evitados':None},'note':'Compras y consumo calculados por día. El stock no registrado y los costos no configurados quedan pendientes; no se asumen como cero. Excedente es stock sobre demanda máxima; no implica caducidad. Ahorro realizado, desperdicio y quiebres evitados requieren registrar costos reales, mermas y resultados; no se inventan estos indicadores.'+approximation_note}
