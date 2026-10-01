import type { IconName } from './Icon';

export const agents: { id: string; name: string; icon: IconName }[] = [
  { id: 'codex', name: 'Codex', icon: 'terminal' },
  { id: 'claude_code', name: 'Claude Code', icon: 'sparkle' },
  { id: 'hermes', name: 'Hermes Agent', icon: 'hermes' },
  { id: 'vscode_copilot', name: 'VS Code Copilot', icon: 'copilot' },
  { id: 'antigravity', name: 'Antigravity', icon: 'sparkle' },
];
export const agentName = (id: string) => agents.find((agent) => agent.id === id)?.name ?? id;
export const modelName = (name: string | null) => name ?? 'Model not recorded';
