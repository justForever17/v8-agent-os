function buildTrayMenuModel(options = {}) {
  const desktopPetState = String(options.desktopPetState || 'stopped');
  const desktopPetProcessRunning = Boolean(options.desktopPetProcessRunning);
  const updateStatus = options.updateStatus && typeof options.updateStatus === 'object'
    ? options.updateStatus
    : { state: 'idle' };
  const stateLabels = {
    stopped: '已关闭',
    starting: '启动中',
    waiting_v8os: '等待 V8OS',
    connected: '已连接',
    stopping: '退出中',
    error: '异常',
  };
  const busy = desktopPetState === 'starting' || desktopPetState === 'stopping';
  const shouldStop = desktopPetProcessRunning || ['waiting_v8os', 'connected', 'error'].includes(desktopPetState);
  const updateAction = (() => {
    if (updateStatus.state === 'checking') {
      return { id: 'update-status', label: '正在检查更新... / Checking for updates...', enabled: false };
    }
    if (updateStatus.state === 'available' && updateStatus.version) {
      return { id: 'open-update-release', label: `新版本 ${updateStatus.version} / Update available` };
    }
    if (updateStatus.state === 'current') {
      return { id: 'check-update', label: '已是最新版，点击复查 / Up to date' };
    }
    if (updateStatus.state === 'error') {
      return { id: 'check-update', label: '更新检查失败，点击重试 / Update check failed' };
    }
    if (updateStatus.state === 'disabled') {
      return { id: 'update-status', label: '更新检测不可用 / Update check unavailable', enabled: false };
    }
    return { id: 'check-update', label: '检查更新 / Check for updates' };
  })();
  return [
    { id: 'open-web', label: '打开 V8OS' },
    { id: 'open-admin', label: '打开设置' },
    { type: 'separator' },
    { id: 'desktop-pet-status', label: `桌宠：${stateLabels[desktopPetState] || stateLabels.error}`, enabled: false },
    {
      id: shouldStop ? 'stop-desktop-pet' : 'start-desktop-pet',
      label: shouldStop ? '退出桌宠' : '启动桌宠',
      enabled: !busy,
    },
    { type: 'separator' },
    updateAction,
    { id: 'service-status', label: '查看服务状态' },
    { type: 'separator' },
    { id: 'quit-v8os', label: '退出 V8OS' },
  ];
}

module.exports = {
  buildTrayMenuModel,
};
