'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cementerio', {
  load: () => ipcRenderer.sendSync('data:load'),
  save: text => ipcRenderer.sendSync('data:save', text),
  info: () => ipcRenderer.invoke('bk:info'),
  backupNow: reason => ipcRenderer.invoke('bk:now', reason),
  openFolder: () => ipcRenderer.invoke('bk:openFolder'),
  chooseExtra: () => ipcRenderer.invoke('bk:chooseExtra'),
  clearExtra: () => ipcRenderer.invoke('bk:clearExtra'),
  readBackup: name => ipcRenderer.invoke('bk:read', name),
  savePdf: opts => ipcRenderer.invoke('pdf:save', opts)
});
