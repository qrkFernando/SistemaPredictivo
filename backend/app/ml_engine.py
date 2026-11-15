"""Real supervised estimators and causal features for demand forecasting."""
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import holidays

CALENDAR=holidays.country_holidays('PE',language='es')
MODEL_VERSION='temporal-selection-v4'
CANDIDATES={
    'ridge':'Ridge estacional',
    'forest':'Random Forest robusto',
    'extra':'Extra Trees robusto',
}
FEATURE_NAMES=['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo','Tendencia temporal','Feriado nacional','Temperatura °C','Lluvia mm','Ventas hace 1 día','Ventas hace 7 días','Ventas hace 14 días','Ventas hace 21 días','Ventas hace 28 días','Media últimos 7 días','Mediana últimos 28 días','Promedio mismo día (8 semanas)','Mediana mismo día (8 semanas)']
FEATURE_GROUPS=['Día de semana']*7+['Tendencia','Calendario','Clima','Clima']+['Ventas recientes']*7+['Patrón semanal']*2


def feature_vector(day,history,temperature,rain):
    # Filter before every statistic: even a caller passing future data cannot leak it.
    past=history.loc[history.index<day]
    observed=past.dropna()
    recent=observed.tail(28)
    fallback=float(recent.median()) if len(recent) else 0.0
    peers=observed[observed.index.dayofweek==day.dayofweek].tail(8)
    def lag(n):
        value=past.get(day-pd.Timedelta(days=n),fallback)
        return float(value) if pd.notna(value) else fallback
    return [float(day.dayofweek==d) for d in range(7)]+[(day-pd.Timestamp('2020-01-01')).days/365.25,float(day.date() in CALENDAR),float(temperature),float(rain)]+[lag(n) for n in [1,7,14,21,28]]+[float(observed.tail(7).mean()) if len(observed) else fallback,fallback,float(peers.mean()) if len(peers) else fallback,float(peers.median()) if len(peers) else fallback]


def seasonal_value(day,history):
    observed=history.loc[history.index<day].dropna()
    peers=observed[observed.index.dayofweek==day.dayofweek].tail(8)
    value=peers.mean() if len(peers) else observed.tail(28).mean()
    return max(0,float(value)) if pd.notna(value) else 0.0


@dataclass
class FittedModel:
    kind:str
    estimator:object
    samples:int
    feature_count:int=len(FEATURE_NAMES)

    @property
    def label(self):return CANDIDATES[self.kind]

    def predict(self,vector):
        if self.kind=='ridge':
            scaler,regressor=self.estimator.steps[0][1],self.estimator.steps[1][1]
            scaled=scaler.transform([vector])[0]
            value=float(regressor.intercept_)+sum(float(a)*float(b) for a,b in zip(scaled,regressor.coef_))
            return max(0,math.expm1(value))
        return max(0,float(self.estimator.predict(np.asarray([vector],dtype=float))[0]))

    def explanation(self,vector):
        result={'kind':self.kind,'model':self.label,'samples':self.samples,'features':[{'name':name,'value':round(float(value),5),'group':group} for name,value,group in zip(FEATURE_NAMES,vector,FEATURE_GROUPS)],'prediction_base':self.predict(vector)}
        if self.kind=='ridge':
            scaler,regressor=self.estimator.steps[0][1],self.estimator.steps[1][1]
            transformed=scaler.transform([vector])[0]
            contributions=transformed*regressor.coef_
            result.update(intercept=float(regressor.intercept_),linear_sum=float(regressor.intercept_+contributions.sum()),terms=[{'feature':name,'contribution':float(value)} for name,value in zip(FEATURE_NAMES,contributions)],contribution_unit='log(1 + porciones)',equation='Porciones = exp(intercepto + suma de aportes) − 1')
            weights=np.abs(regressor.coef_)
        else:
            trees=self.estimator.estimators_
            result['tree_predictions']=[max(0,float(tree.predict([vector])[0])) for tree in trees]
            tree=trees[0].tree_;node=0;path=[]
            while tree.children_left[node]!=tree.children_right[node]:
                idx=int(tree.feature[node]);threshold=float(tree.threshold[node]);value=float(vector[idx]);left=value<=threshold
                path.append({'feature':FEATURE_NAMES[idx],'value':value,'threshold':threshold,'comparison':'≤' if left else '>','samples':int(tree.n_node_samples[node])})
                node=int(tree.children_left[node] if left else tree.children_right[node])
            result.update(first_tree_path=path,first_tree_leaf=float(tree.value[node].ravel()[0]),equation='Porciones = promedio de los resultados de todos los árboles',contribution_unit='Importancia interna del modelo; no es un efecto causal')
            weights=self.estimator.feature_importances_
        groups={}
        for group,weight in zip(FEATURE_GROUPS,weights):groups[group]=groups.get(group,0)+float(weight)
        total=sum(groups.values()) or 1
        result['feature_groups']=[{'name':name,'weight':round(weight/total*100,2)} for name,weight in sorted(groups.items(),key=lambda item:-item[1])]
        result['importance_note']='Distribución de pesos internos normalizados. No representa causalidad ni cambios independientes de demanda.'
        return result


