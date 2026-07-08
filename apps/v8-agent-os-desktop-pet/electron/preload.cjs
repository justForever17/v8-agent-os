const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('v8CyberCore', {
  platform: process.platform,
  openAdmin: (url) => ipcRenderer.invoke('v8-desktop:open-admin', url),
  setAlwaysOnTop: (enabled) => ipcRenderer.invoke('v8-desktop:set-always-on-top', Boolean(enabled)),
  setClickThrough: (enabled) => ipcRenderer.invoke('v8-desktop:set-click-through', Boolean(enabled)),
  setPanelOpen: (enabled) => ipcRenderer.invoke('v8-desktop:set-panel-open', Boolean(enabled)),
  setCompanionScale: (scale) => ipcRenderer.invoke('v8-desktop:set-companion-scale', Number(scale) || 0.7),
  moveWindowBy: (dx, dy) => ipcRenderer.invoke('v8-desktop:move-window-by', Number(dx) || 0, Number(dy) || 0),
  readLocalConfig: (key) => ipcRenderer.invoke('v8-desktop:read-local-config', key),
  writeLocalConfig: (key, value) => ipcRenderer.invoke('v8-desktop:write-local-config', key, value),
  getMediaPermissionStatus: (kind) => ipcRenderer.invoke('v8-desktop:get-media-permission-status', kind),
  requestMediaAccess: (kind) => ipcRenderer.invoke('v8-desktop:request-media-access', kind),
  openMediaPrivacySettings: (kind) => ipcRenderer.invoke('v8-desktop:open-media-privacy-settings', kind),
  onPrepareShutdown: (callback) => {
    const listener = () => {
      try {
        callback?.();
      } catch {
        // renderer cleanup is best effort
      }
    };
    ipcRenderer.on('v8-desktop:prepare-shutdown', listener);
    return () => ipcRenderer.off('v8-desktop:prepare-shutdown', listener);
  },
  onPanelExpandDirection: (callback) => {
    const listener = (_event, data) => {
      try {
        callback?.(data);
      } catch {}
    };
    ipcRenderer.on('v8-desktop:panel-expand-direction', listener);
    return () => ipcRenderer.off('v8-desktop:panel-expand-direction', listener);
  },
  quit: () => ipcRenderer.invoke('v8-desktop:quit'),
});
