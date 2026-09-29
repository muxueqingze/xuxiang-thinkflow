"""Frozen local software-engineering tasks, not an industry benchmark.

Only README/seeds/public smoke tests enter the participant workspace. Hidden
checks run in fresh stdlib-only Python children, with a deadline and no inherited
API credentials. This is process isolation, not an OS security sandbox.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time


VERSION = "2026-09-29.1"
CHECK_TIMEOUT_SECONDS = 4.0
GRADE_TIMEOUT_SECONDS = 24.0
TASKS: dict[str, dict] = {}
_SEEDS: dict[str, dict[str, str]] = {}
_CHECKS: dict[str, list[tuple[str, str]]] = {}


def _register(identifier, title, category, contract, seeds, smoke, checks):
    public = {**seeds, "public_tests.py": textwrap.dedent(smoke).lstrip()}
    public["README.md"] = (
        f"# {title}\n\n" + textwrap.dedent(contract).strip() +
        "\n\nUse only Python 3.10+ standard library. Preserve the documented public API. "
        "You may add implementation files and separate tests of your own. Do not change public_tests.py. "
        "Run `python public_tests.py` from this directory. Hidden checks exercise only the contract below/above; "
        "passing the smoke test alone is not the whole task.\n"
    )
    _SEEDS[identifier] = {name: textwrap.dedent(content).lstrip() for name, content in public.items()}
    _CHECKS[identifier] = checks
    TASKS[identifier] = {"id": identifier, "title": title, "category": category,
        "version": VERSION, "checks_total": len(checks), "public_files": sorted(public)}


_register("receipt_package", "Build a receipt summary package", "multi-file creation", """
Create receipt_tools/__init__.py, receipt_tools/model.py and receipt_tools/cli.py.
Export summarize from both receipt_tools and receipt_tools.model.

summarize(records) consumes an iterable of receipt dictionaries and returns a
list of dictionaries {customer: str, count: int, total: str}, sorted by customer
(Python string order). Strip surrounding customer whitespace and combine equal
names. A customer must be a string with a nonempty stripped value. amount must
be a string matching [0-9]+(?:\\.[0-9]+)? (ASCII digits, no sign, exponent or
whitespace). status is exactly "paid" or "void". All rows, including void rows,
must be validated; missing/invalid fields raise ValueError. Extra fields are
ignored. Only paid rows contribute; void-only customers do not appear.
Sum monetary amounts exactly with Decimal, then round EACH CUSTOMER TOTAL once
to two places using ROUND_HALF_UP. Do not round individual input receipts.
Do not mutate inputs. Empty input produces [].
Benchmark valid inputs contain at most 10000 records per call, and amounts have
at most 12 integer digits and 6 fractional digits. Behavior above these size
limits is not graded; all format/validation rules below those limits still apply.

