/**
 * App — top-level router.
 *
 * Layout:
 *   /             → Chat (default landing; sidebar with project picker)
 *   /chat         → Chat (new conversation)
 *   /chat/:sid    → Chat (specific session)
 *   /today        → Today (stats + recent activity)
 *   /settings     → Settings
 *   /projects     → Projects (legacy page, kept for project CRUD)
 *   /loop         → Loop (legacy page, kept for advanced diagnostics)
 */
import React, { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

import AppLayout from './components/AppLayout';
import Chat from './pages/Chat';
import Today from './pages/Today';
import SettingsPage from './pages/Settings';
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
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/projects" element={<ProjectPage />} />
        <Route path="/loop" element={<Loop />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
};

export default App;
