import React, { useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography } from 'antd';
import {
  DashboardOutlined,
  ProjectOutlined,
  SettingOutlined,
  SyncOutlined,
} from '@ant-design/icons';

import Dashboard from './pages/Dashboard';
import ProjectPage from './pages/Project';
import Loop from './pages/Loop';
import SettingsPage from './pages/Settings';
import { connectWebSocket } from './api/client';

const { Sider, Content } = Layout;
const { Title } = Typography;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
  { key: '/projects', icon: <ProjectOutlined />, label: 'Projects' },
  { key: '/loop', icon: <SyncOutlined />, label: 'Loop Review' },
  { key: '/settings', icon: <SettingOutlined />, label: 'Settings' },
];

const App: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    connectWebSocket();
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={180} theme="dark" collapsedWidth={0} breakpoint="lg">
        <div style={{ padding: '12px 16px' }}>
          <Title level={5} style={{ color: '#fff', margin: 0, fontSize: 14 }}>
            Kairos Code
          </Title>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Content style={{ padding: '12px 16px', overflow: 'auto', background: '#0a0a0a' }}>
        <Routes location={location} key={location.pathname}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<ProjectPage />} />
          <Route path="/loop" element={<Loop />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Content>
    </Layout>
  );
};

export default App;