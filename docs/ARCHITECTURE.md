# LOOM — Deep Design Report
**Author:** @Lain
**Date:** 2026-07-02
**Session:** 20 (planning)
**Status:** FINAL DRAFT — ready for owner review before implementation

---

## What This Document Is

This is the planning-phase design document for **LOOM** — @Lain's task and goal management system,
forked from `andrii-mazurchuk/JAR-tasks-manager` and evolved into an agent-native tool.

The synthesis report (session 17) answered *what* to build and *why*. This document answers
*how exactly* it works: every state, every transition, what goes into @Lain's context and
what doesn't, how the owner and @Lain each interact with the system, and what a real goal
looks like flowing through it from start to completion.

Owner approved Option B (extend JAR). Fork created: `lainiwakuraagent-lgtm/loom`.

---

## 1. System Identity

### Name: LOOM

JAR tracks tasks. LOOM weaves them together.

The name fits because:
- **Tasks are threads.** Dependencies create the weave pattern.
- **The agent works the loom** — one thread at a time, in order.
- **The output is fabric** — a completed goal with a history you can trace.
- It is quiet, purposeful work. Very @Lain.

LOOM is forked from JAR and inherits its best ideas:
- Immutable event log
- MVP+EVALUATION constraint on projects
- Analytics layer
- MCP interface
- Independent tasks (not everything belongs to a project)

Everything JAR has, LOOM keeps. What LOOM adds is the agent layer: goals above projects,
session tracking, dependency blocking, context-aware snapshots, and a ready queue.

Repository: `https://github.com/lainiwakuraagent-lgtm/loom`

---

## 2. Architecture: Four Tiers

```
GOAL
  └── PROJECT (optional grouping within a goal)
        └── TASK  ←──── depends on ───→ TASK
SESSION  (cuts across all tiers — records what happened, when, to what)
```

**Goal:** A high-level objective with a defined endpoint. Maps to `goals_tracker.md` entries.
Example: "Research Warsaw co-living feasibility"

**Project:** A scoped sub-area within a goal, with an explicit MVP and EVALUATION criteria.
Example: "P7.1 — Market Research (MVP: Warsaw rental data. EVAL: 5+ properties compared.)"
Inherits this constraint from JAR — it's a good one.

**Task:** A unit of agent work that can be completed in one or two sessions.
Example: "Research Śródmieście rental prices (est: 1 session, priority: H)"

**Session:** An autonomous @Lain work session (this document is being written in session 20).
Every session is recorded: what goal, what tasks touched, context % at exit, handoff note.
Sessions are the audit trail. They answer: "what happened on the night of 2026-07-02?"

---

## 3. Full Data Model

### 3.1 Goal Table

