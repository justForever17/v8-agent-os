const { contextBridge, ipcRenderer } = require('electron');

const rawTransport = ipcRenderer.sendSync('v8-desktop:get-transport');
const transport = rawTransport
  ? Object.freeze({
      engineWebSocketUrl: String(rawTransport.engineWebSocketUrl || ''),
    })
  : null;

contextBridge.exposeInMainWorld('v8CyberCore', {
  platform: process.platform,
  transport,
  openAdmin: () => ipcRenderer.invoke('v8-desktop:open-admin'),
  reportStatus: (payload) => ipcRenderer.invoke('v8-desktop:report-status', payload),
  openSession: (sessionId) => ipcRenderer.invoke('v8-desktop:open-session', sessionId),
  getActiveSession: () => ipcRenderer.invoke('v8-desktop:get-active-session'),
  shutdownReady: (requestId) => ipcRenderer.invoke('v8-desktop:shutdown-ready', requestId),
  setAlwaysOnTop: (enabled) => ipcRenderer.invoke('v8-desktop:set-always-on-top', Boolean(enabled)),
  setClickThrough: (enabled) => ipcRenderer.invoke('v8-desktop:set-click-through', Boolean(enabled)),
  setInteractionRegions: (regions) => ipcRenderer.send('v8-desktop:set-interaction-regions', regions),
  setPanelOpen: (enabled) => ipcRenderer.invoke('v8-desktop:set-panel-open', Boolean(enabled)),
  setCompanionScale: (scale) => ipcRenderer.invoke('v8-desktop:set-companion-scale', Number(scale) || 0.7),
  moveWindowBy: (dx, dy) => ipcRenderer.invoke('v8-desktop:move-window-by', Number(dx) || 0, Number(dy) || 0),
  readLocalConfig: (key) => ipcRenderer.invoke('v8-desktop:read-local-config', key),
  writeLocalConfig: (key, value) => ipcRenderer.invoke('v8-desktop:write-local-config', key, value),
  getMediaPermissionStatus: (kind) => ipcRenderer.invoke('v8-desktop:get-media-permission-status', kind),
  requestMediaAccess: (kind) => ipcRenderer.invoke('v8-desktop:request-media-access', kind),
  openMediaPrivacySettings: (kind) => ipcRenderer.invoke('v8-desktop:open-media-privacy-settings', kind),
  onPrepareShutdown: (callback) => {
    const listener = (_event, data) => {
      try {
        callback?.(data);
      } catch {
        // renderer cleanup is best effort
      }
    };
    ipcRenderer.on('v8-desktop:prepare-shutdown', listener);
    return () => ipcRenderer.off('v8-desktop:prepare-shutdown', listener);
  },
  onActiveSession: (callback) => {
    const listener = (_event, data) => {
      try {
        callback?.(data);
      } catch {}
    };
    ipcRenderer.on('v8-desktop:shell-active-session', listener);
    return () => ipcRenderer.off('v8-desktop:shell-active-session', listener);
  },
  onDesktopPetConfigChanged: (callback) => {
    const listener = (_event, data) => {
      try {
        callback?.(data);
      } catch {}
    };
    ipcRenderer.on('v8-desktop:config-changed', listener);
    return () => ipcRenderer.off('v8-desktop:config-changed', listener);
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
