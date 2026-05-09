# Codex App — Cross-Platform Proxy Stack 🐧🍎

<p align="left">
  <img src="https://img.shields.io/badge/platform-linux_|_macos-2ea44f" alt="Linux & macOS" />
  <img src="https://img.shields.io/badge/status-active-1f6feb" alt="Status" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

Run Codex Desktop/CLI with **any LLM provider** (DeepSeek, Anthropic, etc.) via LiteLLM proxy instead of OpenAI.

## 🚀 Quick Start

### macOS

```bash
# 1. Clone & install
git clone https://github.com/your-user/codex-app-everywhere.git
cd codex-app-everywhere
./installers/mac/install-mac.sh

# 2. Set your API key
export OPENAI_API_KEY="sk-your-deepseek-key"

# 3. Start everything
./installers/mac/codex-mac-launcher.sh
```

### Linux

```bash
# 1. Clone & install (downloads/extracts from macOS DMG)
git clone https://github.com/your-user/codex-app-everywhere.git
cd codex-app-everywhere
./install-codex-linux.sh          # Full install
# or with local DMG
./install-codex-linux.sh --dmg /path/to/Codex.dmg

# 2. Launch (starts LiteLLM + proxy + Electron)
export OPENAI_API_KEY="sk-your-deepseek-key"
./installers/linux/codex-linux.sh
```

### Codex CLI (both platforms)

```bash
# Just use the shared stack + CLI
./stack/launcher/start-stack.sh
export OPENAI_API_KEY="sk-your-deepseek-key"
codex
```

## 📁 Structure

```
codex-app-everywhere/
├── stack/                          # 🔧 Shared proxy stack
│   ├── litellm/litellm-config.yaml # LiteLLM model routing
│   ├── proxy/codex_proxy.py        # Responses API → Chat Completions filter
│   └── launcher/start-stack.sh     # Start/stop/status script
├── installers/
│   ├── linux/
│   │   └── codex-linux.sh          # Linux launcher (Electron + stack)
│   └── mac/
│       ├── install-mac.sh          # macOS installer (patches Codex.app)
│       └── codex-mac-launcher.sh   # macOS wrapper launcher
├── install-codex-linux.sh          # Linux installer (DMG → Electron)
├── codex-linux/                    # Built Linux bundle (after install)
├── docs/
│   └── ...                         # Detailed guides
└── Reverse-engineering-guide.md    # How the ASAR extraction works
```

## 🔄 Architecture

```
Codex Desktop/CLI
      │
      ▼  Requests to api.openai.com
┌─────────────────┐
│  Proxy (:4000)  │  ← Filters unsupported tool types
└────────┬────────┘
         │
┌────────▼────────┐
│  LiteLLM (:4001) │  ← Translates Responses API → Chat Completions
└────────┬────────┘
         │
┌────────▼────────┐
│  DeepSeek API   │  (or any LiteLLM-supported provider)
└─────────────────┘
```

## ⚙️ Configuration

### Models

Edit `stack/litellm/litellm-config.yaml`:

```yaml
model_list:
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/OPENAI_API_KEY
  - model_name: deepseek-reasoner
    litellm_params:
      model: deepseek/deepseek-reasoner
      api_key: os.environ/OPENAI_API_KEY
  # Add more providers/models
  - model_name: claude-opus
    litellm_params:
      model: anthropic/claude-3-opus-20240229
      api_key: os.environ/ANTHROPIC_API_KEY
```

### CLI config (`~/.codex/config.toml`)

```toml
model = "deepseek-chat"
openai_base_url = "http://localhost:4000"
model_reasoning_effort = "medium"
```

## 🛠 Requirements

| Component | macOS | Linux |
|-----------|-------|-------|
| **Node.js** | ✅ v18+ | ✅ v18+ |
| **Python 3** | ✅ pip | ✅ pip |
| **Codex Desktop** | ✅ /Applications/Codex.app | ❌ (comes from DMG) |
| **Codex CLI** | ✅ npm i -g @openai/codex | ✅ auto-installed |
| **liteLLM** | ✅ pip install litellm | ✅ pip install litellm |
| **aiohttp** | ✅ pip install aiohttp | ✅ pip install aiohttp |

## 💰 Why?

Avoid OpenAI's $200/month Pro plan. Run Codex with cost-effective providers:

| Provider | ~Cost/month | vs ChatGPT Pro |
|----------|-------------|----------------|
| **OpenAI (default)** | $200 | — |
| **DeepSeek V3** | ~$5–15 | **~95% cheaper** |
| **DeepSeek R1** | ~$10–30 | **~85% cheaper** |
| **Claude Opus** | ~$30–60 | **~70% cheaper** |

## 🧹 Uninstall

### macOS

```bash
./installers/mac/install-mac.sh --uninstall
```

### Linux

```bash
rm -rf codex-linux/
# App was never installed system-wide
```

## ⚠️ Caveats

- **macOS**: Each Codex Desktop update overwrites the patched `app.asar`. Re-run `install-mac.sh` after updates.
- **macOS SIP**: You may need to disable SIP or use a self-signed certificate for the patched app.
- **Function calls**: Some Responses API features (`web_search`, `namespace` tools) are filtered by the proxy.
- **Streaming**: WebSocket may have issues — CLI mode is more reliable.

## 🔗 Related

- [Codex App Linux (original repo)](https://github.com/areu01or00/Codex-App-Linux)
- [LiteLLM](https://github.com/BerriAI/litellm)
- [OpenAI Codex](https://codex.ai/)
