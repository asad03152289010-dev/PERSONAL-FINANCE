import importlib.util
import io
import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults


def boot():
    temporary = tempfile.TemporaryDirectory()
    os.environ['DATABASE_PATH'] = str(Path(temporary.name) / 'finance.db')
    os.environ['OWNER_SETUP_KEY'] = 'TEST_' + secrets.token_urlsafe(32)
    os.environ['APP_ENV'] = 'development'
    spec = importlib.util.spec_from_file_location('finance_app_under_test', Path(__file__).resolve().parents[1] / 'app.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return temporary, mod


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.mod = boot()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def request(self, path, method='GET', data=None, cookies='', csrf='', origin='http://localhost:8000'):
        env = {}
        setup_testing_defaults(env)
        env['PATH_INFO'] = path.split('?')[0]
        env['QUERY_STRING'] = path.partition('?')[2]
        env['REQUEST_METHOD'] = method
        env['HTTP_HOST'] = 'localhost:8000'
        env['REMOTE_ADDR'] = '127.0.0.1'
        env['HTTP_ORIGIN'] = origin
        env['HTTP_COOKIE'] = cookies
        env['HTTP_X_CSRF_TOKEN'] = csrf
        if data is not None:
            raw = json.dumps(data).encode()
            env['CONTENT_TYPE'] = 'application/json'
            env['CONTENT_LENGTH'] = str(len(raw))
            env['wsgi.input'] = io.BytesIO(raw)
        response = {}
        def start_response(status, headers):
            response['status'] = int(status.split()[0])
            response['headers'] = headers
        output = b''.join(self.mod.app(env, start_response))
        response['raw'] = output
        try:
            response['json'] = json.loads(output)
        except ValueError:
            response['json'] = None
        return response

    @staticmethod
    def find_cookie(res, name):
        for key, val in res['headers']:
            if key.lower() == 'set-cookie' and val.startswith(name+'='):
                return val.split(';')[0]
        raise AssertionError(name)

    def test_full_workflow(self):
        r=self.request('/api/summary?month=2026-10')
        self.assertEqual(r['status'], 401)
        r=self.request('/api/bootstrap')
        self.assertTrue(r['json']['setup_required'])
        csrf=r['json']['csrf']
        guest=self.find_cookie(r,'fm_guestcsrf')
        wrong=self.request('/api/setup','POST',{'email':'owner@example.com','password':'correct-horse-123','setup_key':'no'},guest,csrf)
        self.assertEqual(wrong['status'],403)
        registered=self.request('/api/setup','POST',{'email':'owner@example.com','password':'correct-horse-123','setup_key':self.mod.SETUP_KEY},guest,csrf)
        self.assertEqual(registered['status'],200)
        self.assertEqual(self.request('/api/setup','POST',{'email':'second@example.com','password':'long-password-2','setup_key':self.mod.SETUP_KEY},guest,csrf)['status'],403)
        for i in range(6):
            self.assertEqual(self.request('/api/login','POST',{'email':'owner@example.com','password':'wrong'},guest,csrf)['status'],401)
        self.assertEqual(self.request('/api/login','POST',{'email':'owner@example.com','password':'wrong'},guest,csrf)['status'],429)
        # Reset logins to avoid lockout in following workflow.
        with self.mod.connect() as db: db.execute('DELETE FROM login_attempts')
        logged=self.request('/api/login','POST',{'email':'owner@example.com','password':'correct-horse-123'},guest,csrf)
        self.assertEqual(logged['status'],200)
        cookie=self.find_cookie(logged,'fm_session');authcsrf=logged['json']['csrf']
        transaction={'kind':'income','date':'2026-10-12','title':'Editing retainer','category':'Video Editing','client':'Client X',
            'project':'Reels','currency':'USD','amount':'100.00','received':'60.00','rate':'280', 'notes':'October'}
        denied=self.request('/api/transactions','POST',transaction,cookie,'INVALID')
        self.assertEqual(denied['status'],403)
        denied_origin=self.request('/api/transactions','POST',transaction,cookie,authcsrf,'https://evil.example')
        self.assertEqual(denied_origin['status'],403)
        created=self.request('/api/transactions','POST',transaction,cookie,authcsrf)
        self.assertEqual(created['status'],201)
        txid=created['json']['id']
        expense={'kind':'expense','date':'2026-10-12','title':'Groceries','category':'Food & Drinks','currency':'PKR','amount':'1500','rate':'1'}
        self.assertEqual(self.request('/api/transactions','POST',expense,cookie,authcsrf)['status'],201)
        summary=self.request('/api/summary?month=2026-10',cookies=cookie)['json']
        self.assertEqual(summary['income'],16800)
        self.assertEqual(summary['pending'],11200)
        self.assertEqual(summary['expenses'],1500)
        self.assertEqual(summary['savings'],15300)
        self.assertEqual(summary['clients'][0]['outstanding'],11200)
        nextmonth=self.request('/api/summary?month=2026-11',cookies=cookie)['json']
        self.assertEqual(nextmonth['income'],0)
        self.assertEqual(self.request('/api/budgets','PUT',{'month':'2026-10','category':'Food & Drinks','limit':'4500'},cookie,authcsrf)['status'],200)
        self.assertEqual(self.request('/api/budgets','PUT',{'month':'2026-11','category':'Food & Drinks','limit':'6000'},cookie,authcsrf)['status'],200)
        self.assertEqual(self.request('/api/summary?month=2026-10',cookies=cookie)['json']['budget'],4500)
        self.assertEqual(self.request('/api/summary?month=2026-11',cookies=cookie)['json']['budget'],6000)
        self.assertEqual(self.request('/api/goals','POST',{'name':'Laptop','target':'100000','saved':'30000','due_month':'2027-03'},cookie,authcsrf)['status'],201)
        self.assertEqual(self.request('/api/goals',cookies=cookie)['json']['goals'][0]['saved'],'30000.00')
        tx=self.request('/api/transactions?month=2026-10',cookies=cookie)['json']['transactions']
        self.assertEqual(len(tx),2)
        self.assertEqual(next(t for t in tx if t['kind']=='income')['status'],'partial')
        self.assertEqual(self.request('/api/transactions/'+str(txid),'PUT',{**transaction,'received':'100'},cookie,authcsrf)['status'],200)
        self.assertEqual(self.request('/api/summary?month=2026-10',cookies=cookie)['json']['pending'],0)
        # An invoice issued in October but paid in November must count in November cash flow.
        late_invoice={**transaction,'title':'November receipt for October invoice',
            'received':'25', 'received_date':'2026-11-05'}
        self.assertEqual(self.request('/api/transactions','POST',late_invoice,cookie,authcsrf)['status'],201)
        self.assertEqual(self.request('/api/summary?month=2026-10',cookies=cookie)['json']['income'],28000)
        self.assertEqual(self.request('/api/summary?month=2026-11',cookies=cookie)['json']['income'],7000)
        csv_res=self.request('/api/export.csv',cookies=cookie)
        self.assertIn(b'Editing retainer',csv_res['raw'])
        backup=self.request('/api/backup.json',cookies=cookie)
        self.assertEqual(backup['json']['format'],'ksm-finance-backup-v1')
        self.assertEqual(self.request('/api/logout','POST',{},cookie,authcsrf)['status'],200)
        self.assertEqual(self.request('/api/summary?month=2026-10',cookies=cookie)['status'],401)

    def test_amount_and_input_validation(self):
        for val in ['NaN','Infinity','5.333','-2','xyz', '0']:
            with self.assertRaises(self.mod.HttpError): self.mod.cents(val)
        with self.assertRaises(self.mod.HttpError): self.mod.valid_month('2026-13')
        with self.assertRaises(self.mod.HttpError): self.mod.valid_date('2026-02-30')


if __name__ == '__main__': unittest.main()
