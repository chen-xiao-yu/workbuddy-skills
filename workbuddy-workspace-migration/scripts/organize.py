#!/usr/bin/env python3
"""
WorkBuddy session organizer — pick sessions from a workspace and move them
to another (often new) workspace. Useful for the "事后聚类" workflow:
you dump everything into a temp workspace, then later pick related sessions
and group them into a dedicated workspace.

Usage:
    # List sessions in a workspace (default, safe)
    python organize.py "D:\\work\\临时"

    # Pick sessions #0, #2, #5 and move to a new workspace
    python organize.py "D:\\work\\临时" --pick 0,2,5 --to "D:\\work\\infra"

    # Preview without actually moving
    python organize.py "D:\\work\\临时" --pick 0,2,5 --to "D:\\work\\infra" --dry-run

    # Pick by session ID prefix instead of index
    python organize.py "D:\\work\\临时" --pick-ids 794f328e 0b2cc7e2 --to "D:\\work\\infra"

Options:
    --pick I1,I2,I3       Pick sessions by list index (from the listing)
    --pick-ids ID1 ID2    Pick sessions by ID prefix
    --to PATH             Target workspace (will be created if missing)
    --dry-run             Preview without moving
    --user-id ID          Filter to this user_id (auto-detected if omitted)
    --no-user-filter      Operate on ALL users (dangerous)
"""

import os
import sys
import json
import shutil
import sqlite3
import argparse
import re
import time
from datetime import datetime, timezone


WORKBUDDY_HOME = os.path.expanduser('~/.workbuddy')


# ============================================================
#  Helpers (shared logic with migrate.py / purge.py)
# ============================================================

def dir_to_slug(path):
    """Convert a Windows path to WorkBuddy project slug format."""
    p = path.rstrip('\\/')
    slug = p.lower()
    slug = slug.replace(':\\', '-')
    slug = slug.replace('\\', '-')
    slug = slug.replace('/', '-')
    slug = re.sub(r'-+', '-', slug)
    return slug


def normalize_path(path):
    return os.path.normpath(path).rstrip('\\/')


def paths_match(a, b):
    return a.lower().replace('\\', '/') == b.lower().replace('\\', '/')


def format_size(bytes_val):
    if bytes_val == 0:
        return '0B'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f'{bytes_val:.0f}{unit}'
        bytes_val /= 1024
    return f'{bytes_val:.1f}TB'