CLI: python -m receipt_tools.cli INPUT.jsonl OUTPUT.csv
Read UTF-8 JSON Lines; ignore blank lines. Write UTF-8 CSV with header
customer,count,total, LF record endings, correct CSV quoting, and the same sorted
summary. Exit 0 on success. A missing input, malformed JSON, or invalid receipt
must exit nonzero with a nonempty stderr and leave a preexisting OUTPUT unchanged
(or leave no OUTPUT if it did not exist). Fully validate before replacing output.
Both input/output arguments are filesystem paths, including paths with spaces.
Example: paid amounts 1.005 and 2.005 for Ada produce total "3.01", count 2.
""", {}, '''
import unittest
from receipt_tools import summarize
class Smoke(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(summarize([{"customer":" Ada ","amount":"1.005","status":"paid"},
            {"customer":"Ada","amount":"2.005","status":"paid"}]),
            [{"customer":"Ada","count":2,"total":"3.01"}])
if __name__ == "__main__": unittest.main()
''', [
    ("package_and_empty", '''
        from receipt_tools import summarize
        from receipt_tools.model import summarize as model
        assert summarize([]) == [] and model([]) == []
        for name in ['__init__.py','model.py','cli.py']: assert (ROOT/'receipt_tools'/name).is_file()
    '''),
    ("aggregate_then_round", '''
        from receipt_tools import summarize
        rows=[dict(customer='A',amount='0.005',status='paid') for _ in range(3)]
        assert summarize(rows)==[dict(customer='A',count=3,total='0.02')]
        assert summarize([dict(customer='B',amount='999999999999.995',status='paid')])[0]['total']=='1000000000000.00'
    '''),
    ("void_sort_and_whitespace", '''
        from receipt_tools import summarize
        rows=[dict(customer=' β ',amount='2',status='paid'),dict(customer='A',amount='4',status='void'),
              dict(customer='β',amount='3.125',status='paid'),dict(customer='Z',amount='0',status='paid')]
        assert summarize(rows)==[dict(customer='Z',count=1,total='0.00'),dict(customer='β',count=2,total='5.13')]
    '''),
    ("validation_including_void", '''
        from receipt_tools import summarize
        for amount in ['NaN','Infinity','1e2','-1','+1',' 2','２','1.','']:
            raises(ValueError,lambda amount=amount:summarize([dict(customer='A',amount=amount,status='void')]))
        for row in [{},dict(customer=' ',amount='1',status='paid'),dict(customer='A',amount=1,status='paid'),dict(customer='A',amount='1',status='other')]:
            raises(ValueError,lambda row=row:summarize([row]))
    '''),
    ("generator_and_no_mutation", '''
        from receipt_tools import summarize
        rows=[dict(customer=' A ',amount='1.00',status='paid',note=['keep'])]; old=copy.deepcopy(rows)
        assert summarize(iter(rows))==[dict(customer='A',count=1,total='1.00')]
        assert rows==old
    '''),
    ("cli_csv_utf8_and_quoting", r'''
        rows=[dict(customer='李,明',amount='1.005',status='paid'),dict(customer='Ada',amount='2',status='paid')]
        source=TMP/'input file.jsonl'; target=TMP/'output file.csv'
        source.write_text('\n'+''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows)+'\n',encoding='utf8')
        code=run_cli(['-m','receipt_tools.cli',str(source),str(target)])
        assert code==0
        with target.open(encoding='utf8',newline='') as stream: data=list(csv.reader(stream))
        assert data==[['customer','count','total'],['Ada','1','2.00'],['李,明','1','1.01']]
        assert b'\r\n' not in target.read_bytes()
    '''),
    ("cli_invalid_keeps_output", r'''
        source=TMP/'bad.jsonl'; target=TMP/'out.csv'; target.write_bytes(b'KEEP')
        source.write_text('not-json\n',encoding='utf8')
        assert run_cli(['-m','receipt_tools.cli',str(source),str(target)],need_error=True)!=0
        assert target.read_bytes()==b'KEEP'
        source.write_text(json.dumps(dict(customer='A',amount='bad',status='paid')),encoding='utf8')
        assert run_cli(['-m','receipt_tools.cli',str(source),str(target)],need_error=True)!=0
        assert target.read_bytes()==b'KEEP'
    '''),
    ("cli_missing_input_no_output", '''
        target=TMP/'never.csv'
        assert run_cli(['-m','receipt_tools.cli',str(TMP/'missing'),str(target)],need_error=True)!=0
        assert not target.exists()
    '''),
])


_register("durable_inbox", "Implement a recoverable idempotent inbox", "state recovery and idempotency", """
Implement Inbox(path) in inbox.py, a single-process, single-writer JSON-file
inbox. No background jobs or threads are needed. A missing file means an empty
inbox; create parent directories when saving. The persistent document is
{'version':1,'records':[...]} with records in original submission order.
Every record has exactly id, payload, status. States: pending, running,
completed, interrupted. snapshot() returns a detached deep copy of the records.

submit(id,payload) validates a nonempty string id <=64 characters and a JSON
value payload (string-key objects, lists, strings, finite numbers, bool, null).
Reject other types/nonfinite numbers with ValueError. Return a detached record.
Duplicate id + identical original payload returns its current record without
re-executing or resetting state. Compare payloads by json.dumps with sort_keys=True,
ensure_ascii=False, separators=(',',':'), allow_nan=False; object key order is
irrelevant, array order matters, and 1 differs from 1.0. A conflicting duplicate
raises ValueError and changes nothing. New records start pending.

start_next() raises RuntimeError if any record is already running. Otherwise
persist the first pending record as running and return a detached copy; return
None if no pending record exists. finish(id) changes a running record to completed
and returns its detached copy. Finishing an already completed record is an
idempotent no-op; a missing id raises KeyError; other states raise ValueError.

