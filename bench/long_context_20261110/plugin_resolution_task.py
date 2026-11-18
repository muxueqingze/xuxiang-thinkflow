"""Independent synthetic plugin resolver releases. Hidden grades run outside the fixture."""
import json
import os
import subprocess
import sys

COMMON = (" Use only this synthetic project, relative paths and the Python standard library. "
          "Do not access the network, machine configuration or parent directories. Verify your work.")

STAGES = [
    """Release A. Fix the existing plugin selection library in versioning.py and chooser.py.
versioning.key(version) accepts one, two, or three dot-separated ASCII unsigned decimal components (leading zeros allowed), and returns an integer triple padded on the right with zeros. All other strings raise ValueError.
chooser.choose(candidates, minimum) returns the original spelling of the greatest version at or above minimum, or None. Ties use the first encounter. The iterable may be one-shot. Keep the old inspect_plugin.py VERSION... interface printing the selected version.
Support has frozen a separate contract for a later launcher: when no compatible plugin exists it must exit 37, write zero stdout bytes, and write exactly "插件不可用 [R-17]" followed by one LF to stderr, encoded as UTF-8. Do not implement the new launcher in this release.""" + COMMON,
    """Release B. Downstream SDKs now call chooser.select(entries, requirement), where entries is an iterable of (plugin label, version) pairs. Return the entire winning pair unchanged, or None. The numerically greatest compatible version wins; ties keep first encounter. Add versioning.accepts(version, requirement). A requirement contains a comma-separated conjunction of >=, <, and == clauses, with optional surrounding spaces; an empty/whitespace requirement accepts all. Exact equality compares numeric normalized versions. Invalid clauses or version strings raise ValueError. Support each of the three operators separately and in combination. Keep the old API and CLI working through this change.""" + COMMON,
    """Release C. Implement the launcher discussed in Release A as 'python launch.py REQUIREMENT LABEL@VERSION ...'. It accepts zero or more candidates; select through the Release B resolver. A successful match exits 0, writes the original winning label, an '@', original version spelling and one LF in UTF-8 on stdout; stderr stays empty. When there is no match use Support's original contract exactly. Extend chooser.select with keyword-only exclude=(), an iterable of labels to skip (case-sensitive); the launcher's optional repeated '--skip LABEL' arguments before REQUIREMENT map to it. Preserve both earlier public APIs and the old inspect CLI. Verify with this Windows environment in mind.""" + COMMON,
]

SEED = {
    "versioning.py": """def key(version):
    return tuple(version.split("."))
""",
    "chooser.py": """from versioning import key


def choose(candidates, minimum):
    options = [value for value in candidates if key(value) >= key(minimum)]
    return min(options, default=None)
""",
    "inspect_plugin.py": """import sys
from chooser import choose


if __name__ == "__main__":
    winner = choose(sys.argv[1:], "0")
    if winner is not None:
        print(winner)
""",
}


def seed(workspace):
    for name, source in SEED.items():
        (workspace / name).write_text(source, encoding='utf8')


def grade(workspace, stage):
    checks = {}

    def process(name, args, expected):
        try:
            p = subprocess.run([sys.executable, '-B', *args], cwd=workspace, capture_output=True,
                               env={**os.environ, 'PYTHONIOENCODING':'ascii'}, timeout=8)
            checks[name] = (p.returncode, p.stdout, p.stderr) == expected
        except subprocess.TimeoutExpired:
            checks[name] = False

    def api(name, code):
        process(name, ['-c', 'import chooser,versioning\n' + code], (0,b'',b''))

    api('numeric-normalization', "assert [versioning.key(v) for v in ('02','2.10','002.10.00')] == [(2,0,0),(2,10,0),(2,10,0)]")
    api('invalid-components', """for v in ('', '1.', '1.2.3.4', '-1', '1.+2',' 2','２','1_0'):
    try: versioning.key(v)
    except ValueError: continue
    raise AssertionError(v)""")
    api('legacy-tie-and-iterator', "assert chooser.choose(iter(['2.9','02.010','2.10.0','1.90']), '2.1') == '02.010'")
    api('legacy-no-match', "assert chooser.choose(iter(['1.9','1.2']), '2') is None and chooser.choose([], '0') is None")
    # Existing inspect CLI predates the byte-oriented launcher; Windows CRLF remains valid.
    result = subprocess.run([sys.executable, '-B', 'inspect_plugin.py','2.9','2.10','02.010'],
                            cwd=workspace,capture_output=True,timeout=8)
    checks['legacy-cli'] = (result.returncode, result.stdout.splitlines(),result.stderr) == (0,[b'2.10'],b'')
    if stage == 1:
        return checks
    api('range-boundaries', """assert [versioning.accepts(v,' >= 2 , < 2.10 ') for v in ('1.99','02','2.9','2.10.0')] == [False,True,True,False]
assert versioning.accepts('2.10',' == 02.010.0, >= 2 , <3')
assert versioning.accepts('02','   ')""")
    api('reject-invalid-clauses', """for req in ('>2','>=','<=3','==2.0.0.0','>=1,','>=1,,<2'):
    try: versioning.accepts('2',req)
    except ValueError: continue
    raise AssertionError(req)""")
    api('sdk-iterator-and-ties', """entries = [('z','02.010'), ('q','2.11'), ('a','2.10.0')]
assert chooser.select(iter(entries), '>=2,<2.11') == entries[0]
assert chooser.select(entries, '==0') is None and chooser.select([], '') is None""")
    if stage == 2:
        return checks
    api('excluded-labels-and-case', """entries = [('A','5'),('a','4'),('b','3')]
assert chooser.select(iter(entries), '', exclude=iter(['b','A'])) == entries[1]
assert chooser.select(entries, '>=5', exclude=['A']) is None""")
    process('launcher-utf8-lf', ['launch.py','--skip','X','--skip','x','>=2,<3','X@2.9','x@2.8','泠@02.7','same@2.7.0'],
            (0,'泠@02.7\n'.encode(),b''))
    no_match = (37,b'','插件不可用 [R-17]\n'.encode())
    process('launcher-no-candidates-original-contract',['launch.py',''], no_match)
    process('launcher-incompatible-original-contract',['launch.py','>=3','present@2.99'], no_match)
    return checks
