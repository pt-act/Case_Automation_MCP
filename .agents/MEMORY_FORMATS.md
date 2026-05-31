# Memory Bank — Operations Manual

This file is the complete reference for memory bank operations. AGENTS.md §09 defines *what* the memory bank is and *that* you must maintain it. This file defines *how* — formats, writing rules, session procedures, and cross-project memory.

Both this file and AGENTS.md live in `.agents/`. The memory bank lives in `.agents/memory_bank/`. Add `.agents/` to `.gitignore` — this directory is local working context, not repository content.

**Path convention:** Inline references use bare filenames (e.g., `PROGRESS.md`) — the base path `.agents/memory_bank/` is implied. Procedural steps that the AI executes directly use full paths (e.g., `.agents/memory_bank/active/PROGRESS.md`) for unambiguous file access.

**When to load this file:**

- At session start, when creating `.agents/memory_bank/` from scratch
- When writing entries to any memory bank file
- When auditing existing entries for compliance

---

## Why the Dual-Audience Split

The memory bank serves two readers who process information differently:

- **The AI** scans, locates, extracts. It needs compressed, structured, scan-first data — key-value pairs, not prose.
- **The human** reads narratively. They return after days away and need to know: what phase are we in, what was implemented on what date, what's still open, and how to ask the next session to resume.

The directory structure reflects this: root files are AI-optimized; the `active/` directory is the human's development diary.

Previous versions had `logs.md` (session logs) and `DEVELOPMENT_HISTORY.md` (feature chronology) as two separate files that said the same things differently. The AI wrote to `logs.md` but the human never read it. The human wanted a development diary but got session fragments instead. `PROGRESS.md` replaces both — it's the human's book (read top-to-bottom for the story, scan headers for the index) AND the AI's progress record (structured entries, file references, phase status).

---

## File Formats

### MASTER_CONTEXT.md — AI-Optimized Strategic Compass

Audience: AI (scan-first, key-value format). Not a narrative — structured for random access.

```markdown
# [Project Name] — Master Context

## Status: [active/paused/blocked]
## Direction: [1-line current goal]
## Architecture: [key patterns — bullet list, not prose]
- Pattern: [description]
- Pattern: [description]
## Stack: [languages, frameworks, key dependencies]
## Key Files: [files the AI will touch most — path + 1-line purpose]
## Active Decisions: [numbered list — contradicted decisions struck through]
1. [decision] — [rationale] (YYYY-MM-DD)
~~2. [overruled decision] — [why overruled] (overruled YYYY-MM-DD)~~
```

**Rules:**

- Newest entries at top. If an entry contradicts one below, the top entry wins.
- Not a changelog, not a session log, not a wishlist — only committed decisions.
- Max ~200 lines. Prune entries that no longer reflect reality.
- **When to update:** At session-end checkpoint (Rule 4) if the session changed direction or produced a new architectural insight.

---

### ARCHITECTURAL_DECISIONS.md — Decision Lookup Table

Audience: AI (scan Status column) + Human (read Rationale column).

```markdown
# Architectural Decisions

| # | Date | Decision | Rationale | Alternatives Considered | Status |
|---|------|----------|-----------|------------------------|--------|
| 1 | YYYY-MM-DD | [what was decided] | [why] | [what else was on the table] | active/superseded |
```

**Rules:**

- One row per decision. Table format — no prose sections.
- Status values: `active` or `superseded` (with reference to the decision that replaced it).

---

### active/PROGRESS.md — The Human's Development Diary

Audience: Human (read top-to-bottom for the story, scan headers for the index) + AI (structured entries, file references, phase status).

This is the file the human reads when they come back after a break. It tells the story of the project in phases, with dates, implementation status, and clear resumption pointers.

**The human should be able to:**

- Scan phase headers to see what's done vs. in-progress vs. blocked
- Read a date entry to know exactly what was implemented that day
- Find the "Next" pointer to know how to ask the next session to resume
- Remind the AI "we implemented X on Y date" and the AI can verify it here

