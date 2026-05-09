# Reverse Engineering OpenAI Codex macOS App for Linux

A technical deep-dive into extracting, analyzing, and porting the OpenAI Codex desktop application from macOS to Linux through binary analysis and runtime patching.

**Instructions for Claude**: Execute these steps to port the OpenAI Codex desktop app from macOS (.dmg) to Linux. Run all commands using Bash. The user should have the `Codex.dmg` file available.

---

## Phase 1: DMG Container Analysis

### Step 1.1: Acquire Extraction Tools

```bash
curl -sL https://www.7-zip.org/a/7z2408-linux-x64.tar.xz -o /tmp/7z.tar.xz
tar -xf /tmp/7z.tar.xz -C /tmp/
```

### Step 1.2: Analyze DMG Header Structure

```bash
hexdump -C Codex.dmg | head -20
```

Expected output reveals zlib-compressed Apple Disk Image:
```
00000000  78 da 63 60 18 05 43 18  fc fb ff ff 1d 10 33 02  |x.c`..C.......3.|
          ^^ ^^
          zlib magic bytes (0x78 0xda = default compression)
```

The DMG structure:
```
┌─────────────────────────────────────────┐
│ Offset 0x00000000: zlib compressed data │
│ ...                                     │
│ Offset 0x08C2A9B1: XML property list    │  <- Partition map
│ Offset 0x08C31A5B: Blob trailer         │  <- koly block
└─────────────────────────────────────────┘
```

### Step 1.3: Extract HFS+ Filesystem

```bash
/tmp/7zz x Codex.dmg -o./codex_extracted -y
```

7zip decompresses the zlib stream and extracts the HFS+ partition containing the app bundle.

### Step 1.4: Verify Extraction - Locate App Bundle

```bash
find ./codex_extracted -name "*.app" -type d
```

Result: `./codex_extracted/Codex Installer/Codex.app`

---

## Phase 2: Application Bundle Analysis

### Step 2.1: macOS App Bundle Structure

```
Codex.app/
├── Contents/
│   ├── Info.plist              <- Bundle metadata (CFBundleIdentifier, etc.)
│   ├── MacOS/
│   │   └── Codex               <- Mach-O executable (Electron main)
│   ├── Frameworks/
│   │   ├── Electron Framework.framework/   <- Chromium + Node.js runtime
│   │   ├── Codex Helper.app/               <- Renderer process
│   │   ├── Codex Helper (GPU).app/         <- GPU process
│   │   ├── Codex Helper (Renderer).app/    <- Renderer sandbox
│   │   └── Squirrel.framework/             <- Auto-updater
│   └── Resources/
│       ├── app.asar            <- Application source (encrypted archive)
│       ├── app.asar.unpacked/  <- Native modules extracted
│       └── *.lproj/            <- Localization bundles
```

### Step 2.2: Identify Framework - Confirm Electron

```bash
find ./codex_extracted -name "Electron Framework.framework" -o -name "app.asar" 2>/dev/null
```

Presence of `Electron Framework.framework` confirms this is an Electron application - a Chromium-based runtime that bundles Node.js, enabling cross-platform potential.

### Step 2.3: Analyze Main Executable Header

```bash
file "./codex_extracted/Codex Installer/Codex.app/Contents/MacOS/Codex"
```

Output:
```
Mach-O 64-bit executable arm64
```

Mach-O header structure at offset 0x0:
```
┌────────────────────────────────────────┐
│ 0x00: magic      = 0xFEEDFACF (64-bit) │
│ 0x04: cputype    = 0x0100000C (ARM64)  │
│ 0x08: cpusubtype = 0x00000000          │
│ 0x0C: filetype   = 0x00000002 (EXEC)   │
│ 0x10: ncmds      = load command count  │
│ 0x14: sizeofcmds = load commands size  │
└────────────────────────────────────────┘
```

This binary is macOS-specific, but we don't need it - we'll use Linux Electron.

---

## Phase 3: ASAR Archive Extraction

### Step 3.1: ASAR Format Analysis

ASAR (Atom Shell Archive) is Electron's custom archive format:

```
┌─────────────────────────────────────────────────────┐
│ Bytes 0-3: Header size (uint32 LE, pickled)         │
│ Bytes 4-N: JSON header (file tree + offsets)        │
│ Bytes N+1-EOF: Concatenated file contents           │
└─────────────────────────────────────────────────────┘
```

### Step 3.2: Install ASAR Tools

```bash
npm install -g @electron/asar
```

### Step 3.3: Dump ASAR Header (File Manifest)

```bash
ASAR_PATH="./codex_extracted/Codex Installer/Codex.app/Contents/Resources/app.asar"
asar list "$ASAR_PATH" | head -50
```

Key entries in the archive:
```
/.vite/build/main.js      <- Main process entry (IPC, window management)
/.vite/build/preload.js   <- Preload script (context bridge)
/.vite/build/worker.js    <- Background worker threads
/webview/index.html       <- Renderer entry point
/webview/assets/          <- Bundled React application
/native/sparkle.node      <- Native auto-updater (macOS-only)
/node_modules/            <- Runtime dependencies
```

### Step 3.4: Extract Archive Contents

```bash
asar extract "$ASAR_PATH" ./codex_app_src
```

### Step 3.5: Analyze package.json - Runtime Manifest

```bash
cat ./codex_app_src/package.json
```

Critical fields:
```json
{
  "name": "openai-codex-electron",
  "main": ".vite/build/main.js",           // Entry point
  "devDependencies": {
    "electron": "40.0.0"                   // Runtime version - MUST MATCH
  },
  "dependencies": {
    "better-sqlite3": "^12.4.6",           // Native: SQLite bindings
    "node-pty": "^1.1.0",                  // Native: PTY for terminal
    "electron-liquid-glass": "1.1.1"       // Native: macOS visual effects
  }
}
```

---

## Phase 4: Native Module Analysis

### Step 4.1: Identify Native Binaries

```bash
find ./codex_app_src -name "*.node" -exec sh -c 'echo "=== {} ===" && file {} && xxd {} | head -4' \;
```

Native modules are Node.js addons compiled to platform-specific shared libraries.

### Step 4.2: Binary Classification

**better_sqlite3.node** - Mach-O 64-bit bundle:
```
00000000: cffa edfe 0c00 0001 0000 0000 0600 0000  ................
          ^^^^ ^^^^
          0xFEEDFACF = Mach-O 64-bit magic
