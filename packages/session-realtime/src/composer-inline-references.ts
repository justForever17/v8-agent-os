export type ComposerInlineReferenceKind = "command" | "skill" | "subagent_family" | "plugin";

export type ComposerInlineReference = {
  kind: ComposerInlineReferenceKind;
  id: string;
  label: string;
};

export type ComposerPresentation = {
  text: string;
  references: ComposerInlineReference[];
};

export type ComposerInlineSegment = {
  type: "text" | "reference";
  text: string;
  start: number;
  end: number;
  reference?: ComposerInlineReference;
};

export type ComposerInlineQuery = {
  kind: "command" | "mention";
  start: number;
  end: number;
  query: string;
};

export type ComposerReferenceDeletion = {
  text: string;
  caret: number;
  removedReferenceIds: string[];
};

const QUERY_BOUNDARY = /[\s([{（【]/;
const QUERY_TERMINATOR = /[\r\n@/，。！？!?；;]/;

export function composerReferenceToken(reference: ComposerInlineReference): string {
  const label = String(reference.label || "").trim().replace(/^[@/]+/, "");
  return `${reference.kind === "command" ? "/" : "@"}${label}`;
}

function referenceTokens(references: readonly ComposerInlineReference[]) {
  return references
    .map((reference) => ({ reference, token: composerReferenceToken(reference) }))
    .filter((item) => item.token.length > 1)
    .sort((left, right) => right.token.length - left.token.length);
}

export function buildComposerInlineSegments(
  text: string,
  references: readonly ComposerInlineReference[],
): ComposerInlineSegment[] {
  const value = String(text || "");
  const tokens = referenceTokens(references);
  if (!value || tokens.length === 0) {
    return value ? [{ type: "text", text: value, start: 0, end: value.length }] : [];
  }

  const matches: Array<{ start: number; end: number; reference: ComposerInlineReference; token: string }> = [];
  for (const { reference, token } of tokens) {
    let fromIndex = 0;
    while (fromIndex < value.length) {
      const start = value.indexOf(token, fromIndex);
      if (start < 0) break;
      matches.push({ start, end: start + token.length, reference, token });
      fromIndex = start + token.length;
    }
  }
  matches.sort((left, right) => left.start - right.start || right.token.length - left.token.length);

  const segments: ComposerInlineSegment[] = [];
  let cursor = 0;
  for (const match of matches) {
    if (match.start < cursor) continue;
    if (match.start > cursor) {
      segments.push({ type: "text", text: value.slice(cursor, match.start), start: cursor, end: match.start });
    }
    segments.push({
      type: "reference",
      text: match.token,
      start: match.start,
      end: match.end,
      reference: match.reference,
    });
    cursor = match.end;
  }
  if (cursor < value.length) {
    segments.push({ type: "text", text: value.slice(cursor), start: cursor, end: value.length });
  }
  return segments;
}

export function resolveComposerInlineQuery(
  text: string,
  caret: number,
  commandSelected: boolean,
  references: readonly ComposerInlineReference[] = [],
): ComposerInlineQuery | null {
  const value = String(text || "");
  const safeCaret = Math.max(0, Math.min(value.length, Number.isFinite(caret) ? caret : value.length));
  const searchStart = Math.max(0, safeCaret - 80);
  const referenceStarts = new Set(
    buildComposerInlineSegments(value, references)
      .filter((segment) => segment.type === "reference")
      .map((segment) => segment.start),
  );
  for (let index = safeCaret - 1; index >= searchStart; index -= 1) {
    const character = value[index];
    if (character === "\n" || character === "\r") break;
    if (character !== "@" && character !== "/") continue;
    if (referenceStarts.has(index)) continue;
    const previous = index > 0 ? value[index - 1] : "";
    if (previous && !QUERY_BOUNDARY.test(previous)) continue;
    const query = value.slice(index + 1, safeCaret);
    if (QUERY_TERMINATOR.test(query)) return null;
    if (character === "/" && commandSelected) return null;
    return {
      kind: character === "/" ? "command" : "mention",
      start: index,
      end: safeCaret,
      query,
    };
  }
  return null;
}

export function insertComposerReference(
  text: string,
  query: ComposerInlineQuery,
  reference: ComposerInlineReference,
): { text: string; caret: number } {
  const value = String(text || "");
  const token = composerReferenceToken(reference);
  const before = value.slice(0, query.start);
  const after = value.slice(query.end);
  const separator = after.length === 0 || !/^[\s,.;:!?，。；：！？)}\]】）]/.test(after) ? " " : "";
  return {
    text: `${before}${token}${separator}${after}`,
    caret: before.length + token.length + separator.length,
  };
}

export function stripComposerReferences(
  text: string,
  references: readonly ComposerInlineReference[],
): string {
  const plain = buildComposerInlineSegments(text, references)
    .filter((segment) => segment.type === "text")
    .map((segment) => segment.text)
    .join("");
  return plain
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+([,.;:!?，。；：！？])/g, "$1")
    .replace(/^[ \t]+|[ \t]+$/gm, "")
    .trim();
}

export function removeComposerReferenceAtBackspace(
  text: string,
  references: readonly ComposerInlineReference[],
  selectionStart: number,
  selectionEnd: number,
): ComposerReferenceDeletion | null {
  const value = String(text || "");
  const start = Math.max(0, Math.min(value.length, selectionStart));
  const end = Math.max(start, Math.min(value.length, selectionEnd));
  const referenceSegments = buildComposerInlineSegments(value, references)
    .filter((segment) => segment.type === "reference");

  let removeStart = start;
  let removeEnd = end;
  let matches = referenceSegments.filter((segment) => (
    start === end
      ? (start > segment.start && start <= segment.end)
      : (segment.start < end && segment.end > start)
  ));
  if (start === end && matches.length === 0 && start > 0 && /\s/.test(value[start - 1] || "")) {
    matches = referenceSegments.filter((segment) => segment.end === start - 1);
    if (matches.length > 0) removeEnd = start;
  }
  if (matches.length === 0) return null;

  removeStart = Math.min(removeStart, ...matches.map((segment) => segment.start));
  removeEnd = Math.max(removeEnd, ...matches.map((segment) => segment.end));
  if (removeEnd < value.length && value[removeEnd] === " " && (removeStart === 0 || /\s/.test(value[removeStart - 1] || ""))) {
    removeEnd += 1;
  }
  return {
    text: `${value.slice(0, removeStart)}${value.slice(removeEnd)}`,
    caret: removeStart,
    removedReferenceIds: Array.from(new Set(matches.map((segment) => segment.reference?.id || "").filter(Boolean))),
  };
}

export function composerTextContainsReference(text: string, reference: ComposerInlineReference): boolean {
  return String(text || "").includes(composerReferenceToken(reference));
}