On loading a file, convert all running records to interrupted and persist this
recovery before returning. Pending records remain pending and NEVER auto-run.
All mutating methods must durably save before acknowledging the new state. Use
a same-directory temporary JSON file, flush+os.fsync its data, then atomically
replace the destination with os.replace (imported aliases are equally valid).
Any saving OSError must propagate; the instance's snapshot must remain exactly
as it was before that operation. Successful data must survive a new Inbox
instance. Malformed JSON or invalid document/record schema raises ValueError
and must not overwrite the original file. Validate duplicate stored IDs,
record fields/statuses and payloads too. Returned records and caller-owned
payload objects must not mutate stored state through aliasing.
""", {"inbox.py": '''
class Inbox:
    def __init__(self, path):
        self.path = path
        raise NotImplementedError("Implement durable inbox")

    def submit(self, identifier, payload):
        raise NotImplementedError

    def start_next(self):
        raise NotImplementedError

    def finish(self, identifier):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError
'''}, '''
import tempfile, unittest
from pathlib import Path
from inbox import Inbox
class Smoke(unittest.TestCase):
    def test_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            box=Inbox(Path(tmp)/'inbox.json')
            first=box.submit('a',{'text':'hello'})
            self.assertEqual(box.submit('a',{'text':'hello'}),first)
            self.assertEqual(len(box.snapshot()),1)
if __name__ == '__main__': unittest.main()
''', [
    ("duplicate_identity_and_conflict", '''
        from inbox import Inbox
        b=Inbox(TMP/'box.json'); first=b.submit('a',{'x':1,'y':[2]})
        assert b.submit('a',{'y':[2],'x':1})==first
        raises(ValueError,lambda:b.submit('a',{'x':1.0,'y':[2]}))
        raises(ValueError,lambda:b.submit('a',{'x':2}))
        assert b.snapshot()==[first]
    '''),
    ("fifo_and_running_reservation", '''
        from inbox import Inbox
        b=Inbox(TMP/'box.json'); b.submit('b',2);b.submit('a',1)
        assert b.start_next()=={'id':'b','payload':2,'status':'running'}
        raises(RuntimeError,b.start_next); b.finish('b')
        assert b.start_next()['id']=='a';b.finish('a');assert b.start_next() is None
    '''),
    ("finish_rules_and_idempotency", '''
        from inbox import Inbox
        b=Inbox(TMP/'box.json');b.submit('a',None)
        raises(ValueError,lambda:b.finish('a'));raises(KeyError,lambda:b.finish('missing'))
        b.start_next();done=b.finish('a');assert b.finish('a')==done
        assert b.submit('a',None)['status']=='completed'
    '''),
    ("restart_recovers_without_replay", '''
        from inbox import Inbox
        p=TMP/'box.json';b=Inbox(p);b.submit('a',{'v':'李'});b.submit('b',[]);b.start_next()
        restored=Inbox(p);assert [r['status'] for r in restored.snapshot()]==['interrupted','pending']
        assert json.loads(p.read_text(encoding='utf8'))['records'][0]['status']=='interrupted'
        assert restored.submit('a',{'v':'李'})['status']=='interrupted'
        assert restored.start_next()['id']=='b'
    '''),
    ("save_failure_preserves_memory_then_retries", '''
        p=TMP/'box.json';real_replace=os.replace;real_fsync=os.fsync;synced=set();commits=[]
        def observe_sync(fd):
            info=os.fstat(fd);synced.add((info.st_dev,info.st_ino,info.st_size));return real_fsync(fd)
        def observe_replace(source,destination,*args,**kwargs):
            if Path(destination).resolve()==p.resolve():
                source=Path(source).resolve();assert source.parent==p.parent and source!=p
                info=source.stat();assert (info.st_dev,info.st_ino,info.st_size) in synced,'temporary data must be flushed and fsynced before replacement'
                synced.clear();commits.append(True)
            return real_replace(source,destination,*args,**kwargs)
        with patch('os.replace',side_effect=observe_replace),patch('os.fsync',side_effect=observe_sync):
            from inbox import Inbox
            b=Inbox(p);b.submit('a',1);before=b.snapshot();assert commits
            original=p.read_bytes();p.unlink();p.mkdir();(p/'obstacle').write_text('do not replace')
            raises(OSError,lambda:b.submit('b',2));assert b.snapshot()==before
            (p/'obstacle').unlink();p.rmdir();p.write_bytes(original)
            b.submit('b',2);assert [r['id'] for r in Inbox(p).snapshot()]==['a','b']
    '''),
    ("corrupt_document_preserved", '''
        from inbox import Inbox
        p=TMP/'box.json'
        invalid=['not json',json.dumps({'version':2,'records':[]}),json.dumps({'version':1,'records':[{'id':'a','payload':0,'status':'invented'}]}),
          json.dumps({'version':1,'records':[{'id':'a','payload':0,'status':'pending'},{'id':'a','payload':0,'status':'pending'}]})]
        for raw in invalid:
            p.write_text(raw,encoding='utf8');raises(ValueError,lambda:Inbox(p));assert p.read_text(encoding='utf8')==raw
    '''),
    ("payload_validation", '''
        from inbox import Inbox
        b=Inbox(TMP/'box.json')
        for identifier,payload in [('',1),('x'*65,1),(3,1),('a',float('nan')),('a',float('inf')),('a',{1:'bad'}),('a',{'x':set()})]:
            raises(ValueError,lambda identifier=identifier,payload=payload:b.submit(identifier,payload))
        assert b.snapshot()==[]
    '''),
    ("detached_payloads_and_parent_creation", '''
        from inbox import Inbox
        p=TMP/'nested space'/'状态.json';b=Inbox(p);payload={'items':[1]};record=b.submit('a',payload)
        payload['items'].append(2);record['payload']['items'].append(3)
        snap=b.snapshot();snap[0]['payload']['items'].append(4)
        expected=[{'id':'a','payload':{'items':[1]},'status':'pending'}]
        assert b.snapshot()==expected and Inbox(p).snapshot()==expected
    '''),
])


_register("frame_decoder", "Decode a bounded incremental byte protocol", "protocol parsing and boundaries", """
Implement Decoder(max_size=1024) in decoder.py. A frame is ASCII decimal BYTE
length, ':', exactly that many UTF-8 payload bytes, then ','. Example b'2:hi,'.
Length 0 is legal; leading zeros are illegal except the single digit 0. No signs,
whitespace, non-ASCII digits or empty length. The payload may itself contain
commas, colons, newlines or NUL. Length is bytes, not Unicode characters.

