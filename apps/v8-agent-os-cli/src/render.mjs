export function printJson(payload) {
  console.log(JSON.stringify(payload, null, 2));
}

export function renderStatus(statuses) {
  console.log("V8OS 服务状态");
  for (const item of statuses) {
    const marker = item.state === "managed_running" ? "RUNNING" : item.state === "external_port_in_use" ? "EXTERNAL" : "STOPPED";
    const pid = item.pid ? ` pid=${item.pid}` : "";
    const port = item.port ? ` port=${item.port}` : "";
    console.log(`- ${item.label}: ${marker}${port}${pid}`);
  }
}

export function renderStartResults(results) {
  for (const item of results) {
    if (item.status === "started") console.log(`Started ${item.id}: pid=${item.pid}${item.port ? `, port=${item.port}` : ""}`);
    else if (item.status === "already_running") console.log(`${item.id} already running: pid=${item.pid}`);
    else if (item.status === "port_in_use") console.log(`${item.id} skipped: port ${item.port} is already in use by an external process.`);
    else console.log(`${item.id}: ${item.status}`);
  }
}

export function renderDoctor(payload) {
  const summary = payload.summary || {};
  console.log(`V8OS Doctor (${payload.source || "unknown"})`);
  console.log(`Summary: ok=${summary.ok ?? 0}, warning=${summary.warning ?? 0}, failed=${summary.failed ?? 0}, total=${summary.total ?? payload.checks?.length ?? 0}`);
  for (const check of payload.checks || []) {
    const state = String(check.status || check.severity || "unknown").toUpperCase();
    console.log(`- [${state}] ${check.id || check.name}: ${check.summary || check.message || ""}`);
  }
  const actions = payload.repairPlan?.actions || [];
  if (actions.length) {
    console.log("Repair suggestions:");
    for (const action of actions) {
      console.log(`- ${action.title || action.id}${action.safe === false ? " (needs explicit approval)" : ""}`);
    }
  }
}

export function renderConfigDomains(result) {
  console.log(`Config domains (${result.source})`);
  for (const item of result.domains || []) {
    console.log(`- ${item.domain}${item.title && item.title !== item.domain ? `: ${item.title}` : ""}`);
  }
}

export function renderMcpServers(result) {
  console.log(`MCP servers (${result.source})`);
  if (!result.servers.length) {
    console.log("- none");
    return;
  }
  for (const server of result.servers) {
    console.log(`- ${server.name}: ${server.type}${server.disabled ? " (disabled)" : ""}`);
  }
}

export function renderMcpStatus(result) {
  console.log(`MCP status (${result.source})`);
  const servers = result.payload?.servers || result.servers || [];
  if (result.message) console.log(result.message);
  if (!servers.length) {
    console.log("- none");
    return;
  }
  for (const server of servers) {
    const name = server.name || server.id || "unknown";
    const state = server.status || server.state || (server.disabled ? "disabled" : "configured");
    console.log(`- ${name}: ${state}`);
  }
}

export function renderModelRoles(result) {
  console.log(`Model roles (${result.source})`);
  const roles = result.roles || {};
  const entries = Object.entries(roles).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) {
    console.log("- none");
    return;
  }
  for (const [role, modelRef] of entries) {
    console.log(`- ${role}: ${modelRef}`);
  }
}

export function renderPhoneManifest(result) {
  console.log(`Phone connection manifest (${result.source})`);
  const manifest = result.manifest || {};
  console.log(`serverId: ${manifest.serverId || ""}`);
  console.log(`instanceId: ${manifest.instanceId || ""}`);
  const urls = manifest.adminUrls || [];
  if (!urls.length) {
    console.log("adminUrls: none");
    return;
  }
  console.log("adminUrls:");
  for (const url of urls) console.log(`- ${url}`);
}
