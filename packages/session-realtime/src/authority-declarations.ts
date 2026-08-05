export type AuthorityRecordDeclaration = {
  record: Record<string, unknown>;
  sessionIds: string[];
  workspaceIds: string[];
};

export type AuthorityDeclarations = {
  records: Record<string, unknown>[];
  recordDeclarations: AuthorityRecordDeclaration[];
  sessionIds: Set<string>;
  workspaceIds: Set<string>;
  conflicted: boolean;
};

export type AliasedText = {
  value: string;
  values: string[];
  conflicted: boolean;
};

const SESSION_ID_KEYS = ["sessionId", "session_id", "conversationId", "conversation_id"] as const;
const WORKSPACE_ID_KEYS = ["workspaceId", "workspace_id"] as const;

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function collectAliasedText(record: Record<string, unknown>, keys: readonly string[]): AliasedText {
  return collectAliasedTextFromRecords([record], keys);
}

export function collectAliasedTextFromRecords(
  records: readonly Record<string, unknown>[],
  keys: readonly string[],
): AliasedText {
  const values = Array.from(new Set(records.flatMap(
    (record) => keys.map((key) => text(record[key])).filter(Boolean),
  )));
  return {
    value: values[0] || "",
    values,
    conflicted: values.length > 1,
  };
}

function appendRecord(target: Record<string, unknown>[], value: unknown): Record<string, unknown> {
  const record = recordOf(value);
  if (Object.keys(record).length) target.push(record);
  return record;
}

function appendRecordFamily(target: Record<string, unknown>[], value: unknown): void {
  const record = appendRecord(target, value);
  if (!Object.keys(record).length) return;
  appendRecord(target, record.lineage);
  appendRecord(target, record.provenance);
}

export function collectAuthorityDeclarations(value: unknown): AuthorityDeclarations {
  const root = recordOf(value);
  const records: Record<string, unknown>[] = [];
  if (!Object.keys(root).length) {
    return {
      records,
      recordDeclarations: [],
      sessionIds: new Set(),
      workspaceIds: new Set(),
      conflicted: false,
    };
  }

  appendRecordFamily(records, root);
  const metadata = recordOf(root.metadata);
  appendRecordFamily(records, metadata);
  for (const rawResourceRef of [root.resourceRef, root.resource_ref]) {
    const resourceRef = recordOf(rawResourceRef);
    if (!Object.keys(resourceRef).length) continue;
    appendRecordFamily(records, resourceRef);
    appendRecordFamily(records, resourceRef.metadata);
  }

  const sessionIds = new Set<string>();
  const workspaceIds = new Set<string>();
  const recordDeclarations = records.map((record) => {
    const directSessionIds = collectAliasedText(record, SESSION_ID_KEYS).values;
    const directWorkspaceIds = collectAliasedText(record, WORKSPACE_ID_KEYS).values;
    for (const sessionId of directSessionIds) sessionIds.add(sessionId);
    for (const workspaceId of directWorkspaceIds) workspaceIds.add(workspaceId);
    return {
      record,
      sessionIds: directSessionIds,
      workspaceIds: directWorkspaceIds,
    };
  });

  return {
    records,
    recordDeclarations,
    sessionIds,
    workspaceIds,
    conflicted: sessionIds.size > 1 || workspaceIds.size > 1,
  };
}