def fit_model(frame,kind='ridge'):
    frame=frame.sort_index()
    history=frame['cantidad']/(1+frame['evento'].fillna(0))
    temp=float(frame['temperatura'].median()) if frame['temperatura'].notna().any() else 14.0
    rain=float(frame['lluvia'].median()) if frame['lluvia'].notna().any() else 0.0
    X=[];y=[]
    for day,row in frame.iterrows():
        if pd.isna(row['cantidad']):continue
        past=history.loc[history.index<day]
        if past.notna().sum()<7:continue
        X.append(feature_vector(day,past,float(row['temperatura']) if pd.notna(row['temperatura']) else temp,float(row['lluvia']) if pd.notna(row['lluvia']) else rain))
        y.append(float(history.loc[day]))
    if len(y)<14:return None,history,temp,rain
    if kind=='ridge':
        # Log targets limit the influence of isolated extreme sales without changing observations.
        estimator=make_pipeline(StandardScaler(),Ridge(alpha=30.0))
        with np.errstate(divide='ignore',over='ignore',invalid='ignore'):
            estimator.fit(X,np.log1p(y))
        if not np.isfinite(estimator.steps[-1][1].coef_).all():
            raise ValueError('No se pudo ajustar el modelo. Revisa valores extremos en las ventas.')
    else:
        constructor=RandomForestRegressor if kind=='forest' else ExtraTreesRegressor
        estimator=constructor(n_estimators=64,max_depth=8,min_samples_leaf=6,criterion='absolute_error',random_state=42,n_jobs=1)
        estimator.fit(X,y)
    return FittedModel(kind,estimator,len(y)),history,temp,rain


def recursive_predict(model,history,dates,temp,rain,impacts,explain=False):
    history=history.copy();reference_history=history.copy();result=[];traces=[]
    for day in dates:
        vector=feature_vector(day,history,temp,rain)
        base=model.predict(vector) if model else seasonal_value(day,history)
        seasonal=seasonal_value(day,reference_history)
        impact=impacts.get(day.strftime('%Y-%m-%d'),0)
        if explain:
            detail=model.explanation(vector) if model else {'kind':'seasonal','model':'Promedio estacional (evidencia insuficiente)','prediction_base':base,'features':[{'name':n,'value':float(v),'group':g} for n,v,g in zip(FEATURE_NAMES,vector,FEATURE_GROUPS)],'feature_groups':[],'samples':len(history.dropna())}
            traces.append({**detail,'fecha':day.strftime('%Y-%m-%d'),'event_impact':impact,'temperature':temp,'rain':rain,'prediction':base*(1+impact),'baseline_expected':seasonal*(1+impact)})
        history.loc[day]=base
        reference_history.loc[day]=seasonal
        result.append({'esperado':base*(1+impact),'baseline_expected':seasonal*(1+impact)})
    return result,traces


def score(actual,predicted):
    a=np.asarray(actual,dtype=float);p=np.asarray(predicted,dtype=float)
    valid=np.isfinite(a)&np.isfinite(p);a=a[valid];p=p[valid]
    if not len(a):return {'mae':None,'rmse':None,'mape':None,'test_days':0,'zero_days':0}
    nonzero=a>0
    return {'mae':float(np.mean(np.abs(a-p))),'rmse':float(np.sqrt(np.mean((a-p)**2))),'mape':float(np.mean(np.abs(a[nonzero]-p[nonzero])/a[nonzero])*100) if nonzero.any() else None,'test_days':len(a),'zero_days':int((a==0).sum())}


def evaluate_window(frame,kind,split,days=7):
    train=frame.iloc[:split];test=frame.iloc[split:split+days]
    model,history,temp,rain=fit_model(train,kind)
    impacts={day.strftime('%Y-%m-%d'):float(row['evento']) if pd.notna(row['evento']) else 0 for day,row in test.iterrows()}
    predictions,_=recursive_predict(model,history,test.index,temp,rain,impacts)
    points=[{'fecha':day.strftime('%Y-%m-%d'),'real':float(row['cantidad']),'prediccion':float(pred['esperado']),'train_until':train.index.max().strftime('%Y-%m-%d')} for (day,row),pred in zip(test.iterrows(),predictions) if pd.notna(row['cantidad'])]
    return points,score([p['real'] for p in points],[p['prediccion'] for p in points])


def select_model(frame):
    # Selection period ends before all reported outer backtesting periods.
    selection_end=len(frame)-21
    split=selection_end-21
    candidates=[]
    if split>=35:
        for kind,label in CANDIDATES.items():
            _,metrics=evaluate_window(frame.iloc[:selection_end],kind,split,21)
            candidates.append({'kind':kind,'model':label,**metrics,'train_until':frame.index[split-1].strftime('%Y-%m-%d'),'validation_start':frame.index[split].strftime('%Y-%m-%d'),'validation_end':frame.index[selection_end-1].strftime('%Y-%m-%d')})
        eligible=[c for c in candidates if c['mape'] is not None]
        selected=min(eligible,key=lambda c:(c['mape'],c['mae']))['kind'] if eligible else 'ridge'
    else:
        selected='ridge'
    return selected,candidates
