"""Author self-tests: reference implementations and plausible wrong solutions.

These solutions never enter materialized participant workspaces. This verifies
the grader, not any harness's performance or industry-level coding ability.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

TASK_FILE = Path(__file__).resolve().parents[1] / 'bench/harness_benchmark_20260929/tasks.py'
spec = importlib.util.spec_from_file_location('benchmark_tasks', TASK_FILE)
tasks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tasks)


REFERENCES = {
    'receipt_package': {
        'receipt_tools/__init__.py': 'from .model import summarize\n',
        'receipt_tools/model.py': r'''
            from decimal import Decimal, ROUND_HALF_UP
            import re
            def summarize(records):
                totals={}
                for row in records:
                    if not isinstance(row,dict):raise ValueError('row')
                    name=row.get('customer');amount=row.get('amount');status=row.get('status')
                    if not isinstance(name,str) or not name.strip():raise ValueError('customer')
                    if not isinstance(amount,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',amount):raise ValueError('amount')
                    if status not in ('paid','void'):raise ValueError('status')
                    if status=='void':continue
                    name=name.strip();count,total=totals.get(name,(0,Decimal(0)))
                    totals[name]=(count+1,total+Decimal(amount))
                return [dict(customer=name,count=count,total=format(total.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'.2f')) for name,(count,total) in sorted(totals.items())]
        ''',
        'receipt_tools/cli.py': r'''
            import csv,json,os,sys,tempfile
            from pathlib import Path
            from .model import summarize
            def main():
                temp=None
                try:
                    source,target=map(Path,sys.argv[1:])
                    with source.open(encoding='utf8') as stream: rows=[json.loads(line) for line in stream if line.strip()]
                    result=summarize(rows)
                    fd,temp=tempfile.mkstemp(dir=target.parent)
                    with os.fdopen(fd,'w',encoding='utf8',newline='') as stream:
                        writer=csv.writer(stream,lineterminator='\n');writer.writerow(['customer','count','total'])
                        for row in result:writer.writerow([row['customer'],row['count'],row['total']])
                    os.replace(temp,target);return 0
                except Exception as error:
                    print(type(error).__name__,file=sys.stderr);return 1
                finally:
                    if temp and os.path.exists(temp):os.unlink(temp)
            if __name__=='__main__':sys.exit(main())
        ''',
    },
    'repair_routes': {'router.py': r'''
        import re
        def normalize(value,pattern=False):
            if not isinstance(value,str) or not value.startswith('/'):raise ValueError('path')
            if pattern and ('?' in value or '#' in value):raise ValueError('pattern')
            if not pattern:value=re.split(r'[?#]',value,1)[0]
            return tuple(segment for segment in value.split('/') if segment)
        class Router:
            def __init__(self):self.routes=[]
            def add(self,pattern,handler):
                parts=normalize(pattern,True);names=[]
                for part in parts:
                    if part.startswith(':'):
                        if not re.fullmatch(r':[A-Za-z_][A-Za-z0-9_]*',part) or part in names:raise ValueError('parameter')
                        names.append(part)
                for index,(existing,_) in enumerate(self.routes):
                    if existing==parts:self.routes[index]=(parts,handler);return
                self.routes.append((parts,handler))
            def resolve(self,path):
                actual=normalize(path);candidates=[]
                for order,(pattern,handler) in enumerate(self.routes):
                    if len(pattern)!=len(actual):continue
                    params={}
                    for want,got in zip(pattern,actual):
                        if want.startswith(':'):params[want[1:]]=got
                        elif want!=got:break
                    else:candidates.append((-sum(not part.startswith(':') for part in pattern),order,handler,params))
                if not candidates:raise KeyError(path)
                _,_,handler,params=min(candidates,key=lambda item:item[:2]);return handler,params
    '''},
    'interval_coverage': {'coverage.py': '''
        def coverage(intervals,start,end):
            if type(start) is not int or type(end) is not int or start>end:raise ValueError('window')
            clipped=[]
            for pair in intervals:
                if not isinstance(pair,(list,tuple)) or len(pair)!=2:raise ValueError('pair')
                lo,hi=pair
                if type(lo) is not int or type(hi) is not int or lo>hi:raise ValueError('bounds')
                lo=max(start,lo);hi=min(end,hi)
                if lo<hi:clipped.append([lo,hi])
            merged=[]
            for lo,hi in sorted(clipped):
                if merged and lo<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],hi)
                else:merged.append([lo,hi])
            cursor=start;gaps=[]
            for lo,hi in merged:
                if cursor<lo:gaps.append([cursor,lo])
                cursor=hi
            if cursor<end:gaps.append([cursor,end])
            return dict(merged=merged,gaps=gaps,covered=sum(hi-lo for lo,hi in merged))
    '''},
    'durable_inbox': {'inbox.py': r'''
        import copy,json,math,os,tempfile
        from pathlib import Path
        def canonical(payload):
            def validate(value):
                if value is None or type(value) in (str,int,bool):return
                if type(value) is float:
                    if not math.isfinite(value):raise ValueError('number')
                    return
                if type(value) is list:
                    for item in value:validate(item)
                    return
                if type(value) is dict:
                    for key,item in value.items():
                        if not isinstance(key,str):raise ValueError('key')
                        validate(item)
                    return
                raise ValueError('payload')
            validate(payload)
            return json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        def valid_id(identifier):
            if not isinstance(identifier,str) or not 1<=len(identifier)<=64:raise ValueError('id')
        class Inbox:
            def __init__(self,path):
                self.path=Path(path);self.records=[]
                if not self.path.exists():return
                try:
                    doc=json.loads(self.path.read_text(encoding='utf8'))
                    if type(doc) is not dict or set(doc)!={'version','records'} or type(doc['version']) is not int or doc['version']!=1 or type(doc['records']) is not list:raise ValueError('document')
                    ids=set()
                    for record in doc['records']:
                        if type(record) is not dict or set(record)!={'id','payload','status'}:raise ValueError('record')
                        valid_id(record['id']);canonical(record['payload'])
                        if record['id'] in ids or record['status'] not in ('pending','running','completed','interrupted'):raise ValueError('record')
                        ids.add(record['id'])
                    self.records=doc['records']
                except (TypeError,KeyError,UnicodeError) as error:raise ValueError('document') from error
                updated=copy.deepcopy(self.records)
                for item in updated:
                    if item['status']=='running':item['status']='interrupted'
                if updated!=self.records:self._commit(updated)
            def _commit(self,updated):
                self.path.parent.mkdir(parents=True,exist_ok=True)
                fd,tmp=tempfile.mkstemp(dir=self.path.parent)
                try:
                    with os.fdopen(fd,'w',encoding='utf8') as stream:
                        json.dump({'version':1,'records':updated},stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
                    os.replace(tmp,self.path)
                    self.records=updated
                finally:
                    if os.path.exists(tmp):os.unlink(tmp)
            def snapshot(self):return copy.deepcopy(self.records)
            def submit(self,identifier,payload):
                valid_id(identifier);key=canonical(payload)
                for record in self.records:
                    if record['id']==identifier:
                        if canonical(record['payload'])!=key:raise ValueError('conflict')
                        return copy.deepcopy(record)
                record={'id':identifier,'payload':copy.deepcopy(payload),'status':'pending'}
                self._commit(self.snapshot()+[record]);return copy.deepcopy(record)
            def start_next(self):
                if any(item['status']=='running' for item in self.records):raise RuntimeError('busy')
                updated=self.snapshot()
                for item in updated:
                    if item['status']=='pending':
                        item['status']='running';self._commit(updated);return copy.deepcopy(item)
                return None
            def finish(self,identifier):
                updated=self.snapshot()
                for item in updated:
                    if item['id']==identifier:
                        if item['status']=='completed':return copy.deepcopy(item)
                        if item['status']!='running':raise ValueError('state')
                        item['status']='completed';self._commit(updated);return copy.deepcopy(item)
                raise KeyError(identifier)
    '''},
    'frame_decoder': {'decoder.py': r'''
        class Decoder:
            def __init__(self,max_size=1024):
                if type(max_size) is not int or max_size<0:raise ValueError('max_size')
                self.max=max_size;self.buffer=bytearray();self.closed=False
            def feed(self,data):
                if self.closed:raise ValueError('closed')
                if not isinstance(data,(bytes,bytearray)):raise TypeError('bytes required')
                result=[]
                try:
                    for byte in data:
                        self.buffer.append(byte)
                        try:colon=self.buffer.index(58)
                        except ValueError:colon=-1
                        header=self.buffer if colon<0 else self.buffer[:colon]
                        if any(not 48<=x<=57 for x in header) or len(header)>1 and header[0]==48:raise ValueError('length')
                        if colon==0:raise ValueError('length')
                        size=int(header) if header else 0
                        if size>self.max:raise ValueError('large')
                        if colon>=0 and len(self.buffer)==colon+size+2:
                            if self.buffer[-1]!=44:raise ValueError('comma')
                            result.append(bytes(self.buffer[colon+1:-1]).decode('utf8'));self.buffer.clear()
                    return result
                except (ValueError,UnicodeError):self.closed=True;raise ValueError('invalid frame')
            def finish(self):
                if self.closed:raise ValueError('closed')
                self.closed=True
                if self.buffer:raise ValueError('incomplete')
    '''},
    'pricing_refactor': {
        'pricing.py': r'''
            from decimal import Decimal,ROUND_HALF_UP
            import re
            def price_line(unit_price,quantity,tier='bronze'):
                if not isinstance(unit_price,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',unit_price):raise ValueError('price')
                if type(quantity) is not int or quantity<0 or tier not in ('bronze','silver','gold'):raise ValueError('arguments')
                amount=Decimal(unit_price)*quantity*{'bronze':Decimal(1),'silver':Decimal('.90'),'gold':Decimal('.80')}[tier]
                if quantity>=10:amount*=Decimal('.95')
                return amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        ''',
        'checkout.py': '''
            import pricing
            from decimal import Decimal
            def quote(items,tier='bronze'):
                lines=[];total=Decimal(0)
                for item in items:
                    amount=pricing.price_line(item['unit_price'],item['quantity'],tier);total+=amount
                    lines.append({'sku':item['sku'],'amount':format(amount,'.2f')})
                return {'lines':lines,'total':format(total,'.2f')}
        ''',
        'reporting.py': '''
            import pricing
            from decimal import Decimal
            REPORT_FORMAT='customer-totals-v1'
            def customer_totals(orders):
                totals={}
                for order in orders:
                    name=order['customer'];totals.setdefault(name,Decimal(0))
                    for item in order['items']:totals[name]+=pricing.price_line(item['unit_price'],item['quantity'],order['tier'])
                return [{'customer':name,'total':format(total,'.2f')} for name,total in sorted(totals.items())]
        ''',
    },
}


class BenchmarkTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='benchmark-author-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fixture(self, identifier, replacements=None):
        directory = self.root / identifier
        tasks.materialize(identifier, directory)
        for name, source in {**REFERENCES[identifier], **(replacements or {})}.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(textwrap.dedent(source).lstrip(), encoding='utf8')
        return directory

    def test_contracts_are_complete_public_and_grading_code_compiles(self):
        self.assertEqual(len(tasks.TASKS), 6)
        for identifier, metadata in tasks.TASKS.items():
            with self.subTest(task=identifier):
                folder = self.root / identifier
                prompt = tasks.materialize(identifier, folder)
                self.assertIn('README.md', prompt)
                self.assertEqual(metadata['checks_total'], 8)
                self.assertEqual(sorted(str(p.relative_to(folder)).replace('\\','/') for p in folder.rglob('*') if p.is_file()), metadata['public_files'])
                for name, source in tasks._CHECKS[identifier]:compile(textwrap.dedent(source).strip(), name, 'exec')
                with self.assertRaises(FileExistsError):tasks.materialize(identifier, folder)

    def test_correct_solutions_pass_all_checks(self):
        for identifier in tasks.TASKS:
            with self.subTest(task=identifier):
                result = tasks.grade(identifier, self.fixture(identifier))
                self.assertTrue(result['passed'], result)
                self.assertEqual(result['checks_passed'], 8)

    def test_plausible_wrong_solutions_fail(self):
        mutations = {
            'receipt_package': ('receipt_tools/model.py', 'total+Decimal(amount)', "total+Decimal(amount).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)"),
            'repair_routes': ('router.py', "-sum(not part.startswith(':') for part in pattern)", '0'),
            'interval_coverage': ('coverage.py', 'lo<=merged[-1][1]', 'lo<merged[-1][1]'),
            'durable_inbox': ('inbox.py', 'os.replace(tmp,self.path)\n                    self.records=updated', 'self.records=updated\n                    os.replace(tmp,self.path)'),
            'frame_decoder': ('decoder.py', "bytes(self.buffer[colon+1:-1]).decode('utf8')", "bytes(self.buffer[colon+1:-1]).decode('latin1')"),
            'pricing_refactor': ('pricing.py', 'rounding=ROUND_HALF_UP', "rounding='ROUND_DOWN'"),
        }
        for identifier, (name, old, new) in mutations.items():
            with self.subTest(task=identifier):
                source = REFERENCES[identifier][name]
                self.assertIn(old, source)
                result = tasks.grade(identifier, self.fixture(identifier, {name: source.replace(old,new)}))
                self.assertFalse(result['passed'], result)
                self.assertLess(result['checks_passed'], result['checks_total'])

    def test_timeout_and_exit_do_not_escape_grader_process(self):
        folder = self.root / 'hostile'
        folder.mkdir()
        (folder / 'coverage.py').write_text('while True: pass\n',encoding='utf8')
        with patch.object(tasks,'CHECK_TIMEOUT_SECONDS',.35), patch.object(tasks,'GRADE_TIMEOUT_SECONDS',.7):
            result = tasks.grade('interval_coverage',folder)
        self.assertFalse(result['passed'])
        self.assertEqual(result['checks_total'],8)
        self.assertTrue(any('timed out' in item.get('error','') for item in result['details']))
        (folder / 'coverage.py').write_text('import os\nos._exit(17)\n',encoding='utf8')
        result = tasks.grade('interval_coverage',folder)
        self.assertEqual(result['checks_passed'],0)

    def test_environment_does_not_forward_credentials(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'AUTHOR_TEST_ONLY','DEEPSEEK_API_KEY':'AUTHOR_TEST_ONLY'}):
            env=tasks._environment(self.root)
        self.assertNotIn('OPENAI_API_KEY',env)
        self.assertNotIn('DEEPSEEK_API_KEY',env)

    def test_cli_ignores_workspace_sitecustomize(self):
        folder=self.fixture('receipt_package')
        (folder/'sitecustomize.py').write_text("raise SystemExit('must not load workspace sitecustomize')\n",encoding='utf8')
        result=tasks.grade('receipt_package',folder)
        self.assertTrue(result['passed'],result)

    def test_durable_imported_os_aliases_and_missing_fsync(self):
        source=REFERENCES['durable_inbox']['inbox.py']
        good=source.replace('import copy,json,math,os,tempfile','import copy,json,math,os,tempfile\n        from os import replace, fsync')
        good=good.replace('os.replace(', 'replace(').replace('os.fsync(', 'fsync(')
        folder=self.fixture('durable_inbox',{'inbox.py':good})
        result=tasks.grade('durable_inbox',folder)
        self.assertTrue(result['passed'],result)
        (folder/'inbox.py').write_text(textwrap.dedent(source).lstrip().replace('os.fsync(stream.fileno())','None'),encoding='utf8')
        result=tasks.grade('durable_inbox',folder)
        self.assertFalse(result['passed'],result)
        failed=[item['check'] for item in result['details'] if not item['passed']]
        self.assertIn('save_failure_preserves_memory_then_retries',failed)

    def test_public_smoke_rejects_seeds_and_accepts_references(self):
        bootstrap="import runpy,sys;from pathlib import Path;root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.argv=[str(root/'public_tests.py')];runpy.run_path(sys.argv[0],run_name='__main__')"
        for identifier in tasks.TASKS:
            with self.subTest(task=identifier):
                folder=self.root/identifier;tasks.materialize(identifier,folder)
                command=[sys.executable,'-I','-S','-B','-c',bootstrap,str(folder)]
                seed=subprocess.run(command,cwd=folder,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
                self.assertNotEqual(seed.returncode,0)
                for name,source in REFERENCES[identifier].items():
                    target=folder/name;target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text(textwrap.dedent(source).lstrip(),encoding='utf8')
                correct=subprocess.run(command,cwd=folder,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
                self.assertEqual(correct.returncode,0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
