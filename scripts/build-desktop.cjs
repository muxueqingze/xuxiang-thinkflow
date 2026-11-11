/* Build an isolated Windows distribution without changing prior artifacts. */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit', windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (exit ${result.status}).`);
}

async function main() {
  if (process.platform !== 'win32') throw new Error('Windows distribution must be built on Windows.');
  const root = path.resolve(__dirname, '..');
  const version = JSON.parse(fs.readFileSync(path.join(root, 'desktop', 'package.json'), 'utf8')).version;
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const output = path.join(root, 'artifacts', `desktop-${stamp}`);
  const work = path.join(root, 'temp', 'desktop-build', stamp);
  fs.mkdirSync(work, { recursive: true });
  const python = process.env.THINKFLOW_PYTHON || 'python';
  // pywinpty loads this executable beside its ConPTY DLL.
  const openConsole = execFileSync(python, [
    '-X', 'utf8', '-c', 'from pathlib import Path; import winpty; print(Path(winpty.__file__).with_name("OpenConsole.exe"))',
  ], { cwd: root, encoding: 'utf8', windowsHide: true }).trim();
  run(python, [
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
    '--name', 'thinkflow-service', '--distpath', path.join(work, 'runtime'),
    '--workpath', path.join(work, 'build'), '--specpath', work,
    '--paths', root, '--add-data', `${path.join(root, 'src', 'skills')};src/skills`,
    '--add-binary', `${openConsole};winpty`,
    '--exclude-module', 'PIL', '--exclude-module', 'numpy',
    path.join(root, 'scripts', 'desktop_entry.py'),
  ], root);
  const { packager } = await import(pathToFileURL(path.join(root, 'desktop', 'node_modules', '@electron', 'packager', 'dist', 'index.js')).href);
  const electronVersion = JSON.parse(fs.readFileSync(path.join(root, 'desktop', 'node_modules', 'electron', 'package.json'), 'utf8')).version;
  const zipName = `electron-v${electronVersion}-win32-x64.zip`;
  const cacheRoot = path.join(process.env.LOCALAPPDATA || '', 'electron', 'Cache');
  const cachedZip = fs.existsSync(cacheRoot) ? fs.readdirSync(cacheRoot, { withFileTypes: true })
    .filter(entry => entry.isDirectory()).map(entry => path.join(cacheRoot, entry.name))
    .find(folder => fs.existsSync(path.join(folder, zipName))) : undefined;
  const [appDir] = await packager({
    dir: path.join(root, 'desktop'), out: output, name: 'ThinkFlow',
    platform: 'win32', arch: 'x64', asar: true, prune: true,
    electronVersion, ...(cachedZip ? { electronZipDir: cachedZip } : {}),
    ignore: [/^\/tests($|\/)/, /^\/test-results($|\/)/, /^\/temp($|\/)/, /^\/artifacts($|\/)/, /^\/installer\.iss$/],
    appVersion: version, appCopyright: 'ThinkFlow Contributors',
    win32metadata: { CompanyName: 'ThinkFlow Contributors', FileDescription: '续想 ThinkFlow', ProductName: '续想 ThinkFlow' },
  });
  fs.renameSync(path.join(work, 'runtime', 'thinkflow-service'), path.join(appDir, 'resources', 'backend'));
  fs.copyFileSync(path.join(root, 'LICENSE'), path.join(appDir, 'ThinkFlow-LICENSE.txt'));
  fs.writeFileSync(path.join(appDir, '使用说明.txt'),
    `续想 ThinkFlow ${version}\r\n\r\n双击 ThinkFlow.exe。首次使用选择工作区，在设置中填写兼容端点与模型。\r\nEnter 发送，Shift+Enter 换行，Ctrl+K 搜索会话与操作。运行中发送的消息进入队列，停止/失败/重启后须手动继续队列。\r\n此文件夹包含 Python 与 Electron 运行时；请整体保存，勿只移动 exe。\r\n会话与加密凭据保存在当前用户的应用数据目录。无需 Python、Node.js 或管理员权限。\r\n应用未做商业代码签名。模型调用使用您配置的端点。\r\n`, 'utf8');
  const baseName = `ThinkFlow-${version}-Windows-x64`;
  const installer = path.join(output, `${baseName}-Setup.exe`);
  const portable = path.join(output, `${baseName}.zip`);
  const iscc = process.env.THINKFLOW_ISCC || 'ISCC.exe';
  run(iscc, [
    `/DAppVersion=${version}`, `/DAppSource=${appDir}`, `/O${output}`, `/F${baseName}-Setup`,
    path.join(root, 'desktop', 'installer.iss'),
  ], root);
  run(python, [
    '-c', 'import shutil, sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[2], sys.argv[3])',
    path.join(output, baseName), output, path.basename(appDir),
  ], root);
  const manifest = path.join(output, 'latest-desktop.json');
  fs.writeFileSync(manifest, JSON.stringify({ path: appDir, version, installer, portable }, null, 2));
  fs.renameSync(manifest, path.join(root, 'artifacts', 'latest-desktop.json'));
  console.log(`Desktop distribution: ${appDir}\nInstaller: ${installer}\nPortable ZIP: ${portable}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
