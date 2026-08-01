# DeepResearch v2 Hook

This hook ensures the autonomous research workflow runs to completion.

## Installation

Copy this hook file to your `.claude/hooks/` directory:

```bash
cp claude-deep-research/hooks/check-completion.cjs .claude/hooks/
chmod +x .claude/hooks/check-completion.cjs
```

Or if `.claude/` is read-only in your environment, use this from `claude-deep-research/hooks/` directly and update the hook path in command files.

## Hook Overview

### `check-completion.cjs` - Workflow Completion Check

**When:** Runs when Claude is about to stop responding

**Purpose:** Prevents stopping if the research workflow is incomplete

**Checks:**
- Looks for active research session in `DeepResearch/sessions/*/research-state.json`
- Verifies `phase === "complete"`

**Blocks stop if:**
- Research session exists and `phase !== "complete"`
- Shows session ID, topic, current phase, and status
- Prompts continuation of the research workflow

**Allows stop if:**
- No active workflow detected
- Workflow phase is "complete"

---

## How It Works

The hook receives data via stdin as JSON and communicates via exit codes:

- **Exit 0**: Allow operation to proceed
- **Exit 2**: Block operation and show message to Claude

### Example: Check-completion Hook

```javascript
// Before stopping, verify workflow complete
const state = JSON.parse(readFile('DeepResearch/sessions/*/research-state.json'));

if (state.phase !== 'complete') {
  console.error('RESEARCH SESSION INCOMPLETE - DO NOT STOP');
  process.exit(2); // Blocks stop, prompts continuation
}
```

---

## Testing the Hook

```bash
# Create incomplete state
mkdir -p DeepResearch/sessions/test-001
echo '{"phase":"wakeup","sessionId":"test-001","topic":"test"}' > DeepResearch/sessions/test-001/research-state.json

# Run hook
node .claude/hooks/check-completion.cjs
# Should exit with code 2 (blocked)
```

---

## Troubleshooting

**Hook not running:**
- Verify hook is executable: `chmod +x .claude/hooks/check-completion.cjs`
- Check hook path in command YAML frontmatter
- Ensure Node.js is available in PATH

**Hook blocking when it shouldn't:**
- Check state file is up to date
- Verify phase is "complete"
- Check for stale session folders

---

## Hook Integration in Commands

Commands reference hooks in their YAML frontmatter:

```yaml
---
description: Deep research workflow
hooks:
    Stop:
      - hooks:
          - type: command
            command: "node .claude/hooks/check-completion.cjs"
            timeout: 5
---
```

The hook is automatically invoked by Claude Code when the workflow is about to stop.