feed(data) accepts bytes or bytearray and returns a list of decoded strings for
all complete frames in that chunk; retain incomplete data for future feed calls.
Support arbitrary splitting, multiple frames per call, and empty chunks. Reject
bad input types with TypeError WITHOUT changing decoder state. max_size is a
nonnegative int (bool invalid); invalid constructor values raise ValueError.
Reject a length as soon as its decimal value exceeds max_size, even before ':'.
Malformed framing or invalid UTF-8 raises ValueError and permanently fails the
decoder. After such failure every feed/finish raises ValueError. Do not silently
resynchronize. A call that raises does not return any partial result list.

finish() signals EOF: return None and close the decoder if no partial frame is
left; otherwise raise ValueError and fail it. After a successful finish, later
feed or finish also raises ValueError. Be bounded by max_size plus a small
header; never allocate a buffer of a length merely claimed by the input.
""", {"decoder.py": '''
class Decoder:
    def __init__(self, max_size=1024):
        raise NotImplementedError("Implement incremental decoder")
    def feed(self, data):
        raise NotImplementedError
    def finish(self):
        raise NotImplementedError
'''}, '''
import unittest
from decoder import Decoder
class Smoke(unittest.TestCase):
    def test_chunks(self):
        d=Decoder();self.assertEqual(d.feed(b'2:h'),[])
        self.assertEqual(d.feed(b'i,0:,'),['hi','']);self.assertIsNone(d.finish())
if __name__ == '__main__': unittest.main()
''', [
    ("utf8_every_byte", '''
        from decoder import Decoder
        d=Decoder();raw='李🙂'.encode('utf8');wire=str(len(raw)).encode()+b':'+raw+b',';out=[]
        for byte in wire:out.extend(d.feed(bytes([byte])))
        assert out==['李🙂'];assert d.finish() is None
    '''),
    ("multiple_frames_embedded_delimiters", r'''
        from decoder import Decoder
        d=Decoder();assert d.feed(b'0:,4:a,:\n,1:\x00,')==['','a,:\n','\x00'];assert d.finish() is None
    '''),
    ("maximum_zero_and_invalid_constructor", '''
        from decoder import Decoder
        assert Decoder(0).feed(b'0:,')==['']
        assert Decoder(3).feed(b'3:abc,')==['abc']
        for value in [-1,True,1.0,'1']:raises(ValueError,lambda value=value:Decoder(value))
    '''),
    ("illegal_headers_poison", '''
        from decoder import Decoder
        for wire in [b'01:a,',b':,',b'+1:a,',b' 1:a,',b'x:abc,']:
            d=Decoder();raises(ValueError,lambda:d.feed(wire));raises(ValueError,lambda:d.feed(b'0:,'));raises(ValueError,d.finish)
    '''),
    ("comma_and_utf8_errors", r'''
        from decoder import Decoder
        for wire in [b'1:a;',b'1:\xff,',b'2:\xe2\x82,']:
            d=Decoder();raises(ValueError,lambda:d.feed(wire));raises(ValueError,lambda:d.feed(b''))
    '''),
    ("oversize_rejected_before_payload", '''
        from decoder import Decoder
        d=Decoder(12);assert d.feed(b'1')==[];raises(ValueError,lambda:d.feed(b'3'))
        raises(ValueError,d.finish)
    '''),
    ("eof_closed_and_type_errors", '''
        from decoder import Decoder
        for wire in [b'2',b'2:',b'2:a',b'2:ab']:
            d=Decoder();d.feed(wire);raises(ValueError,d.finish);raises(ValueError,lambda:d.feed(b','))
        d=Decoder();d.feed(b'2:a');raises(TypeError,lambda:d.feed('b,'));assert d.feed(bytearray(b'b,'))==['ab']
        assert d.finish() is None;raises(ValueError,d.finish);raises(ValueError,lambda:d.feed(b''))
    '''),
    ("deterministic_arbitrary_chunks", '''
        from decoder import Decoder
        values=['alpha','','β,colon:','🙂','long'*10];wire=b''.join(str(len(x.encode())).encode()+b':'+x.encode()+b',' for x in values)
        for seed in range(15):
            rng=random.Random(seed);d=Decoder();out=[];offset=0
            while offset<len(wire):
                size=rng.randrange(1,12);out+=d.feed(wire[offset:offset+size]);offset+=size
                assert d.feed(b'')==[]
            assert out==values;assert d.finish() is None
    '''),
])


_register("pricing_refactor", "Unify pricing across checkout and reports", "cross-file refactoring", """
Refactor the existing checkout.py and reporting.py to share a NEW pricing.py.
Implement pricing.price_line(unit_price, quantity, tier='bronze') -> Decimal,
quantized to 0.01 using ROUND_HALF_UP. unit_price is an ASCII nonnegative decimal
string matching [0-9]+(?:\\.[0-9]+)?; quantity is a nonnegative int, not bool.
tier is exactly bronze/silver/gold. Invalid money, quantity or tier raises
ValueError. Compute exactly: unit_price * quantity, multiply by 1/0.90/0.80 for
tier, then multiply by 0.95 when quantity >= 10. Round ONCE per final line.
Benchmark valid inputs contain at most 10000 line items per call, quantity is
at most 1000, and unit prices have at most 12 integer and 6 fractional digits.
Behavior above these size limits is not graded; validation rules within them apply.

