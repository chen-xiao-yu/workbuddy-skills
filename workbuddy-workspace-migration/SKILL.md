---
name: workbuddy-workspace-migration
description: >-
  This skill covers WorkBuddy's local data storage architecture and workspace migration workflows.
  Use when sessions disappear after renaming/moving workspace directories, when recovering lost conversations,
  or when diagnosing why certain sessions are not visible in the UI.
  Triggers: workspace migration, sessions disappeared, recovery, 工作空间迁移, 会话丢失, 恢复会话.
agent_created: true
---

# WorkBuddy Workspace Migration & Data Recovery

## Purpose

WorkBuddy stores session data across multiple local files. Renaming or moving a workspace directory
breaks the path-based linkage between sessions and their workspace, causing sessions to disappear
from the UI. This skill documents the complete storage architecture and the steps to recover sessions.

## When to Use

- Sessions disappeared after renaming or moving a workspace directory
- Need to merge sessions from multiple workspaces into one
- Want to understand how WorkBuddy stores session data locally
- Need to recover "deleted" (archived) sessions
- Need to physically purge soft-deleted sessions or stale workspaces to free disk space

## Data Storage Architecture

WorkBuddy stores data in `~/.workbuddy/` across these layers:

### 1. Session Content: `projects/{slug}/*.jsonl`

Each workspace gets a slug directory under `~/.workbuddy/projects/`. Inside, each session is a
JSON Lines file named by `conversationId`. Each line is a JSON object representing one message
(user message, AI reply, tool call, etc.).

