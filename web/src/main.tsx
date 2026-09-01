/**
 * main.tsx — React entry.
 *
 * The theme is owned by `useThemeStore` (Zustand). We wrap the app
 * in `ConfigProvider` and pick the right AntD algorithm per mode.
 * The CSS body class is also toggled so the `prefers-color-scheme`
 * media query and any global styles in App.css react correctly.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider, theme, App as AntdApp } from 'antd';

import App from './App';
import { useThemeStore } from './stores/themeStore';
import { toAntdTokens, DARK, LIGHT } from './styles/theme';
import './App.css';

const RootShell: React.FC = () => {
  // Subscribe to the theme store so ConfigProvider re-renders on
  // toggle. (AntD doesn't pick up token changes otherwise.)
  const mode = useThemeStore((s) => s.mode);
  const tokens = mode === 'dark' ? DARK : LIGHT;
  // Apply the body class so our App.css can flip a few things.
  React.useEffect(() => {
    document.body.dataset.theme = mode;
    document.body.style.background = tokens.bgBase;
    document.body.style.color = tokens.labelPrimary;
  }, [mode, tokens.bgBase, tokens.labelPrimary]);
  return (
    <ConfigProvider
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
    <RootShell />
  </React.StrictMode>,
);
