export type Locale = 'zh-CN' | 'en-US';

export const normalizeLocale = (value: unknown): Locale =>
  String(value || '').toLowerCase().startsWith('en') ? 'en-US' : 'zh-CN';

export function detectSystemLanguage(): Locale {
  const envLang = process.env.V8OS_LANG || process.env.LC_ALL || process.env.LC_MESSAGES || process.env.LANG;
  if (envLang) {
    return normalizeLocale(envLang);
  }
  try {
    const locale = Intl.DateTimeFormat().resolvedOptions().locale;
    if (locale) {
      return normalizeLocale(locale);
    }
  } catch {
    // Fallback to default
  }
  return 'zh-CN';
}

const exact: Record<string, string> = {
  '设置': 'Settings', '帮助 / 首次安装': 'Help / First run', '操作菜单': 'Command palette',
  '输入消息，或按 / 查看操作。': 'Type a message, or press / for commands.',
  'V8OS · 开始对话': 'V8OS · Start a conversation', '小窗口模式': 'Compact terminal mode',
  '先按 F3 连接模型并选择工作区。': 'Press F3 to connect a model and choose a workspace.',
  '请先配置模型（按 F3 或输入 /setup 打开配置）。': 'Please configure a model first (Press F3 or run /setup).',
  '首次运行 v8os 自动准备 Engine；v8os start 仅启动后台服务。F3 配置模型与工作区。': 'Run v8os to prepare Engine automatically; v8os start starts only the daemon. F3 configures models and workspace.',
  '会话概览 · Ctrl+B选择': 'Sessions · Ctrl+B to select', '任务概览 · Ctrl+T详情': 'Task · Ctrl+T for details',
  '连接': 'Connections', '返回': 'Back', '返回对话': 'Back to chat', '刷新': 'Refresh',
  '模型 / Provider / 预算': 'Models / providers / budgets', '工作区与信任': 'Workspace and trust',
  'Runtime 能力包': 'Runtime capability packs', 'MCP / 插件': 'MCP / extensions',
  '记忆 / 调度': 'Memory / scheduling', '实例 / 诊断': 'Instance / diagnostics',
  '手机网关设置': 'Phone gateway settings', '组网设置': 'Network settings',
  '语言 / Language': 'Language / 语言', '中文（简体）': 'Chinese (Simplified)', 'English': 'English',
  '视觉主题 / Theme': 'Visual theme / Theme',
    '工作区修改 /diff': 'Workspace diff /diff',
  '查看工作区代码修改与 Unified Diff': 'View workspace git diff and unified changes',
  '回滚轮次 /rewind': 'Rewind turn /rewind',
  '回退上一轮对话并恢复草稿': 'Rewind the last turn and restore draft',
  '恢复版本 /restore': 'Restore revision /restore',
  '从历史快照或分支恢复消息': 'Restore message from revision snapshot or branch',
  '初始化规范 /init': 'Initialize contract /init',
  '探测工作区依赖并生成 AGENTS.md': 'Detect workspace dependencies and initialize AGENTS.md',
  '工作区代码修改 / Diff': 'Workspace diff / Diff',
  '回退上一轮对话 / Rewind': 'Rewind turn / Rewind',
  '恢复历史版本 / Restore': 'Restore revision / Restore',
  '初始化项目契约 / Init': 'Initialize project contract / Init',
  '分叉分支 /branch': 'Fork branch /branch', '分叉分支 /fork': 'Fork branch /fork',
  '上下文压缩 /compress': 'Context compress /compress',
  '将当前会话分叉为独立分支': 'Fork current session into an independent branch',
  '查看并手动触发上下文压缩': 'View and manually trigger context compression',
  '分叉会话分支 / Fork': 'Fork session branch / Fork',
  '上下文用量与压缩 / Compress': 'Context usage and compression / Compress',
  '模型管理 /model': 'Model hub /model', '恢复会话 /resume': 'Resume session /resume',
  '新建会话 /new': 'New session /new', '清空会话 /clear': 'Clear session /clear',
  '工作区管理 /workspace': 'Workspace manager /workspace', 'MCP 扩展 /mcp': 'MCP extensions /mcp',
  '系统诊断 /doctor': 'System diagnostics /doctor', '设置 /settings': 'Settings /settings',
  '审批模式 /approval': 'Approval mode /approval', '快速配置 /setup': 'Quick setup /setup',
  '会话列表 /sessions': 'Sessions /sessions',
  '切换多行': 'Toggle multiline', '会话列表': 'Sessions', '新建会话': 'New session', '任务详情': 'Task details',
  '待处理': 'Inbox', '待处理 /inbox': 'Inbox /inbox', '附件 / 产物': 'Attachments / artifacts', '停止当前任务': 'Stop current task',
  '重试当前任务': 'Retry current task', '外部编辑器 /editor': 'External editor /editor',
  '本次会话审批模式': 'Approval mode for this session', '核对发送结果': 'Reconcile send result',
  '加载更早历史': 'Load older history', '回到底部': 'Follow latest output', '切换会话侧栏': 'Toggle session sidebar',
  '切换任务侧栏': 'Toggle task sidebar', '退出终端': 'Exit terminal', '帮助': 'Help', '发送': 'Send',
  '管理 Phone 与 Peer 连接': 'Manage Phone and Peer connections', '查看目标，再确认停止': 'Review target, then confirm stop',
  '核对 Engine 能力并确认重试': 'Recheck Engine capability before retrying', '使用 VISUAL / EDITOR 编辑草稿': 'Edit the draft with VISUAL / EDITOR',
  '显式选择后续消息审批模式': 'Choose the approval mode for later messages', '只回读，避免重复发送': 'Read back state without resending',
  '读取更早的会话消息': 'Load older session messages', '恢复跟随最新输出': 'Follow the latest output',
  '显示或隐藏会话概览': 'Show or hide the session sidebar', '显示或隐藏任务概览': 'Show or hide the task sidebar',
  '退出终端并释放前台 Engine': 'Exit the UI and release the foreground Engine',
  '退出界面，释放前台 Engine': 'Exit the UI and release the foreground Engine', '快捷键与首次安装指导': 'Shortcuts and first-run help',
  '切换并持久化 TUI 界面语言': 'Switch and persist the TUI language',
  '切换本地终端视觉主题': 'Switch local terminal visual theme',
  '切换单行与多行输入': 'Toggle single-line and multiline input',
  '模型管理中心、向导式注册与角色选项卡分配': 'Model management hub, registration wizard and role tab assignments',
  '选择并恢复历史会话': 'Select and resume historical sessions',
  '保留当前草稿，进入新会话': 'Keep current draft and start a new session',
  '清空当前上下文，进入全新会话': 'Clear current context and start a clean session',
  '选择并信任项目工作区': 'Select and trust project workspace',
  '管理 MCP 扩展服务与工具': 'Manage MCP extensions and tools',
  '检查环境与服务健康状态': 'Check environment and service health status',
  '模型、工作区与预算配置': 'Configure models, workspace and budgets',
  '首次配置本机身份、模型与工作区': 'First-time setup for identity, models, and workspace',
  '搜索并恢复已有会话': 'Search and resume existing sessions',
  '查看当前任务与运行状态': 'View current task and run status',
  '查看审批与待回答问题': 'View approvals and pending questions',
  '管理附件和生成产物': 'Manage attachments and generated artifacts',
  '选择后立即保存到本机 TUI 视图；不会修改 Engine 或其他客户端。': 'Saved immediately to local TUI view; does not modify Engine or other clients.',
  '界面语言：简体中文': 'UI language: Chinese (Simplified)',
  'Tab 切换字段 · F9 保存/预览 · Esc 返回': 'Tab switch fields · F9 save/preview · Esc back',
  'Enter 发送 · F8 多行 · Ctrl+P 操作 · F1 帮助 · Ctrl+D 退出': 'Enter send · F8 multiline · Ctrl+P commands · F1 help · Ctrl+D exit',
  'Tab 补全 · Enter执行 · Esc 返回草稿': 'Tab complete · Enter run · Esc restore draft',
  '没有匹配的操作；请修改关键词或 Esc 返回。': 'No matching command; edit the query or press Esc to return.',
  '输入搜索 · 上下选择 · Enter 执行 · Esc 返回': 'Type to search · arrows select · Enter run · Esc back',
  '操作仍在处理中；可继续搜索、切换页面或 Esc 返回。': 'An operation is still running; you can search, navigate, or press Esc.',
  '命令候选中不会发送草稿；Enter 选择，Esc 返回。': 'Command search never sends the draft; Enter selects and Esc returns.',
  '↑↓选择，Tab补全，Enter执行，Esc返回原草稿': '↑↓ select · Tab complete · Enter run · Esc restore draft',
  '选择编号，Enter确认；Esc返回': 'Type a number, Enter confirms; Esc returns', '输入': 'Input',
  '输入已清空；Ctrl+Z 撤销。': 'Input cleared; Ctrl+Z restores it.',
  'Ctrl+D 退出终端；停止任务请选择菜单中的停止任务。': 'Ctrl+D exits; stop a run from the command menu.',
};

