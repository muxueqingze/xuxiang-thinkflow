/* Build an isolated Windows distribution without changing prior artifacts. */
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

async function main() {
  if (process.platform !== 'win32') throw new Error('Windows distribution must be built on Windows.');
  const root = path.resolve(__dirname, '..');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const output = path.join(root, 'artifacts', `desktop-${stamp}`);
  const work = path.join(root, 'temp', 'desktop-build', stamp);
  fs.mkdirSync(work, { recursive: true });
  const python = process.env.THINKFLOW_PYTHON || 'python';
  const result = spawnSync(python, [
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
    '--name', 'thinkflow-service', '--distpath', path.join(output, 'runtime'),
    '--workpath', path.join(work, 'build'), '--specpath', work,
    '--paths', root, '--add-data', `${path.join(root, 'src', 'skills')};src/skills`,
    '--exclude-module', 'PIL', '--exclude-module', 'numpy',
    path.join(root, 'scripts', 'desktop_entry.py'),
  ], { cwd: root, stdio: 'inherit', windowsHide: true });
  if (result.error || result.status !== 0) throw result.error || new Error('Python runtime build failed.');
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
    ignore: [/^\/tests($|\/)/, /^\/test-results($|\/)/, /^\/temp($|\/)/, /^\/artifacts($|\/)/],
    appVersion: '0.6.0', appCopyright: 'ThinkFlow Contributors',
    win32metadata: { CompanyName: 'ThinkFlow Contributors', FileDescription: '续想 ThinkFlow', ProductName: '续想 ThinkFlow' },
  });
  fs.cpSync(path.join(output, 'runtime', 'thinkflow-service'), path.join(appDir, 'resources', 'backend'), { recursive: true });
  fs.copyFileSync(path.join(root, 'LICENSE'), path.join(appDir, 'ThinkFlow-LICENSE.txt'));
  fs.writeFileSync(path.join(appDir, '使用说明.txt'),
    '续想 ThinkFlow 0.6.0\r\n\r\n双击 ThinkFlow.exe。首次使用选择工作区，在设置中填写兼容端点与模型。\r\n此文件夹包含 Python 与 Electron 运行时；请整体保存，勿只移动 exe。\r\n会话与加密凭据保存在当前用户的应用数据目录。无需 Python、Node.js 或管理员权限。\r\n应用未做商业代码签名。模型调用使用您配置的端点。\r\n', 'utf8');
  fs.writeFileSync(path.join(root, 'artifacts', 'latest-desktop.json'), JSON.stringify({ path: appDir, version: '0.6.0' }, null, 2));
  console.log(`Desktop distribution: ${appDir}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