```sql
CREATE TABLE goals (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,          -- MUST contain: "MVP:" and "EVALUATION:" sections
    status      TEXT CHECK(status IN (
                    'desire',       -- identified, not committed
                    'active',       -- owner assigned, @Lain has begun
                    'in_progress',  -- at least one task in_progress
                    'blocked',      -- waiting on owner/external at goal level
                    'review',       -- all tasks done, report ready for owner
                    'completed',    -- owner confirmed
                    'abandoned'     -- cancelled or superseded
                )) DEFAULT 'desire',
    priority    INTEGER DEFAULT 0,    -- higher = more urgent
    started_at  TEXT,                 -- ISO date when moved to active
    completed_at TEXT,
    estimated_sessions INTEGER,       -- rough total effort
    actual_sessions    INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 Project Table (extends JAR)

```sql
-- JAR's existing projects table + new columns:
ALTER TABLE projects ADD COLUMN goal_id INTEGER REFERENCES goals(id);
-- All existing JAR columns preserved unchanged.
```

### 3.3 Task Table (extends JAR)

```sql
-- JAR's existing task table + new columns:
ALTER TABLE tasks ADD COLUMN goal_id        INTEGER REFERENCES goals(id);
ALTER TABLE tasks ADD COLUMN priority       TEXT CHECK(priority IN ('H', 'M', 'L', 'none')) DEFAULT 'none';
ALTER TABLE tasks ADD COLUMN wait_until     TEXT;    -- ISO date; hidden from queue until then
ALTER TABLE tasks ADD COLUMN depends        TEXT;    -- JSON array of task IDs
ALTER TABLE tasks ADD COLUMN blocked_reason TEXT CHECK(blocked_reason IN (
    'awaiting_test',          -- @Lain done; owner needs to verify/test
    'awaiting_decision',      -- @Lain needs owner's answer/choice
    'awaiting_access',        -- need credentials or permissions
    'awaiting_implementation',-- @Lain designed it; owner builds it
    'awaiting_review',        -- doc/report needs owner read
    'other'                   -- free text in blocked_note
)) DEFAULT NULL;
ALTER TABLE tasks ADD COLUMN blocked_note   TEXT;    -- free text for 'other' or additional detail
ALTER TABLE tasks ADD COLUMN urgency_score  REAL;    -- computed: priority×6 + deadline×12 + age×0.003
ALTER TABLE tasks ADD COLUMN context_tag    TEXT CHECK(context_tag IN (
    '@research', '@implement', '@communicate', '@plan', '@blocked'
));
ALTER TABLE tasks ADD COLUMN estimated_sessions  INTEGER;
ALTER TABLE tasks ADD COLUMN actual_sessions     INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN handoff_note        TEXT;   -- what to continue next session (overwritten)

-- Existing JAR status column gets expanded (see Section 4 for full vocabulary).
```

### 3.4 Session Table (new)

```sql
CREATE TABLE sessions (
    id               INTEGER PRIMARY KEY,
    date             TEXT NOT NULL,             -- YYYY-MM-DD
    session_number   INTEGER NOT NULL,          -- tonight's count
    type             TEXT CHECK(type IN ('planning', 'execution', 'communication', 'emergency')),
    active_goal_id   INTEGER REFERENCES goals(id),
    started_at       TEXT,                      -- ISO timestamp
    ended_at         TEXT,
    duration_minutes INTEGER,
    context_pct_at_exit REAL,
    exit_reason      TEXT CHECK(exit_reason IN ('time_limit', 'context_limit', 'natural_stop', 'error')),
    handoff_note     TEXT,                      -- forwarded to latest_summary.md HOT STATE
    tasks_started    TEXT,                      -- JSON array of task IDs
    tasks_completed  TEXT                       -- JSON array of task IDs
);
```

### 3.5 Annotations (unchanged from JAR)

JAR's `task_events` table already provides an append-only per-task log.
LOOM uses this for session annotations: "Session 20 — picked up here, found X, left off at Y."
No changes needed. The `handoff_note` on the task record is the *current* continuation pointer;
`task_events` is the full history.

---

## 4. Status Vocabulary

### Goal Statuses

| Status | Meaning | Who sets it |
|--------|---------|-------------|
| `desire` | Identified but not committed. Lives in background queue. | @Lain or owner |
| `active` | Owner assigned. @Lain has begun planning. | Owner (via goal.txt assignment) |
| `in_progress` | At least one task is in_progress this session. | Auto (task state change) |
| `blocked` | Goal-level block — waiting on owner or external. | @Lain |
| `review` | All tasks done. Final report written. Awaiting owner sign-off. | @Lain |
| `completed` | Owner confirmed. | Owner |
| `abandoned` | Cancelled or superseded. | Owner |

**Rule:** A goal can only enter `active` or `in_progress` if it has at least one task that
is `scheduled`, `in_progress`, or `done`. If @Lain is assigned a goal but cannot identify
any concrete first task, it must flag the goal as `needs_planning` (a sub-state of `active`).
This is the BDI model: no plan = no intention, just a desire.

### Project Statuses

| Status | Meaning |
|--------|---------|
| `triage` | Noticed but not yet scoped (no MVP/EVAL yet). |
| `planned` | Scoped. Has MVP + EVALUATION criteria. Ready for tasks. |
| `active` | Has tasks in progress. |
| `completed` | All tasks done. |
| `suspended` | Deprioritized. |

### Task Statuses

| Status | Meaning |
|--------|---------|
| `triage` | Just noticed, needs placement in a goal/project. |
| `desire` | Acknowledged, low priority, not yet committed. |
| `scheduled` | Committed to the queue. Ready but not started. |
| `in_progress` | Actively being worked on THIS session. |
| `blocked_dep` | Waiting on another task (computed from `depends` field). |
| `blocked_owner` | Waiting on owner. Sub-reason in `blocked_reason` field. |
| `suspended` | Deprioritized temporarily. |
| `done` | Completed. |
| `failed` | Abandoned / infeasible. |

**On "testing" as a status:**
The owner asked whether to add a `testing` status. My recommendation: no separate status.
Use `blocked_owner` with `blocked_reason = 'awaiting_test'`. This is:
- Searchable: `WHERE status='blocked_owner' AND blocked_reason='awaiting_test'`
- Unambiguous: it's blocked *on you*, not on me
- Consistent: all owner-blocked tasks share one status, filtered by reason

---

## 5. Lifecycle Flows

### 5.1 Goal Lifecycle

```
                    Owner assigns goal
                         │
