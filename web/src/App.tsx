/**
 * App — top-level router.
 *
 * R38.6.3: simplified routing. The 4 obsolete pages
 * (Today / Tools / Loop / Trace / Project) all redirect to
 * /chat. Their page components are still in the codebase
 * (referenced from the redirect handlers) but the imports
 * are gone so they don't bloat the bundle. Settings is now
 * an avatar-dropdown drawer; the legacy /settings URL just
 * redirects to /chat as well.
 */
import React, { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

import AppLayout from './components/AppLayout';
import Chat from './pages/Chat';
import Today from './pages/Today';
import ProjectPage from './pages/Project';
import Loop from './pages/Loop';
import Trace from './pages/Trace';
import Tools from './pages/Tools';
import { connectWebSocket } from './api/client';

const App: React.FC = () => {
  useEffect(() => { connectWebSocket(); }, []);
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:sessionId" element={<Chat />} />
        <Route path="/today" element={<Today />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/tools/:tool" element={<Tools />} />
        <Route path="/trace" element={<Navigate to="/chat" replace />} />
        <Route path="/trace/:projectId" element={<Trace />} />
        <Route path="/trace/:projectId/:sessionId" element={<Trace />} />
        <Route path="/settings" element={<Navigate to="/chat" replace />} />
        <Route path="/projects" element={<ProjectPage />} />
        <Route path="/loop" element={<Loop />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
};

export default App;
