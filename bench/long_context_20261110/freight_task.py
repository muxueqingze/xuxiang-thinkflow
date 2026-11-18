"""Frozen three-release freight fixture and black-box acceptance checks."""
import json
import subprocess
import sys


COMMON = (' Work in the current project only. Use relative paths, Python standard library and the existing '
          'public interfaces. Do not browse the network, parent folders or machine configuration. '
          'The project runs on Windows. Verify the release before finishing.')
STAGES = [
    'Release A. This existing freight invoice module has a Python API, JSON CLI, and a data catalog. '
    'Implement freight.quote(route, grams) using the catalog: zero grams costs zero, otherwise charge '
    'base plus rate for each STARTED 500 grams, in integer cents. Normalize input route by stripping surrounding '
    'whitespace and uppercasing; unknown routes raise KeyError. Return an integer. Keep the CLI compatible. '
    'For our future CSV release remember the buyer contract: first line exactly "batch,waybill,charge_cents"; '
    'batch literal BW-73 on every row; use LF, quote CSV fields correctly, and preserve input encounter order. '
    'Do not ship a CSV command in this release.' + COMMON,
    'Release B. Add aliases and apply the attached dispatch bulletin to the catalog. Rows are in publication order. '
    'Only LIVE rows apply; a later LIVE upsert replaces the earlier route. Route aliases use the same strip and '
    'uppercase normalization. Keep every legacy route and its price unless the bulletin changes it. '
    'Each nonempty alias in a LIVE row remains valid. The existing JSON quote CLI must work with these aliases. '
    'The new freight.manifest(rows) accepts a list of objects containing waybill, route, grams and returns a list '
    'of objects containing EXACTLY waybill and charge_cents, in arrival order. Empty input returns [].'
    + COMMON,
    'Release C. Reopen the earlier buyer CSV agreement and ship it now. Add "python shipping.py export" reading '
    'a JSON list from stdin and writing the agreed UTF-8 CSV to stdout, with a single final newline. '
    'A waybill can now have a correction or cancellation. Fold events by waybill. A row with cancelled=true '
    'removes it without needing route or grams; repeated cancellations and unknown cancellations are harmless. '
    'A later booking restores it at its ORIGINAL first booking position, and a correction keeps that position. '
    'Use the last surviving booking values. Update freight.manifest and the CLI consistently. '
    'Apply dispatch correction: Cedar route now charges 23 cents per started half kilo, same base; keep '
    'all aliases and other catalog prices from release B. Preserve JSON quote behavior.' + COMMON,
]
CATALOG = {'CEDAR': {'base': 80, 'rate': 11}, 'DELTA': {'base': 100, 'rate': 17}}
SEED = {
    'rates.json': json.dumps(CATALOG, indent=2) + '\n',
    'freight.py': '''import json
from pathlib import Path

def quote(route, grams):
    rates = json.loads(Path(__file__).with_name("rates.json").read_text(encoding="utf-8"))
    return rates[route]["base"]
''',
    'shipping.py': '''import json
import sys
from freight import quote

if __name__ == "__main__":
    if sys.argv[1] == "quote":
        print(json.dumps(quote(sys.argv[2], int(sys.argv[3]))))
''',
}


def bulletin():
    live = {9: ('LARCH', 35, 7, 'LC'), 35: ('CEDAR', 85, 13, 'CD'),
            71: ('FEN', 58, 21, 'FN'), 114: ('LARCH', 40, 9, 'LR'),
            147: ('FJORD', 76, 15, 'FJ')}
    lines = ['ticket|status|route|base|rate|alias|dispatch_note']
    for i in range(160):
        name, base, rate, alias = live.get(i, (('CEDAR', 'FJORD', 'DELTA', 'FEN', 'LARCH')[i % 5],
                                                15 + i, 3 + i % 19, 'H' + str(i)))
        state = 'LIVE' if i in live else ('VOID', 'DRAFT', 'SUPERSEDED')[i % 3]
        lines.append(f'DSP-{8000+i}|{state}|{name}|{base}|{rate}|{alias}|Dock {i % 12 + 1} tariff register, carrier revision {i + 1}.')
    return '\n'.join(lines)


