/* Inspect an extracted portable app; never calls an external model. */
const { _electron } = require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const appDir=process.argv[2];
if(!appDir){console.error('Provide the extracted application directory.');process.exit(2);}
const root=path.resolve(__dirname,'../..'),directory=path.join(root,'desktop/temp',`zip-start-${Date.now()}`),profile=path.join(directory,'profile');
fs.mkdirSync(directory,{recursive:true});let application;
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;delete env.DEEPSEEK_API_KEY;
 application=await _electron.launch({executablePath:path.join(path.resolve(appDir),'ThinkFlow.exe'),args:[`--user-data-dir=${profile}`],env});
 const page=await application.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.waitForFunction(()=>document.getElementById('connection-label').textContent==='本地后端已连接');
 const actual=await application.evaluate(({app})=>({version:app.getVersion(),packaged:app.isPackaged,userData:app.getPath('userData')}));
 const state=await page.evaluate(()=>window.thinkflow.request('get_state'));
 assert.equal(actual.version,JSON.parse(fs.readFileSync(path.join(root,'desktop/package.json'),'utf8')).version);assert.equal(actual.packaged,true);assert.equal(path.resolve(actual.userData),profile);
 assert.equal(state.status,'idle');assert.equal(state.messages.length,0);assert.equal(state.config.has_api_key,false);assert.equal(await page.locator('#send').isDisabled(),true);
 await page.bringToFront();await page.screenshot({path:path.join(directory,'startup.png')});assert.deepEqual(errors,[]);
 const report={passed:true,...actual,source:path.resolve(appDir),checks:['extracted package starts','bundled Python backend connects','isolated clean user data','no task or model request on startup'],errors};
 fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({directory,...report}));
})().catch(e=>{console.error(e.message);process.exitCode=1;}).finally(async()=>{if(application)await application.close();});
