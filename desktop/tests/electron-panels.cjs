/* Actual desktop controls and ConPTY, isolated from user data and remote models. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname,'../..');
const directory = path.join(root,'temp',`desktop-panels-${Date.now()}`);
const workspace = path.join(directory,'workspace'), profile = path.join(directory,'profile');
fs.mkdirSync(workspace,{recursive:true});
const git = (...args) => execFileSync('git',args,{cwd:workspace,windowsHide:true,stdio:'pipe'});
git('init','-q'); git('config','user.name','Local fixture'); git('config','user.email','fixture@example.invalid');
fs.writeFileSync(path.join(workspace,'说明.md'),'# 项目说明\n原始内容\n'); git('add','说明.md'); git('commit','-qm','fixture');
fs.writeFileSync(path.join(workspace,'说明.md'),'# 项目说明\n暂存内容\n'); git('add','说明.md');
fs.appendFileSync(path.join(workspace,'说明.md'),'未暂存内容\n'); fs.writeFileSync(path.join(workspace,'new.txt'),'新增文件\n');
let application, page;
const errors = [], checks = [];
const rpc = (method,params={}) => page.evaluate(({method,params})=>window.thinkflow.request(method,params),{method,params});
async function settle(){ await page.waitForTimeout(280); }
async function until(fn,label){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(80);}throw new Error('Timeout: '+label);}
async function screenshot(name){await settle();await page.screenshot({path:path.join(directory,`${name}.png`),scale:'css'});}
async function main(){
  const env={...process.env,THINKFLOW_TEST_USER_DATA:profile};delete env.ELECTRON_RUN_AS_NODE;
  application=await electron.launch({args:[path.join(root,'desktop')],env});page=await application.firstWindow();
  page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
  await screenshot('home');
  await application.evaluate(({dialog},cwd)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[cwd]});},workspace);await page.locator('#workspace').click();
  await page.locator('#settings-side').click();await page.locator('[name=model]').fill('local-fixture');await page.locator('[name=base_url]').fill('http://127.0.0.1:1');
  await page.locator('#settings-nav-context').click();await page.locator('[data-runtime-path="compaction.max_input_tokens"]').fill('12000');await page.locator('#settings-nav-advanced').click();
  assert.equal(JSON.parse(await page.locator('#runtime-config').inputValue()).compaction.max_input_tokens,12000);
  await page.locator('#runtime-config').fill('{"compaction":{"max_input_tokens":16000,"keep_recent_tokens":4000},"max_read_chars":24000}');
  await page.locator('#settings-nav-context').click();assert.equal(await page.locator('[data-runtime-path="compaction.max_input_tokens"]').inputValue(),'16000');
  await page.locator('#save-settings').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  assert.equal((await rpc('get_state')).runtime_config.compaction.keep_recent_tokens,4000);checks.push('Settings categories and JSON share one saved configuration');
  await page.locator('#settings-side').click();await page.locator('#settings-nav-advanced').click();await page.locator('#runtime-config').fill('{invalid');await page.locator('#save-settings').click();
  await page.locator('#settings-error').waitFor();assert.equal((await rpc('get_state')).runtime_config.compaction.keep_recent_tokens,4000);await page.locator('#cancel-settings').click();checks.push('Invalid JSON preserves editor and saved configuration');
  await page.locator('#open-files').click();await page.locator('.file-entry[data-path="说明.md"]').click();await page.locator('#preview-file-content').waitFor();
  const before=await page.locator('#preview-file-content').textContent();await page.locator('#close-ledger').click();await page.locator('#open-files').click();assert.equal(await page.locator('#preview-file-content').textContent(),before);checks.push('Hiding panes preserves file preview');
  await page.locator('#open-changes').click();await page.locator('[data-git-path="说明.md"][data-staged=true]').click();await until(async()=>(await page.locator('.git-file-details[data-git-group=staged] .git-inline-diff').textContent()).includes('+暂存内容'),'staged diff');
  await page.locator('[data-git-path="说明.md"][data-staged=false]').click();await until(async()=>(await page.locator('.git-file-details[data-git-group=unstaged] .git-inline-diff').textContent()).includes('+未暂存内容'),'worktree diff');
  await page.locator('[data-git-path="new.txt"]').click();await until(async()=>(await page.locator('.git-file-details[data-git-group=untracked] .git-inline-diff').textContent()).includes('+新增文件'),'new file diff');checks.push('Staged, unstaged and untracked Git patches');
  await page.locator('#toggle-terminal').click();await until(async()=>(await rpc('terminal_list')).terminals.length===1,'terminal startup');await settle();
  const first=(await rpc('terminal_list')).terminals[0].id;
  await page.locator('.terminal-view:not([hidden]) .xterm-helper-textarea').focus();await page.keyboard.type("Write-Output ('PTY_'+'OK')");await page.keyboard.press('Enter');
  await until(async()=>(await rpc('terminal_list')).terminals[0].scrollback.includes('PTY_OK'),'actual terminal typing');
  await page.locator('#close-terminal-panel').click();await page.locator('#toggle-terminal').click();assert.equal((await rpc('terminal_list')).terminals[0].id,first);
  await page.locator('#new-session').click();assert.equal((await rpc('terminal_list')).terminals[0].id,first);checks.push('Real xterm input, persistent terminal across hiding and new sessions');
  await page.locator('#new-terminal').click();await until(async()=>(await rpc('terminal_list')).terminals.length===2,'second terminal');
  const second=(await rpc('terminal_list')).terminals[1].id;await page.locator('.terminal-view:not([hidden]) .xterm-helper-textarea').focus();await page.keyboard.type('exit 7');await page.keyboard.press('Enter');
  await until(async()=>(await rpc('terminal_list')).terminals.find(t=>t.id===second).exited,'exit');await until(async()=>(await page.locator('#terminal-tabs').textContent()).includes('已退出 7'),'exit tab state');await page.locator('#terminal-tabs .terminal-tab').last().locator('button').last().click();checks.push('Natural exit keeps its tab and exit code until explicit close');
  if(await page.locator('#open-changes').getAttribute('aria-pressed')!=='true')await page.locator('#open-changes').click();await settle();
  const separator=await page.locator('#inspector-resize').boundingBox();await page.mouse.move(separator.x+4,separator.y+100);await page.mouse.down();await page.mouse.move(separator.x-64,separator.y+100,{steps:8});await page.mouse.up();await settle();
  const width=await page.locator('#inspector').evaluate(node=>node.getBoundingClientRect().width);assert.ok(width>400);
  await page.evaluate(()=>{for(let i=0;i<4;i++){document.getElementById('close-ledger').click();document.getElementById('open-changes').click();}});await settle();assert.ok(Math.abs(await page.locator('#inspector').evaluate(node=>node.getBoundingClientRect().width)-width)<2);checks.push('Pointer resizing and rapid toggle reversal preserve width');
  await screenshot('workbench-light');
  await page.locator('#settings-side').click();await page.locator('#settings-nav-appearance').click();await page.locator('[data-theme-choice=dark]').click();await page.locator('#reduce-motion').check();
  assert.equal(await page.locator('#workspace-upper').evaluate(node=>getComputedStyle(node).transitionDuration),'0s');await screenshot('settings-dark');await page.locator('#settings-nav-context').click();await screenshot('settings-context');await page.locator('#close-settings').click();
  await application.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1000,700));await settle();await screenshot('workbench-1000-dark');
  const geometry=await page.evaluate(()=>{const rect=id=>document.getElementById(id).getBoundingClientRect().toJSON();return {width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth,composer:rect('composer'),terminal:rect('terminal-panel'),inspector:rect('inspector'),messages:rect('messages-scroll')};});
  assert.ok(geometry.scroll<=geometry.width);assert.ok(geometry.composer.bottom<=geometry.terminal.top);assert.ok(geometry.messages.height>0);assert.ok(geometry.inspector.right<=geometry.width+1);checks.push('1000×700 panes and composer fit; reduced motion and dark theme');
  await page.locator('#close-ledger').click();assert.equal(await page.locator('#inspector').evaluate(node=>node.inert),true);checks.push('Closed panes leave keyboard navigation');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify({passed:true,checks,errors,geometry},null,2));console.log(JSON.stringify({directory,passed:true,checks}));
}
main().catch(async error=>{console.error(error);if(page)await screenshot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{if(application)await application.close();});
