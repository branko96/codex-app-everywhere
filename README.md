# Codex App — Cross-Platform Proxy Stack

<p align="left">
  <img src="https://img.shields.io/badge/platform-linux_|_macos-2ea44f" alt="Linux & macOS" />
  <img src="https://img.shields.io/badge/status-active-1f6feb" alt="Status" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

Run Codex Desktop/CLI with **DeepSeek V4** (flash & pro) via a local proxy stack instead of OpenAI.

## Architecture

```
Codex Desktop / Claude Code (compiled from source)
      │
      │  OPENAI_BASE_URL=http://localhost:4000/v1
      ▼
┌─────────────────────────────────────────────────┐
│  codex_proxy.py (:4000)                         │
│  - Model routing (gpt-5.x / o3 / o1 → DeepSeek) │
│  - Responses API → Chat Completions conversion   │
│  - reasoning_content injection (thinking mode)   │
│  - Filters unsupported tool types                │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  LiteLLM (:4001)                                │
│  - Routes to DeepSeek API                       │
│  - Uses openai/ provider with custom base URL   │
└────────────────────┬────────────────────────────┘
                     │
              DeepSeek API
         deepseek-v4-flash / deepseek-v4-pro
```

## Model Mapping

| Codex UI selects | Routes to |
|---|---|
| `deepseek-v4-flash` | deepseek-v4-flash |
| `deepseek-v4-pro` | deepseek-v4-pro |
| `gpt-4o-mini`, `gpt-5.4-mini`, `gpt-5.5-mini` | deepseek-v4-flash |
| `gpt-4o`, `gpt-5`, `gpt-5.4`, `gpt-5.5`, `o3`, `o1` | deepseek-v4-pro |

---

## Setup — macOS

### 1. Prerequisites

```bash
python3 -m venv ~/.codex/venv
~/.codex/venv/bin/pip install litellm aiohttp
```

### 2. Clone & configure

```bash
git clone https://github.com/branko96/codex-app-everywhere.git
cd codex-app-everywhere
```

### 3. Export API key and start the stack

```bash
export DEEPSEEK_API_KEY=sk-your-deepseek-key
./stack/launcher/start-stack.sh
```

### 4. Point Codex / Claude Code at the proxy

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=dummy   # requerido por el cliente pero no validado
```

Then launch Codex or Claude Code normally. Select any model from the picker — they all route through the proxy.

---

## Setup — Linux (Claude Code compiled from source)

### 1. Prerequisites

```bash
# Python venv
python3 -m venv ~/.codex/venv
~/.codex/venv/bin/pip install litellm aiohttp

# Node.js 18+ and bun (for building Claude Code)
# https://bun.sh/docs/installation
```

### 2. Clone the proxy stack

```bash
git clone https://github.com/branko96/codex-app-everywhere.git
cd codex-app-everywhere
```

### 3. Start the stack

```bash
export DEEPSEEK_API_KEY=sk-your-deepseek-key
./stack/launcher/start-stack.sh
```

Verify it's running:

```bash
./stack/launcher/start-stack.sh --status
curl http://localhost:4000/v1/models
```

### 4. Build Claude Code from source (if not already built)

```bash
cd /path/to/claude-code-source
bun install
bun run build        # or whatever build command the repo uses
```

### 5. Launch Claude Code pointing at the proxy

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=dummy
node /path/to/claude-code-source/cli.js
# or if you have it installed globally:
claude
```

### 6. Select your model

In the Claude Code / Codex UI, open the model picker and select:
- **`deepseek-v4-flash`** — fast, cheap, good for most tasks
- **`deepseek-v4-pro`** — more capable, better reasoning

Or any of the aliased names (`gpt-5.5`, `o3`, etc.).

---

## Stack management

```bash
# Start
./stack/launcher/start-stack.sh

# Stop
./stack/launcher/start-stack.sh --stop

# Status
./stack/launcher/start-stack.sh --status

# Logs
tail -f ~/.codex/litellm.log
tail -f ~/.codex/proxy.log
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | Your DeepSeek API key |
| `OPENAI_BASE_URL` | Yes (client) | `http://localhost:4000/v1` |
| `OPENAI_API_KEY` | Yes (client) | Any non-empty string (not validated) |
| `LITELLM_URL` | No | LiteLLM upstream, default `http://localhost:4001` |
| `PROXY_PORT` | No | Proxy listen port, default `4000` |

---

## Troubleshooting

**`Authentication Fails (governor)`**
LiteLLM started without `DEEPSEEK_API_KEY`. Stop everything and restart from a shell where the key is exported:
```bash
./stack/launcher/start-stack.sh --stop
export DEEPSEEK_API_KEY=sk-...
./stack/launcher/start-stack.sh
```

**Stack says "already running" but still fails**
The start script skips restart if the port responds. Kill manually:
```bash
kill $(cat /tmp/codex-litellm.pid) $(cat /tmp/codex-proxy.pid) 2>/dev/null
./stack/launcher/start-stack.sh
```

**`reasoning_content` error**
The proxy injects `reasoning_content: ""` automatically on all assistant messages. If you see this error, the proxy version is outdated — pull master and restart.

**`Invalid model name`**
The model name sent by the UI isn't in the proxy's `MODEL_MAP`. Add it to `stack/proxy/codex_proxy.py` under `MODEL_MAP`.

---

## File structure

```
codex-app-everywhere/
├── stack/
│   ├── litellm/
│   │   └── litellm-config.yaml     # LiteLLM model definitions
│   ├── proxy/
│   │   └── codex_proxy.py          # Main proxy (Responses API ↔ Chat Completions)
│   └── launcher/
│       └── start-stack.sh          # Start / stop / status
├── installers/                     # Platform-specific launchers
├── install-codex-linux.sh          # Linux Codex Desktop installer
└── README.md
```
