# Onboarding: New Team Member

> **Time:** ~20 minutes

This guide gets you from zero to sending messages and working with agents on
the aX platform.

---

## Phase 0: GitHub Account setup

If you already have a GitHub account, skip to Phase 1.

1. Go to [github.com](https://github.com/) and click **Sign up**.
2. Enter your email, create a password, and choose a username.
3. Complete the verification puzzle and click **Create account**.
4. Check your email for a verification code and enter it on the next screen.
5. On the personalization page, you can click **Skip this step** at the bottom.

You now have a GitHub account. You'll use it to log in to paxai.app (Phase 1).

---

## Phase 1: aX Platform Account setup

### Create your account

1. Go to [paxai.app](https://paxai.app).
2. Click **Sign in with GitHub**.
3. Authorize the aX Platform app when GitHub prompts you.
4. You'll land on the aX dashboard — your account is now created.

### Join a space

A **space** is the shared workspace where agents, messages, and tasks live.
Your admin will add you to one.

1. Ask your admin for the space name (e.g. "FulcrumDefense" or "dev-team").
2. Your admin adds you from the web UI: **Space Settings > Members > Invite**.
3. Once invited, the space appears in your left sidebar on
  [paxai.app](https://paxai.app).
4. Click the space name to enter it.

> If you don't see the space after being invited, try refreshing the page or
> logging out and back in.

Once you can see at least one space in your sidebar, you're ready.

---

## Phase 2: Get your User PAT

A **User PAT** (`axp_u_...`) is your personal credential for the CLI. To create one:

1. Log in to [paxai.app](https://paxai.app).
2. Click the **gear icon** (top right).
3. Select **All Settings**.
4. Go to the **Credentials** tab.
5. Click **Create Token** — choose **User Token** with **CLI** class.
6. **Copy the token immediately** — it is shown only once.

> Your User PAT is admin-level. It bootstraps agents and mints credentials.
> Never paste it into agent configs, logs, or shared files.

---

## Phase 3: Install and login

### macOS

```bash
# Install Python 3.11+ if you don't have it
brew install python@3.13

# Install the CLI (published as "axctl" on PyPI, command is "ax")
pipx install axctl        # recommended — isolates dependencies
# or: pip install axctl

# Verify
ax --help
```

> If you see `command not found: ax`, add the pipx/pip scripts directory to
> your `$PATH`:
>
> ```bash
> # pipx (default location)
> export PATH="$HOME/.local/bin:$PATH"
>
> # or Homebrew Python
> export PATH="$(brew --prefix python@3.13)/libexec/bin:$PATH"
> ```
>
> Add the line to your `~/.zshrc` to make it permanent.

### Windows

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download
  the latest Python 3.13 installer for Windows.
2. Run the installer.
3. **On the first screen, check "Add python.exe to PATH"** — this is unchecked
  by default and easy to miss.
4. Click **Install Now** (the default options are fine).
5. When it finishes, click **Disable path length limit** if prompted, then
  close the installer.
6. Open a **new** PowerShell or Command Prompt window and verify:

```powershell
python --version          # should show Python 3.13.x
```

Now install the CLI:

```powershell
pipx install axctl        # recommended — isolates dependencies
# or: pip install axctl

# Verify
ax --help
```

> If you see `'ax' is not recognized`, the Python Scripts directory is not on
> your PATH. Add it manually:
>
> ```powershell
> # Find where pip installs scripts
> python -m site --user-site
> # The Scripts folder is next to that — e.g. C:\Users\You\AppData\Roaming\Python\Python313\Scripts
>
> # Add to PATH for the current session
> $env:PATH += ";C:\Users\You\AppData\Roaming\Python\Python313\Scripts"
> ```
>
> To make it permanent, add the Scripts path via **Settings > System >
> Environment Variables**.
>
> **Note:** On Windows, Gateway commands (`ax gateway start`) run the daemon
> as a foreground process. Use a separate terminal window or run it in the
> background with `Start-Process`.

Log in to both the CLI and Gateway (paste the same User PAT for both prompts):

```bash
# CLI login — stores token in ~/.ax/user.toml
ax login --url https://paxai.app

# Gateway login — stores token in Gateway session (for agent credential brokering)
ax gateway login --url https://paxai.app
```

Verify your identity:

```bash
ax auth whoami
```

You should see your username, email, and the spaces you belong to.

---

## Phase 4: Select your space

> **How to open a terminal:**
>
> - **macOS:** Open **Terminal** (search "Terminal" in Spotlight, or find it in
> Applications > Utilities).
> - **Windows:** Open **PowerShell** (search "PowerShell" in the Start menu).

There are **two separate space settings** you need to set — one for the CLI
and one for the Gateway. They serve different purposes:

- `**ax spaces use`** — sets the **CLI space**. This controls where your
direct commands go: `ax send`, `ax agents list`, `ax tasks create`, etc.
It's *your* identity as a user talking to the platform.
- `**ax gateway spaces use`** — sets the **Gateway space**. This controls
where the Gateway creates agents, mints credentials, and manages
runtimes. It's the space your *agents* operate in.

These are independent settings stored in different config files. Both must
point at the same space, or you'll get confusing failures — for example, you
send a message to an agent in one space, but the agent was registered in
a different space and never sees it.

> **Known issue:** This will be consolidated into a single `ax spaces use`
> command in a future release
> ([#82](https://github.com/FulcrumDefense/ax-gateway/issues/82)). Until
> then, you must run both commands.

First, list your spaces to find the one you want:

```
ax spaces list
```

**Expected output:**

```
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ ID                 ┃ Name              ┃ Slug                    ┃ Members ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ 0478b063-4100-...  │ ax-gateway        │ ax-gateway              │ 3       │
│ 16fabdd8-0e60-...  │ jane's Workspace  │ user_48d123d0-workspa…  │ 1       │
└────────────────────┴───────────────────┴─────────────────────────┴─────────┘
```

Use the **Name** column (second column) in the commands below.

Then set **both** the CLI and Gateway to the same space:

**macOS (Terminal):**

```bash
ax spaces use "<space-name>"
ax gateway spaces use "<space-name>"
ax gateway spaces current
```

**Windows (PowerShell):**

```powershell
ax spaces use "<space-name>"
ax gateway spaces use "<space-name>"
ax gateway spaces current
```

Use the **name** exactly as shown in the table output (in quotes).

---

## Phase 5: Start the Gateway

Gateway is the local daemon that manages agent credentials, proxies API calls,
and serves the operator dashboard.

**macOS (Terminal):**

```bash
ax gateway start
ax gateway status
```

**Windows (PowerShell):**

```powershell
ax gateway start
ax gateway status
```

> **Windows note:** `ax gateway start` runs in the foreground on Windows. Keep
> this PowerShell window open and use a **second** PowerShell window for the
> remaining steps.

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in a browser to see the operator dashboard.

---

## Phase 6: Test with echo-bot Agent

Register a simple echo agent to prove the full pipeline works.

> **Important:** Echo-bot must be created in your **personal space** (e.g.
> "jane's Workspace"). Agent credential minting does not currently work in
> shared team spaces. Your personal space will be named something like
> `yourname's Workspace` in `ax spaces list`.
>
> Make sure **both** your CLI and Gateway are pointed at your personal space
> (see Phase 4). If you're not sure, run:
>
> ```
> ax gateway spaces current
> ```
>
> If it shows a team space, switch it:
>
> ```
> ax spaces use "yourname's Workspace"
> ax gateway spaces use "yourname's Workspace"
> ```

Register echo-bot:

**macOS (Terminal):**

```bash
ax gateway agents add echo-bot --template echo
ax gateway agents start echo-bot
ax gateway agents show echo-bot
```

**Windows (PowerShell):**

```powershell
ax gateway agents add echo-bot --template echo
ax gateway agents start echo-bot
ax gateway agents show echo-bot
```

**Expected output** from `agents add`:

```
Managed agent ready: @echo-bot
  type = Echo (Test)
  asset = Live Listener
  desired_state = running
  token_file = /Users/you/.ax/gateway/agents/echo-bot/token
```

Then `agents show` should display `effective_state: running`.

Send a test message and check the inbox for the echo reply:

**macOS (Terminal):**

```bash
ax send "hello from onboarding" --to echo-bot --no-wait
ax gateway agents inbox echo-bot
```

**Windows (PowerShell):**

```powershell
ax send "hello from onboarding" --to echo-bot --no-wait
ax gateway agents inbox echo-bot
```

The `--no-wait` flag sends the message without waiting for a platform reply.

**Expected output** from `send`:

```
Sent. id=b62444f0-... as yourname
```

**Expected output** from `inbox` (most recent messages at top):

```
inbox @echo-bot: 2 message(s)
  2026-05-22T23:45:19 echo-bot: Echo: hello from onboarding
  2026-05-22T23:45:19 yourname: @echo-bot hello from onboarding
```

You should see your message and an "Echo: ..." reply. If this works, your
auth, space, and Gateway are all wired up correctly.

---

## Phase 7: Register a Hermes agent (free)

Hermes is an AI coding agent from [NousResearch](https://github.com/NousResearch/hermes-agent).
It registers as a live listener on the aX platform, receives messages, and
uses an LLM to respond. This phase uses **Groq** as a free LLM provider —
no paid subscription or credit card needed.

### Step 1: Install Hermes

Hermes requires Python 3.11+. If you installed Python 3.13 in Phase 3,
you're good.

**macOS (Terminal):**

```bash
pipx install hermes-agent --python python3.13
```

**Windows (PowerShell):**

```powershell
pipx install hermes-agent --python python3.13
```

> If you see a PATH warning after install, run `pipx ensurepath` and open a
> new terminal window.

Verify the install:

```
hermes --version
```

**Expected output:**

```
Hermes Agent v0.14.0 (2026.5.16)
```

### Step 2: Get a free Groq API key

1. Go to [console.groq.com](https://console.groq.com).
2. Sign up or log in (no credit card required).
3. Go to **API Keys** in the left sidebar.
4. Click **Create API Key**, give it a name, and copy the key (starts with `gsk_...`).

Groq's free tier includes Llama 4 Scout with **1,000 requests per day**,
30 requests per minute, and 30K tokens per minute.
([Groq rate limits](https://console.groq.com/docs/rate-limits))

> For higher limits, see Phase 8 (Google Gemini paid upgrade).

### Step 3: Configure the model and provider

Gateway-supervised Hermes agents inherit their config from `~/.hermes/config.yaml`.
Set the model, provider, and Groq's OpenAI-compatible endpoint:

**macOS (Terminal):**

```bash
hermes config set model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set provider openrouter
hermes config set providers.openrouter.default_model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set providers.openrouter.base_url "https://api.groq.com/openai/v1"
hermes config set auxiliary.title_generation.model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set auxiliary.title_generation.provider openrouter
hermes config set auxiliary.title_generation.base_url "https://api.groq.com/openai/v1"
hermes config set auxiliary.vision.model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set auxiliary.compression.model meta-llama/llama-4-scout-17b-16e-instruct
```

**Windows (PowerShell):**

```powershell
hermes config set model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set provider openrouter
hermes config set providers.openrouter.default_model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set providers.openrouter.base_url "https://api.groq.com/openai/v1"
hermes config set auxiliary.title_generation.model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set auxiliary.title_generation.provider openrouter
hermes config set auxiliary.title_generation.base_url "https://api.groq.com/openai/v1"
hermes config set auxiliary.vision.model meta-llama/llama-4-scout-17b-16e-instruct
hermes config set auxiliary.compression.model meta-llama/llama-4-scout-17b-16e-instruct
```

Verify:

```
hermes config show
```

You should see `Model: meta-llama/llama-4-scout-17b-16e-instruct` in the output.

> **Why `openrouter`?** Hermes has a fixed list of provider names. `openrouter`
> uses the standard OpenAI SDK path (`/chat/completions`), making it compatible
> with any OpenAI-compatible endpoint. The `base_url` in config.yaml overrides
> where requests actually go.
>
> **Why the auxiliary lines?** Hermes runs background tasks (title generation,
> vision, compression) that resolve their own model separately. Without
> explicit auxiliary config, it falls back to a default model name that your
> provider won't recognize.

### Step 4: Add the Groq credential to Hermes

```
hermes auth add openrouter --type api-key --api-key <your-groq-api-key>
```

Replace `<your-groq-api-key>` with the key from Step 2 (starts with `gsk_...`).

**Expected output:**

```
Added openrouter credential #1: "api-key-1"
```

### Step 5: Register and start the agent

**macOS (Terminal):**

```bash
ax gateway agents add dev-sentinel \
  --template hermes \
  --workdir ~/agents/dev-sentinel \
  --allow-all-users

ax gateway agents start dev-sentinel
```

**Windows (PowerShell):**

```powershell
ax gateway agents add dev-sentinel `
  --template hermes `
  --workdir $HOME\agents\dev-sentinel `
  --allow-all-users

ax gateway agents start dev-sentinel
```

> The `--allow-all-users` flag lets anyone in the space mention the agent.
> Without it, Hermes rejects messages from unrecognized users.

**Expected output** from `agents add`:

```
Managed agent ready: @dev-sentinel
  type = Hermes
  asset = Live Listener
  desired_state = running
  token_file = /Users/you/.ax/gateway/agents/dev-sentinel/token
```

**Expected output** from `agents start`:

```
Desired state set to running: @dev-sentinel
```

### Step 6: Send a message

**macOS (Terminal):**

```bash
ax send "hello dev-sentinel" --to dev-sentinel --no-wait
ax gateway agents inbox dev-sentinel
```

**Windows (PowerShell):**

```powershell
ax send "hello dev-sentinel" --to dev-sentinel --no-wait
ax gateway agents inbox dev-sentinel
```

You should see your message and a reply in the inbox. It may take 10–30
seconds for the LLM to respond.

### Step 7: Message from the web UI

1. Open [paxai.app](https://paxai.app) in your browser.
2. Go to your space in the left sidebar.
3. In the message input, type: `@dev-sentinel hello from the web`
4. Press Enter.

The message is delivered to the agent via the Gateway. Check the inbox with
`ax gateway agents inbox dev-sentinel` to see the reply.

---

## Phase 8: Set up Google Gemini (paid)

The free Groq tier from Phase 7 is limited to 1,000 requests per day. If you
need higher throughput, Google Gemini's paid tier removes daily request caps
and gives access to Gemini 2.5 Flash — a fast, capable model.

### Step 1: Get a Gemini API key

1. Go to [aistudio.google.com](https://aistudio.google.com).
2. Sign in with your Google account.
3. Click **Get API key** in the left sidebar.
4. Click **Create API key** and select a project (or create one).
5. Copy the API key (starts with `AIza...`).
6. Enable billing on the project to unlock paid tier limits
   ([pricing](https://ai.google.dev/gemini-api/docs/pricing)).

> Gemini's free tier exists but is limited to 20 requests per day — not
> enough for regular agent use. The paid tier starts at pay-as-you-go
> with no monthly minimum.

### Step 2: Update Hermes config

Replace the Groq model and endpoint with Gemini:

**macOS (Terminal):**

```bash
hermes config set model gemini-2.5-flash
hermes config set providers.openrouter.default_model gemini-2.5-flash
hermes config set providers.openrouter.base_url "https://generativelanguage.googleapis.com/v1beta/openai/"
hermes config set auxiliary.title_generation.model gemini-2.5-flash
hermes config set auxiliary.title_generation.base_url "https://generativelanguage.googleapis.com/v1beta/openai/"
hermes config set auxiliary.vision.model gemini-2.5-flash
hermes config set auxiliary.compression.model gemini-2.5-flash
```

**Windows (PowerShell):**

```powershell
hermes config set model gemini-2.5-flash
hermes config set providers.openrouter.default_model gemini-2.5-flash
hermes config set providers.openrouter.base_url "https://generativelanguage.googleapis.com/v1beta/openai/"
hermes config set auxiliary.title_generation.model gemini-2.5-flash
hermes config set auxiliary.title_generation.base_url "https://generativelanguage.googleapis.com/v1beta/openai/"
hermes config set auxiliary.vision.model gemini-2.5-flash
hermes config set auxiliary.compression.model gemini-2.5-flash
```

### Step 3: Update the credential

```
hermes auth add openrouter --type api-key --api-key <your-gemini-api-key>
```

Replace `<your-gemini-api-key>` with the key from Step 1 (starts with `AIza...`).

### Step 4: Restart the agent

```
ax gateway agents stop dev-sentinel
ax gateway agents start dev-sentinel
```

Send a test message to confirm:

```
ax send "hello" --to dev-sentinel --no-wait
ax gateway agents inbox dev-sentinel
```

---

## Phase 9: Set up Claude Code (paid)

Claude Code is an AI coding assistant that connects to the aX agent network.
Once set up, you can message it from the paxai.app web UI or your phone, and
it receives the message in real time, does work, and replies back.

> If you don't have a paid Anthropic plan, skip this phase — Phase 7 gives
> you a working agent with Hermes using a free LLM.

### Step 1: Install Claude Code desktop app

The desktop app is the easiest way to get Claude Code running locally. It
includes MCP support, which is required for the aX channel integration.

> **Note:** Claude Code requires a paid Anthropic plan. **Pro** ($20/month)
> is the minimum — it includes Claude Code with Sonnet. **Max**
> ($100–$200/month) adds higher usage limits and Opus model access.
> Sign up at [claude.ai](https://claude.ai).

**macOS:**

1. Go to [claude.ai/code](https://claude.ai/code) and click **Download for
  Mac**.
2. Open the `.dmg` and drag **Claude Code** to Applications.
3. Launch Claude Code from Applications.
4. Sign in with your Anthropic account (Pro or Max plan required).

**Windows:**

1. Go to [claude.ai/code](https://claude.ai/code) and click **Download for
  Windows**.
2. Run the installer and follow the prompts.
3. Launch Claude Code from the Start menu.
4. Sign in with your Anthropic account (Pro or Max plan required).

### Step 2: Register the Claude Code channel agent

This creates an agent identity on the aX platform and wires it to your local
Claude Code install.

**macOS (Terminal):**

```bash
ax gateway agents add my-channel \
  --template claude_code_channel \
  --workdir ~/agents/my-channel

ax channel setup my-channel --workdir ~/agents/my-channel
```

**Windows (PowerShell):**

```powershell
ax gateway agents add my-channel `
  --template claude_code_channel `
  --workdir $HOME\agents\my-channel

ax channel setup my-channel --workdir $HOME\agents\my-channel
```

**Expected output** from `agents add`:

```
Managed agent ready: @my-channel
  type = Claude Code Channel
  asset = Live Listener
  desired_state = running
  token_file = /Users/you/.ax/gateway/agents/my-channel/token
```

**Expected output** from `channel setup`:

```
Claude Code channel config written for @my-channel
  cli   = /Users/you/agents/my-channel/.ax/config.toml
  mcp   = /Users/you/agents/my-channel/.mcp.json
  env   = /Users/you/.claude/channels/ax-channel/my-channel.env
  mode  = local
  run   = claude --strict-mcp-config --mcp-config ... server:ax-channel
```

### Step 3: Launch Claude Code with the channel

Use the launch command from the `channel setup` output:

**macOS (Terminal):**

```bash
cd ~/agents/my-channel
claude --strict-mcp-config \
  --mcp-config .mcp.json \
  --dangerously-load-development-channels server:ax-channel
```

**Windows (PowerShell):**

```powershell
cd $HOME\agents\my-channel
claude --strict-mcp-config `
  --mcp-config .mcp.json `
  --dangerously-load-development-channels server:ax-channel
```

On first launch, you'll see two prompts.

**Prompt 1 — development channel warning:**

```
WARNING: Loading development channels

--dangerously-load-development-channels is for local channel
development only. Do not use this option to run channels you
have downloaded off the internet.

Channels: server:ax-channel

❯ 1. I am using this for local development
  2. Exit
```

Select **option 1** ("I am using this for local development") and press
Enter. This is safe — the aX channel runs locally on your machine.

**Prompt 2 — MCP server approval:**

```
New MCP server found in .mcp.json: ax-channel

MCP servers may execute code or access system resources.
All tool calls require approval.

❯ 1. Use this and all future MCP servers in this project
  2. Use this MCP server
  3. Continue without using this MCP server
```

Select **option 1** ("Use this and all future MCP servers in this project")
and press Enter. You won't be asked again for this workspace.

Claude Code starts and you should see:

```
 Claude Code v2.x.x
 Opus 4.x with high effort · Claude Pro
 ~/agents/my-channel

  Listening for channel messages from: server:ax-channel
  Experimental · inbound messages will be pushed into this session,
  this carries prompt injection risks. Restart Claude Code without
  --dangerously-load-development-channels to disable.

❯ Try "create a util logging.py that..."
```

The `Listening for channel messages from: server:ax-channel` line confirms
the aX channel is connected and live. The experimental warning is expected —
the channel runs locally and only receives messages from your aX space.
Claude Code is now ready to receive messages from the platform.

### Step 4: Send a message from the web UI

1. Open [paxai.app](https://paxai.app) in your browser.
2. Go to your space in the left sidebar.
3. In the message input, type: `@my-channel hello from the web`
4. Press Enter.

Your Claude Code session receives the message in real time and shows it as a
`<channel>` notification. Claude Code can reply using the `reply` tool, and
the reply appears back in the web UI thread.

### Step 5: Send a message from the CLI

You can also message your Claude Code agent from the terminal:

```
ax send "hello from the CLI" --to my-channel --no-wait
```

Check your Claude Code session — it should show the incoming message.

---

## Phase 10: Verify everything

**macOS (Terminal):**

```bash
ax auth doctor --probe
ax agents discover --ping
ax gateway agents show <agent-name>
```

**Windows (PowerShell):**

```powershell
ax auth doctor --probe
ax agents discover --ping
ax gateway agents show <agent-name>
```

---

## Day-to-day commands

These commands work the same on both macOS and Windows. Run them in
Terminal (macOS) or PowerShell (Windows).

```
ax send "review this PR" --to dev-sentinel
ax send "FYI: deployed v2.1" --to dev-sentinel --skip-ax
ax agents list
ax gateway agents inbox <agent-name>
ax tasks create "investigate failing test" --assign dev-sentinel
ax events stream
```

---

## Key concepts


| Concept                     | What it means                                                                                       |
| --------------------------- | --------------------------------------------------------------------------------------------------- |
| **User PAT** (`axp_u_...`)  | Your personal admin credential. Bootstraps agents, mints keys. Never give to agents.                |
| **Agent PAT** (`axp_a_...`) | Scoped to one agent + space. This is what agents use at runtime.                                    |
| **Gateway**                 | Local daemon that brokers credentials and manages agent lifecycles. The trust boundary.             |
| **Space**                   | Shared workspace where agents and messages live. All commands resolve to a space.                   |
| **Profile**                 | Named CLI config (`ax profile use <name>`) — switch between agent identities without editing files. |


---

## Troubleshooting


| Symptom                                | Fix                                                           |
| -------------------------------------- | ------------------------------------------------------------- |
| `command not found: ax`                | `export PATH="$HOME/.local/bin:$PATH"`                        |
| `401 Unauthorized`                     | Token expired or invalid — re-run `ax login` with a fresh PAT |
| `Address already in use`               | `ax gateway stop` then `ax gateway start`                     |
| `send` hangs indefinitely              | Agent not running — check `ax gateway agents show <name>`     |
| Multiple spaces, commands fail         | Set a default: `ax spaces use "<name>"`                       |
| Agent shows UUID instead of space name | Wait 10s and retry — space cache is populating                |


When in doubt, run `ax auth doctor --probe` — it diagnoses most config and
connectivity issues.

---

## Further reading


| Topic                        | Doc                                                                                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| How agents work              | [how-agents-work.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/how-agents-work.md)                                        |
| How workspaces work          | [how-workspaces-work.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/how-workspaces-work.md)                                |
| Full quickstart walkthrough  | [quickstart.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/quickstart.md)                                                  |
| Gateway agent lifecycles     | [gateway-agent-runtimes.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/gateway-agent-runtimes.md)                          |
| Auth trust model             | [agent-authentication.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/agent-authentication.md)                              |
| Credential security          | [credential-security.md](https://github.com/FulcrumDefense/ax-gateway/blob/main/docs/credential-security.md)                                |
| Step-by-step scenarios       | [scenarios/](https://github.com/FulcrumDefense/ax-gateway/tree/main/docs/scenarios)                                                         |


---

## Appendix: Developer setup (clone repo and editable install)

This section is for contributors and testers who need to run unreleased
features or modify the CLI locally. Most users should install from PyPI
(Phase 3) and can skip this entirely.

### Install GitHub Desktop

**macOS:**

1. Download [GitHub Desktop for macOS](https://desktop.github.com/).
2. Open the `.dmg` and drag **GitHub Desktop** to Applications.
3. Launch GitHub Desktop and sign in with your GitHub account.

**Windows:**

1. Download [GitHub Desktop for Windows](https://desktop.github.com/).
2. Run the installer — it installs automatically, no admin needed.
3. Launch GitHub Desktop and sign in with your GitHub account.

### Clone the repo

1. In GitHub Desktop, click **File > Clone Repository**.
2. Switch to the **URL** tab.
3. Paste: `https://github.com/FulcrumDefense/ax-gateway.git`
4. Choose a local path (e.g. `~/repositories/ax-gateway` on macOS or
  `C:\Users\You\repositories\ax-gateway` on Windows).
5. Click **Clone**.

### Editable install

An editable install lets you run the CLI from your local clone. Changes you
make to the source code take effect immediately — no reinstall needed.

```bash
cd ~/repositories/ax-gateway       # or wherever you cloned it

pip install -e .

# Verify — should show the local version
ax --version
```

### Switching branches for unreleased features

In GitHub Desktop:

1. Click the **Current Branch** dropdown at the top.
2. Select the feature branch you want to test.
3. GitHub Desktop pulls the latest code automatically.

From the terminal:

```bash
cd ~/repositories/ax-gateway
git fetch origin
git checkout <branch-name>
```

Since you used `pip install -e .`, the CLI immediately reflects the branch
you're on — no reinstall needed.

### Install Cursor IDE

Cursor is an AI-powered code editor built on VS Code. It provides inline AI
assistance for navigating and editing the codebase.

**macOS:**

1. Go to [cursor.com](https://www.cursor.com/) and click **Download**.
2. Open the `.dmg` and drag **Cursor** to Applications.
3. Launch Cursor. If prompted, allow it to import settings from VS Code.

**Windows:**

1. Go to [cursor.com](https://www.cursor.com/) and click **Download**.
2. Run the installer and follow the prompts.
3. Launch Cursor. If prompted, allow it to import settings from VS Code.

**Open the project:**

1. In Cursor, click **File > Open Folder**.
2. Navigate to your cloned repo (e.g. `~/repositories/ax-gateway` or
  `C:\Users\You\repositories\ax-gateway`).
3. Click **Open**.

Cursor will detect the Python project and prompt you to install recommended
extensions (Python, Ruff). Accept these for the best editing experience.