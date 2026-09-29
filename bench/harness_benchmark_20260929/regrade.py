"""Recheck one published solution offline in a temporary workspace; no model API."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile

from tasks import grade, materialize


def regrade(report_directory: Path, run_id: str):
    results=json.loads((report_directory/'results.json').read_text(encoding='utf-8'))
    expected=results['manifest']['files']['bench/harness_benchmark_20260929/tasks.py']
    if hashlib.sha256(Path(__file__).with_name('tasks.py').read_bytes()).hexdigest()!=expected:
        raise ValueError('Task validator differs from the frozen experiment')
    solution=json.loads((report_directory/'solutions.json').read_text(encoding='utf-8'))[run_id]
    with tempfile.TemporaryDirectory(prefix='thinkflow-benchmark-regrade-') as folder:
        workspace=Path(folder).resolve()
        materialize(solution['task'],workspace)
        for name,source in solution['files'].items():
            relative=PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or not name.endswith('.py'):
                raise ValueError('Invalid published solution path')
            target=workspace/relative
            if not target.resolve().is_relative_to(workspace):
                raise ValueError('Solution path escaped workspace')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(source,encoding='utf-8')
        return grade(solution['task'],workspace)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id',help='Exact run ID listed in reports/results.json')
    parser.add_argument('--reports',type=Path,default=Path(__file__).with_name('reports'))
    args=parser.parse_args()
    print(json.dumps(regrade(args.reports,args.run_id),ensure_ascii=True,indent=2))


if __name__=='__main__':main()
