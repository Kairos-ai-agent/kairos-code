/**
 * Design tokens for the ChatGPT-beta / DSH-style UI.
 *
 * Colors and spacing are inspired by:
 *   - DSH Desktop (DeepSeek Harness) — extracted from the bundled
 *     dsh-web-frontend CSS, see the launch screen tokens (--dsh-boot-bg,
 *     --dsh-boot-label-*, etc.)
 *   - ChatGPT beta — left sidebar + main thread + sticky composer
 *
 * AntD's `theme.token` accepts a flat color/spacing object, so we
 * compose the two halves (light + dark) at runtime. The two halves
 * are kept structurally identical — every key in DARK has a
 * counterpart in LIGHT with the same purpose — so consumers can
 * switch at runtime without component re-renders based on which
 * theme they're using.
 */

export type ThemeMode = 'light' | 'dark';

export interface ThemeTokens {
  // Surface
  bgBase: string;          // page background
  bgElevated: string;      // sidebar / top bar background
  bgLay1: string;          // raised cards / inputs
  bgLay2: string;          // hover / active states
  // Borders
  border: string;          // subtle border
  borderStrong: string;    // focus / strong border
  // Text
  labelPrimary: string;    // main text
  labelSecondary: string;  // secondary text
  labelTertiary: string;   // tertiary / hints
  // Accent
  brand: string;           // primary accent (sender-user bubble, links)
  brandHover: string;
  // Roles (chat thread bubbles)
  userBubble: string;      // user message background
  agentBubble: string;     // coder/reviewer/tool message background
  agentBubbleBorder: string;
  toolBubble: string;      // tool call/result block
  reviewerAccent: string;  // reviewer-specific accent
  coderAccent: string;     // coder-specific accent
  // State
  success: string;
  warning: string;
  danger: string;
  // Scrollbar
  scrollbarThumb: string;
  scrollbarThumbHover: string;
}

export const LIGHT: ThemeTokens = {
  bgBase: '#ffffff',
  bgElevated: '#f7f7f8',
  bgLay1: '#ffffff',
  bgLay2: '#ececf1',
  border: 'rgb(0 0 0 / 8%)',
  borderStrong: 'rgb(0 0 0 / 18%)',
  labelPrimary: '#0f1115',
  labelSecondary: '#5b6068',
  labelTertiary: '#8a8f96',
  brand: '#0f1115',
  brandHover: '#2a2d33',
  userBubble: '#f4f4f4',
  agentBubble: '#ffffff',
  agentBubbleBorder: 'rgb(0 0 0 / 8%)',
  toolBubble: '#f7f7f8',
  reviewerAccent: '#7c3aed',  // purple
  coderAccent: '#2563eb',     // blue
  success: '#10a37f',
  warning: '#d97706',
  danger: '#dc2626',
  scrollbarThumb: 'rgb(0 0 0 / 18%)',
  scrollbarThumbHover: 'rgb(0 0 0 / 32%)',
};

export const DARK: ThemeTokens = {
  bgBase: '#0f0f10',          // matches DSH's boot bg #151517 closely
  bgElevated: '#171719',      // sidebar / topbar
  bgLay1: '#1f1f21',          // raised cards
  bgLay2: '#2a2a2d',          // hover / active
  border: 'rgb(255 255 255 / 8%)',
  borderStrong: 'rgb(255 255 255 / 16%)',
  labelPrimary: '#f9fafb',
  labelSecondary: '#cfd3d6',
  labelTertiary: '#8a8f96',
  brand: '#f9fafb',            // DSH default brand is near-white
  brandHover: '#ffffff',
  userBubble: '#2a2a2d',
  agentBubble: '#1f1f21',
  agentBubbleBorder: 'rgb(255 255 255 / 10%)',
  toolBubble: '#171719',
  reviewerAccent: '#a78bfa',  // softer purple on dark
  coderAccent: '#60a5fa',     // softer blue on dark
  success: '#34d399',
  warning: '#fbbf24',
  danger: '#f87171',
  scrollbarThumb: 'rgb(255 255 255 / 14%)',
  scrollbarThumbHover: 'rgb(255 255 255 / 24%)',
};

export function tokensFor(mode: ThemeMode): ThemeTokens {
  return mode === 'dark' ? DARK : LIGHT;
}

/**
 * Convert our design tokens to the shape AntD's `ConfigProvider.theme.token`
 * expects. AntD uses semantic names (colorBgLayout, colorText, colorPrimary,
 * colorBorder, etc.) so we map our flat tokens into those.
 */
export function toAntdTokens(t: ThemeTokens) {
  return {
    colorPrimary: t.brand,
    colorBgLayout: t.bgBase,
    colorBgContainer: t.bgLay1,
    colorBgElevated: t.bgElevated,
    colorBorder: t.border,
    colorBorderSecondary: t.border,
    colorText: t.labelPrimary,
    colorTextSecondary: t.labelSecondary,
    colorTextTertiary: t.labelTertiary,
    colorTextDescription: t.labelTertiary,
    colorSuccess: t.success,
    colorWarning: t.warning,
    colorError: t.danger,
    colorInfo: t.coderAccent,
    borderRadius: 12,
    borderRadiusLG: 16,
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
    // Tighten the default AntD vertical rhythm for a chatty UI.
    controlHeight: 36,
    controlHeightLG: 44,
  };
}

/** Single-source-of-truth layout constants (px). */
export const LAYOUT = {
  topbarHeight: 52,
  sidebarWidth: 280,
  sidebarCollapsedWidth: 0,
  composerMaxWidth: 768,
  threadMaxWidth: 768,
};
