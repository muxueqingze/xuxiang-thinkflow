const { contextBridge, ipcRenderer } = require('electron');
async function invoke(channel, ...args) {
  const result = await ipcRenderer.invoke(channel, ...args);
  if (!result.ok) throw new Error(result.error);
  return result.result;
}
contextBridge.exposeInMainWorld('thinkflow', Object.freeze({
  request: (method, params = {}) => invoke('thinkflow:request', method, params),
  chooseWorkspace: () => invoke('thinkflow:workspace'),
  openRecentWorkspace: path => invoke('thinkflow:recent-workspace', path),
  exportDialog: () => invoke('thinkflow:export'),
  copyText: text => invoke('thinkflow:copy', text),
  onEvent: callback => {
    if (typeof callback !== 'function') throw new TypeError('事件订阅需要回调函数');
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on('thinkflow:event', handler);
    return () => ipcRenderer.removeListener('thinkflow:event', handler);
  }
}));
