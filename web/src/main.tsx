/**
 * main.tsx — React entry.
 *
 * The theme is owned by `useThemeStore` (Zustand). We wrap the app
 * in `ConfigProvider` and pick the right AntD algorithm per mode.
 * The CSS body class is also toggled so the `prefers-color-scheme`
 * media query and any global styles in App.css react correctly.
 *
 * i18n: `<I18nProvider>` wraps everything and drives AntD's own locale
 * (date pickers, pagination, empty states) through `ConfigProvider`.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider, theme, App as AntdApp } from 'antd';

import App from './App';
import { I18nProvider, useI18n } from './i18n';
import { useThemeStore } from './stores/themeStore';
import { toAntdTokens, DARK, LIGHT } from './styles/theme';
import './App.css';

const RootShell: React.FC = () => {
  // Subscribe to the theme store so ConfigProvider re-renders on
  // toggle. (AntD doesn't pick up token changes otherwise.)
  const mode = useThemeStore((s) => s.mode);
  // Language: re-render on switch so AntD's locale flips too.
  // Language drives AntD's locale *and* its direction (RTL locales mirror
  // dropdowns, Select arrows and Modal controls).
  const { antdLocale, dir } = useI18n();
  const tokens = mode === 'dark' ? DARK : LIGHT;
  // Apply the body class so our App.css can flip a few things.
  React.useEffect(() => {
    document.body.dataset.theme = mode;
    document.body.style.background = tokens.bgBase;
    document.body.style.color = tokens.labelPrimary;
  }, [mode, tokens.bgBase, tokens.labelPrimary]);
  return (
    <ConfigProvider
      locale={antdLocale}
      direction={dir}
      theme={{
        algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: toAntdTokens(tokens),
      }}
    >
      <AntdApp>
        <BrowserRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <App />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
};

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <RootShell />
    </I18nProvider>
  </React.StrictMode>,
);