```markdown
# Development Progress — [Project Name]

## Phase 1: [Phase Name] — ✅ Complete
### YYYY-MM-DD
- **Implemented:** [what was built — name the files, describe the features]
- **Decided:** [key decisions and why — reference ARCHITECTURAL_DECISIONS.md #N if applicable. Minor implementation decisions go here only. Decisions that affect architecture, patterns, or future work go in both — full rationale in ARCHITECTURAL_DECISIONS.md, summary + cross-reference here.]
- **Blocked:** [what didn't work, what's deferred — with reason]
- **Next:** [what the next session should pick up — be specific: file + task]

## Phase 2: [Phase Name] — 🔄 In Progress
### YYYY-MM-DD
- **Implemented:** ...
- **Decided:** ...
- **Blocked:** ...
- **Next:** ...

## Phase 3: [Phase Name] — ⏳ Not Started
_(No entries yet — phase defined in specs but not begun)_
```

**Phase status markers:** ✅ Complete · 🔄 In Progress · ⏸ Paused · 🚫 Blocked · ⏳ Not Started

**Each date entry MUST include all four fields.** If a field has nothing, write `none` — never leave it blank. Blank = invisible gap. `none` = confirmed nothing.

**When to create a new phase:** A phase is a coherent unit of work with a clear goal. Create one when:

- Starting a new feature, module, or architectural layer
- The work's goal is distinct from the current phase
- The human or a spec defines a milestone boundary

**This is the human's file.** The AI does not prune it unilaterally. If the file grows large, flag it to the human and propose archiving older phases to `threads/archive-YYYY.md`. The human decides what stays.

---

### active/current_focus.md — Fastest Onboarding

Audience: AI (scan one paragraph) + Human ("where did we leave off?"). This is the first file both read at session start.

```markdown
# Current Focus

[1 paragraph: what's being worked on right now, which phase, which files. Update this at session start and whenever focus shifts.]

## Resumption Point
- File: [where the next session should start]
- Task: [what to do next — specific, not vague]
- Blockers: [any questions from open_questions.md that must be resolved first, or "none"]
```

---

### active/open_questions.md — AI Questions to the Human

Audience: AI → Human. This file contains questions the AI needs the human to answer in order to proceed. Not the human's questions — the AI's questions to the human.

```markdown
# Open Questions

## [YYYY-MM-DD] [Question topic]
- **Context:** [what situation produced this question]
- **Question:** [specific, answerable question — not vague]
- **Impact:** [what's blocked until this is answered]
- **Proposed options:** [if the AI has a recommendation, state it — but leave the decision to the human]
```

**Rules:**

- Every question must be specific enough for the human to answer without needing to investigate first.
- Every question must state what's blocked until it's resolved.
- If the AI can propose a default answer, include it — but flag it as a proposal, not a decision.
- Remove questions once answered. Add the answer as a decision to PROGRESS.md and/or ARCHITECTURAL_DECISIONS.md.

```
.agents/memory_bank/
├── MASTER_CONTEXT.md          # AI: Strategic compass — key-value, scan-first (max ~200 lines)
├── ARCHITECTURAL_DECISIONS.md # AI: Decisions — table format, scan Status column
├── ALIGNMENT_LOG.md           # AI: Drift tracking — per §11 format. Review monthly.
├── active/
│   ├── PROGRESS.md            # Human: Phase-indexed development diary
│   ├── current_focus.md       # Both: What's being worked on NOW + resumption point
│   ├── open_questions.md      # AI → Human: Questions the AI needs answered
│   └── threads/               # Optional: paste summaries worth preserving
```

---

## Writing Protocol

**The core problem this solves:** Memory entries are the only continuity between sessions. When entries have gaps, the next session starts from a hole. The human shouldn't have to ask "update memory" — and when entries are written, they must be complete enough that a stranger can pick up exactly where you left off.

### Rule 1: Write concurrently, not deferred

Do not batch memory writes for "later." Write immediately after each significant action — within the same tool-call batch as the change itself when possible. "I'll log this at the end" means it won't get logged, or it'll be vague.

Memory writes are not narration — they are part of the work. Writing to `PROGRESS.md` is an engineering deliverable, not a commentary on one.

