function buildTrayMenuModel(options = {}) {
  const desktopPetRunning = Boolean(options.desktopPetRunning);
  return [
    { id: 'open-web', label: '打开 V8OS' },
    { id: 'open-admin', label: '打开设置' },
    { type: 'separator' },
    {
      id: desktopPetRunning ? 'stop-desktop-pet' : 'start-desktop-pet',
      label: desktopPetRunning ? '退出桌宠' : '启动桌宠',
    },
    { id: 'service-status', label: '查看服务状态' },
    { type: 'separator' },
    { id: 'quit-v8os', label: '退出 V8OS' },
  ];
}

module.exports = {
  buildTrayMenuModel,
};
