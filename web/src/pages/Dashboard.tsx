import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Tag, List, Typography, Space } from 'antd';
import { TeamOutlined, ProjectOutlined, MessageOutlined, ThunderboltOutlined } from '@ant-design/icons';
import api from '../api/client';
import { useAgentStore } from '../stores/agentStore';
import type { DashboardData, AgentState } from '../types';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  idle: 'default',
  thinking: 'processing',
  acting: 'processing',
  error: 'error',
};

const roleColors: Record<string, string> = {
  team_leader: 'blue',
  product_manager: 'green',
  architect: 'purple',
  frontend_dev: 'cyan',
  backend_dev: 'orange',
  qa_engineer: 'magenta',
  code_reviewer: 'red',
  devops: 'lime',
};

const Dashboard: React.FC = () => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const { setAgents, setMessages } = useAgentStore();

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await api.get('/dashboard');
        setData(res.data);
        setAgents(res.data.agents);
        setMessages(res.data.recent_messages);
      } catch (e) {
        if (window.location.hostname === 'localhost') console.error('Failed to load dashboard:', e);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  return (
    <div>
      <Title level={3}>Dashboard</Title>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Projects"
              value={data?.project_count ?? 0}
              prefix={<ProjectOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Active Agents"
              value={data?.agent_count ?? 0}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Messages"
              value={data?.recent_messages?.length ?? 0}
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="System Status"
              value="Online"
              prefix={<ThunderboltOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={14}>
          <Card title="Agent Team" loading={loading}>
            <Row gutter={[12, 12]}>
              {(data?.agents ?? []).map((agent: AgentState) => (
                <Col span={12} key={agent.agent_id}>
                  <Card size="small" hoverable>
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      <Space>
                        <Text strong>{agent.name}</Text>
                        <Tag color={roleColors[agent.role] || 'default'}>{agent.role}</Tag>
                      </Space>
                      <Space>
                        <Tag color={statusColors[agent.status]}>{agent.status}</Tag>
                        {agent.current_turn && agent.total_turns ? (
                          <Tag color="cyan">{agent.current_turn}/{agent.total_turns}</Tag>
                        ) : null}
                        {agent.current_tool ? (
                          <Tag color="geekblue">{agent.current_tool}</Tag>
                        ) : null}
                        <Text type="secondary" style={{ fontSize: 12 }}>{agent.model}</Text>
                      </Space>
                      {agent.current_task && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          Task: {agent.current_task}
                        </Text>
                      )}
                    </Space>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
        <Col span={10}>
          <Card title="Recent Messages" loading={loading}>
            <List
              size="small"
              dataSource={(data?.recent_messages ?? []).slice(-10).reverse()}
              renderItem={(msg: any) => (
                <List.Item>
                  <Space direction="vertical" size={0} style={{ width: '100%' }}>
                    <Space>
                      <Tag color="blue">{msg.sender}</Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {new Date(msg.timestamp * 1000).toLocaleTimeString()}
                      </Text>
                    </Space>
                    <Text style={{ fontSize: 13 }}>
                      {typeof msg.content === 'string'
                        ? msg.content.slice(0, 120)
                        : JSON.stringify(msg.content).slice(0, 120)}
                    </Text>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
