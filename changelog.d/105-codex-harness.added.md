- **Codex sessions.** An agent whose harness is `codex` runs `codex app-server`
  and behaves like a Claude or OpenCode session: streamed text, mid-turn prompts
  through `turn/steer`, interrupts, resume, aegis tools over MCP, subagents as
  Task rows, and model, effort and permission changes that need no restart unless the
  provider changes. The
  card prices OpenAI models at their published rates and free OpenRouter models
  at zero. A model is `provider/model`, such as `openai/gpt-6.1-sol` or
  `openrouter/nvidia/nemotron-3-super-120b-a12b:free`.
