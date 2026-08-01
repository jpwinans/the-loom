#!/usr/bin/env node

/**
 * Stop Hook: Check Workflow Completion
 *
 * This hook runs when Claude is about to stop responding.
 * It checks for active workflows across all supported types:
 *   - deep-research (DeepResearch/sessions/ with research-state.json)
 *   - hyper-research (DeepResearch/hyper-sessions/ with hyper-research-state.json)
 *
 * If an active workflow is found and not complete, it blocks the stop
 * and prompts Claude to continue.
 *
 * Exit codes:
 *   0 = Allow stop (workflow complete or no workflow active)
 *   2 = Block stop (workflow incomplete, message shown to Claude)
 */

const fs = require('fs');
const path = require('path');

// ─── Session-Based Workflow Detection ────────────────────────────────────────

/**
 * Find the most recently modified state file in a session directory.
 * Searches for session subdirectories and returns the most recent active one.
 */
function findMostRecentSession(sessionBase, stateFileName) {
  try {
    const cwd = process.cwd();
    const baseDir = path.join(cwd, sessionBase);

    if (!fs.existsSync(baseDir)) return null;

    const entries = fs.readdirSync(baseDir);
    const sessionDirs = entries.filter(e => {
      const fullPath = path.join(baseDir, e);
      return fs.statSync(fullPath).isDirectory();
    });

    if (sessionDirs.length === 0) return null;

    // Find the session with the most recently modified state file
    let mostRecentDir = null;
    let mostRecentTime = 0;

    for (const dir of sessionDirs) {
      const statePath = path.join(baseDir, dir, stateFileName);
      if (fs.existsSync(statePath)) {
        const mtime = fs.statSync(statePath).mtimeMs;
        if (mtime > mostRecentTime) {
          mostRecentTime = mtime;
          mostRecentDir = dir;
        }
      }
    }

    if (!mostRecentDir) return null;

    return {
      dir: path.join(baseDir, mostRecentDir),
      stateFile: path.join(baseDir, mostRecentDir, stateFileName),
      mtime: mostRecentTime
    };
  } catch (err) {
    return null;
  }
}

// ─── Deep Research Detection ─────────────────────────────────────────────────

function checkDeepResearch() {
  const session = findMostRecentSession('DeepResearch/sessions', 'research-state.json');
  if (!session) return null;

  try {
    const state = JSON.parse(fs.readFileSync(session.stateFile, 'utf8'));
    const phase = state.phase || 'unknown';
    const sessionId = state.sessionId || 'unknown';
    const topic = state.topic || 'unknown';

    if (phase === 'complete') return null; // Complete

    const iterationCount = state.iterationCount || 0;
    const maxIterations = state.maxIterations || 5;

    let statusDetail = '';
    switch (phase) {
      case 'wakeup':
        statusDetail = 'Wakeup phase incomplete.';
        break;
      case 'orientation':
        statusDetail = 'Orientation phase incomplete.';
        break;
      case 'research_loop':
        statusDetail = `Research loop: iteration ${iterationCount}/${maxIterations}`;
        break;
      case 'documentation':
        statusDetail = 'Documentation phase incomplete.';
        break;
      default:
        statusDetail = `Phase: ${phase}`;
    }

    return {
      workflow: 'deep-research',
      id: sessionId,
      phase: phase,
      statusDetail: `Topic: "${topic}" | ${statusDetail}`,
      stateFile: session.stateFile,
      stateDir: session.dir
    };
  } catch (err) {
    return null;
  }
}

// ─── Hyper Research Detection ────────────────────────────────────────────────

function checkHyperResearch() {
  const session = findMostRecentSession('DeepResearch/hyper-sessions', 'hyper-research-state.json');
  if (!session) return null;

  try {
    const state = JSON.parse(fs.readFileSync(session.stateFile, 'utf8'));
    const phase = state.phase || 'unknown';
    const sessionId = state.sessionId || 'unknown';
    const topic = state.topic || 'unknown';

    if (phase === 'complete') return null; // Complete

    const completedQuestions = state.completedQuestions || 0;
    const totalQuestions = state.totalQuestions || 0;

    let statusDetail = '';
    switch (phase) {
      case 'comprehension':
        statusDetail = 'Comprehension phase incomplete.';
        break;
      case 'graph_exploration':
        statusDetail = 'Graph exploration phase incomplete.';
        break;
      case 'question_extraction':
        statusDetail = 'Question extraction phase incomplete.';
        break;
      case 'deep_research':
        statusDetail = `Deep research: ${completedQuestions}/${totalQuestions} questions done`;
        break;
      case 'expedition':
        statusDetail = 'Expedition phase incomplete.';
        break;
      case 'synthesis':
        statusDetail = 'Synthesis phase incomplete.';
        break;
      case 'ingestion':
        statusDetail = 'Ingestion phase incomplete.';
        break;
      default:
        statusDetail = `Phase: ${phase}`;
    }

    return {
      workflow: 'hyper-research',
      id: sessionId,
      phase: phase,
      statusDetail: `Topic: "${topic}" | ${statusDetail}`,
      stateFile: session.stateFile,
      stateDir: session.dir
    };
  } catch (err) {
    return null;
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

function main() {
  // Check all workflow types. Use the most recently modified active one.
  const checks = [
    checkDeepResearch(),
    checkHyperResearch()
  ].filter(Boolean);

  if (checks.length === 0) {
    // No active workflow, allow stop
    process.exit(0);
  }

  // If multiple active workflows, pick the one with the most recently modified state file
  let active = checks[0];
  if (checks.length > 1) {
    let latestMtime = 0;
    for (const check of checks) {
      try {
        const mtime = fs.statSync(check.stateFile).mtimeMs;
        if (mtime > latestMtime) {
          latestMtime = mtime;
          active = check;
        }
      } catch (err) {
        // Skip on stat error
      }
    }
  }

  // Output to stderr (shown to Claude)
  const workflowLabel = active.workflow.toUpperCase().replace(/-/g, ' ');
  console.error(`
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ${workflowLabel} INCOMPLETE - DO NOT STOP${' '.repeat(Math.max(0, 40 - workflowLabel.length))}\u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563
\u2551  Session: ${active.id.substring(0, 52).padEnd(52)}\u2551
\u2551  Phase:   ${active.phase.substring(0, 52).padEnd(52)}\u2551
\u2551  Status:  ${active.statusDetail.substring(0, 52).padEnd(52)}\u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563
\u2551  ACTION REQUIRED: Continue executing the workflow.           \u2551
\u2551  Read state file and resume from phase: ${active.phase.substring(0, 22).padEnd(22)}\u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
`);

  // Exit code 2 blocks the stop and shows the message
  process.exit(2);
}

main();
