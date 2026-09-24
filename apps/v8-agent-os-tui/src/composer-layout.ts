/**
 * The composer is rendered after two header rows, the transcript viewport,
 * one notice row, one label row and one divider row. Keep that contract in a
 * pure function so cursor placement and the Ink tree use the same accounting.
 */
export type ComposerCursorInput = {
  columns: number;
  historyHeight: number;
  overlayHeight: number;
  inputRow: number;
  inputColumn: number;
  inputOffset: number;
  headerRows?: number;
  noticeRows?: number;
  labelRows?: number;
  dividerRows?: number;
  promptWidth?: number;
};

export function composerCursorPosition(input: ComposerCursorInput) {
  const rowsBeforeInput = Math.max(0, input.headerRows ?? 2)
    + Math.max(0, input.historyHeight)
    + Math.max(0, input.noticeRows ?? 1)
    + Math.max(0, input.labelRows ?? 1)
    + Math.max(0, input.dividerRows ?? 1)
    + Math.max(0, input.overlayHeight);
  const maxColumn = Math.max(0, (input.columns || 1) - 1);
  const maxPromptColumn = Math.max(0, (input.promptWidth ?? input.columns) - 1);
  const column = Math.min(maxPromptColumn, Math.max(0, 2 + input.inputColumn));
  return {
    x: Math.min(maxColumn, column),
    y: Math.max(0, rowsBeforeInput + input.inputRow - Math.max(0, input.inputOffset)),
  };
}
