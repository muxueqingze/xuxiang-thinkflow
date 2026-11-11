/* Official CPython distribution used by the desktop's independent Python tool. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

const version = '3.12.10';
const archiveName = `python-${version}-embed-amd64.zip`;
const url = `https://www.python.org/ftp/python/${version}/${archiveName}`;
// SHA-256 from the release's official .zip.sigstore messageDigest.
const sha256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3';

async function preparePythonRuntime(root, destination, buildPython) {
  const cache = path.join(root, 'artifacts', 'build-inputs');
  const archive = path.join(cache, archiveName);
  if (!fs.existsSync(archive)) {
    fs.mkdirSync(cache, { recursive: true });
    console.log(`Downloading Python tool runtime: ${url}`);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Python runtime download failed: HTTP ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== sha256) {
      throw new Error('Downloaded Python runtime does not match the official release digest.');
    }
    fs.writeFileSync(archive, bytes);
  }
  if (crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex') !== sha256) {
    throw new Error(`Python runtime cache does not match the official release digest: ${archive}`);
  }
  execFileSync(buildPython, ['-c',
    'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', archive, destination],
  { cwd: root, stdio: 'inherit', windowsHide: true });
  fs.copyFileSync(path.join(__dirname, 'python_stdin.py'), path.join(destination, 'thinkflow_stdin.py'));
  return destination;
}

module.exports = { preparePythonRuntime };
