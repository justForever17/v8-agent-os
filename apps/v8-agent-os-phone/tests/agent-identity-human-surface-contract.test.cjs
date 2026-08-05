const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const phoneRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(phoneRoot, relativePath), "utf8");
}

function loadChatState() {
  const filename = path.join(phoneRoot, "src/lib/chat-state.ts");
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText;
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded.require = (id) => {
    if (id === "@v8/session-realtime") {
      return { mergeTimelineNodesByIdentity: (_base, incoming) => incoming };
    }
    if (id === "@/src/lib/locale") {
      return {
        translateCurrent: (key) => key.endsWith("text_2") ? "Lead" : "Supervisor",
      };
    }
    return require(id);
  };
  loaded._compile(output, filename);
  return loaded.exports;
}

test("history identity in the Admin envelope outranks canonical placeholders", () => {
  const { normalizeMessagesForState } = loadChatState();
  const [message] = normalizeMessagesForState([{
    id: "message-1",
    role: "assistant",
    content: "done",
    agentName: "智能主管",
    agentAvatar: "/brand-mark.png",
    agentRoleLabel: "主理人",
    metadata: {
      agent: {
        name: "Mira",
        avatar: "/Avatar/mira.png",
        roleLabel: "创意总监",
      },
    },
  }]);

  assert.equal(message.agentName, "Mira");
  assert.equal(message.agentAvatar, "/Avatar/mira.png");
  assert.equal(message.agentRoleLabel, "创意总监");
});

test("live merge cannot replace a configured identity with a canonical placeholder", () => {
  const { normalizeMessagesForState } = loadChatState();
  const [message] = normalizeMessagesForState([
    {
      id: "assistant-local",
      role: "assistant",
      runId: "run-1",
      content: "",
      agentName: "Mira",
      agentAvatar: "/Avatar/mira.png",
      agentRoleLabel: "创意总监",
    },
    {
      id: "assistant-server",
      role: "assistant",
      runId: "run-1",
      content: "done",
      agentName: "智能主管",
      agentAvatar: "/brand-mark.png",
      agentRoleLabel: "主理人",
    },
  ]);

  assert.equal(message.agentName, "Mira");
  assert.equal(message.agentAvatar, "/Avatar/mira.png");
  assert.equal(message.agentRoleLabel, "创意总监");
});

test("overview and optimistic placeholders resolve authoritative profile assets", () => {
  const overview = read("src/components/chat/SessionOverviewPanel.tsx");
  const screen = read("src/screens/ChatScreen.tsx");

  assert.match(overview, /resolveAdminAssetUrl\(adminBaseUrl, item\.avatar\)/);
  assert.match(overview, /<SubagentReturnItem[^>]*adminBaseUrl=\{adminBaseUrl\}/);
  assert.match(screen, /realtimeMessageStateRef\.current\.activeAgentProfile/);
  assert.match(screen, /agentName: agentProfile\?\.agentName/);
  assert.match(screen, /agentAvatar: agentProfile\?\.agentAvatar/);
  assert.match(screen, /agentRoleLabel: agentProfile\?\.agentRoleLabel/);
});