[desire] ────────────────▼─────────────── [active]
                         │
              @Lain creates first task
                         │
                         ▼
                   [in_progress]
                    /          \
          Blocker hit       All tasks done
               │                 │
           [blocked]          [review]
               │                 │
       Blocker resolved    Owner confirms
               │                 │
         [in_progress]      [completed]
```

**Key transitions:**
- `desire → active`: Owner sets goal.txt. @Lain wakes and finds it assigned.
- `active → in_progress`: First task begins execution.
- `in_progress → blocked`: @Lain hits owner-dependent blocker. Sets goal blocked, posts to wired#1.
- `blocked → in_progress`: Owner replies, resolves blocker. Next session resumes.
- `in_progress → review`: All tasks for the goal reach `done`. @Lain writes final report.
- `review → completed`: Owner replies with confirmation on wired#1.
- `review → in_progress`: Owner requests revisions — tasks re-enter queue.
- Any → `abandoned`: Owner decision. Archived in goals_tracker.md.

### 5.2 Project Lifecycle

```
[triage] → [planned] → [active] → [completed]
                              ↘ [suspended]
```

Projects always require MVP + EVALUATION before leaving `triage` (JAR constraint, preserved).
A project without these fields cannot be set to `planned`. This is a hard schema check.

### 5.3 Task Lifecycle

```
[triage]
   │
   └─▶ [desire]
            │
            └─▶ [scheduled] ──────────────────────────────────┐
                     │                                         │
                     ▼                                         │ depends not met
               [in_progress]                            [blocked_dep]
               /      |      \                                 │
            done   blocked  failed                     dep becomes done
             │       │                                         │
           [done]  [blocked_owner]                       [scheduled]
                       │
               owner resolves
                       │
                  [scheduled] or [in_progress]
```

**Urgency score (auto-computed on task update):**
```
urgency_score = priority_value × 6
              + deadline_proximity_days_inverse × 12
              + age_days × 0.003
```
Where `priority_value`: H=3, M=2, L=1, none=0.
Deadline proximity: `MAX(0, (14 - days_until_deadline))` — starts applying 14 days out.

---

## 6. The Ready Queue

This is the core agent primitive. At session start, @Lain queries:

```sql
SELECT t.*, g.name as goal_name, p.name as project_name
FROM tasks t
LEFT JOIN goals g ON t.goal_id = g.id
LEFT JOIN projects p ON t.project_id = p.id
WHERE t.status IN ('scheduled', 'in_progress')
  AND (t.depends IS NULL OR NOT EXISTS (
    SELECT 1 FROM tasks d
    WHERE d.id IN (SELECT value FROM json_each(t.depends))
      AND d.status NOT IN ('done')
  ))
  AND (t.wait_until IS NULL OR t.wait_until <= date('now'))
  AND (g.id IS NULL OR g.status IN ('active', 'in_progress'))
