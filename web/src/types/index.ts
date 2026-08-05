export interface AgentState {
  agent_id: string;
  name: string;
  role: string;
  status: 'idle' | 'thinking' | 'acting' | 'error';
  current_task?: string;
  model: string;
  last_activity: number;
  message_count: number;
  // Live progress — `current_turn` / `total_turns` show where the agent
  // is in its tool-calling loop; `current_tool` is the tool name when
  // status === 'acting'. Optional so older server builds (pre-progress
  // fields) still typecheck.
  current_turn?: number;
  total_turns?: number;
  current_tool?: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  workspace: string;
  status: string;
  task_count: number;
  agent_count: number;
  created_at: number;
  // Optional — present on detail fetches, may be absent on the list view.
  work_dir?: string;
  requirements?: string;
  files?: ProjectFile[];
}

export interface ProjectFile {
  id: string;
  name: string;
  mime: string;
  size: number;
  uploaded_at: number;
}

export interface Message {
  id: string;
  sender: string;
  receiver: string;
  topic: string;
  content: string | Record<string, unknown>;
  msg_type: string;
  timestamp: number;
  metadata: Record<string, unknown>;
}

export interface ReviewIssue {
  category: 'CRITICAL' | 'MAJOR' | 'MINOR' | 'SUGGESTION';
  file: string;
  line: number;
  description: string;
  code_snippet: string;
  suggestion: string;
}

export interface ReviewReport {
  project_path: string;
  files_reviewed: number;
  total_issues: number;
  critical_count: number;
  major_count: number;
  minor_count: number;
  suggestion_count: number;
  overall_score: number;
  file_reviews: { file_path: string; issues: ReviewIssue[]; score: number }[];
  summary: string;
}

export interface ModelConfig {
  models: string[];
  role_mappings: Record<string, string>;
}

export interface DashboardData {
  project_count: number;
  agent_count: number;
  agents: AgentState[];
  recent_messages: Message[];
}


// ---------------------------------------------------------------- Loop page types

export interface LoopRoundEntry {
  round: number;
  score: number;
  approve: boolean;
  issues: number;
  summary: string;
  ts: number;
}

export interface LoopStats {
  running: boolean;
  rounds: LoopRoundEntry[];
  score_window: number[];
  total_tokens_used: number;
  approximate_cost_usd: number;
  infra_failure_streak: number;
  no_progress_count: number;
  loop_health?: number;
}

export interface LoopState {
  running: boolean;
  session_id?: string;
  round: number;
  last_score: number;
  last_approve: boolean;
  no_progress_count: number;
  history: any[];
}

export interface PlanState {
  pending: boolean;
  decision: string | null;
  text: string;
  round: number;
}

export interface PlanVisualization {
  mermaid: string;
  file_tree: string;
  round: number;
}

export interface AskState {
  pending: boolean;
  question: string;
  context: string;
  round: number;
}

export interface Checkpoint {
  sha: string;
  round: number;
  score: number;
  approved: boolean;
  summary: string;
  ts: number;
}

export interface RoundDiff {
  from_round: number;
  to_round: number;
  from_sha?: string;
  to_sha?: string;
  files: Array<{ path: string; added: number; removed: number }>;
  patch: string;
  available: boolean;
}

export interface CodeComment {
  title: string;
  body: string;
  file: string;
  start: number;
  end: number;
  priority: 0 | 1 | 2 | 3;
  metadata: {
    project_id: string;
    round: number;
    category: string;
    severity: 'CRITICAL' | 'MAJOR' | 'MINOR' | 'SUGGESTION';
  };
}

export interface RoundComments {
  round: number;
  comments: CodeComment[];
  jsonl: string;
}

export interface Issue {
  category: string;
  severity: 'CRITICAL' | 'MAJOR' | 'MINOR' | 'SUGGESTION';
  file?: string;
  line?: number;
  description: string;
  fix_instruction: string;
  _source_reviewer?: string;
}

export interface LoopConfig {
  specialists: string[];
  best_of_n: number;
}