Preserve checkout.quote(items,tier='bronze') -> {'lines':[{'sku':..., 'amount':
'0.00'}, ...], 'total':'0.00'}. Preserve input order; sum already rounded line
amounts for total. Each item has sku, unit_price, quantity. Preserve
reporting.customer_totals(orders) -> [{'customer':..., 'total':'0.00'}, ...],
sorted by customer, aggregating the same rounded lines across orders. An order
has customer, tier, items. Empty orders still contribute a 0.00 customer row.
Both APIs return []/0.00 appropriately for empty inputs and must not mutate
caller dictionaries/lists. Extra item fields are ignored. Inputs have valid
nonempty sku/customer strings; validate the monetary fields described above.

The pricing formula must have one owner: both public modules must call
pricing.price_line dynamically at runtime. A caller replacing that function
must affect both checkout and reports without re-importing them. Keep the
existing REPORT_FORMAT='customer-totals-v1' constant and public signatures.
Do not solve this by copying the formula into each module or doing float math.
""", {
"checkout.py": '''
def quote(items, tier='bronze'):
    discounts={'bronze':1,'silver':.9,'gold':.8}
    lines=[]
    for item in items:
        amount=round(float(item['unit_price'])*item['quantity']*discounts[tier],2)
        lines.append({'sku':item['sku'],'amount':f'{amount:.2f}'})
    return {'lines':lines,'total':f"{sum(float(x['amount']) for x in lines):.2f}"}
''',
"reporting.py": '''
REPORT_FORMAT='customer-totals-v1'

def customer_totals(orders):
    totals={}
    for order in orders:
        amount=sum(float(item['unit_price'])*item['quantity'] for item in order['items'])
        totals[order['customer']]=totals.get(order['customer'],0)+amount
    return [{'customer':customer,'total':f'{amount:.2f}'} for customer,amount in totals.items()]
''',
}, '''
import unittest
from checkout import quote
from reporting import customer_totals, REPORT_FORMAT
class Smoke(unittest.TestCase):
    def test_shared_behavior(self):
        items=[{'sku':'x','unit_price':'2.345','quantity':10}]
        self.assertEqual(quote(items,'silver')['total'],'20.05')
        self.assertEqual(customer_totals([{'customer':'Ada','tier':'silver','items':items}]),[{'customer':'Ada','total':'20.05'}])
        self.assertEqual(REPORT_FORMAT,'customer-totals-v1')
if __name__ == '__main__': unittest.main()
''', [
    ("decimal_discount_and_bulk", "from pricing import price_line\nassert price_line('2.345',10,'silver')==Decimal('20.05');assert isinstance(price_line('1',1),Decimal);assert price_line('10',10,'gold')==Decimal('76.00')"),
    ("per_line_half_up_not_total_rounding", '''
        from checkout import quote
        items=[dict(sku='a',unit_price='0.005',quantity=1),dict(sku='b',unit_price='0.005',quantity=1)]
        assert quote(items)=={'lines':[{'sku':'a','amount':'0.01'},{'sku':'b','amount':'0.01'}],'total':'0.02'}
    '''),
    ("report_aggregation_order_and_zero", '''
        from reporting import customer_totals
        orders=[dict(customer='Z',tier='gold',items=[]),dict(customer='A',tier='silver',items=[dict(sku='x',unit_price='10',quantity=1)]),
          dict(customer='A',tier='gold',items=[dict(sku='y',unit_price='10',quantity=1)])]
        assert customer_totals(orders)==[dict(customer='A',total='17.00'),dict(customer='Z',total='0.00')]
    '''),
    ("monetary_validation", '''
        from pricing import price_line
        from checkout import quote
        for price,qty,tier in [('NaN',1,'gold'),('1e2',1,'gold'),('1',True,'bronze'),('1',-1,'bronze'),('1',1,'unknown'),('-1',1,'gold')]:
            raises(ValueError,lambda price=price,qty=qty,tier=tier:price_line(price,qty,tier))
            raises(ValueError,lambda price=price,qty=qty,tier=tier:quote([dict(sku='x',unit_price=price,quantity=qty)],tier))
    '''),
    ("no_input_mutation", '''
        from checkout import quote
        from reporting import customer_totals
        items=[dict(sku='x',unit_price='3.005',quantity=11,notes=['keep'])];orders=[dict(customer='B',tier='gold',items=items)]
        old=copy.deepcopy(orders);quote(items,'gold');customer_totals(orders);assert orders==old
    '''),
    ("shared_runtime_dependency", '''
        import checkout,reporting,pricing
        calls=[]
        def fake(unit_price,quantity,tier='bronze'):
            calls.append((unit_price,quantity,tier));return Decimal('7.00')
        pricing.price_line=fake
        item=dict(sku='x',unit_price='5',quantity=2)
        assert checkout.quote([item],'gold')['total']=='7.00'
        assert reporting.customer_totals([dict(customer='A',tier='silver',items=[item])])==[dict(customer='A',total='7.00')]
        assert len(calls)==2
    '''),
    ("legacy_api_and_empty", "from checkout import quote\nfrom reporting import customer_totals,REPORT_FORMAT\nassert REPORT_FORMAT=='customer-totals-v1';assert quote([])=={'lines':[],'total':'0.00'};assert customer_totals([])==[]"),
    ("deterministic_money_cases", '''
        from pricing import price_line
        rng=random.Random(51)
        for _ in range(40):
            price=str(Decimal(rng.randrange(1,10000))/Decimal(1000));qty=rng.randrange(0,21);tier=rng.choice(['bronze','silver','gold'])
            expected=Decimal(price)*qty*{'bronze':Decimal(1),'silver':Decimal('.90'),'gold':Decimal('.80')}[tier]
            if qty>=10:expected*=Decimal('.95')
            assert price_line(price,qty,tier)==expected.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    '''),
])


def materialize(task_id: str, dest: Path) -> str:
    """Create public fixtures only. Refuse overwriting an existing task file."""
    files = _SEEDS[task_id]
    dest = Path(dest).resolve()
    collisions = [name for name in files if (dest / name).exists()]
    if collisions:
        raise FileExistsError("Task files already exist: " + ", ".join(collisions))
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf8", newline="\n")
    return (f"Complete the software-engineering task: {TASKS[task_id]['title']}. "
        "Read README.md for the full public contract and inspect the existing files. "
        "Implement the solution in this workspace using only Python standard library. "
        "Run python public_tests.py and any additional tests you need. "
        "Do not merely describe a solution; leave the working implementation on disk.")


_PRELUDE = r'''
import copy, csv, json, os, random, subprocess, sys, tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch
ROOT=Path(sys.argv[1]); RESULT=Path(sys.argv[2]); TMP=RESULT.parent/'scratch';TMP.mkdir()
sys.path.insert(0,str(ROOT))
def raises(kind, action):
    try: action()
    except kind: return
    raise AssertionError('Expected '+kind.__name__)
def runs(values):
    result=[]
    for value in values:
        if result and result[-1][1]==value:result[-1][1]=value+1
        else:result.append([value,value+1])
    return result
def run_cli(args, need_error=False):
    assert args[0]=='-m'
    bootstrap="import runpy,sys; root=sys.argv.pop(1); module=sys.argv.pop(1); sys.path.insert(0,root); sys.argv[0]=module; runpy.run_module(module,run_name='__main__')"
    with tempfile.TemporaryFile() as errors:
        proc=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),*args[1:]],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=errors,timeout=2)
        if need_error:
            errors.seek(0);assert errors.read(1), 'Expected a nonempty stderr'
        return proc.returncode
'''


def _environment(scratch: Path) -> dict[str, str]:
    names = ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
    return {**{name: os.environ[name] for name in names if name in os.environ},
        "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def _stop_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


def grade(task_id: str, dest: Path) -> dict:
    """Run each hidden check in a fresh process, outside participant ownership."""
    checks = _CHECKS[task_id]
    dest = Path(dest).resolve()
    details = []
    deadline = time.monotonic() + GRADE_TIMEOUT_SECONDS
    for name, source in checks:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            details.append({"check": name, "passed": False, "error": "grading deadline exceeded"})
            continue
        with tempfile.TemporaryDirectory(prefix="harness-hidden-grade-") as directory:
            temp = Path(directory).resolve()
            output = temp / "result.json"
            script = _PRELUDE + "\nCHECK = " + repr(textwrap.dedent(source).strip()) + r'''
try:
    exec(compile(CHECK,'<hidden-check>','exec'),globals())
    outcome={'passed':True}
except BaseException as error:
    outcome={'passed':False,'error':type(error).__name__+': '+str(error)[:800]}
RESULT.write_text(json.dumps(outcome,ensure_ascii=True),encoding='utf8')
'''
            try:
                process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-", str(dest), str(output)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=temp, env=_environment(temp), text=True, encoding="utf8",
                    **({"start_new_session": True} if os.name != "nt" else {}))
                try:
                    process.communicate(script, timeout=min(CHECK_TIMEOUT_SECONDS, remaining))
                except subprocess.TimeoutExpired:
                    _stop_child(process)
                    details.append({"check": name, "passed": False, "error": "validator timed out"})
                    continue
                if process.returncode != 0 or not output.is_file() or output.stat().st_size > 4096:
                    outcome = {"passed": False, "error": "validator exited without a valid result"}
                else:
                    outcome = json.loads(output.read_text(encoding="utf8"))
                    if type(outcome.get("passed")) is not bool:
                        raise ValueError("invalid result schema")
                details.append({"check": name, "passed": outcome["passed"],
                    **({"error": str(outcome.get("error", ""))[:1000]} if not outcome["passed"] else {})})
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                details.append({"check": name, "passed": False, "error": f"validator infrastructure: {type(error).__name__}"})
    passed = sum(item["passed"] for item in details)
    return {"passed": passed == len(checks), "checks_passed": passed,
            "checks_total": len(checks), "details": details}


_register("repair_routes", "Repair route matching without changing its API", "existing-code diagnosis", """
Fix router.py. Router.add(pattern, handler) registers an opaque handler value;
Router.resolve(path) returns (handler, params_dict), never invokes the handler.
Paths/patterns must be strings starting with '/'; otherwise raise ValueError.
Collapse repeated '/' and ignore trailing '/' (root stays '/'). On resolve only,
discard the first '?' or '#' and everything after it. There is no URL decoding.
Patterns may not contain '?' or '#'. A parameter occupies a whole segment,
e.g. ':user_id', with ASCII identifier [A-Za-z_][A-Za-z0-9_]*. Parameter names in
one pattern must be unique. Any segment starting ':' that is not a valid
parameter is invalid. Other static segment characters are literal, not regex.
Invalid add raises ValueError without changing any registered route.

