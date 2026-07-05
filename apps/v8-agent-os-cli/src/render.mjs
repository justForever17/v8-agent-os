export function printJson(payload) {
  console.log(JSON.stringify(payload, null, 2));
}

export function renderStatus(statuses) {
  console.log("V8OS 服务状态");
  for (const item of statuses) {
    const marker = item.state === "managed_running" ? "RUNNING" : item.state === "external_port_in_use" ? "EXTERNAL" : "STOPPED";
    const pid = item.pid ? ` pid=${item.pid}` : "";
    console.log(`- ${item.label}: ${marker} port=${item.port}${pid}`);
  }
}

export function renderStartResults(results) {
  for (const item of results) {
    if (item.status === "started") console.log(`Started ${item.id}: pid=${item.pid}, port=${item.port}`);
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
