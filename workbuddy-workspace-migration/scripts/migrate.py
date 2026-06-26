#!/usr/bin/env python3
"""
WorkBuddy workspace migration script.

Migrates all sessions from one workspace directory to another, syncing four
storage layers: workspace files, JSONL conversation records, workbuddy.db
metadata, sessions.json mapping, and workspaces table.

Usage:
    python migrate.py <old_dir> <new_dir> [--no-copy] [--dry-run]

Arguments:
    old_dir     Source workspace path (e.g. C:\\Users\\User\\WorkBuddy\\project)
    new_dir     Target workspace path (e.g. D:\\work\\migrated)
    --no-copy   Skip file copy step (useful when files already moved manually)
    --dry-run   Preview changes without making changes

Examples:
    python migrate.py "C:\\Users\\User\\WorkBuddy\\old" "D:\\work\\new"
    python migrate.py "C:\\Users\\User\\WorkBuddy\\old" "D:\\work\\new" --no-copy

IMPORTANT — What this script does NOT do:
-----------------------------------------
This script rewrites the `cwd` field in every JSONL record (so the session
shows up in the new workspace's UI), but it does NOT rewrite the absolute
`file_path` arguments embedded in Write/Edit/MultiEdit tool calls.

Consequence: after migration, if you continue an existing conversation and
ask the AI to "edit foo.py", the AI will look up the historical file_path
(which still points at the OLD workspace) and edit the file there. The
copy of foo.py in the NEW workspace becomes a dead file that nobody touches.

This is fine for the original use case (recovering sessions that disappeared
after a workspace rename, where you just want to read old conversations).
But if you intend to KEEP WORKING on the project after migration, the
recommended workflow is:

    1. Run migrate.py to bring sessions + files into the new workspace
    2. Start a FRESH session in the new workspace
    3. Paste key conclusions / architecture decisions from the old session
       as context into the new session
    4. All subsequent file operations will use the new cwd naturally

Rewriting file_path inside JSONL records is possible (the data is there),
but is deliberately not implemented because:
  - Deciding which paths to rewrite is non-trivial (some paths may point
    outside the workspace, to system directories or other workspaces)
  - URL-encoded paths in artifact-index add encoding-corruption risk
  - The "start a fresh session" workflow sidesteps the issue entirely
"""

import os
import sys
import json
import shutil
import sqlite3
import argparse
import re
import time


def detect_current_user():
    """Auto-detect the current user's user_id.

    Priority:
    1. sessions.json — most recent entry's userId
    2. workbuddy.db — user_id with the most recent active session
    """
    WORKBUDDY_HOME = os.path.expanduser('~/.workbuddy')

    # Method 1: sessions.json
    sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
    if os.path.exists(sessions_json):
        try:
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            entries = data.get('sessions', [])
            best = None
            best_time = ''
            for s in entries:
                resumed = s.get('resumedAt', s.get('startedAt', ''))
                if resumed > best_time:
                    best_time = resumed
                    best = s
            if best and best.get('userId'):
                return best['userId']
        except Exception:
            pass

    # Method 2: most recent active session in DB
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                SELECT user_id FROM sessions
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass

    return None


def dir_to_slug(path):
    """Convert a Windows path to WorkBuddy project slug format.
    C:\\Users\\User\\Foo Bar -> c-Users-User-Foo Bar
    D:\\work\\临时 -> d-work-临时
    """
    # Remove trailing separator
    p = path.rstrip('\\/')
    # Lowercase and replace :\ with -
    slug = p.lower()
    slug = slug.replace(':\\', '-')
    slug = slug.replace('\\', '-')
    slug = slug.replace('/', '-')
    # Collapse multiple dashes
    slug = re.sub(r'-+', '-', slug)
    return slug


def normalize_path(path):
    """Normalize a path for consistent comparison."""
    p = os.path.normpath(path).rstrip('\\/')
    return p


def paths_match(a, b):
    """Case-insensitive path comparison."""
    return a.lower().replace('\\', '/') == b.lower().replace('\\', '/')


