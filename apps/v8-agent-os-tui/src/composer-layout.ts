/**
 * The composer sits right above the bottom hint row in the alternate-screen viewport.
 * Using bottom-anchored coordinates guarantees that the cursor stays locked to the
 * prompt row (`> `) even when notices wrap, dynamic suggestions expand, or the
 * window resizes under Windows ConPTY.
 */
export type ComposerCursorInput = {
  columns: number;
  rows?: number;
  historyHeight?: number;
  overlayHeight?: number;
  inputRow: number;
  inputColumn: number;
  inputOffset: number;
  inputHeight?: number;
  hintRows?: number;
  headerRows?: number;
  noticeRows?: number;
  labelRows?: number;
  dividerRows?: number;
  promptWidth?: number;
};

export function composerCursorPosition(input: ComposerCursorInput) {
  const maxColumn = Math.max(0, (input.columns || 1) - 1);
  const maxPromptColumn = Math.max(0, (input.promptWidth ?? input.columns) - 1);
  const column = Math.min(maxPromptColumn, Math.max(0, 2 + input.inputColumn));
  const currentRowOffset = Math.max(0, input.inputRow - Math.max(0, input.inputOffset));

  // If total terminal rows is supplied, anchor directly to the bottom.
  // The layout from bottom is: [inputHeight rows] followed by [hintRows rows].
  if (typeof input.rows === 'number' && input.rows > 0) {
    const hintRows = Math.max(0, input.hintRows ?? 1);
    const inputHeight = Math.max(1, input.inputHeight ?? 1);
    const targetY = Math.max(0, input.rows - hintRows - inputHeight + currentRowOffset);
    return {
      x: Math.min(maxColumn, column),
      y: targetY,
    };
  }

  // Fallback top-relative calculation for partial-tree mocks or isolated unit tests.
  const rowsBeforeInput = Math.max(0, input.headerRows ?? 2)
    + Math.max(0, input.historyHeight ?? 0)
    + Math.max(0, input.noticeRows ?? 1)
    + Math.max(0, input.labelRows ?? 1)
    + Math.max(0, input.dividerRows ?? 1)
    + Math.max(0, input.overlayHeight ?? 0);

  return {
    x: Math.min(maxColumn, column),
    y: Math.max(0, rowsBeforeInput + currentRowOffset),
  };
}