Matching uses the same number of segments. A parameter captures one nonempty
segment. Among all matches choose the route with the most static segments;
ties keep registration order. Adding the same NORMALIZED pattern again replaces
its handler while retaining its original order. No match raises KeyError.
Preserve the Router class and add/resolve signatures. Do not mutate params from
previous resolve calls. A root route is valid.
""", {"router.py": '''
class Router:
    def __init__(self):
        self.routes = []

    def add(self, pattern, handler):
        self.routes.append((pattern.strip('/').split('/'), handler))

    def resolve(self, path):
        parts = path.strip('/').split('/')
        for pattern, handler in self.routes:
            if len(parts) != len(pattern):
                continue
            params = {}
            for wanted, actual in zip(pattern, parts):
                if wanted.startswith(':'):
                    params[wanted[1:]] = actual
                elif wanted != actual:
                    break
            else:
                return handler, params
        raise KeyError(path)
'''}, '''
import unittest
from router import Router
class Smoke(unittest.TestCase):
    def test_static_precedes_parameter(self):
        r=Router(); r.add('/users/:id','user'); r.add('/users/new','create')
        self.assertEqual(r.resolve('/users/new'),('create',{}))
if __name__ == '__main__': unittest.main()
''', [
    ("static_specificity", "from router import Router\nr=Router();r.add('/:a/:b','wild');r.add('/x/:b','specific');assert r.resolve('/x/y')==('specific',{'b':'y'})"),
    ("ties_keep_registration", "from router import Router\nr=Router();r.add('/x/:b','first');r.add('/:a/y','second');assert r.resolve('/x/y')==('first',{'b':'y'})"),
    ("normalized_replacement", "from router import Router\nr=Router();r.add('/x/:b','old');r.add('/:a/y','second');r.add('//x//:b/','new');assert r.resolve('/x/y')==('new',{'b':'y'})"),
    ("root_query_fragment_slashes", "from router import Router\nr=Router();r.add('/','root');r.add('/a/:id','a');assert r.resolve('///?x=1')==('root',{});assert r.resolve('//a//v///#frag?ignored')==('a',{'id':'v'})"),
    ("validation_is_nonmutating", '''
        from router import Router
        r=Router();r.add('/ok','ok')
        for pattern in ['', 'a', '/:x/:x','/:9bad','/:','/x?y','/x#y',None]:
            raises(ValueError,lambda pattern=pattern:r.add(pattern,'bad'))
        assert r.resolve('/ok')==('ok',{})
        for path in ['', 'relative', None]: raises(ValueError,lambda path=path:r.resolve(path))
    '''),
    ("missing_and_parameter_length", "from router import Router\nr=Router();r.add('/a/:v','a');raises(KeyError,lambda:r.resolve('/a'));raises(KeyError,lambda:r.resolve('/a/v/extra'));assert r.resolve('/a/%2F')==('a',{'v':'%2F'})"),
    ("literal_static_characters", "from router import Router\nr=Router();r.add('/a.b/[x]','literal');assert r.resolve('/a.b/[x]')==('literal',{});raises(KeyError,lambda:r.resolve('/axb/x'))"),
    ("opaque_handler_and_fresh_params", '''
        from router import Router
        def handler(): raise AssertionError('handler must not execute')
        r=Router();r.add('/a/:name',handler)
        first=r.resolve('/a/β');assert first==(handler,{'name':'β'})
        first[1]['name']='changed';assert r.resolve('/a/new')==(handler,{'name':'new'})
    '''),
])


_register("interval_coverage", "Normalize half-open interval coverage", "data transformation and boundaries", """
Implement coverage(intervals, start, end) in coverage.py. Bounds and all interval
endpoints are integers (bool is invalid), start <= end, and each input item is
a two-element list or tuple [lo, hi] with lo <= hi. Invalid inputs raise
ValueError, including invalid intervals wholly outside the requested window.
intervals is any finite iterable. Do not mutate its elements or reorder a list
passed by the caller.