```
Status: Cross-platform, rebuild for Linux

**node-pty.node** - Mach-O 64-bit bundle:
```
00000000: cffa edfe 0c00 0001 0000 0000 0600 0000  ................
```
Status: Cross-platform, rebuild for Linux

**sparkle.node** - Mach-O 64-bit bundle:
```
00000000: cffa edfe 0c00 0001 0000 0000 0600 0000  ................
```
Status: macOS-only (Sparkle.framework dependency), requires stub

**electron-liquid-glass** - Mach-O prebuilds:
```
/prebuilds/darwin-arm64/node.napi.armv8.node
/prebuilds/darwin-x64/node.napi.node
```
Status: macOS-only (NSVisualEffectView), requires stub

### Step 4.3: Node Module ABI Compatibility

```
NODE_MODULE_VERSION mapping:
├── Node.js 18.x  → ABI 108
├── Node.js 20.x  → ABI 115
├── Node.js 22.x  → ABI 127
└── Electron 40   → ABI 143  <- Target ABI
```

Native modules must be compiled against Electron's Node ABI, not system Node.

---

## Phase 5: Linux Runtime Construction

### Step 5.1: Initialize Project Structure

```bash
mkdir -p codex-linux-port
cd codex-linux-port
npm init -y
```

### Step 5.2: Install Electron Runtime (ABI 143)

```bash
npm install electron@40.0.0
```

This downloads the Linux Electron binary (~180MB) containing:
- Chromium renderer
- Node.js 20.x (V8 engine)
- Platform abstractions for X11/Wayland

### Step 5.3: Transplant Application Code

```bash
# Main process bundle
cp -r ../codex_app_src/.vite ./

# Native module directory (will patch)
cp -r ../codex_app_src/native ./

# Renderer bundle (React SPA)
cp -r ../codex_app_src/webview ./
```

### Step 5.4: Install JavaScript Dependencies

```bash
npm install immer lodash memoizee mime-types shell-env shlex smol-toml zod
```

---

## Phase 6: Native Module Patching

### Step 6.1: Rebuild Cross-Platform Modules for Linux + Electron ABI

```bash
npm install better-sqlite3@12.4.6 node-pty@1.1.0
npm install @electron/rebuild
npx @electron/rebuild
```

This recompiles native addons against Electron's headers:
```
Rebuilding better-sqlite3 → linux-x64-143
Rebuilding node-pty       → linux-x64-143
```

### Step 6.2: Stub macOS-Only Modules

**Remove Sparkle binary** (app handles absence gracefully):
```bash
rm native/sparkle.node 2>/dev/null || true
```

**Create electron-liquid-glass shim**:

```bash
mkdir -p node_modules/electron-liquid-glass

cat > node_modules/electron-liquid-glass/index.js << 'EOF'
// Shim: NSVisualEffectView not available on Linux
// Original calls setMaterial(), setBlendingMode() on macOS
// We return no-op functions to prevent runtime errors

const stub = {
  isGlassSupported: () => false,  // Always false on non-Darwin
  enable: (webContents, opts) => {
    // Would call: [NSVisualEffectView alloc] initWithFrame:
    // Linux: no-op
  },
  disable: (webContents) => {},
  setOptions: (webContents, opts) => {}
};

module.exports = stub;
module.exports.default = stub;
EOF

