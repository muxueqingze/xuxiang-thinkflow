"""Opt-in live checks; credentials never enter artifacts or command-line arguments.

python -B scripts/live_deepseek.py --diagnose
Use DEEPSEEK_API_KEY or Windows current-user DPAPI at the documented private path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.provider_diagnostics import catalog, probe


def load_key():
    if os.environ.get('DEEPSEEK_API_KEY'):
        return os.environ['DEEPSEEK_API_KEY']
    if os.name != 'nt':
        raise ValueError('Set DEEPSEEK_API_KEY for this explicit live check.')
    script = r"""$ErrorActionPreference='Stop'; $p=Join-Path $env:LOCALAPPDATA 'ThinkFlow\credentials\deepseek.dpapi'; $s=Get-Content -LiteralPath $p | ConvertTo-SecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { [Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.strip():
        raise ValueError('Local encrypted DeepSeek credential is unavailable.')
    return result.stdout.strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', action='store_true', help='Make a model-list request and a small explicit completion')
    args = parser.parse_args()
    if not args.diagnose:
        parser.print_help()
        return
    config = {'provider': 'openai', 'base_url': 'https://api.deepseek.com',
              'model': 'deepseek-flash', 'api_key': load_key()}
    # No raw request, response body, prompt, thinking text or key in reports.
    result = {'catalog': await catalog(config), 'probe': await probe(config)}
    destination = Path(__file__).resolve().parents[1] / 'temp/live-deepseek/diagnostics.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        # Avoid transport reprs that could include request material.
        print(f'Live check failed: {type(exc).__name__}. See configured endpoint and credential.', file=sys.stderr)
        sys.exit(1)