Treat intervals as half-open [lo,hi). Clip to [start,end); ignore empty clipped
intervals; sort and merge overlapping OR touching intervals. Return exactly:
{'merged': [[lo,hi], ...], 'gaps': [[lo,hi], ...], 'covered': integer_length}.
Gaps are the sorted nonempty complement within the requested window. For an
empty window start==end both lists are empty and covered is 0. Support negative
and arbitrarily large Python integers; do not enumerate every integer in a
potentially huge range.
""", {"coverage.py": '''
def coverage(intervals, start, end):
    raise NotImplementedError("Implement the README contract")
'''}, '''
import unittest
from coverage import coverage
class Smoke(unittest.TestCase):
    def test_clip_and_merge(self):
        self.assertEqual(coverage([[3,5],[-2,2],[2,4]],0,7),
            {'merged':[[0,5]],'gaps':[[5,7]],'covered':5})
if __name__ == '__main__': unittest.main()
''', [
    ("overlap_clip", "from coverage import coverage\nassert coverage([[3,8],[-5,2],[1,4]],0,6)==dict(merged=[[0,6]],gaps=[],covered=6)"),
    ("touching_and_duplicates", "from coverage import coverage\nassert coverage([[3,5],[1,3],[1,3],[5,5]],0,7)==dict(merged=[[1,5]],gaps=[[0,1],[5,7]],covered=4)"),
    ("empty_and_outside", "from coverage import coverage\nassert coverage([],2,2)==dict(merged=[],gaps=[],covered=0);assert coverage([[-4,0],[5,9]],0,5)==dict(merged=[],gaps=[[0,5]],covered=0)"),
    ("negative_and_disjoint", "from coverage import coverage\nassert coverage([[-8,-5],[-2,3]],-10,0)==dict(merged=[[-8,-5],[-2,0]],gaps=[[-10,-8],[-5,-2]],covered=5)"),
    ("large_integer_no_enumeration", "from coverage import coverage\nn=10**30;assert coverage([[n,n+10],[n+20,n+30]],n-5,n+35)==dict(merged=[[n,n+10],[n+20,n+30]],gaps=[[n-5,n],[n+10,n+20],[n+30,n+35]],covered=20)"),
    ("generator_and_immutability", "from coverage import coverage\nx=[[4,8],[1,2]];old=copy.deepcopy(x);assert coverage(iter(x),0,10)['covered']==5;assert x==old"),
    ("reject_invalid_even_outside", '''
        from coverage import coverage
        for value in [[[3,2]],[[False,2]],[[1.0,2]],[[1,2,3]],['ab'],[None],[[100,99]]]:
            raises(ValueError,lambda value=value:coverage(value,0,5))
        for a,b in [(True,3),(0,False),(4,3),(0,2.0)]: raises(ValueError,lambda a=a,b=b:coverage([],a,b))
    '''),
    ("deterministic_mixed_cases", '''
        from coverage import coverage
        rng=random.Random(713)
        for _ in range(50):
            intervals=[sorted([rng.randrange(-8,13),rng.randrange(-8,13)]) for _ in range(rng.randrange(9))]
            points={n for lo,hi in intervals for n in range(max(-4,lo),min(9,hi))}
            expected={'merged':runs(sorted(points)),'gaps':runs(sorted(set(range(-4,9))-points)),'covered':len(points)}
            assert coverage(intervals,-4,9)==expected
    '''),
])