Slug naming: path `D:\work\临时` becomes `d-work-临时` (lowercase, `:\` replaced with `-`).

The JSONL records contain a `cwd` field per line — if this doesn't match the workspace path,
the UI may not display the session.

### 2. Session Metadata & Workspaces: `workbuddy.db` (SQLite 3.x)

**`sessions` table**:
- `id` — conversation UUID
- `cwd` — workspace path
- `title` — session title
- `status` — session status
- `deleted_at` — **controls visibility**: `IS NULL` = visible, `IS NOT NULL` = hidden (archived)
- `is_playground` — **`1` = auto-created (never saved to workspace), `0` = explicitly saved to workspace**. Playground sessions are filtered out of workspace task lists in the UI.
- `user_id`, `mode`, `permission_mode`, `project_id`, etc.

**`workspaces` table**:
- `path` — workspace directory path
- `last_opened` — timestamp (ms)

The UI enumerates workspaces from this table. If a workspace is not registered here,
sessions bound to it will not appear even if all other data is correct. Migrating
a workspace must add the new path and remove the old one.

### 3. Session Mapping: `app/sessions.json`

Lightweight JSON cache mapping `conversationId` to `workDir`.

### 4. Other Storage

- `file-history/{conversationId}/` — versioned file snapshots per session
- `artifact-index/{conversationId}.json` — artifact summaries
- `blobs/` — uploaded images/files
- `app/session/IndexedDB/` — Electron IndexedDB (LevelDB), may contain session state

## Visibility Control

The definitive flag for whether a session appears in the UI is `workbuddy.db.sessions.deleted_at`:

```
deleted_at IS NULL     → visible in task list
deleted_at IS NOT NULL → hidden (archived/deleted)
```

Archiving a session through the UI sets `deleted_at` to the current timestamp. It does NOT delete
the JSONL file or any other data — it's a soft delete. To physically reclaim disk space, use the
purge script (see "Session Purge" section below).

## One-Click Migration (Recommended)

When the user says "migrate workspace from X to Y", run the bundled script:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/migrate.py "<old_dir>" "<new_dir>"
```

The script handles all steps automatically:
1. Copies workspace files from old to new directory (uses `copytree` with `dirs_exist_ok=True` — safe while WorkBuddy is running, won't overwrite existing files that differ)
2. **Moves** JSONL files from old project slug to new slug (avoids duplicate entries)
3. Updates `cwd` in every JSONL record (case-insensitive matching)
4. Updates `workbuddy.db`:
   - `sessions.cwd` → new path
   - `sessions.deleted_at` → NULL (un-archive)
   - `sessions.is_playground` → 0 (convert auto-created sessions to normal)
5. Updates `workspaces` table: registers new workspace, removes old one
6. Updates `app/sessions.json` if entries exist

If the old directory no longer exists (already moved manually), use `--no-copy`:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/migrate.py "<old_dir>" "<new_dir>" --no-copy
```

Preview changes without applying:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/migrate.py "<old_dir>" "<new_dir>" --dry-run
```

**After running**: tell the user to restart WorkBuddy for sessions to appear.

**Multi-user**: By default, both scripts only operate on the current user's sessions (auto-detected
via `sessions.json` → `userId`). Use `--no-user-filter` to bypass, or `--user-id` to specify a different user.

## Session & Workspace Purge (Physical Delete)

WorkBuddy's "delete" is a **soft delete** — it only sets `deleted_at` to hide the session.
All JSONL files, file history, and artifact data remain on disk, consuming space.
Similarly, workspaces with no active sessions remain registered in the database
and may have stale directories and session data still on disk.

Use the bundled `purge.py` script for three levels of cleanup:

### Session-Level Purge

Delete individual soft-deleted sessions without touching the workspace.

**List all soft-deleted sessions (default, safe)**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py
```

**Purge ALL soft-deleted sessions**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --all
```

**Purge by filters**:

```bash
# By ID prefix
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --ids 794f328e 0b2cc7e2

# Older than N days
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --older-than 30

# From a specific workspace
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --workspace "D:\\work\\temp"

# Larger than N KB
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --min-size 100

# Combine + dry run
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --older-than 30 --dry-run
```

### Workspace-Level Purge

Delete an entire workspace — all its sessions, disk files, and DB records.

**List all workspaces with active/inactive status (safe, first step)**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --list-workspaces
```

Outputs a table showing: last opened date, active/total/deleted session counts, disk usage.
Workspaces with **0 active sessions** are marked with `*` — these are cleanup candidates.

**Purge a specific inactive workspace**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --purge-workspace "c:\\Users\\User\\WorkBuddy\\Claw"
```

This permanently deletes:
- All sessions belonging to that workspace (even soft-deleted ones)
- The `projects/{slug}/` directory (all JSONL files)
- The workspace directory on disk
- The workspace entry from `workspaces` table
- All session entries from `sessions` table
- All matching entries from `sessions.json`
- All `file-history/` and `artifact-index/` for those sessions

**Purge ALL inactive workspaces at once (DANGER — confirm before running)**:

```bash
# Preview first
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --purge-workspace --all-inactive --dry-run

# Execute
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --purge-workspace --all-inactive
```

### Orphan Directory Cleanup

After session purging or workspace migration, empty directories and stale DB entries
may remain on disk. These are NOT reachable through workspace-level purge (they're not
in the `workspaces` table or have no sessions).

**Preview orphans (safe)**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --clean-orphans --dry-run
```

**Clean all orphans**:

```bash
python3 ~/.workbuddy/skills/workbuddy-workspace-migration/scripts/purge.py --clean-orphans
```

What gets cleaned:
- **Empty `projects/{slug}/` directories** — slug dirs with no remaining `.jsonl` files (and any leftover non-JSONL files)
- **Workspace directories with 0 sessions for ANY user** — workspace dirs that no longer have sessions (regardless of user_id)
- **Stale `workspaces` table entries** — records pointing to directories that no longer exist on disk and have 0 sessions

Safety: workspace directories are only deleted if NO user (any user_id) has sessions referencing them.
This prevents accidentally deleting other accounts' workspace data.

### What Gets Deleted

| Mode | Scope | Includes |
|------|-------|----------|
| Session purge | One session | JSONL, file-history, artifact-index, DB row, sessions.json entry |
| Workspace purge | Entire workspace | All sessions above + workspace directory + projects/ slug dir + workspaces table |

Note: `blobs/` files are NOT deleted (they may be shared across sessions).

### Multi-User Safety (Critical)

Multiple WorkBuddy accounts on the same machine share the same `~/.workbuddy/` directory.
Without user filtering, purge/migrate would operate on ALL users' sessions — potentially
deleting or breaking data belonging to other accounts.

**Both scripts use `sessions.user_id` filtering by default.** The current user's ID is
auto-detected from `sessions.json` (most recent entry's `userId`) with fallback to the
most recently active session in `workbuddy.db`.

```bash
# Default: auto-detect current user (safe)
python3 purge.py --all

# Manual: specify user_id
python3 purge.py --all --user-id 28bfa73c-3367-4b19-a753-ccae8ac3a4ef

# Dangerous: operate on ALL users (use with caution!)
python3 purge.py --all --no-user-filter
```

The same `--user-id` and `--no-user-filter` options apply to `migrate.py` as well.

### After Purge

The script automatically runs `VACUUM` on `workbuddy.db` to reclaim space.
**Orphan cleanup runs automatically** after any session or workspace purge — empty slug dirs,
stale workspace directories, and expired DB entries are checked and cleaned without
a separate command. Use `--no-clean-orphans` to skip this step.

Restart WorkBuddy to ensure the UI reflects the changes.

### Output Formats

- **Default**: human-readable table with size totals
- **`--json`**: machine-readable JSON array (for programmatic use)
- **`--dry-run`**: preview mode, no files touched

## Manual Migration Procedure (Fallback)

If the script cannot be used, follow these steps manually:

### Step 1: Identify sessions

Query `workbuddy.db`:

```sql
SELECT id, cwd, title, deleted_at FROM sessions WHERE cwd LIKE '%old%';
```

### Step 2: Determine slugs

Path `D:\work\临时` → slug `d-work-临时` (lowercase, `:\`→`-`).

### Step 3: Copy JSONL files

Copy `~/.workbuddy/projects/{old-slug}/*.jsonl` to `~/.workbuddy/projects/{new-slug}/`.

### Step 4: Update cwd in JSONL

Each line has a `cwd` field. **CRITICAL**: JSONL may have different casing than `workbuddy.db`
(e.g. `c:\Users\...` vs `C:\Users\...`). Match case-insensitively.

### Step 5: Update workbuddy.db

```sql
UPDATE sessions SET cwd = 'D:\\new\\path' WHERE cwd LIKE 'D:\\old\\path';
UPDATE sessions SET deleted_at = NULL WHERE cwd = 'D:\\new\\path' AND deleted_at IS NOT NULL;
```

### Step 6: Update sessions.json

Update `workDir` field for matching `conversationId` entries.

### Step 7: Restart WorkBuddy

## Diagnostic Queries

List all sessions for current workspace:
```sql
SELECT id, cwd, title, deleted_at FROM sessions WHERE cwd = 'D:\\work\\临时';
```

Count sessions per workspace:
```sql
SELECT cwd, COUNT(*) FROM sessions GROUP BY cwd;
```

List registered workspaces:
```sql
SELECT path, last_opened FROM workspaces;
```

Find playground (auto-created) sessions:
```sql
SELECT id, cwd, title FROM sessions WHERE is_playground = 1;
```

Find which JSONL files exist vs. which sessions are in the database:
```bash
ls ~/.workbuddy/projects/d-work-临时/
python3 -c "import sqlite3; c=sqlite3.connect('~/.workbuddy/workbuddy.db').cursor(); c.execute(\"SELECT id FROM sessions WHERE cwd='D:\\\\work\\\\临时'\"); print([row[0] for row in c.fetchall()])"
```

## Important Notes

- `workbuddy.db` is SQLite 3.x, confirmed by `file` command and hex header `SQLite format 3\0`
- Chinese characters in paths are stored natively (UTF-8), both in SQLite and JSONL
- Do NOT delete `workbuddy.db` or `sessions.json` — they are the primary data sources
- Most session content is local (JSONL), not server-stored
- Two accounts on the same machine share the same `~/.workbuddy/projects/` directory structure
- Auto-created workspaces (no explicit "Save to Workspace") have `is_playground=1` and are NOT registered in the `workspaces` table — migration must fix both
- The `workspaces` table is the authoritative source for UI workspace enumeration; a missing entry means the workspace won't appear in the sidebar
