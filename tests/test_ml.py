import math
import unittest
import numpy as np
import pandas as pd
from backend.app.ml_engine import feature_vector,fit_model,recursive_predict,select_model,evaluate_window,score
from backend.app.savings import compare_savings


def history(days=90):
    dates=pd.date_range('2026-01-01',periods=days)
    quantities=np.array([40+(d.dayofweek>=4)*18+i*.03 for i,d in enumerate(dates)])
    return pd.DataFrame({'cantidad':quantities,'temperatura':14.0,'lluvia':0.0,'evento':0.0},index=dates)


class ModelTests(unittest.TestCase):
    def test_mape_is_not_capped_to_target(self):
        measured=score([10,100],[30,100])
        self.assertEqual(measured['mape'],100)
        self.assertGreater(measured['mape'],12)
        self.assertEqual(measured['mae'],10)

    def test_zero_sales_are_still_in_mae_and_rmse(self):
        result=score([0,10],[10,10])
        self.assertEqual(result['mape'],0)
        self.assertEqual(result['mae'],5)
        self.assertEqual(result['zero_days'],1)

    def test_features_cannot_read_future_sales(self):
        frame=history();day=frame.index[45]
        original=feature_vector(day,frame['cantidad'],14,0)
        modified=frame['cantidad'].copy();modified.loc[modified.index>=day]=99999999
        self.assertEqual(original,feature_vector(day,modified,14,0))

    def test_selection_ends_before_outer_evaluation(self):
        frame=history()
        kind,candidates=select_model(frame)
        self.assertEqual(len(candidates),3)
        self.assertIn(kind,['ridge','forest','extra'])
        for candidate in candidates:
            self.assertLess(candidate['train_until'],candidate['validation_start'])
            self.assertLess(candidate['validation_end'],str(frame.index[-21].date()))
        points,result=evaluate_window(frame,kind,len(frame)-21)
        self.assertTrue(all(p['train_until']<p['fecha'] for p in points))
        recalculated=score([p['real'] for p in points],[p['prediccion'] for p in points])
        self.assertEqual(recalculated,result)

    def test_real_ridge_terms_reconstruct_prediction(self):
        model,hist,temp,rain=fit_model(history(),'ridge')
        day=hist.index.max()+pd.Timedelta(days=1)
        vector=feature_vector(day,hist,temp,rain)
        detail=model.explanation(vector)
        total=detail['intercept']+sum(t['contribution'] for t in detail['terms'])
        self.assertAlmostEqual(max(0,math.expm1(total)),model.predict(vector),places=8)
        self.assertEqual(len(detail['features']),20)

    def test_real_tree_results_average_to_forest_prediction(self):
        model,hist,temp,rain=fit_model(history(),'forest')
        vector=feature_vector(hist.index.max()+pd.Timedelta(days=1),hist,temp,rain)
        detail=model.explanation(vector)
        self.assertEqual(len(detail['tree_predictions']),64)
        self.assertAlmostEqual(sum(detail['tree_predictions'])/64,detail['prediction_base'],places=7)
        self.assertGreater(len(detail['first_tree_path']),0)

    def test_explanation_and_prediction_use_same_calculation(self):
        model,hist,temp,rain=fit_model(history(),'ridge')
        dates=pd.date_range(hist.index.max()+pd.Timedelta(days=1),periods=3)
        impacts={str(dates[0].date()):.2}
        results,traces=recursive_predict(model,hist,dates,temp,rain,impacts,True)
        for result,trace in zip(results,traces):
            self.assertEqual(result['esperado'],trace['prediction'])
        self.assertAlmostEqual(traces[0]['prediction_base']*1.2,traces[0]['prediction'])

    def test_reference_plan_is_independent_of_model_predictions(self):
        from types import SimpleNamespace
        hist=history()['cantidad']
        dates=pd.date_range(hist.index.max()+pd.Timedelta(days=1),periods=30)
        low,_=recursive_predict(SimpleNamespace(predict=lambda vector:1),hist,dates,14,0,{})
        high,_=recursive_predict(SimpleNamespace(predict=lambda vector:999),hist,dates,14,0,{})
        self.assertEqual([r['baseline_expected'] for r in low],[r['baseline_expected'] for r in high])


class SavingsTests(unittest.TestCase):
    def prediction(self,expected=10,baseline=10,days=1):
        return {'rows':[{'fecha':f'2026-10-{22+i}','sede':'San Carlos','producto':'Pollo broaster','turno':'Noche','esperado':expected,'baseline_expected':baseline} for i in range(days)]}
    def recipes(self,cost=10):
        return [{'producto':'Pollo broaster','insumo':'Pollo','amount':1,'unit':'kg','cost':cost}]
    def test_visible_comparison_formula(self):
        result=compare_savings(self.prediction(),self.recipes(),{('San Carlos','Pollo'):0},.2)
        self.assertEqual(result['baseline_cost'],120)
        self.assertEqual(result['recommended_cost'],100)
        self.assertEqual(result['amount'],20)
        self.assertFalse(result['measured'])
        self.assertEqual(result['status'],'completo')

    def test_unknown_prices_never_create_savings(self):
        result=compare_savings(self.prediction(),self.recipes(None),{('San Carlos','Pollo'):0})
        self.assertIsNone(result['amount'])
        self.assertEqual(result['status'],'pendiente')
        self.assertEqual(result['missing_prices'],['Pollo'])

    def test_missing_stock_is_not_assumed_zero(self):
        result=compare_savings(self.prediction(),self.recipes(),{})
        self.assertIsNone(result['amount'])
        self.assertIsNone(result['rows'][0]['compra_recomendada'])

    def test_additional_purchases_reduce_net_savings(self):
        result=compare_savings(self.prediction(expected=12,baseline=10),self.recipes(),{('San Carlos','Pollo'):0},0)
        self.assertEqual(result['net_amount'],-20)
        self.assertEqual(result['amount'],0)
        self.assertEqual(result['additional_cost'],20)

    def test_inventory_is_consumed_once_in_both_comparisons(self):
        result=compare_savings(self.prediction(days=2),self.recipes(),{('San Carlos','Pollo'):15},.2)
        self.assertEqual(result['rows'][0]['compra_referencia'],0)
        self.assertEqual(result['rows'][0]['compra_recomendada'],0)
        self.assertEqual(result['rows'][1]['compra_referencia'],9)
        self.assertEqual(result['rows'][1]['compra_recomendada'],5)
        self.assertEqual(result['amount'],40)

    def test_partial_price_coverage_is_explicit(self):
        recipes=self.recipes()+[{'producto':'Pollo broaster','insumo':'Papa','amount':1,'unit':'kg','cost':None}]
        result=compare_savings(self.prediction(),recipes,{('San Carlos','Pollo'):0,('San Carlos','Papa'):0})
        self.assertEqual(result['status'],'parcial')
        self.assertEqual(result['coverage_percent'],50)
        self.assertEqual(result['amount'],20)
