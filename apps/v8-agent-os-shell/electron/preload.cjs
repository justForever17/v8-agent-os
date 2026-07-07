const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("v8osShell", {
  isShell: true,
  minimize: () => ipcRenderer.send("v8os-shell:minimize"),
  toggleMaximize: () => ipcRenderer.send("v8os-shell:toggle-maximize"),
  close: () => ipcRenderer.send("v8os-shell:close"),
  openWeb: () => ipcRenderer.send("v8os-shell:open-web"),
  openAdmin: () => ipcRenderer.send("v8os-shell:open-admin"),
});
