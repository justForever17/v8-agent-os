export type ThemeName = 'auto' | 'dark' | 'light' | 'high-contrast' | 'mono';

export type ThemeTokens = {
  name: Exclude<ThemeName, 'auto'>;
  text: string; muted: string; border: string; selected: string;
  success: string; warning: string; error: string; approval: string; code: string;
  ansi: boolean;
};

const themes: Record<Exclude<ThemeName, 'auto'>, ThemeTokens> = {
  dark: { name: 'dark', text: 'white', muted: 'gray', border: 'gray', selected: 'cyan', success: 'green', warning: 'yellow', error: 'red', approval: 'magenta', code: 'blue', ansi: true },
  light: { name: 'light', text: 'black', muted: 'gray', border: 'gray', selected: 'blue', success: 'green', warning: 'yellow', error: 'red', approval: 'magenta', code: 'cyan', ansi: true },
  'high-contrast': { name: 'high-contrast', text: 'white', muted: 'white', border: 'white', selected: 'yellow', success: 'green', warning: 'yellow', error: 'red', approval: 'magenta', code: 'cyan', ansi: true },
  mono: { name: 'mono', text: 'white', muted: 'white', border: 'white', selected: 'white', success: 'white', warning: 'white', error: 'white', approval: 'white', code: 'white', ansi: false },
};

export function normalizeTheme(value: unknown): ThemeName {
  return value === 'dark' || value === 'light' || value === 'high-contrast' || value === 'mono' || value === 'auto' ? value : 'auto';
}

/** Resolve auto from the terminal hint. NO_COLOR always wins and never emits color. */
export function resolveTheme(value: unknown, env: NodeJS.ProcessEnv = process.env): ThemeTokens {
  const requested = normalizeTheme(value);
  if (requested === 'mono' || env.NO_COLOR !== undefined) return themes.mono;
  if (requested !== 'auto') return themes[requested];
  return env.COLORFGBG?.startsWith('15;') ? themes.light : themes.dark;
}

export const themeNames: readonly ThemeName[] = ['auto', 'dark', 'light', 'high-contrast', 'mono'];

/** Pure local action for a settings page or `/settings` shortcut. */
export function nextTheme(value: unknown): ThemeName {
  const index = themeNames.indexOf(normalizeTheme(value));
  return themeNames[(index + 1) % themeNames.length];
}
