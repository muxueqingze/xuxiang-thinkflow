"""Fixed CLI adapters. Personal provider keys and user integrations are not inherited."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PI_ROOT = HERE / 'node_modules/@earendil-works/pi-coding-agent'
# Official /v1/models capability metadata observed 2026-09-29; not run budgets.
PRODUCTION_MODEL_CONTEXT = 1048576
PRODUCTION_MODEL_OUTPUT = 393216
PRODUCTION_CAPABILITIES = {
    'source':'https://api.deepseek.com/v1/models', 'observed':'2026-09-29',
    'model':'deepseek-flash', 'name':'DeepSeek-V4.1-Flash',
    'context_window':PRODUCTION_MODEL_CONTEXT, 'max_output_tokens':PRODUCTION_MODEL_OUTPUT,
    'client_output_limit':None,
    'upstream_default':'Omitting max_tokens uses provider default: thinking/high 64K.',
    'default_source':'https://api-docs.deepseek.com/api/create-chat-completion/',
}


def write_configs(files):
    # Configuration writes use Node and validate the on-disk JSON before execution.
    source = "const fs=require('fs'),p=require('path');for(const [f,v] of Object.entries(JSON.parse(fs.readFileSync(0,'utf8')))){fs.mkdirSync(p.dirname(f),{recursive:true});fs.writeFileSync(f,JSON.stringify(v,null,2)+'\\n');if(JSON.stringify(JSON.parse(fs.readFileSync(f,'utf8')))!==JSON.stringify(v))throw Error('Config roundtrip failed');}"
    subprocess.run([shutil.which('node'), '-e', source], input=json.dumps(files), text=True, check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)


def configuration(harness, run_dir, endpoint, local_token, *, mode='bounded'):
    if mode not in ('bounded', 'production'):
        raise ValueError(f'Unknown benchmark mode: {mode}')
    production = mode == 'production'
    profile = run_dir / 'profile'
    profile.mkdir(parents=True, exist_ok=True)
    # Deliberately exclude all inherited *_API_KEY, credentials and other agents' routing.
    names = ('PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','SYSTEMDRIVE','PROGRAMFILES',
             'PROGRAMFILES(X86)','PROGRAMDATA','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')
    env = {k:v for k,v in os.environ.items() if k.upper() in names}
    scratch = profile / 'tmp'
    scratch.mkdir(exist_ok=True)
    env.update(TEMP=str(scratch), TMP=str(scratch), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               BENCH_URL=endpoint, BENCH_API_KEY=local_token, BENCH_RECORD=str(run_dir/'harness.json'),
               CI='1', NO_COLOR='1')
    if production:
        env['BENCH_MODE'] = 'production'
    env.update({key:str(profile/folder) for key,folder in (
        ('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache'))})
    for key in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME'):
        Path(env[key]).mkdir(exist_ok=True)
    if harness == 'thinkflow':
        env['THINKFLOW_HOME'] = str(profile/'thinkflow')
        return [sys.executable,'-B',str(HERE/'thinkflow_worker.py')],env
    if harness == 'pi':
        agent_dir = profile/'pi'
        env['PI_CODING_AGENT_DIR'] = str(agent_dir)
        env.update(PI_SKIP_VERSION_CHECK='1',PI_TELEMETRY='0',PI_OFFLINE='1')
        model = {'id':'deepseek-flash','name':'DeepSeek Flash','reasoning':True,'input':['text'],
                 'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0},
                 'contextWindow':131072,'maxTokens':8192,
                 'compat':{'thinkingFormat':'deepseek','supportsStore':False,'supportsDeveloperRole':False,
                           'requiresReasoningContentOnAssistantMessages':True,'supportsReasoningEffort':True,
                           'supportsUsageInStreaming':True,'maxTokensField':'max_tokens','supportsStrictMode':False}}
        settings = {'retry':{'enabled':True,'maxRetries':1},
                    'quietStartup':True,'cacheWarming':'off'}
        if production:
            model.update(contextWindow=PRODUCTION_MODEL_CONTEXT, maxTokens=PRODUCTION_MODEL_OUTPUT)
            settings.pop('retry')  # Use Pi's normal client retry policy.
            settings['compaction'] = {'enabled':False}
        write_configs({str(agent_dir/'models.json'):{'providers':{'benchmark':{
            'baseUrl':endpoint,'api':'openai-completions','apiKey':'${BENCH_API_KEY}','models':[model]}}},
            str(agent_dir/'settings.json'):settings})
        command = [shutil.which('node'),str(PI_ROOT/'dist/cli.js'),'--provider','benchmark','--model','deepseek-flash',
                '--thinking','high','--no-approve','--no-context-files','--no-extensions','--no-skills',
                '--no-prompt-templates','--no-themes','--offline','--no-session','--mode','json','-p']
        if production:
            # Explicit -e survives --no-extensions; discovery stays disabled.
            command.extend(['-e', str(HERE/'production-pi-extension.mjs')])
        return command, env
    if harness == 'opencode':
        config = {'$schema':'https://opencode.ai/config.json','model':'benchmark/deepseek-flash',
                  'small_model':'benchmark/deepseek-flash', 'share':'disabled','autoupdate':False,
                  'enabled_providers':['benchmark'], 'plugin':[], 'instructions':[],
                  'permission':{'*':'allow','external_directory':'deny','webfetch':'deny','websearch':'deny',
                                'task':'deny','skill':'deny','question':'deny'},
                  'tools':{'webfetch':False,'websearch':False,'task':False,'skill':False,'question':False},
                  'mcp':{}, 'lsp':False,'formatter':False,
                  'provider':{'benchmark':{'npm':'@ai-sdk/openai-compatible','name':'Benchmark local meter',
                      'options':{'baseURL':endpoint,'apiKey':'{env:BENCH_API_KEY}'},
                      'models':{'deepseek-flash':{'name':'DeepSeek Flash','reasoning':True,
                          'interleaved':{'field':'reasoning_content'},
                          'limit':{'context':131072,'output':8192}}}}}}
        if production:
            config['provider']['benchmark']['models']['deepseek-flash']['limit'] = {
                'context':PRODUCTION_MODEL_CONTEXT, 'output':PRODUCTION_MODEL_OUTPUT}
            config['compaction'] = {'auto':False, 'prune':False}
            config['plugin'] = [(HERE/'production-opencode-plugin.mjs').as_uri()]
        config_path=profile/'opencode.json'
        write_configs({str(config_path):config})
        env.update(OPENCODE_CONFIG=str(config_path),OPENCODE_CONFIG_DIR=str(profile/'config'),
                   OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_CLAUDE_CODE='1',
                   OPENCODE_DISABLE_EXTERNAL_SKILLS='1',OPENCODE_DISABLE_DEFAULT_PLUGINS='1',
                   OPENCODE_GIT_BASH_PATH='C:/Program Files/Git/bin/bash.exe')
        if production:
            # --pure disables even our explicit protocol hook. Replace it with
            # isolated config/home roots and disabled project discovery.
            env.update(OPENCODE_DISABLE_PROJECT_CONFIG='1', OPENCODE_TEST_HOME=str(profile),
                       OPENCODE_DISABLE_MODELS_FETCH='1')
        cli=Path(os.environ.get('APPDATA',''))/'npm/node_modules/opencode-ai/bin/opencode.exe'
        return [str(cli),'run',*([] if production else ['--pure']),'--auto','--format','json','--model','benchmark/deepseek-flash',
                '--title','Harness benchmark'],env
    raise ValueError(harness)
