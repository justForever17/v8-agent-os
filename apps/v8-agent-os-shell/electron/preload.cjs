const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("v8osShell", {
  isShell: true,
  minimize: () => ipcRenderer.send("v8os-shell:minimize"),
  toggleMaximize: () => ipcRenderer.send("v8os-shell:toggle-maximize"),
  getWindowState: () => ipcRenderer.invoke("v8os-shell:get-window-state"),
  onWindowStateChange: (callback) => {
    const listener = (_event, state) => callback?.(state);
    ipcRenderer.on("v8os-shell:window-state", listener);
    return () => ipcRenderer.off("v8os-shell:window-state", listener);
  },
  close: () => ipcRenderer.send("v8os-shell:close"),
  openWeb: () => ipcRenderer.send("v8os-shell:open-web"),
  openAdmin: () => ipcRenderer.send("v8os-shell:open-admin"),
});
