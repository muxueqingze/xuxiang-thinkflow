const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
class BackendClient extends EventEmitter {
  constructor(command, args, options = {}) {
    super(); this.nextId = 1; this.pending = new Map(); this.buffer = ''; this.closed = false;
    this.child = spawn(command, args, { ...options, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', chunk => this.receive(chunk));
    // stderr can contain provider secrets; never forward or persist raw output.
    this.child.stderr.resume();
    this.child.on('error', () => this.fail('后端无法启动，请检查 Python 或安装包是否完整'));
    this.child.on('exit', () => this.fail('本地后端已退出，请重新启动续想'));
    this.child.stdin.on('error', () => this.fail('与本地后端的连接已断开'));
  }
  receive(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 32 * 1024 * 1024) { this.fail('后端响应超过大小限制'); this.child.kill(); return; }
    let end;
    while ((end = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, end); this.buffer = this.buffer.slice(end + 1);
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch { this.fail('后端返回了无效协议数据'); this.child.kill(); return; }
      if (message.event) this.emit('event', message.event);
      else {
        const request = this.pending.get(String(message.id));
        if (!request) continue;
        this.pending.delete(String(message.id)); clearTimeout(request.timer);
        if (message.error) request.reject(new Error(message.error.message || '请求失败'));
        else request.resolve(message.result);
      }
    }
  }
  request(method, params = {}, timeout = 30000) {
    if (this.closed) return Promise.reject(new Error('本地后端未连接'));
    const id = String(this.nextId++);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('本地后端响应超时')); }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => { if (error) this.fail('无法向本地后端发送请求'); });
    });
  }
  fail(message) {
    if (this.closed) return;
    this.closed = true;
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(new Error(message)); }
    this.pending.clear(); this.emit('event', { type: 'backend_error', message });
  }
  async shutdown() {
    if (!this.closed) { try { await this.request('shutdown', {}, 15000); } catch {} }
    if (this.child.exitCode === null) this.child.kill();
  }
}
module.exports = { BackendClient };
