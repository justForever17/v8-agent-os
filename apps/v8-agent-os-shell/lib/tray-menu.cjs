function buildTrayMenuModel(options = {}) {
  const desktopPetState = String(options.desktopPetState || 'stopped');
  const desktopPetProcessRunning = Boolean(options.desktopPetProcessRunning);
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
    { id: 'service-status', label: '查看服务状态' },
    { type: 'separator' },
    { id: 'quit-v8os', label: '退出 V8OS' },
  ];
}

module.exports = {
  buildTrayMenuModel,
};
