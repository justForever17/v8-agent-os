/**
 * 智能主管伪回复模板系统
 * 用于在 Supervisor 只有决策输出时生成友好的群聊式回复
 */

// 任务分配阶段 (planning_end)
export const TASK_ASSIGN_TEMPLATES = [
    "明白了老板！接下来有请 @{agent} 闪亮登场 🎤",
    "收到！让我们请出 @{agent} 来处理这个任务 💪",
    "好的～ @{agent} 已收到任务，马上开始！",
    "交给 @{agent} 准没错！请开始你的表演 ✨",
    "了解！@{agent} 这就为您效劳 🚀",
];

// 任务继续阶段 (evaluating_end + CONTINUE)
export const CONTINUE_TEMPLATES = [
    "干得漂亮！接下来有请 @{agent} 继续推进 🎯",
    "进展不错！现在轮到 @{agent} 登场了 🚀",
    "很好！@{agent} 准备好了吗？接下来看你的！",
    "太棒了！让 @{agent} 接力完成下一步 ⚡",
    "完美衔接！@{agent} 请继续 💫",
];

// 任务完成阶段 (evaluating_end + FINISH)
export const FINISH_TEMPLATES = [
    "{agent} 做得非常棒！任务已圆满完成 ✅ 有任何问题随时找我们哦～",
    "完美！感谢 {agent} 的精彩表现！任务交付成功 🎉",
    "太棒了！{agent} 完美收工！还有什么需要帮忙的吗？",
    "出色！{agent} 已完成任务，结果已呈上 ✨ 随时为您服务～",
    "任务完成！{agent} 表现出色！期待下次合作 🌟",
];

/**
 * 获取随机伪回复
 * @param type - 'assign' | 'continue' | 'finish'
 * @param agentName - Agent 名称
 * @returns 格式化后的伪回复文本
 */
export function getPseudoReply(
    type: 'assign' | 'continue' | 'finish',
    agentName: string
): string {
    let templates: string[];

    switch (type) {
        case 'assign':
            templates = TASK_ASSIGN_TEMPLATES;
            break;
        case 'continue':
            templates = CONTINUE_TEMPLATES;
            break;
        case 'finish':
            templates = FINISH_TEMPLATES;
            break;
        default:
            templates = TASK_ASSIGN_TEMPLATES;
    }

    const randomIndex = Math.floor(Math.random() * templates.length);
    const template = templates[randomIndex];

    return template.replace(/{agent}/g, agentName);
}
