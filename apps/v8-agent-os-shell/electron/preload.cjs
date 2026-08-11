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
  retryStartup: () => ipcRenderer.send("v8os-shell:retry-startup"),
  openAdmin: () => ipcRenderer.send("v8os-shell:open-admin"),
  openWorkspaceFolder: (workspacePath) => ipcRenderer.invoke("v8os-shell:open-workspace-folder", workspacePath),
  revealWorkspaceFile: (workspaceRelativePath, workspacePath) => ipcRenderer.invoke("v8os-shell:reveal-workspace-file", workspaceRelativePath, workspacePath),
  selectGodotExecutable: () => ipcRenderer.invoke("v8os-shell:select-godot-executable"),
  selectGodotProjectDirectory: () => ipcRenderer.invoke("v8os-shell:select-godot-project-directory"),
  reportActiveSession: (sessionId) => ipcRenderer.send("v8os-shell:active-session", sessionId || null),
  getDesktopPetState: () => ipcRenderer.invoke("v8os-shell:get-desktop-pet-state"),
  setDesktopPetEnabled: (enabled) => ipcRenderer.invoke("v8os-shell:set-desktop-pet-enabled", Boolean(enabled)),
  getUpdateStatus: () => ipcRenderer.invoke("v8os-shell:get-update-status"),
  checkForUpdates: () => ipcRenderer.invoke("v8os-shell:check-for-updates"),
  openUpdateRelease: () => ipcRenderer.invoke("v8os-shell:open-update-release"),
  onUpdateStatusChange: (callback) => {
    const listener = (_event, state) => callback?.(state);
    ipcRenderer.on("v8os-shell:update-status", listener);
    return () => ipcRenderer.off("v8os-shell:update-status", listener);
  },
  onDesktopPetStateChange: (callback) => {
    const listener = (_event, state) => callback?.(state);
    ipcRenderer.on("v8os-shell:desktop-pet-state", listener);
    return () => ipcRenderer.off("v8os-shell:desktop-pet-state", listener);
  },
});