const prefixes: Array<[string, string]> = [
  ['Engine 未连接。运行 v8os start 启动；首次安装见 F1 帮助。', 'Engine is disconnected. Run v8os start; see F1 for first-run help. '],
  ['搜索：', 'Search: '], ['匹配 ', 'Matches '], ['已暂停跟随', 'Follow paused'], ['已连接', 'Connected'],
  ['连接中', 'Connecting'], ['未连接', 'Disconnected'], ['未选择工作区', 'No workspace selected'],
  ['工作区：', 'Workspace: '], ['状态：', 'Status: '], ['当前选择：', 'Current selection: '],
  ['输入消息，或按 / 查看操作。', 'Type a message, or press / for commands.'],
  ['语言已切换为：', 'Language switched to: '],
  ['视觉主题已切换：', 'Visual theme switched to: '],
  ['未知命令：', 'Unknown command: '],
  ['已回退上一轮对话，原问题已恢复至输入框草稿。', 'Rewound last turn; previous user input restored to draft.'],
  ['已从历史快照版本成功恢复。', 'Successfully restored from revision snapshot.'],
  ['已成功初始化 AGENTS.md 规范契约。', 'Successfully initialized AGENTS.md contract.'],
  ['已备份并重新生成 AGENTS.md 规范契约。', 'Backed up and regenerated AGENTS.md contract.'],
  ['已分叉为新分支会话：', 'Forked to new branch session: '],
];

const statusLabels: Record<string, string> = {
  '已完成': 'Completed', '失败': 'Failed', '运行中': 'Running', '正在回复': 'Streaming',
  '等待中': 'Waiting', '排队中': 'Queued', '等待确认': 'Awaiting approval', '已取消': 'Cancelled',
  '已中断': 'Interrupted', '部分完成': 'Partially complete', '结果待确认': 'Outcome pending confirmation',
  '已超时': 'Timed out', '已终止': 'Terminated', '操作受阻': 'Blocked', '查看详情': 'See details', '未运行': 'Not running',
};

export function localize(value: unknown, locale: Locale): string {
  const text = String(value ?? '');
  if (locale === 'zh-CN') return text;
  if (exact[text]) return exact[text];
  if (statusLabels[text]) return statusLabels[text];
  for (const [from, to] of prefixes) if (text.startsWith(from)) return to + text.slice(from.length);
  return Object.entries(exact).filter(([from]) => from.length > 1).reduce((value, [from, to]) => value.replaceAll(from, to), text);
}

export function localizeLines(values: readonly string[], locale: Locale): string[] {
  return values.map(value => localize(value, locale));
}