cat > node_modules/electron-liquid-glass/package.json << 'EOF'
{"name":"electron-liquid-glass","version":"1.0.0","main":"index.js"}
EOF
```

---

## Phase 7: Runtime Configuration

### Step 7.1: Configure Entry Point

```bash
cat > package.json << 'EOF'
{
  "name": "codex-linux-port",
  "productName": "Codex",
  "version": "1.0.0-linux",
  "main": ".vite/build/main.js",
  "scripts": {
    "start": "electron .",
    "start:debug": "electron . --enable-logging"
  },
  "dependencies": {
    "better-sqlite3": "^12.4.6",
    "node-pty": "^1.1.0",
    "immer": "^10.1.1",
    "lodash": "^4.17.21",
    "memoizee": "^0.4.15",
    "mime-types": "^2.1.35",
    "shell-env": "^4.0.1",
    "shlex": "^3.0.0",
    "smol-toml": "^1.5.2",
    "zod": "^3.22.0"
  },
  "devDependencies": {
    "electron": "40.0.0",
    "@electron/rebuild": "^3.6.0"
  }
}
EOF

# Sync dependencies
npm install
```

### Step 7.2: Install Backend CLI

The Codex app is a frontend for the Codex CLI agent:

```bash
npm install -g @openai/codex
which codex  # Expected: /usr/local/bin/codex
```

---

## Phase 8: Runtime Execution

### Step 8.1: Environment Configuration

The app checks `app.isPackaged` to determine renderer URL. Since we're running unpackaged, we override via environment:

```bash
CODEX_CLI_PATH=/usr/local/bin/codex \
ELECTRON_RENDERER_URL="file://${PWD}/webview/index.html" \
./node_modules/.bin/electron . --no-sandbox --enable-logging 2>&1 | head -100
```

Flags:
- `--no-sandbox`: Bypass SUID sandbox (requires root-owned chrome-sandbox otherwise)
- `--enable-logging`: Verbose Chromium/Node logs to stdout

### Step 8.2: Expected Runtime Output

Successful initialization sequence:
```
[IpcRouterManager] Starting router...
Launching app {
  buildFlavor: 'dev',
  platform: 'linux',          <- Detected Linux
  packaged: false
}
[IpcRouterManager] Listening on /tmp/codex-ipc/ipc-1000.sock
[ElectronAppServerConnection] Using CLI from override { overrideCandidate: '/usr/local/bin/codex' }
[ElectronAppServerConnection] Codex CLI initialized    <- CLI connected
[ElectronAppServerConnection] Server response received  <- RPC working
```

### Step 8.3: Troubleshooting Matrix

| Error Signature | Root Cause | Resolution |
|----------------|------------|------------|
| `NODE_MODULE_VERSION 127... requires 143` | ABI mismatch | `npx @electron/rebuild` |
| `Cannot find module 'X'` | Missing JS dependency | `npm install X` |
| `Failed to load URL: http://localhost:5175` | Dev server expected | Set `ELECTRON_RENDERER_URL` |
| `SUID sandbox helper binary` | Sandbox permissions | Use `--no-sandbox` |
| `Unable to locate the Codex CLI binary` | CLI not found | Set `CODEX_CLI_PATH` |
| `spawn codex-box ENOENT` | Sandbox binary missing | Non-critical, ignore |

---

## Phase 9: Create Launcher

```bash
cat > codex-linux.sh << 'EOF'
#!/bin/bash
# Codex for Linux - Runtime Launcher
# Configures environment and spawns Electron process

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Override renderer URL (bypasses isPackaged check)
export ELECTRON_RENDERER_URL="file://${SCRIPT_DIR}/webview/index.html"

# CLI backend path
export CODEX_CLI_PATH="${CODEX_CLI_PATH:-/usr/local/bin/codex}"

# Launch Electron with sandbox disabled
exec ./node_modules/.bin/electron . --no-sandbox "$@"
EOF

chmod +x codex-linux.sh
```

---

## Phase 10: Verification

```bash
./codex-linux.sh
```

**Success Criteria:**
- [ ] Electron window renders
- [ ] React UI loads (not blank)
- [ ] Authentication flow works
- [ ] Can create and run coding tasks
- [ ] Terminal output shows no fatal errors

---

## Final Architecture

```
codex-linux-port/
├── .vite/
│   └── build/
│       ├── main.js         # 1.4MB - Main process (Electron APIs, IPC)
│       ├── preload.js      # 1.5KB - Context bridge (exposes safe APIs)
│       └── worker.js       # 838KB - Background workers (git, file ops)
├── webview/
│   ├── index.html          # Entry point (loads React app)
│   └── assets/
│       ├── index-*.js      # ~2MB - React bundle (UI components)
│       └── index-*.css     # Styles
├── native/
│   └── (sparkle.node removed - macOS updater stubbed)
├── node_modules/
│   ├── electron/                    # Linux runtime
│   ├── better-sqlite3/              # Rebuilt: linux-x64-143
│   ├── node-pty/                    # Rebuilt: linux-x64-143
│   └── electron-liquid-glass/       # Shimmed: no-op
├── package.json
└── codex-linux.sh
```

---

## Legal

For personal use and educational purposes. Do not redistribute modified binaries.
