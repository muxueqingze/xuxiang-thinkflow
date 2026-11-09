"""Delivery failure and cancellation probes. Same arguments as reproduce.py."""
import asyncio
import json
import sys
from reproduce import ROOT, run_fixture, text, STOP, DONE, SecurityPolicy
from src.delivery import _run


async def main():
    cwd = ROOT / 'delivery_failure_default'
    cwd.mkdir(exist_ok=True)
    (cwd / 'node_modules').mkdir(exist_ok=True)
    (cwd / 'check.py').write_text('raise SystemExit(7)\n')
    (cwd / 'package.json').write_text(json.dumps({'name': 'fixture', 'scripts': {'build': 'python check.py'}}))
    result = await run_fixture('delivery_failure_default', [[text('<tf-write id="1" path="app.txt">x</tf-write>'), STOP], DONE],
        security=SecurityPolicy(allowed_roots=[str(cwd)], bash_policy='off', approval_mode='request_all'),
        delivery_verify=True)

    cancel_root = ROOT / 'delivery_cancel'
    cancel_root.mkdir(exist_ok=True)
    (cancel_root / 'delay.py').write_text('from pathlib import Path\nimport time\nPath("started").write_text("yes")\ntime.sleep(.5)\nPath("after_cancel").write_text("STILL_EXECUTED")\n')
    task = asyncio.create_task(_run('python delay.py', str(cancel_root)))
    for _ in range(100):
        if (cancel_root / 'started').exists():
            break
        await asyncio.sleep(.02)
    assert (cancel_root / 'started').exists(), 'fixture child failed to start'
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    absent_when_cancel_returned = not (cancel_root / 'after_cancel').exists()
    await asyncio.sleep(.8)
    print(json.dumps({'default_failure_limit': result, 'cancel': {
        'absent_when_cancel_returned': absent_when_cancel_returned,
        'appeared_after_cancel': (cancel_root / 'after_cancel').read_text() if (cancel_root / 'after_cancel').exists() else None,
    }}, ensure_ascii=True, indent=2))


asyncio.run(main())
