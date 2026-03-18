# AGENTS.md

This repository supports multiple coding agents (Codex and Claude Code).

## Purpose
Use this file for stable operating rules and collaboration protocol.
Do not use this file as a running project log.

## Shared Brain (Single Source of Truth)
- Brain file: `PROJECT_BRAIN.md`
- All agents must read `PROJECT_BRAIN.md` before making changes.
- All agents must update `PROJECT_BRAIN.md` after meaningful work.

## Required Brain Updates
After completing work, update these fields in `PROJECT_BRAIN.md`:
- `Last updated`
- `What changed`
- `Current status`
- `Next steps`
- `Open decisions`
- `Blockers` (if any)

## File Roles
- `AGENTS.md`: Stable instructions, workflow rules, and handoff protocol.
- `PROJECT_BRAIN.md`: Living project memory, plan, decisions, and progress.
- `CLAUDE.md`: Pointer file for Claude users to follow shared brain + AGENTS rules.

## Agent Workflow
1. Read `AGENTS.md`.
2. Read `PROJECT_BRAIN.md`.
3. Execute scoped task.
4. Update `PROJECT_BRAIN.md` with outcomes and next actions.
5. Keep scope aligned to current POC boundaries unless explicitly expanded.

## Guardrails
- Preserve POC boundary unless owner expands scope.
- Prefer retrieval quality and data correctness over model complexity.
- Keep source priority rules consistent with `PROJECT_BRAIN.md`.
- Surface uncertainty instead of guessing when product facts conflict.