def main():
    parser = argparse.ArgumentParser(description='Migrate WorkBuddy workspace')
    parser.add_argument('old_dir', help='Source workspace directory path')
    parser.add_argument('new_dir', help='Target workspace directory path')
    parser.add_argument('--no-copy', action='store_true',
                        help='Skip copying files from old to new directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without making changes')
    parser.add_argument('--user-id', metavar='ID',
                        help='Filter operations to this user_id. Auto-detected if omitted.')
    parser.add_argument('--no-user-filter', action='store_true',
                        help='Disable user_id filtering (operate on ALL users — use with caution)')
    args = parser.parse_args()

    WORKBUDDY_HOME = os.path.expanduser('~/.workbuddy')
    OLD_DIR = normalize_path(args.old_dir)
    NEW_DIR = normalize_path(args.new_dir)

    # Determine user_id for filtering
    user_id = args.user_id
    if not user_id and not args.no_user_filter:
        user_id = detect_current_user()
        if not user_id:
            print("Warning: Could not auto-detect user_id. Run with --no-user-filter to skip, or --user-id to specify.")
            sys.exit(1)

    if OLD_DIR.lower() == NEW_DIR.lower():
        print("ERROR: old and new directories are the same. Nothing to do.")
        sys.exit(1)

    # Determine slugs
    old_slug = dir_to_slug(OLD_DIR)
    new_slug = dir_to_slug(NEW_DIR)

    print(f"Old workspace: {OLD_DIR}  →  slug: {old_slug}")
    print(f"New workspace: {NEW_DIR}  →  slug: {new_slug}")
    if args.dry_run:
        print("*** DRY RUN — no changes will be made ***")
    print()

    # ============================================================
    # Step 1: Copy workspace files (if old dir exists)
    # ============================================================
    if not args.no_copy:
        if os.path.isdir(OLD_DIR):
            if args.dry_run:
                print(f"[DRY RUN] Would copy: {OLD_DIR} → {NEW_DIR}")
            else:
                print(f"Copying workspace files: {OLD_DIR} → {NEW_DIR}")
                os.makedirs(os.path.dirname(NEW_DIR), exist_ok=True)
                if os.path.exists(NEW_DIR):
                    print(f"  WARNING: {NEW_DIR} already exists, merging files")
                shutil.copytree(OLD_DIR, NEW_DIR, dirs_exist_ok=True)
                print("  Done.")
        else:
            print(f"  Old directory not found: {OLD_DIR}")
            print("  (Files already moved? Use --no-copy to skip this step.)")
        print()

    # ============================================================
    # Step 2: Move JSONL files from old slug to new slug
    # ============================================================
    projects_dir = os.path.join(WORKBUDDY_HOME, 'projects')
    old_proj = os.path.join(projects_dir, old_slug)
    new_proj = os.path.join(projects_dir, new_slug)

    if not os.path.isdir(old_proj):
        print(f"  WARNING: Project slug directory not found: {old_proj}")
        print(f"  No JSONL files to migrate.")
        jsonl_files = []
    else:
        jsonl_files = [f for f in os.listdir(old_proj) if f.endswith('.jsonl')]
        print(f"Found {len(jsonl_files)} JSONL files to migrate")

        if not args.dry_run:
            os.makedirs(new_proj, exist_ok=True)
            for fname in jsonl_files:
                src = os.path.join(old_proj, fname)
                dst = os.path.join(new_proj, fname)
                shutil.move(src, dst)

        # Update cwd in each JSONL — match case-insensitively
        # (JSONL may have lowercase drive letter while DB has uppercase)
        for fname in jsonl_files:
            filepath = os.path.join(new_proj, fname)
            if not os.path.exists(filepath):
                # dry-run: file hasn't been moved yet, read from old_proj
                filepath = os.path.join(old_proj, fname)

            records = []
            fixed = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    rec = json.loads(line)
                    cwd = rec.get('cwd', '')
                    if paths_match(cwd, OLD_DIR):
                        if not args.dry_run:
                            rec['cwd'] = NEW_DIR
                        fixed += 1
                    records.append(rec)

            if not args.dry_run and fixed > 0:
                with open(filepath, 'w', encoding='utf-8') as f:
                    for rec in records:
                        f.write(json.dumps(rec, ensure_ascii=False) + '\n')

            print(f"  {fname}: {fixed} cwd references updated")
        print()

    # ============================================================
    # Step 3: Update workbuddy.db (cwd + deleted_at + is_playground)
    # ============================================================
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')

    if not os.path.exists(db_path):
        print(f"ERROR: workbuddy.db not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Find sessions matching old cwd (case-insensitive), scoped to current user
    if user_id:
        c.execute("SELECT id, cwd, deleted_at, is_playground FROM sessions WHERE user_id = ?",
                  (user_id,))
    else:
        c.execute("SELECT id, cwd, deleted_at, is_playground FROM sessions")
    all_sessions = c.fetchall()

    target_sessions = []
    for sid, cwd, deleted, playground in all_sessions:
        if cwd and paths_match(cwd, OLD_DIR):
            target_sessions.append((sid, cwd, deleted, playground))

    print(f"Found {len(target_sessions)} sessions in workbuddy.db to update")

    if not args.dry_run:
        playground_fixed = 0
        for sid, old_cwd, deleted, playground in target_sessions:
            # Update cwd
            c.execute("UPDATE sessions SET cwd = ? WHERE id = ? AND cwd = ?",
                      (NEW_DIR, sid, old_cwd))
            # Clear deleted_at
            c.execute("UPDATE sessions SET deleted_at = NULL WHERE id = ?",
                      (sid,))
            # Fix is_playground: auto-created sessions have is_playground=1,
            # which prevents them from appearing in workspace task lists
            if playground == 1:
                c.execute("UPDATE sessions SET is_playground = 0 WHERE id = ?",
                          (sid,))
                playground_fixed += 1

        conn.commit()

        status_parts = ["cwd updated", "deleted_at cleared"]
        if playground_fixed > 0:
            status_parts.append(f"is_playground: 1→0 ({playground_fixed} sessions)")
        print(f"  {', '.join(status_parts)}")
    else:
        for sid, cwd, deleted, playground in target_sessions:
            flags = []
            if deleted is not None:
                flags.append("deleted")
            if playground == 1:
                flags.append("playground")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            print(f"  [DRY RUN] {sid[:16]}... cwd={cwd}{flag_str}")
    print()

    # ============================================================
    # Step 4: Update workspaces table
    #   - Register new workspace if not already present
    #   - Remove old workspace if all its sessions were migrated
    # ============================================================
    import time
    now_ts = int(time.time() * 1000)

    # Register new workspace
    c.execute("SELECT COUNT(*) FROM workspaces WHERE path = ?", (NEW_DIR,))
    exists = c.fetchone()[0]
    if exists > 0:
        print(f"workspaces: {NEW_DIR} already registered — updating last_opened")
        if not args.dry_run:
            c.execute("UPDATE workspaces SET last_opened = ? WHERE path = ?",
                      (now_ts, NEW_DIR))
    else:
        print(f"workspaces: registering {NEW_DIR}")
        if not args.dry_run:
            c.execute("INSERT INTO workspaces (path, last_opened) VALUES (?, ?)",
                      (NEW_DIR, now_ts))

    # Remove old workspace if no sessions remain with that cwd
    c.execute("SELECT COUNT(*) FROM sessions")
    sessions_remaining = c.execute(
        "SELECT COUNT(*) FROM sessions WHERE cwd = ?", (next(
            (s[1] for s in all_sessions if paths_match(s[1], OLD_DIR)),
            OLD_DIR
        ),)
    ).fetchone()[0]

    # After update, check if ANY session still points to old cwd or its variants
    old_cwds = set()
    for _, cwd, _, _ in all_sessions:
        if cwd and paths_match(cwd, OLD_DIR):
            old_cwds.add(cwd)

    if not args.dry_run:
        # Re-query to check actual remaining count (case-insensitive)
        remaining = 0
        for cwd_val in old_cwds:
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ?", (cwd_val,))
            remaining += c.fetchone()[0]

        if remaining == 0:
            c.execute("DELETE FROM workspaces WHERE path = ?", (OLD_DIR,))
            print(f"workspaces: removed old workspace {OLD_DIR}")
        else:
            # Also try removing exact match
            c.execute("SELECT path FROM workspaces")
            ws_paths = [row[0] for row in c.fetchall()]
            for wp in ws_paths:
                if paths_match(wp, OLD_DIR):
                    c.execute("DELETE FROM workspaces WHERE path = ?", (wp,))
                    print(f"workspaces: removed old workspace {wp}")
                    break
    else:
        print(f"[DRY RUN] Would register {NEW_DIR} in workspaces")
        print(f"[DRY RUN] Would remove old workspace {OLD_DIR} from workspaces table")

    conn.commit()
    conn.close()
    print()

    # ============================================================
    # Step 5: Update sessions.json
    # ============================================================
    sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')

    if os.path.exists(sessions_json):
        with open(sessions_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        updated = 0
        for s in data.get('sessions', []):
            old_wd = s.get('workDir', '')
            if paths_match(old_wd, OLD_DIR):
                if not args.dry_run:
                    s['workDir'] = NEW_DIR
                updated += 1

        if not args.dry_run and updated > 0:
            with open(sessions_json, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"sessions.json: {updated} entries updated")
    else:
        print("sessions.json not found — skipping")

    print()
    if args.dry_run:
        print("*** DRY RUN complete. Run without --dry-run to apply changes. ***")
    else:
        print("Migration complete. Restart WorkBuddy to see the sessions.")


if __name__ == '__main__':
    main()
