/**
 * useThemeTokens — read the current theme's design tokens.
 *
 * Subscribes to the theme store so components re-render when the
 * user toggles dark/light. Tokens are read-only; mutating them is
 * not supported (use the theme store to change the mode).
 */
import { useThemeStore } from '../stores/themeStore';
import { tokensFor, type ThemeTokens } from '../styles/theme';

export function useThemeTokens(): ThemeTokens {
  const mode = useThemeStore((s) => s.mode);
  return tokensFor(mode);
}