def prompts():
    return [STAGES[0], STAGES[1] + '\n\n' + bulletin(), STAGES[2]]


def seed(workspace):
    for name, data in SEED.items():
        (workspace / name).write_text(data, encoding='utf8')


def grade(workspace, stage):
    checks = {}

    def run(name, args, expected, stdin=None, *, json_output=False):
        try:
            result = subprocess.run([sys.executable, '-B', *args], cwd=workspace,
                                    input=stdin, capture_output=True, timeout=10)
            actual = json.loads(result.stdout) if json_output and result.returncode == 0 else result.stdout
            checks[name] = result.returncode == 0 and actual == expected
        except (subprocess.TimeoutExpired, ValueError, UnicodeError):
            checks[name] = False

    def api(name, expr, expected):
        code = 'import json,freight; print(json.dumps(' + expr + ', sort_keys=True, ensure_ascii=True))'
        run(name, ['-c', code], expected, json_output=True)

    rate = (11, 13, 23)[stage - 1]
    base = 80 if stage == 1 else 85
    api('started-half-kilo-boundaries', "[freight.quote('  cEDar ',n) for n in [0,1,499,500,501,1500]]",
        [0, base + rate, base + rate, base + rate, base + rate*2, base + rate*3])
    api('legacy-route', "freight.quote('DeLTA',1201)", 151)
    unknown = "import freight\ntry: freight.quote('H150',1)\nexcept KeyError: pass\nelse: raise AssertionError()"
    run('unknown-route-and-draft-alias', ['-c', unknown], b'')
    run('legacy-json-cli', ['shipping.py', 'quote', ' delta ', '750'], 134, json_output=True)
    if stage == 1:
        return checks
    api('published-prices-and-sticky-aliases',
        "[freight.quote(k,501) for k in ['FEn',' fn ','lc',' LR','LARCH','FJ',' cd ']]",
        [100, 100, 58, 58, 58, 106, base + rate*2])
    run('alias-json-cli', ['shipping.py', 'quote', ' fj ', '500'], 91, json_output=True)
    api('empty-manifest', 'freight.manifest([])', [])
    bookings = [{'waybill':'Q,9','route':'  CD','grams':501},{'waybill':'M"7','route':'lr ','grams':0},
                {'waybill':'汉','route':'FJ','grams':1500}]
    expected = [{'waybill':row['waybill'], 'charge_cents':amount} for row,amount in zip(bookings,[base+rate*2,0,121])]
    api('ordered-manifest-schema', 'freight.manifest(' + ascii(bookings) + ')', expected)
    if stage == 2:
        return checks
    canceled = lambda wb: {'waybill':wb,'cancelled':True}
    events = [canceled('never'),*bookings,canceled('Q,9'),canceled('Q,9'),canceled('汉'),
              {'waybill':'Z','route':' FJ ','grams':500},{'waybill':'Q,9','route':'cedar','grams':500},
              {'waybill':'M"7','route':'delta','grams':501}]
    api('restore-correction-cancel-order', 'freight.manifest(' + ascii(events) + ')',
        [{'waybill':wb,'charge_cents':n} for wb,n in [('Q,9',108),('M"7',134),('Z',91)]])
    for name, rows, csv in [
        ('csv-release-contract', events, 'BW-73,"Q,9",108\nBW-73,"M""7",134\nBW-73,Z,91\n'),
        ('csv-utf8', [bookings[2]], 'BW-73,汉,121\n'), ('csv-empty', [], '')]:
        run(name, ['shipping.py','export'], ('batch,waybill,charge_cents\n' + csv).encode(),
            json.dumps(rows, ensure_ascii=True).encode())
    return checks