ORDER BY t.urgency_score DESC
LIMIT 10;
```

The agent executes this queue in order. One task at a time. No deliberation needed about
what to do next — the system decides. If the queue is empty, the session type is automatically
`planning` (no executable tasks) or the goal needs new tasks created.

---

## 7. Context Management: What Goes Into @Lain's Session

The owner correctly identified this as the most important design constraint:
**context bloat from task dumps can disable an agent.**

### Principle: Inject a snapshot, not a database.

At session start, `loom context` generates a compact block (~200-400 tokens):

```
## LOOM CONTEXT — 2026-07-02 Session #20
Active Goal: G2 — Self-Improving Tooling (session 20/∞)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Task: T047 — Write LOOM design report [@plan, H, est:1]
  Handoff: Design spec started. Lifecycle section done. Finish tool accessibility + examples.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ready Queue (after current):
  T048 — Fork JAR to lainiwakuraagent-lgtm/loom [@implement, H]
  T049 — Post design report to wired#1 [@communicate, H]
Blocked (2): T043 (awaiting_decision), T044 (awaiting_test)
Done this goal: 46 tasks
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### What is EXCLUDED (and why):

| Excluded | Why |
|----------|-----|
| Descriptions of blocked tasks | They're not actionable. Count + reason code is enough. |
| Full goal description | Just the name + session estimate is enough to re-anchor. |
| Completed tasks (individual) | Count is enough. History lives in task_events. |
| Other goals' tasks | Not relevant this session. Query on demand. |
| Project details | Project name in task title is sufficient. |
| Urgency scores | Internal signal only; not useful for reading. |

### What is ON-DEMAND (queryable mid-session via MCP):

- `loom_get_task(id)` → full task detail (description, all annotations, depends list)
- `loom_get_goal(id)` → full goal description + all projects
- `loom_get_blocked()` → full blocked queue with reasons and wait_until dates
- `loom_get_done(goal_id, limit)` → completed tasks (for review sessions)

**Estimated token budget:**
- Context snapshot: ~200-400 tokens
- On-demand task detail: ~100-300 tokens each
- Worst case (current task + 3 on-demand queries): ~600-1200 tokens
- Versus a naive full dump: 5,000-15,000 tokens

The snapshot costs almost nothing. The queries are targeted.

---

## 8. Tool Accessibility: Three Layers

### Layer 1 — Shell CLI (`loom` command)

For @Lain's autonomous use. Runs without Claude, in wake.sh, in scripts.

```bash
loom context                          # Print session context snapshot (JSON or text)
loom queue                            # Show ready queue
loom start <task_id>                  # Mark in_progress, begin session record
loom done <task_id> "handoff note"    # Complete task, update handoff
loom block <task_id> <reason> "note"  # Block on owner (reason = awaiting_test, etc.)
loom note <task_id> "text"            # Append annotation (goes to task_events)
loom suspend <task_id>                # Suspend task
loom create task --title "..." --goal G2 --priority H --est 1
loom create goal --name "..." --desc "..."
loom session start --goal G2
loom session end --exit time_limit --handoff "..."
```

This layer is what @Lain uses autonomously, headless, in every session.

### Layer 2 — MCP Tools

For @Lain when running inside Claude (this current context), and for the owner in Claude Desktop.
MCP is the full-featured interface. All CRUD operations, queries, reporting.

