import type { Surface } from './surface.js';

const prefix = '/v1/network-supervisor/neighbors';
export function createPeerInvitation(ui: Surface) {
  ui.form('创建 Peer 邀请', [
    { key: 'localNickname', label: '本机显示名称', value: '' },
    { key: 'localRole', label: '本机角色（primary / companion）', value: 'primary' },
  ], async values => {
    const localRole = values.localRole.trim();
    if (!['primary', 'companion'].includes(localRole)) throw new Error('角色请选择 primary 或 companion');
    const result = await ui.client.api(`${prefix}/pairing/invitations`, { method: 'POST', body: { ttlSeconds: 300, localRole, localNickname: values.localNickname.trim() } });
    if (!result.ok || !result.inviteId || !result.code) throw new Error('Engine 未返回完整邀请。');
    ui.open('Peer 邀请已创建', [`有效期：${result.expiresAt}`, `本机角色：${result.localRole}；对方角色：${result.remoteRole}`, '邀请仅可使用一次。关闭页面会清除本页显示；Engine 中未使用的邀请到期失效。',
      ...(result.invitation ? [] : ['尚无可分享的完整邀请：请在组网设置配置可达的 Peer 公告地址。配对码仅适用于对方已发现本机的场景。']),
    ], [
      { label: '关闭并回读 Peer', run: () => ui.peers() },
      { label: '显示邀请供对方复制', run: () => {
        ui.open('复制 Peer 邀请', [`有效期：${result.expiresAt}`, ...(result.invitation ? ['在对方“接受 Peer 邀请”粘贴以下完整内容：', result.invitation] : [`本机 Peer ID：${ui.client.instance.peerId || '请在组网状态查看'}`, `配对码：${result.code}`]), '只交给预期连接的设备；不会写入聊天草稿。'], [{ label: '关闭并回读 Peer', run: () => ui.peers() }]);
        ui.page!.sensitive = true;
      } },
    ]);
  }, ['本机 primary 对应对方 companion，反之亦然。']);
}

export function consumePeerInvitation(ui: Surface) {
  ui.form('接受 Peer 邀请', [
    { key: 'invitation', label: '完整邀请 JSON（隐藏；已有发现记录时可留空）', value: '', secret: true },
    { key: 'peerId', label: '对方 Peer ID（完整邀请可留空）', value: '' },
    { key: 'code', label: '对方配对码（完整邀请可留空；隐藏）', value: '', secret: true },
    { key: 'localNickname', label: '本机显示名称', value: '' },
  ], async values => {
    const invitation = values.invitation.trim();
    let peerId = values.peerId.trim();
    const details: string[] = [];
    if (invitation) {
      let parsed: any;
      try { parsed = JSON.parse(invitation); } catch { throw new Error('邀请必须是对方生成的完整 JSON'); }
      if (parsed?.kind !== 'v8-peer-invitation.v1' || !parsed.peerId || !parsed.code || !parsed.publicKey || !parsed.baseUrl) throw new Error('邀请缺少设备身份、地址或配对信息');
      peerId = parsed.peerId;
      details.push(`名称：${parsed.displayName || peerId}`, `地址：${parsed.baseUrl}`, `公钥：${parsed.publicKey}`, `有效期：${parsed.expiresAt || '由 Engine 核对'}`);
    } else if (!peerId || !values.code.trim()) throw new Error('请输入完整邀请，或已发现的 Peer ID 和配对码');
    const body = { peerId, code: values.code.trim(), localNickname: values.localNickname.trim(), ...(invitation ? { invitation } : {}) };
    const view = ui.client.view; let submitted = false;
    ui.confirm('确认 Peer 身份', [`Peer ID：${peerId}`, ...details, '请和对方核对设备身份。Engine 校验邀请、签名和有效期后建立受信任连接。'], '接受邀请并回读连接', async () => {
      if (view !== ui.client.view) throw new Error('实例已变化，请重新导入邀请');
      if (submitted) throw new Error('已提交过此邀请，请回读 Peer 连接状态');
      submitted = true;
      try {
        await ui.client.api(`${prefix}/pairing/consume`, { method: 'POST', body });
        const links = await ui.client.api(`${prefix}/links`);
        const linked = (links.items || links.links || []).some((link: any) => link.peerId === peerId);
        ui.client.notice = linked ? '已回读到受信任 Peer 连接；在线状态以组网页为准。' : '配对请求已返回，但连接尚未回读确认，请刷新组网状态。';
        await ui.peers();
      } catch (error: any) {
        if (error.staleView) throw error;
        ui.open('Peer 配对待核对', ['本次配对尚未确认完成；不会自动重发。请先回读连接状态，再检查邀请有效期、目标地址和双方身份。'], [{ label: '回读 Peer', run: () => ui.peers() }]);
      } finally { body.code = ''; if ('invitation' in body) body.invitation = ''; }
    });
    ui.page!.sensitive = true;
  });
}