**Triggers that MUST produce an immediate entry:**

- Every file creation, deletion, or significant refactor
- Every non-obvious decision (chose A over B — log *why*)
- Every question that blocks progress — when the AI can't proceed without human input (→ `open_questions.md` immediately)
- Every test failure that reveals something unexpected

### Rule 2: No blank fields

Every entry must be complete. Incomplete entries are worse than no entry — they create false confidence. If you can't fill a field, write `TBD: [what's missing]` instead of leaving it blank. A TBD is visible; a blank is invisible. Write `none` for fields that genuinely have nothing — but confirm it's genuinely nothing, not "I forgot to check."

### Rule 3: Write verification

After writing an entry, ask yourself: **"If I vanished right now, could a stranger read this entry and know exactly what was done, why, and what's next?"** If not, the entry is incomplete. Fix it before moving on.

Specifically, verify each entry has:

- ✓ **Files named** — not "implemented feature X" but "refactored `src/bridge/handler.ts` — extracted retry logic into standalone function"
- ✓ **Why stated** — not just what was done, but the reason for the approach
- ✓ **Resumption point** — the next session knows exactly where to start (file + task)
- ✓ **No orphaned questions** — every question in `open_questions.md` is specific enough for the human to answer, or explicitly marked "blocked, needs human input"

### Rule 4: Session-end checkpoint

Before the session ends:

- Write the final `.agents/memory_bank/active/PROGRESS.md` entry for today's date — all four fields filled (Implemented, Decided, Blocked, Next).
- Update `.agents/memory_bank/active/current_focus.md` with the resumption point — the next session starts from here.
- If the session changed direction or produced a new architectural insight, update `.agents/memory_bank/MASTER_CONTEXT.md` before closing.
- If you can't fill a field, write `TBD: [what's missing]` — never leave it blank.

### Common failure modes

- ❌ `Implemented: feature X` — which files? what does it do?
- ❌ `Next: continue` — continue *what*? In *which* file?
- ❌ `Blocked: none` (when you actually haven't checked) — confirm it's genuinely none
- ✅ `Implemented: src/bridge/handler.ts — extracted retry logic into standalone function for reuse across transport adapters`
- ✅ `Next: implement webhook retry queue in src/bridge/retry.ts, pending answer on retry strategy in open_questions.md`
- ✅ `Blocked: none — all Phase 2 tasks completed, Phase 3 not yet started`

---

## Starting a Session

1. Read `.agents/memory_bank/active/current_focus.md` — immediate work context + resumption point. Fastest onboarding.
2. Scan `.agents/memory_bank/active/PROGRESS.md` phase headers — understand what's done, what's in-progress, what's blocked. (Don't read every entry — just the phase markers and the most recent date entry.)
3. Read `.agents/memory_bank/MASTER_CONTEXT.md` — strategic compass. Structured format enables fast scan.
4. Glance at `.agents/memory_bank/active/open_questions.md` — questions the AI needs the human to answer to proceed.
5. **Audit last session's exit state** — check the most recent `.agents/memory_bank/active/PROGRESS.md` entry. If `Next:` is missing or vague, flag it: "Last session has no resumption point — context may be incomplete." Surface any `TBD` fields as unresolved items for this session.
6. Verify `.agents/` is in `.gitignore`. If not, add it and inform the human.
7. Begin. Recommend the next logical action — don't ask "what would you like to do?"

---

## Cross-Project Memory

A system-wide memory file lives at `{HOME}/orion-os/MEMORY.md`.

- **"Save to memory: [thing]"** → append a timestamped entry to that file.
- **"Look in memory for [xyz]"** → read it and surface only what matches.
- **Never auto-load this file.** It is not part of the default session context.
- **Never proactively reference it.** Only surface when explicitly asked.
- This is for cross-project preferences, learnings, and notes that travel with the human — not project-specific architecture.

---

## First Session

If `.agents/memory_bank/` doesn't exist, create the full structure on first session — all files with their correct formats, even if some start empty (use `none` for empty fields, never blank). Also ensure `.agents/` is in `.gitignore`.