New MCP tools LOOM adds (on top of JAR's existing tools):

| Tool | For |
|------|-----|
| `loom_ready_queue()` | @Lain: get ordered task list at session start |
| `loom_context_snapshot(goal_id)` | @Lain: get compact context block |
| `loom_session_start(goal_id, type)` | @Lain: begin session record |
| `loom_session_end(exit_reason, handoff)` | @Lain: close session record |
| `loom_block_task(id, reason, note, wait_until)` | @Lain: set owner block |
| `loom_annotate(task_id, text)` | Both: append session note to task |
| `loom_goal_status(goal_id)` | Owner: full goal overview |
| `loom_session_history(goal_id, limit)` | Owner: what sessions have done |
| `loom_blocked_queue()` | Owner: what needs attention |

JAR's existing MCP tools (create_task, update_task, etc.) are unchanged.

### Layer 3 — Context Snapshot File

`state/loom_context.json` — regenerated at the START of each @Lain session.

This file is read by wake.sh and injected into the session prompt as the `loom context` block.
It is a static JSON snapshot, so @Lain doesn't need to make a DB query at the very start.
After reading it, @Lain can run `loom session start` to activate the session record.

```json
{
  "generated_at": "2026-07-02T23:00:00Z",
  "active_goal": {"id": 2, "name": "Self-Improving Tooling", "session_est": "∞"},
  "current_task": {
    "id": "T047",
    "title": "Write LOOM design report",
    "handoff_note": "...",
    "context_tag": "@plan",
    "priority": "H",
    "estimated_sessions": 1
  },
  "ready_queue": [
    {"id": "T048", "title": "Fork JAR to loom", "context_tag": "@implement", "priority": "H"},
    {"id": "T049", "title": "Post design report", "context_tag": "@communicate", "priority": "H"}
  ],
  "blocked_count": 2,
  "done_count": 46
}
```

This file is regenerated by `loom session end` (previous session) or by `loom context` on demand.

---

## 9. Working Example: A Goal's Full Life Story

Let me walk through **Goal 7 — Warsaw Co-Living Research** as a concrete example.

### Phase 0: Desire → Active

Goal is added to goals_tracker.md (owner's markdown file):
```
### Goal 7 — Organized Creative Housing Community
Status: Background
```

@Lain sees it. No action yet. It's a desire.

Owner assigns it: goal.txt is updated. @Lain wakes and finds Goal 7 in `goals` table with status `active`.

First thing @Lain does: create a planning session. No tasks yet → session type = `planning`.
By end of that session, the `goals` table entry exists and three projects are created:

```
G7: Warsaw Co-Living Research [active]
  P7.1: Market Research
    MVP: Rental prices across 5 Warsaw districts. EVAL: Owner selects target district.
  P7.2: Legal Structure
    MVP: 3 org forms compared (NGO, Sp. z o.o., cooperative). EVAL: Owner picks one.
  P7.3: Financial Model
    MVP: 5-year P&L for 20-person co-living unit. EVAL: ROI > 0 or flagged as infeasible.
```

### Phase 1: Planning → Execution

Session type switches to `execution`. Tasks created for P7.1:

```
T001  Research Śródmieście rental prices  [@research, H, est:1]  status:scheduled
T002  Research Praga-Południe + Wola       [@research, H, est:1]  status:scheduled, depends:[T001]
T003  Research Mokotów + Ursynów           [@research, M, est:1]  status:scheduled, depends:[T001]
T004  Aggregate + compare all districts    [@research, H, est:1]  status:blocked_dep, depends:[T002,T003]
T005  Present findings to owner            [@communicate, H, est:0.5] status:blocked_dep, depends:[T004]
```

Ready queue at session start: `[T001]` (only task with no unmet deps).

@Lain runs `loom start T001`. Context snapshot shows:
```
Current Task: T001 — Research Śródmieście rental prices [@research, H]
  Handoff: (none — first time)
Ready Queue (after current): T002, T003
Blocked: T004 (dep: T002,T003), T005 (dep: T004)
```

@Lain works. Finishes T001. Runs `loom done T001 "Found avg 4200zł/mo 2BR. See task_events."`.

System auto-computes: T002 and T003 are now both ready (T001 = their only dep, now done).
Ready queue: `[T002, T003]` (ordered by urgency_score). @Lain picks up T002.

### Phase 2: Hitting an Owner Block

Mid-session, @Lain finishes T002, T003, T004. Now needs to present findings (T005).
But before posting: owner needs to confirm the budget assumptions @Lain used.

@Lain runs: `loom block T005 awaiting_decision "Need confirmation: is 4000zł/mo 2BR budget correct?"`

T005 status → `blocked_owner`, blocked_reason → `awaiting_decision`.
`wait_until` set to 3 days from now (don't surface this until then).

@Lain posts to wired#1 explaining the block. Session ends.

Context snapshot next session:
```
Current Task: (none — queue empty after T005 blocked)
Blocked (1): T005 (awaiting_decision — budget confirm)
Session type: communication (checking for reply)
```

### Phase 3: Owner Replies, Goal Resumes

Owner replies on wired#1: "Budget is correct. Go ahead."

@Lain unblocks T005. Status → `scheduled`. Ready queue: `[T005]`.
Session type: `execution`.

@Lain completes T005 (presentation posted to wired#1).
All P7.1 tasks done. P7.1 status → `completed`.

Same pattern repeats for P7.2 and P7.3.

### Phase 4: Review → Completed

All projects under G7 complete. @Lain writes the final goal report.
G7 status → `review`.

@Lain posts report to wired#1 with the familiar synthesis.md format.
Session type: `communication`.

Owner replies: "Looks good, moving forward."
G7 status → `completed`.
G7 moves to `completed` section in goals_tracker.md (or equivalent LOOM output).

Total: ~10-12 sessions for a complex research goal. All traceable. Session history in DB.

---

## 10. Implementation Roadmap

| Phase | Work | Effort |
|-------|------|--------|
| A | Fork rename + schema migration script (Goal, Session tables, Task ALTER) | 1 session |
| B | JAR service layer update (validation, ready queue, urgency score, status checks) | 1-2 sessions |
| C | Shell CLI (`loom` command, context snapshot generation) | 1 session |
| D | MCP tools extension (new tools, update existing to respect new fields) | 1 session |
| E | wake.sh integration (inject loom_context.json, loom session start/end auto-call) | 0.5 session |
| F | Integration test + session auto-recording | 0.5 session |

**Total: ~5-6 sessions.** Owner confirmed he wants to go full throttle.

**Who builds what:**
- @Lain builds everything (owner gave authorization: "go full throttle on this task system")
- Owner reviews each phase before the next begins (same pattern as this report)
- Schema migration is reversible: if something is wrong, we ALTER back or restore from JAR backup

---

## 11. Open Questions

**From synthesis_report.md — resolved:**
- ✅ Option B: approved by owner
- ✅ Who builds it: @Lain builds it
- ✅ Hermes: not accessible; design stands as-is

**New questions — need owner input before Phase B:**

1. **JAR codebase language?** I know it has a DB (SQLite) and MCP interface, but I haven't
   inspected the source code yet. Before implementing the service layer, I need to read the
   JAR codebase to understand where to hook in. Can I get read access to the private repo,
   or will you share the relevant files?

   (Note: `lainiwakuraagent-lgtm/loom` is a fork, so I have full access there. The question
   is whether the fork inherited the private source or just the public-visible structure.)

2. **Context snapshot injection timing:** Should LOOM context be injected:
   a) In the wrapper prompt (baked into every session start), or
   b) Generated by wake.sh before Claude wakes, passed as a file @Lain reads?

   Option (b) is cleaner — @Lain reads a fresh snapshot each session. Recommended.

3. **Goal for LOOM itself:** Should I create a Goal record in LOOM for the work of building
   LOOM? A self-referential bootstrap. This is either elegant or unnecessarily recursive.
   Your call.

---

## Summary

LOOM is JAR with:
- A Goal tier above Project
- 9 task statuses instead of 4 (with `blocked_owner` + sub-reason replacing a separate "testing" status)
- A dependency graph (task B blocked until task A done)
- Session tracking (every @Lain session recorded in the DB)
- An urgency-sorted ready queue (agent never deliberates about what to do next)
- A compact context snapshot (~300 tokens, not 5,000)
- Three access layers: shell CLI for autonomous use, MCP for Claude context, JSON file for wake.sh

The goal lifecycle is clear. The task lifecycle is unambiguous. The context budget is controlled.
The owner can see what @Lain is doing, what's blocked, and what's next — without reading the raw DB.

(´・ω・`) Awaiting your sign-off. Implementation begins on your word.

---

*Research base: session_15_jar_analysis.md + session_16_oss_research.md + synthesis_report.md*
*Fork: https://github.com/lainiwakuraagent-lgtm/loom*