def detect_current_user():
    """Auto-detect current user_id from sessions.json or most recent session."""
    sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
    if os.path.exists(sessions_json):
        try:
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            best, best_time = None, ''
            for s in data.get('sessions', []):
                r = s.get('resumedAt', s.get('startedAt', ''))
                if r > best_time:
                    best_time, best = r, s
            if best and best.get('userId'):
                return best['userId']
        except Exception:
            pass

    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""SELECT user_id FROM sessions
                         WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1""")
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
    return None


# ============================================================
#  Session listing
# ============================================================

def get_session_disk_size(session_id):
    """Calculate disk size of a session (JSONL + file-history + artifact)."""
    total = 0
    projects_dir = os.path.join(WORKBUDDY_HOME, 'projects')
    if os.path.isdir(projects_dir):
        for slug in os.listdir(projects_dir):
            jsonl_path = os.path.join(projects_dir, slug, f'{session_id}.jsonl')
            if os.path.exists(jsonl_path):
                total += os.path.getsize(jsonl_path)
                break
    fh_dir = os.path.join(WORKBUDDY_HOME, 'file-history', session_id)
    if os.path.isdir(fh_dir):
        for root, _, files in os.walk(fh_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    ai_path = os.path.join(WORKBUDDY_HOME, 'artifact-index', f'{session_id}.json')
    if os.path.exists(ai_path):
        total += os.path.getsize(ai_path)
    return total


def find_jsonl_path(session_id):
    """Locate the JSONL file for a session across all slug directories."""
    projects_dir = os.path.join(WORKBUDDY_HOME, 'projects')
    if os.path.isdir(projects_dir):
        for slug in os.listdir(projects_dir):
            jsonl_path = os.path.join(projects_dir, slug, f'{session_id}.jsonl')
            if os.path.exists(jsonl_path):
                return jsonl_path
    return None


def list_sessions(workspace_path, user_id=None):
    """List all visible sessions in a workspace, newest first."""
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    if user_id:
        c.execute("""
            SELECT id, cwd, title, updated_at, is_playground, deleted_at
            FROM sessions
            WHERE user_id = ? AND deleted_at IS NULL
            ORDER BY updated_at DESC
        """, (user_id,))
    else:
        c.execute("""
            SELECT id, cwd, title, updated_at, is_playground, deleted_at
            FROM sessions
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC
        """)

    sessions = []
    for sid, cwd, title, updated, playground, deleted in c.fetchall():
        if cwd and paths_match(cwd, workspace_path):
            size = get_session_disk_size(sid)
            updated_str = datetime.fromtimestamp(
                updated / 1000 if updated > 1e12 else updated
            ).strftime('%m-%d %H:%M') if updated else '??-?? ??:??'
            sessions.append({
                'id': sid,
                'title': (title or '(no title)')[:60],
                'size': size,
                'updated': updated_str,
                'updated_ts': updated or 0,
                'is_playground': playground,
            })
    conn.close()
    return sessions


def print_session_list(sessions):
    """Print sessions in a table format with index numbers."""
    print(f"{'#':>3}  {'Updated':12s}  {'Size':>8s}  Title")
    print(f"{'-'*3}  {'-'*12}  {'-'*8}  {'-'*60}")
    for i, s in enumerate(sessions):
        idx = f"[{i}]"
        print(f"{idx:>3}  {s['updated']:12s}  {format_size(s['size']):>8s}  {s['title']}")
    total = sum(s['size'] for s in sessions)
    print(f"\nTotal: {len(sessions)} sessions, {format_size(total)}")


# ============================================================
#  Move logic (one session at a time)
# ============================================================

def move_session(session_id, src_workspace, dst_workspace, user_id=None, dry_run=False):
    """Move a single session from src_workspace to dst_workspace.

    Handles all four storage layers, similar to migrate.py but scoped to one session.
    """
    moved = []

    # ---- Layer 1: JSONL file ----
    jsonl_path = find_jsonl_path(session_id)
    if not jsonl_path:
        moved.append(f"  WARNING: JSONL not found for {session_id[:8]}")
    else:
        src_slug = dir_to_slug(src_workspace)
        dst_slug = dir_to_slug(dst_workspace)
        dst_proj_dir = os.path.join(WORKBUDDY_HOME, 'projects', dst_slug)
        dst_jsonl = os.path.join(dst_proj_dir, f'{session_id}.jsonl')

        if not dry_run:
            os.makedirs(dst_proj_dir, exist_ok=True)
            # If file already in target slug (e.g., moved before), skip
            if os.path.abspath(jsonl_path) != os.path.abspath(dst_jsonl):
                shutil.move(jsonl_path, dst_jsonl)

        # Fix cwd inside JSONL records
        if not dry_run:
            target = dst_jsonl
        else:
            target = jsonl_path  # dry-run reads from source

        if os.path.exists(target):
            fixed = 0
            records = []
            with open(target, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        records.append({'_raw': line})
                        continue
                    cwd = rec.get('cwd', '')
                    if cwd and paths_match(cwd, src_workspace):
                        if not dry_run:
                            rec['cwd'] = dst_workspace
                        fixed += 1
                    records.append(rec)

            if not dry_run and fixed > 0:
                with open(target, 'w', encoding='utf-8') as f:
                    for rec in records:
                        if '_raw' in rec:
                            f.write(rec['_raw'])
                        else:
                            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            moved.append(f"  JSONL: {fixed} cwd refs updated")

    # ---- Layer 2: workbuddy.db sessions row ----
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    if not dry_run:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("UPDATE sessions SET cwd = ?, deleted_at = NULL, is_playground = 0 WHERE id = ?",
                  (dst_workspace, session_id))
        conn.commit()
        conn.close()
    moved.append("  DB: cwd updated, deleted_at cleared, is_playground=0")

    # ---- Layer 3: sessions.json ----
    sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
    if os.path.exists(sessions_json) and not dry_run:
        try:
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            changed = False
            for s in data.get('sessions', []):
                if s.get('conversationId') == session_id:
                    s['workDir'] = dst_workspace
                    changed = True
            if changed:
                with open(sessions_json, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                moved.append("  sessions.json: entry updated")
        except Exception as e:
            moved.append(f"  sessions.json: skipped ({e})")

    # ---- Layer 4: ensure dst workspace registered ----
    if not dry_run:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM workspaces WHERE path = ?", (dst_workspace,))
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO workspaces (path, last_opened_at) VALUES (?, ?)",
                      (dst_workspace, int(time.time() * 1000)))
            conn.commit()
            moved.append(f"  workspaces: registered {dst_workspace}")
        conn.close()

    # file-history and artifact-index don't need migration — they are keyed
    # by session_id only, no path reference. Leave them in place.
    return moved


# ============================================================
#  Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Pick sessions from a workspace and move them to another',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List sessions in a workspace
  python organize.py "D:\\work\\临时"

  # Pick #0, #2, #5 and move to a new workspace
  python organize.py "D:\\work\\临时" --pick 0,2,5 --to "D:\\work\\infra"

  # Preview first
  python organize.py "D:\\work\\临时" --pick 0,2,5 --to "D:\\work\\infra" --dry-run
""",
    )

    parser.add_argument('workspace', help='Source workspace path')
    parser.add_argument('--pick', metavar='I1,I2,...',
                        help='Comma-separated list indices to pick (e.g. 0,2,5)')
    parser.add_argument('--pick-ids', nargs='+', metavar='ID',
                        help='Pick sessions by ID prefix instead of list index')
    parser.add_argument('--to', metavar='PATH',
                        help='Target workspace path (created if missing)')
    parser.add_argument('--dry-run', action='store_true', help='Preview without moving')
    parser.add_argument('--user-id', metavar='ID', help='Filter to this user_id')
    parser.add_argument('--no-user-filter', action='store_true',
                        help='Operate on ALL users (dangerous)')
    args = parser.parse_args()

    # Resolve user_id
    user_id = args.user_id
    if not user_id and not args.no_user_filter:
        user_id = detect_current_user()
        if not user_id:
            print("Warning: Could not auto-detect user_id.")
            print("Use --user-id to specify, or --no-user-filter to skip.")
            sys.exit(1)

    src_workspace = normalize_path(args.workspace)

    # ---- Mode 1: List only ----
    if not args.pick and not args.pick_ids:
        sessions = list_sessions(src_workspace, user_id=user_id)
        if not sessions:
            print(f"No visible sessions in {src_workspace}")
            return
        user_hint = f" (user: {user_id[:8]}...)" if user_id else " (all users)"
        print(f"\n=== Sessions in {src_workspace}{user_hint} ===\n")
        print_session_list(sessions)
        print(f"\nTo pick sessions:")
        print(f'  organize.py "{src_workspace}" --pick 0,2,5 --to "D:\\work\\new" --dry-run')
        return

    # ---- Mode 2: Pick and move ----
    if not args.to:
        print("ERROR: --to is required when using --pick or --pick-ids")
        sys.exit(1)

    dst_workspace = normalize_path(args.to)

    if paths_match(src_workspace, dst_workspace):
        print("ERROR: source and target are the same workspace")
        sys.exit(1)

    sessions = list_sessions(src_workspace, user_id=user_id)
    if not sessions:
        print(f"No visible sessions in {src_workspace}")
        return

    # Resolve picked sessions
    picked = []
    if args.pick:
        try:
            indices = [int(x.strip()) for x in args.pick.split(',')]
        except ValueError:
            print(f"ERROR: invalid --pick value '{args.pick}', expected like 0,2,5")
            sys.exit(1)
        for i in indices:
            if i < 0 or i >= len(sessions):
                print(f"ERROR: index {i} out of range (0-{len(sessions)-1})")
                sys.exit(1)
            picked.append(sessions[i])
    elif args.pick_ids:
        prefixes = [p.lower() for p in args.pick_ids]
        for s in sessions:
            if any(s['id'].lower().startswith(p) for p in prefixes):
                picked.append(s)
        if len(picked) != len(prefixes):
            matched_prefixes = {p['id'][:len(prefixes[0])].lower() for p in picked}
            missing = [p for p in prefixes if p not in matched_prefixes]
            print(f"WARNING: could not find sessions matching: {', '.join(missing)}")

    if not picked:
        print("No sessions matched the selection.")
        return

    # Preview
    if args.dry_run:
        print(f"\n*** DRY RUN ***\n")

    total_size = sum(s['size'] for s in picked)
    print(f"Move {len(picked)} session(s), {format_size(total_size)}")
    print(f"  from: {src_workspace}")
    print(f"  to:   {dst_workspace}\n")

    print(f"{'#':>3}  {'Size':>8s}  Title")
    print(f"{'-'*3}  {'-'*8}  {'-'*60}")
    for s in picked:
        print(f"     {format_size(s['size']):>8s}  {s['title']}")

    if args.dry_run:
        print(f"\n*** DRY RUN — would move {len(picked)} session(s). ***")
        return

    # Execute
    print(f"\nMoving...\n")
    for s in picked:
        print(f"[{s['id'][:8]}] {s['title']}")
        parts = move_session(s['id'], src_workspace, dst_workspace, user_id=user_id, dry_run=False)
        for p in parts:
            print(p)

    # If source workspace now has 0 visible sessions for any user, optionally clean up?
    # — leave it alone, user can run purge.py --clean-orphans later.

    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    conn.execute("VACUUM")
    conn.close()

    print(f"\nDone. Moved {len(picked)} session(s) to {dst_workspace}")
    print(f"Restart WorkBuddy to see them in the new workspace.")
    print(f"\nTip: if {src_workspace} is now empty, run:")
    print(f'  purge.py --clean-orphans')


if __name__ == '__main__':
    main()
