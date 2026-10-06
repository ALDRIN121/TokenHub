const codex = new URL('../assets/agents/codex.png', import.meta.url).href;
const claude = new URL('../assets/agents/claude.png', import.meta.url).href;
const copilot = new URL('../assets/agents/copilot.svg', import.meta.url).href;
const hermes = new URL('../assets/agents/hermes.svg', import.meta.url).href;
const antigravity = new URL('../assets/agents/antigravity.svg', import.meta.url).href;
const artwork: Record<string, string> = { codex, claude_code: claude, vscode_copilot: copilot, hermes, antigravity };
export function AgentIcon({ provider }: { provider: string }) {
  return artwork[provider] ? <img className="agent-icon" src={artwork[provider]} alt="" aria-hidden="true" width={24} height={24} /> : null;
}
