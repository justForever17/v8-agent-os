import stringWidth from 'string-width';
import type { ThemeTokens } from './theme.js';

export type WelcomeArtVariant = 'prism' | 'ribbon' | 'mark' | 'banner';
export type WelcomeToken = keyof ThemeTokens;

export type WelcomeArtSegment = { text: string; token: WelcomeToken };
export type WelcomeArtRow = { raw: string; width: number; segments: readonly WelcomeArtSegment[] };
export type WelcomeArtProjection = {
  variant: 'prism' | 'ribbon'; compact: boolean; rows: readonly WelcomeArtRow[];
  lines: string[]; tokens: Array<WelcomeToken | undefined>; screenReader: string;
};
type RowSpec = { text: string; highlights?: Array<{ start: number; end: number; token: WelcomeToken }> };

const prism: readonly RowSpec[] = [
  { text: ' ╔════════════════════════════════════════════════════════════════════╗', highlights: [{ start: 0, end: 71, token: 'border' }] },
  { text: ' ║  ██╗   ██╗ ▄████▄    █████╗  ██████╗ ███████╗███╗   ██╗████████╗   ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ║  ██║   ██║██╔═══██╗ ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝   ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ║  ██║   ██║╚██████╔╝ ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║      ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ║  ╚██╗ ██╔╝██╔═══██╗ ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║      ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ║   ╚████╔╝ ╚██████╔╝ ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║      ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ║    ╚═══╝   ╚═════╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝      ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 25, token: 'selected' }, { start: 25, end: 29, token: 'muted' },
    { start: 29, end: 67, token: 'code' }, { start: 67, end: 71, token: 'border' },
  ] },
  { text: ' ╚══════  A G E N T   O S   ·   L O C A L   E N G I N E  ═════════════╝', highlights: [
    { start: 0, end: 9, token: 'border' }, { start: 9, end: 26, token: 'success' }, { start: 26, end: 31, token: 'muted' },
    { start: 31, end: 56, token: 'code' }, { start: 56, end: 71, token: 'border' },
  ] },
];
const ribbon: readonly RowSpec[] = [
  { text: ' ╔═════════════════════════════════════════════════════════════╗', highlights: [{ start: 0, end: 64, token: 'border' }] },
  { text: ' ║  V8 AGENT OS  ·  Autonomous Local Engine Control Plane      ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 15, token: 'selected' }, { start: 15, end: 18, token: 'muted' },
    { start: 18, end: 59, token: 'text' }, { start: 59, end: 64, token: 'border' },
  ] },
  { text: ' ║  ─────────────────────────────────────────────────────────  ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 59, token: 'muted' }, { start: 59, end: 64, token: 'border' },
  ] },
  { text: ' ║  ██╗   ██╗ ▄████▄    Supervisor · Action Kernel · Swarm     ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 24, token: 'selected' }, { start: 24, end: 58, token: 'code' }, { start: 58, end: 64, token: 'border' },
  ] },
  { text: ' ║  ╚████╔╝  ╚██████╔╝  Conversation-first Terminal Surface    ║', highlights: [
    { start: 0, end: 4, token: 'border' }, { start: 4, end: 24, token: 'selected' }, { start: 24, end: 58, token: 'success' }, { start: 58, end: 64, token: 'border' },
  ] },
  { text: ' ╚═════════════════════════════════════════════════════════════╝', highlights: [{ start: 0, end: 64, token: 'border' }] },
];
const specs: Record<'prism' | 'ribbon', readonly RowSpec[]> = { prism, ribbon };
const compactText = 'V8 AGENT OS';

/** Plain rows remain exported for fixtures and non-Ink consumers. */
export const welcomeArtCandidates: Record<'prism' | 'ribbon', readonly string[]> = {
  prism: prism.map(row => row.text), ribbon: ribbon.map(row => row.text),
};

function canonicalVariant(variant: WelcomeArtVariant): 'prism' | 'ribbon' {
  return variant === 'ribbon' || variant === 'banner' ? 'ribbon' : 'prism';
}
function rowFromSpec(spec: RowSpec): WelcomeArtRow {
  const highlights = [...(spec.highlights || [])].sort((a, b) => a.start - b.start);
  const segments: WelcomeArtSegment[] = [];
  let cursor = 0;
  for (const highlight of highlights) {
    const start = Math.max(cursor, Math.min(spec.text.length, highlight.start));
    const end = Math.max(start, Math.min(spec.text.length, highlight.end));
    if (start > cursor) segments.push({ text: spec.text.slice(cursor, start), token: 'muted' });
    if (end > start) segments.push({ text: spec.text.slice(start, end), token: highlight.token });
    cursor = end;
  }
  if (cursor < spec.text.length) segments.push({ text: spec.text.slice(cursor), token: 'muted' });
  const raw = segments.map(segment => segment.text).join('');
  return { raw, width: stringWidth(raw), segments };
}
export function welcomeTextRow(text: string, token: WelcomeToken = 'text'): WelcomeArtRow {
  return { raw: text, width: stringWidth(text), segments: [{ text, token }] };
}
export function welcomeArtWidth(variant: WelcomeArtVariant = 'prism'): number {
  return Math.max(...welcomeArtCandidates[canonicalVariant(variant)].map(line => stringWidth(line)));
}

/** Build a color-token projection without embedding ANSI escape sequences. */
export function welcomeArtProjection(columns: number, compact = false, variant: WelcomeArtVariant = 'prism'): WelcomeArtProjection {
  const width = Math.max(1, Math.floor(columns));
  const chosen = canonicalVariant(variant);
  const isCompact = compact || width < welcomeArtWidth(chosen);
  const rows = isCompact ? [welcomeTextRow(compactText.slice(0, width), 'selected')] : specs[chosen].map(rowFromSpec);
  const lines = rows.map(row => row.raw);
  return { variant: chosen, compact: isCompact, rows, lines, tokens: rows.map(row => row.segments[0]?.token), screenReader: isCompact ? 'V8 Agent OS, compact mark' : `V8 Agent OS, ${chosen} welcome mark` };
}

/** Backwards-compatible plain-text view for callers that do not render tokens. */
export function welcomeArt(columns: number, compact = false, variant: WelcomeArtVariant = 'prism'): string[] {
  return welcomeArtProjection(columns, compact, variant).lines;
}
