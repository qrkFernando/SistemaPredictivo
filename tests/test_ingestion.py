import io
import unittest
import pandas as pd
from fastapi import HTTPException
from backend.app.ingestion import read_file,validate,clean,parse_date

MAP={k:k for k in ['fecha','sede','producto','cantidad']}

class IngestionTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({'fecha':['2026-10-01','02/10/2026','inválida','2026-10-03','2026-10-01'],'sede':['San Carlos']*5,'producto':['Pollo broaster']*5,'cantidad':['10','-2','20','','10']})

    def test_csv_encoding_separator_and_preview(self):
        f,meta,mapping=read_file('fecha;sede;producto;cantidad\n2026-10-01;Giráldez;Pollo;10'.encode('cp1252'),'ventas.csv')
        self.assertEqual(meta['separator'],';')
        self.assertEqual(f.iloc[0]['sede'],'Giráldez')
        self.assertEqual(mapping['fecha'],'fecha')

    def test_quality_and_safe_cleaning(self):
        _,report,_=validate(self.frame(),MAP)
        self.assertEqual(report['issues']['negativos'],1)
        self.assertEqual(report['issues']['fechas_invalidas'],1)
        self.assertEqual(report['issues']['duplicados'],1)
        result,_=clean(self.frame(),MAP,{'negativos':'rellenar','vacios':'rellenar','cantidades_invalidas':'rellenar'})
        self.assertEqual(len(result),3)
        self.assertTrue(result['cantidad'].ge(0).all())
        self.assertFalse(result['fecha'].isna().any())

    def test_missing_mapping_message(self):
        with self.assertRaises(HTTPException) as ctx:validate(self.frame(),{'sede':'sede','producto':'producto','cantidad':'cantidad'})
        self.assertIn('columna fecha',ctx.exception.detail)

    def test_duplicate_aggregation_is_explicit(self):
        result,_=clean(self.frame().iloc[[0,4]].copy().reset_index(drop=True),MAP,{'duplicados':'ignorar'})
        self.assertEqual(result.iloc[0]['cantidad'],20)

    def test_excel_sheets_and_serial_date(self):
        buffer=io.BytesIO()
        with pd.ExcelWriter(buffer,engine='openpyxl') as writer:
            pd.DataFrame({'x':[1]}).to_excel(writer,sheet_name='Notas',index=False)
            self.frame().to_excel(writer,sheet_name='Ventas',index=False)
        frame,meta,_=read_file(buffer.getvalue(),'test.xlsx','Ventas')
        self.assertEqual(meta['sheets'],['Notas','Ventas'])
        self.assertEqual(len(frame),5)
        self.assertEqual(str(parse_date(45000).date()),'2023-03-15')

    def test_nonfinite_and_optional_ranges(self):
        f=self.frame().iloc[:1].copy();f['cantidad']='inf';f['hora']=99
        _,r,_=validate(f,{**MAP,'hora':'hora'})
        self.assertEqual(r['issues']['cantidades_invalidas'],1)
        self.assertEqual(r['issues']['variables_invalidas'],1)

    def test_empty_or_wrong_file(self):
        with self.assertRaises(HTTPException):read_file(b'hello','foo.pdf')
        with self.assertRaises(HTTPException):read_file(b'fecha,sede\n','empty.csv')

    def test_utf16_csv(self):
        data='fecha\tsede\tproducto\tcantidad\n2026-10-01\tGiráldez\tPollo\t10'
        frame,meta,_=read_file(data.encode('utf-16'),'utf16.csv')
        self.assertEqual(meta['encoding'],'UTF-16')
        self.assertEqual(meta['separator'],'\t')
        self.assertEqual(frame.iloc[0]['sede'],'Giráldez')

    def test_filling_blank_quantity_does_not_require_two_policies(self):
        result,report=clean(self.frame(),MAP,{'vacios':'rellenar'})
        self.assertEqual(report['issues']['cantidades_invalidas'],0)
        self.assertEqual(len(result),2)
        self.assertTrue(result['cantidad'].notna().all())

    def test_timezone_dates_use_lima_business_date(self):
        self.assertEqual(str(parse_date('2026-10-01T02:00:00Z').date()),'2026-09-30')

if __name__=='__main__':unittest.main()
