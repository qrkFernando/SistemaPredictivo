"""Auditable purchasing comparison, with explicit assumptions and cost coverage."""
import math
from collections import defaultdict


def purchase(needed,stock,unit):
    deficit=max(0,needed-stock)
    buying=math.ceil(deficit) if unit=='und' else math.ceil(deficit*100-1e-8)/100
    return buying,max(0,stock+buying-needed)


def compare_savings(prediction,recipes,inventory,margin=.20):
    needs=defaultdict(lambda:{'baseline':0.0,'recommended':0.0,'cost':None,'unit':''})
    for row in prediction['rows']:
        baseline=math.ceil(row.get('baseline_expected',row['esperado'])*(1+margin))
        recommended=math.ceil(row['esperado'])
        for recipe in recipes:
            if recipe['producto']!=row['producto']:continue
            value=needs[(row['fecha'],row['sede'],recipe['insumo'])]
            value['baseline']+=baseline*recipe['amount']
            value['recommended']+=recommended*recipe['amount']
            value['cost']=recipe['cost'];value['unit']=recipe['unit']
    baseline_stock=inventory.copy();recommended_stock=inventory.copy()
    rows=[]
    for (day,sede,ingredient),value in sorted(needs.items()):
        key=(sede,ingredient)
        known_stock=key in inventory
        baseline_buy=recommended_buy=None
        if known_stock:
            baseline_buy,baseline_stock[key]=purchase(value['baseline'],baseline_stock[key],value['unit'])
            recommended_buy,recommended_stock[key]=purchase(value['recommended'],recommended_stock[key],value['unit'])
        baseline_cost=round(baseline_buy*value['cost'],2) if known_stock and value['cost'] is not None else None
        recommended_cost=round(recommended_buy*value['cost'],2) if known_stock and value['cost'] is not None else None
        rows.append({'fecha':day,'sede':sede,'insumo':ingredient,'unidad':value['unit'],'preparacion_referencia':round(value['baseline'],3),'preparacion_recomendada':round(value['recommended'],3),'compra_referencia':baseline_buy,'compra_recomendada':recommended_buy,'costo_unitario':value['cost'],'costo_referencia':baseline_cost,'costo_recomendado':recommended_cost,'diferencia':round(baseline_cost-recommended_cost,2) if baseline_cost is not None else None,'falta_stock':not known_stock,'falta_precio':value['cost'] is None})
    result=summarize_savings(rows)
    result.update(rows=rows,margin=margin,baseline='Promedio de ventas del mismo día de semana (hasta 8 semanas) más el margen de preparación indicado.',method='Diferencia de compras valorizadas: preparación de referencia menos recomendación del modelo, consumiendo el mismo inventario inicial en ambos casos.',note='Ahorro potencial estimado, no realizado. El margen es un supuesto editable, no una medición de tu práctica actual. Solo se valorizan insumos con precio y stock registrados; un resultado parcial no representa el total del negocio. Las compras adicionales se restan del ahorro. El promedio de referencia es sensible a ventas atípicas: revisa las alertas de evaluación antes de usar el importe como presupuesto.',missing_prices=sorted({r['insumo'] for r in rows if r['falta_precio']}),missing_stock=sorted({r['sede']+' · '+r['insumo'] for r in rows if r['falta_stock']}))
    return result


def summarize_savings(rows):
    priced=[r for r in rows if r['diferencia'] is not None]
    daily=defaultdict(lambda:{'costo_referencia':0.0,'costo_recomendado':0.0,'neto':0.0,'valorizados':0,'total':0})
    for row in rows:
        current=daily[(row['fecha'],row['sede'])];current['total']+=1
        if row['diferencia'] is not None:
            current['costo_referencia']+=row['costo_referencia'];current['costo_recomendado']+=row['costo_recomendado'];current['neto']+=row['diferencia'];current['valorizados']+=1
    days=[{'fecha':day,'sede':sede,**{k:round(v,2) if isinstance(v,float) else v for k,v in value.items()},'parcial':value['valorizados']<value['total']} for (day,sede),value in sorted(daily.items())]
    net=round(sum(r['diferencia'] for r in priced),2) if priced else None
    totals_by_date=defaultdict(float)
    for row in days:totals_by_date[row['fecha']]+=row['neto']
    cumulative=0;first=None
    for day,total in sorted(totals_by_date.items()):
        cumulative+=total
        if first is None and cumulative>0:first=day
    return {'status':'completo' if rows and len(priced)==len(rows) else ('parcial' if priced else 'pendiente'),'amount':max(0,net) if net is not None else None,'net_amount':net,'additional_cost':max(0,-net) if net is not None else None,'baseline_cost':round(sum(r['costo_referencia'] for r in priced),2) if priced else None,'recommended_cost':round(sum(r['costo_recomendado'] for r in priced),2) if priced else None,'covered_rows':len(priced),'total_rows':len(rows),'coverage_percent':round(len(priced)/len(rows)*100,1) if rows else 0,'first_positive_date':first,'daily':days,'measured':False}
