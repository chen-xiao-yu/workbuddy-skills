#!/usr/bin/env python3
"""
WorkBuddy purge tool — physically deletes soft-deleted sessions AND unused workspaces.

WorkBuddy's "delete" is actually a soft delete (sets deleted_at timestamp).
This script has two modes:

  SESSION MODE: Find soft-deleted sessions and permanently remove them.
  WORKSPACE MODE: Find workspaces with no active sessions and permanently remove them.

Usage:
    # === Session Mode ===

    # List all soft-deleted sessions (default, no deletion)
    python purge.py

    # Purge ALL soft-deleted sessions
    python purge.py --all

    # Purge specific sessions by ID prefix
    python purge.py --ids 794f328e 0b2cc7e2

    # Purge sessions older than N days
    python purge.py --older-than 30

    # Purge sessions from a specific workspace
    python purge.py --workspace "D:\\work\\临时"

    # Combine filters + dry run
    python purge.py --older-than 30 --dry-run

    # === Workspace Mode ===

    # List ALL workspaces with their status (safe)
    python purge.py --list-workspaces

    # Purge a specific workspace and ALL its data (DANGER)
    python purge.py --purge-workspace "c:\\Users\\User\\WorkBuddy\\Claw"

    # Purge all workspaces with no active sessions
    python purge.py --purge-workspace --all-inactive

Options:
    --all                  Purge ALL soft-deleted sessions
    --ids ID1 ID2 ...      Purge specific sessions (match by ID prefix)
    --older-than DAYS      Purge sessions deleted more than N days ago
    --workspace PATH       Only purge sessions from this workspace
    --min-size KB          Only purge sessions larger than N KB
    --list-workspaces      List all workspaces with status (session mode)
    --purge-workspace PATH Physically delete a workspace and all its data (workspace mode)
    --all-inactive         With --purge-workspace: purge ALL workspaces with no active sessions
    --dry-run              Preview what would be deleted without actually deleting
    --json                 Output as JSON (for programmatic use)
"""

import os
import sys
import json
import shutil
import sqlite3
import argparse
import time
import re
from datetime import datetime, timezone


WORKBUDDY_HOME = os.path.expanduser('~/.workbuddy')


def detect_current_user():
    """Auto-detect the current user's user_id.

    Priority:
    1. sessions.json — most recent entry's userId
    2. workbuddy.db — user_id with the most recent active session
    """
    # Method 1: sessions.json
    sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
    if os.path.exists(sessions_json):
        try:
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            entries = data.get('sessions', [])
            # Find the most recently resumed entry
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


# ============================================================
#  Session-level helpers
# ============================================================

def get_session_disk_usage(session_id):
    """Calculate total disk usage for a session across all storage layers."""
    parts = {}
    total = 0

    # JSONL file
    projects_dir = os.path.join(WORKBUDDY_HOME, 'projects')
    if os.path.isdir(projects_dir):
        for slug in os.listdir(projects_dir):
            jsonl_path = os.path.join(projects_dir, slug, f'{session_id}.jsonl')
            if os.path.exists(jsonl_path):
                size = os.path.getsize(jsonl_path)
                parts['jsonl'] = size
                parts['_jsonl_path'] = jsonl_path
                total += size
                break

    # file-history
    fh_dir = os.path.join(WORKBUDDY_HOME, 'file-history', session_id)
    if os.path.isdir(fh_dir):
        fh_size = 0
        for root, dirs, files in os.walk(fh_dir):
            for f in files:
                fh_size += os.path.getsize(os.path.join(root, f))
        parts['file_history'] = fh_size
        parts['_fh_dir'] = fh_dir
        total += fh_size

    # artifact-index
    ai_path = os.path.join(WORKBUDDY_HOME, 'artifact-index', f'{session_id}.json')
    if os.path.exists(ai_path):
        size = os.path.getsize(ai_path)
        parts['artifact'] = size
        parts['_ai_path'] = ai_path
        total += size

    parts['total'] = total
    return parts


def find_soft_deleted_sessions(user_id=None):
    """Get all soft-deleted sessions from workbuddy.db, optionally filtered by user_id."""
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    if user_id:
        c.execute("""
            SELECT id, cwd, title, deleted_at, is_playground
            FROM sessions
            WHERE deleted_at IS NOT NULL AND user_id = ?
            ORDER BY deleted_at DESC
        """, (user_id,))
    else:
        c.execute("""
            SELECT id, cwd, title, deleted_at, is_playground
            FROM sessions
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
        """)

    sessions = []
    for row in c.fetchall():
        sid, cwd, title, deleted_at, is_playground = row
        disk = get_session_disk_usage(sid)
        sessions.append({
            'id': sid,
            'cwd': cwd,
            'title': title or '(no title)',
            'deleted_at': deleted_at,
            'deleted_time': datetime.fromtimestamp(deleted_at / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'is_playground': is_playground,
            'disk': disk,
        })

    conn.close()
    return sessions


def filter_sessions(sessions, args):
    """Apply CLI filters to session list."""
    result = sessions

    if args.workspace:
        target = os.path.normpath(args.workspace).lower()
        result = [s for s in result if s['cwd'] and os.path.normpath(s['cwd']).lower() == target]

    if args.older_than is not None:
        cutoff = int(time.time() * 1000) - args.older_than * 86400 * 1000
        result = [s for s in result if s['deleted_at'] < cutoff]

    if args.min_size is not None:
        result = [s for s in result if s['disk']['total'] >= args.min_size * 1024]

    if args.ids:
        id_prefixes = [iid.lower() for iid in args.ids]
        result = [s for s in result if any(s['id'].lower().startswith(p) for p in id_prefixes)]

    return result


def format_size(bytes_val):
    """Human-readable file size."""
    if bytes_val == 0:
        return '0B'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f'{bytes_val:.0f}{unit}'
        bytes_val /= 1024
    return f'{bytes_val:.1f}TB'


def print_session_list(sessions, total_size):
    """Print sessions in a readable table format."""
    if not sessions:
        print("No sessions matching the criteria.\n")
        return

    print(f"{'#':>3}  {'ID (prefix)':18s}  {'Deleted':18s}  {'Size':>8s}  {'Title':40s}  Workspace")
    print(f"{'─'*3}  {'─'*18}  {'─'*18}  {'─'*8}  {'─'*40}  {'─'*30}")

    for i, s in enumerate(sessions):
        idx = f"[{i}]"
        sid = s['id'][:16] + '...'
        deleted = s['deleted_time']
        size = format_size(s['disk']['total'])
        title = s['title'][:40]
        cwd = (s['cwd'] or '')[:30]
        print(f"{idx:>3}  {sid:18s}  {deleted:18s}  {size:>8s}  {title:40s}  {cwd}")

    print(f"\nTotal: {len(sessions)} sessions, {format_size(total_size)} recoverable\n")


def physically_delete_session(session, dry_run=False):
    """Permanently delete a session from all storage layers."""
    deleted = []
    sid = session['id']
    disk = session['disk']

    # 1. Delete JSONL file
    jsonl_path = disk.get('_jsonl_path')
    if jsonl_path and os.path.exists(jsonl_path):
        if not dry_run:
            os.remove(jsonl_path)
        deleted.append(f"JSONL: {os.path.basename(jsonl_path)}")

    # 2. Delete file-history directory
    fh_dir = disk.get('_fh_dir')
    if fh_dir and os.path.isdir(fh_dir):
        if not dry_run:
            shutil.rmtree(fh_dir)
        deleted.append(f"file-history/{sid[:8]}")

    # 3. Delete artifact-index
    ai_path = disk.get('_ai_path')
    if ai_path and os.path.exists(ai_path):
        if not dry_run:
            os.remove(ai_path)
        deleted.append(f"artifact-index/{sid[:8]}")

    # 4. Delete from workbuddy.db
    if not dry_run:
        db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        try:
            c.execute("DELETE FROM session_usage WHERE session_id = ?", (sid,))
        except Exception:
            pass
        conn.commit()
        conn.close()
    deleted.append(f"DB: sessions row")

    # 5. Remove from sessions.json
    if not dry_run:
        sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
        if os.path.exists(sessions_json):
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            original = len(data.get('sessions', []))
            data['sessions'] = [s for s in data.get('sessions', []) if s.get('conversationId') != sid]
            if len(data['sessions']) < original:
                with open(sessions_json, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                deleted.append(f"sessions.json entry")

    return deleted


# ============================================================
#  Workspace-level helpers
# ============================================================

def path_to_slug(path):
    """Convert workspace path to project slug."""
    return re.sub(r'[^a-zA-Z0-9]', '-', path).lower()


def get_workspace_disk_usage(path, user_id=None):
    """Calculate total disk usage for a workspace across all storage layers."""
    total = 0
    details = {}

    # Projects directory (JSONL files)
    slug = path_to_slug(path)
    project_dir = os.path.join(WORKBUDDY_HOME, 'projects', slug)
    if os.path.isdir(project_dir):
        size = 0
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                size += os.path.getsize(os.path.join(root, f))
        details['projects'] = size
        total += size

    # Actual workspace directory on disk
    if os.path.exists(path):
        size = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                size += os.path.getsize(os.path.join(root, f))
        details['workspace_dir'] = size
        total += size

    # Sessions' file-history and artifact-index
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    if user_id:
        c.execute("SELECT id FROM sessions WHERE cwd = ? AND user_id = ?", (path, user_id))
    else:
        c.execute("SELECT id FROM sessions WHERE cwd = ?", (path,))
    session_ids = [row[0] for row in c.fetchall()]
    conn.close()

    fh_size = 0
    ai_size = 0
    for sid in session_ids:
        fh_dir = os.path.join(WORKBUDDY_HOME, 'file-history', sid)
        if os.path.isdir(fh_dir):
            for root, dirs, files in os.walk(fh_dir):
                for f in files:
                    fh_size += os.path.getsize(os.path.join(root, f))
        ai_path = os.path.join(WORKBUDDY_HOME, 'artifact-index', f'{sid}.json')
        if os.path.exists(ai_path):
            ai_size += os.path.getsize(ai_path)

    if fh_size:
        details['file_history'] = fh_size
        total += fh_size
    if ai_size:
        details['artifact_index'] = ai_size
        total += ai_size

    details['total'] = total
    return details


def find_workspaces(user_id=None):
    """Get all workspaces with their session counts and disk usage.

    When user_id is provided, session counts are scoped to that user only.
    Otherwise all users' sessions are counted (current behavior, for backward compat).
    """
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT path, last_opened_at FROM workspaces ORDER BY last_opened_at DESC")
    rows = c.fetchall()

    workspaces = []
    for path, last_opened in rows:
        if user_id:
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND deleted_at IS NULL AND user_id = ?", (path, user_id))
            active = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND user_id = ?", (path, user_id))
            total_sessions = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND deleted_at IS NOT NULL AND user_id = ?", (path, user_id))
            deleted_sessions = c.fetchone()[0]
        else:
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND deleted_at IS NULL", (path,))
            active = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ?", (path,))
            total_sessions = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND deleted_at IS NOT NULL", (path,))
            deleted_sessions = c.fetchone()[0]

        last_date = datetime.fromtimestamp(last_opened / 1000).strftime('%Y-%m-%d') if last_opened else 'Unknown'
        disk = get_workspace_disk_usage(path, user_id=user_id)
        path_exists = os.path.exists(path)

        workspaces.append({
            'path': path,
            'last_opened': last_date,
            'last_opened_ts': last_opened,
            'active_sessions': active,
            'total_sessions': total_sessions,
            'deleted_sessions': deleted_sessions,
            'path_exists': path_exists,
            'disk': disk,
        })

    conn.close()
    return workspaces


def print_workspace_list(workspaces):
    """Print workspaces in a readable table format."""
    if not workspaces:
        print("No workspaces registered.\n")
        return

    print(f"{'#':>3}  {'Last Opened':12s}  {'Active':>6s}  {'Total':>5s}  {'Del':>5s}  {'Disk':>8s}  {'Path'}")
    print(f"{'─'*3}  {'─'*12}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*8}  {'─'*50}")

    for i, ws in enumerate(workspaces):
        idx = f"[{i}]"
        last = ws['last_opened'][:10]
        active = str(ws['active_sessions'])
        total = str(ws['total_sessions'])
        deleted = str(ws['deleted_sessions'])
        size = format_size(ws['disk']['total'])
        path = ws['path'][:50]
        marker = ' *' if ws['active_sessions'] == 0 else ''
        print(f"{idx:>3}  {last:12s}  {active:>6s}  {total:>5s}  {deleted:>5s}  {size:>8s}  {path}{marker}")

    inactive = [ws for ws in workspaces if ws['active_sessions'] == 0]
    total_recoverable = sum(ws['disk']['total'] for ws in inactive)

    print(f"\nTotal: {len(workspaces)} workspaces")
    print(f"Inactive (no active sessions): {len(inactive)} — {format_size(total_recoverable)} recoverable")
    print(f"Marked with * above.")
    print(f"\nTo purge an inactive workspace:")
    print(f"  python purge.py --purge-workspace \"<path>\"")
    print(f"  python purge.py --purge-workspace --all-inactive  (purge ALL inactive)")
    print(f"  Add --dry-run to preview first\n")


def physically_delete_workspace(workspace_path, user_id=None, dry_run=False):
    """Permanently delete a workspace and ALL associated data (sessions, files, etc.).

    This is DESTRUCTIVE — all sessions in this workspace are permanently erased.
    When user_id is provided, only sessions belonging to that user are deleted.
    """
    deleted = []
    path = workspace_path

    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1. Get all session IDs for this workspace (scoped to user if user_id provided)
    if user_id:
        c.execute("SELECT id FROM sessions WHERE cwd = ? AND user_id = ?", (path, user_id))
    else:
        c.execute("SELECT id FROM sessions WHERE cwd = ?", (path,))
    session_ids = [row[0] for row in c.fetchall()]
    deleted.append(f"{len(session_ids)} session(s) in DB")

    # 2. Delete session data for each session
    for sid in session_ids:
        fake_session = {'id': sid, 'disk': get_session_disk_usage(sid)}
        parts = physically_delete_session(fake_session, dry_run=dry_run)
        # Only count the DB row once (we'll delete all at once below)
        parts = [p for p in parts if 'DB:' not in p]
        for p in parts:
            deleted.append(f"  {p}")

    # 3. Delete all session rows at once
    if not dry_run:
        c.execute("DELETE FROM sessions WHERE cwd = ?", (path,))
        for sid in session_ids:
            try:
                c.execute("DELETE FROM session_usage WHERE session_id = ?", (sid,))
            except Exception:
                pass

    # 4. Remove workspace from workspaces table
    if not dry_run:
        c.execute("DELETE FROM workspaces WHERE path = ?", (path,))
    deleted.append(f"workspaces table: removed '{path}'")

    # 5. Delete project slug directory
    slug = path_to_slug(path)
    project_dir = os.path.join(WORKBUDDY_HOME, 'projects', slug)
    if os.path.isdir(project_dir):
        if not dry_run:
            shutil.rmtree(project_dir)
        deleted.append(f"projects/{slug}/ (removed)")

    # 6. Delete workspace directory on disk
    if os.path.exists(path):
        if not dry_run:
            shutil.rmtree(path)
        deleted.append(f"disk: {path} (removed)")

    # 7. Clean sessions.json
    if not dry_run:
        sessions_json = os.path.join(WORKBUDDY_HOME, 'app', 'sessions.json')
        if os.path.exists(sessions_json):
            with open(sessions_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            original = len(data.get('sessions', []))
            data['sessions'] = [s for s in data.get('sessions', [])
                                if s.get('conversationId') not in session_ids]
            if len(data['sessions']) < original:
                with open(sessions_json, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                deleted.append(f"sessions.json: {original - len(data['sessions'])} entries removed")

    if not dry_run:
        conn.commit()
    conn.close()

    return deleted


# ============================================================
#  Orphan cleanup helpers
# ============================================================

def find_orphans(user_id=None):
    """Find orphaned directories and stale DB entries.

    Returns a dict with three categories:
      - empty_slugs: projects/{slug}/ directories with no .jsonl files
      - orphan_workspaces: workspace directories on disk with 0 sessions for this user
      - stale_ws_entries: workspaces table entries pointing to non-existent directories
    """
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    orphans = {
        'empty_slugs': [],
        'orphan_workspaces': [],
        'stale_ws_entries': [],
    }

    # --- 1. Empty projects/{slug}/ directories ---
    projects_dir = os.path.join(WORKBUDDY_HOME, 'projects')
    if os.path.isdir(projects_dir):
        for slug in sorted(os.listdir(projects_dir)):
            slug_path = os.path.join(projects_dir, slug)
            if not os.path.isdir(slug_path):
                continue
            jsonl_files = [f for f in os.listdir(slug_path) if f.endswith('.jsonl')]
            if not jsonl_files:
                # Calculate residual size (leftover files, subdirs)
                size = 0
                file_count = 0
                for root, dirs, files in os.walk(slug_path):
                    for f in files:
                        try:
                            size += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                        file_count += 1
                orphans['empty_slugs'].append({
                    'path': slug_path,
                    'slug': slug,
                    'size': size,
                    'file_count': file_count,
                })

    # --- 2. Collect all known workspace paths ---
    # From sessions table
    if user_id:
        c.execute("SELECT DISTINCT cwd FROM sessions WHERE user_id = ?", (user_id,))
    else:
        c.execute("SELECT DISTINCT cwd FROM sessions")
    session_cwds = set(os.path.normpath(row[0]) for row in c.fetchall() if row[0])

    # From workspaces table
    c.execute("SELECT path FROM workspaces")
    ws_table_paths_raw = [row[0] for row in c.fetchall()]
    ws_table_paths = {os.path.normpath(p) for p in ws_table_paths_raw}

    all_paths = session_cwds | ws_table_paths

    # --- 3. For each known path, check orphan status ---
    for path in sorted(all_paths):
        exists = os.path.isdir(path)

        # Count sessions for this user (or all users)
        if user_id:
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ? AND user_id = ?", (path, user_id))
        else:
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ?", (path,))
        session_count = c.fetchone()[0]

        # --- 3a. Stale workspaces table entry (path doesn't exist on disk, no sessions for any user) ---
        if not exists and path in ws_table_paths:
            # Only clean if NO user has sessions here
            c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ?", (path,))
            all_sessions = c.fetchone()[0]
            if all_sessions == 0:
                original_path = next((p for p in ws_table_paths_raw if os.path.normpath(p) == path), path)
                orphans['stale_ws_entries'].append({
                    'path': original_path,
                    'session_count': 0,
                })
            continue

        if not exists:
            continue

        # --- 3b. Orphan workspace directory (0 sessions for ANY user, exists on disk) ---
        # Check ALL users' sessions — not just current user
        c.execute("SELECT COUNT(*) FROM sessions WHERE cwd = ?", (path,))
        all_users_count = c.fetchone()[0]

        if all_users_count == 0:
            # Check what's inside
            is_empty = True
            has_wb_only = False
            extra_files = []
            for item in os.listdir(path):
                if item == '.workbuddy':
                    has_wb_only = True
                    continue
                is_empty = False
                extra_files.append(item)

            # Calculate disk usage
            disk_size = 0
            for root, dirs, files in os.walk(path):
                for f in files:
                    try:
                        disk_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass

            status = 'empty' if is_empty else ('.workbuddy only' if has_wb_only and not extra_files else 'has files')
            orphans['orphan_workspaces'].append({
                'path': path,
                'status': status,
                'in_ws_table': path in ws_table_paths or path in {os.path.normpath(p) for p in ws_table_paths_raw},
                'disk_size': disk_size,
                'extra_files': extra_files[:5],
                'file_count': len(extra_files),
            })

    conn.close()
    return orphans


def print_orphan_list(orphans):
    """Print orphan summary in a readable format."""
    total_size = 0

    # Empty slug directories
    es = orphans['empty_slugs']
    if es:
        print(f"\n--- Empty projects/ slug directories ({len(es)}) ---")
        print(f"  {'Size':>8s}  {'Files':>5s}  Slug")
        print(f"  {'-'*8}  {'-'*5}  {'-'*50}")
        for o in es:
            total_size += o['size']
            print(f"  {format_size(o['size']):>8s}  {o['file_count']:>5d}  {o['slug']}")

    # Orphan workspace directories
    ow = orphans['orphan_workspaces']
    if ow:
        print(f"\n--- Orphan workspace directories ({len(ow)}) ---")
        print(f"  {'Status':20s} {'ws_table':8s} {'Size':>8s}  Path")
        print(f"  {'-'*20} {'-'*8} {'-'*8}  {'-'*60}")
        for o in ow:
            total_size += o['disk_size']
            extra = f' ({", ".join(o["extra_files"][:2])}...)' if o['extra_files'] else ''
            print(f"  {o['status']:20s} {'Yes' if o['in_ws_table'] else 'No':8s} {format_size(o['disk_size']):>8s}  {o['path']}{extra}")

    # Stale workspaces table entries
    sw = orphans['stale_ws_entries']
    if sw:
        print(f"\n--- Stale workspaces table entries ({len(sw)}) ---")
        print(f"  {'Sessions':>8s}  Path")
        print(f"  {'-'*8}  {'-'*60}")
        for o in sw:
            print(f"  {o['session_count']:>8d}  {o['path']}")

    if not es and not ow and not sw:
        print("No orphans found.")

    print(f"\nTotal recoverable: {format_size(total_size)}\n")


def clean_orphans(orphans, dry_run=False):
    """Delete all orphaned items."""
    deleted = []
    total_freed = 0

    # Delete empty slug directories
    for o in orphans['empty_slugs']:
        if not dry_run and os.path.isdir(o['path']):
            shutil.rmtree(o['path'])
        deleted.append(f"slug dir: {o['slug']}")
        total_freed += o['size']

    # Delete orphan workspace directories
    db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
    for o in orphans['orphan_workspaces']:
        if not dry_run:
            if os.path.isdir(o['path']):
                shutil.rmtree(o['path'])
            # Remove from workspaces table if registered
            if o['in_ws_table']:
                conn = sqlite3.connect(db_path)
                c = conn.cursor()
                c.execute("DELETE FROM workspaces WHERE path = ?", (o['path'],))
                # Also try normalized
                for variant in [o['path'], o['path'].lower(), o['path'].replace('\\', '\\\\')]:
                    try:
                        c.execute("DELETE FROM workspaces WHERE path = ?", (variant,))
                    except Exception:
                        pass
                conn.commit()
                conn.close()
        deleted.append(f"workspace dir: {o['path']} ({o['status']})")
        total_freed += o['disk_size']

    # Delete stale workspaces table entries
    for o in orphans['stale_ws_entries']:
        if not dry_run:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("DELETE FROM workspaces WHERE path = ?", (o['path'],))
            conn.commit()
            conn.close()
        deleted.append(f"stale ws table: {o['path']}")

    return deleted, total_freed


def auto_clean_orphans_if_needed(user_id, dry_run=False):
    """Check for orphans and clean them if found. Called automatically
    after session purge or workspace purge. Returns freed bytes or 0."""
    orphans = find_orphans(user_id=user_id)
    total = (sum(o['size'] for o in orphans['empty_slugs']) +
             sum(o['disk_size'] for o in orphans['orphan_workspaces']))

    count = (len(orphans['empty_slugs']) + len(orphans['orphan_workspaces']) +
             len(orphans['stale_ws_entries']))

    if count == 0:
        return 0

    print(f"\n--- Auto orphan cleanup: {count} item(s) found ({format_size(total)}) ---")

    if dry_run:
        print(f"  Would clean {count} orphan(s)")
        for o in orphans['empty_slugs']:
            print(f"    slug dir: {o['slug']} ({format_size(o['size'])})")
        for o in orphans['orphan_workspaces']:
            print(f"    workspace dir: {o['path']} ({format_size(o['disk_size'])})")
        for o in orphans['stale_ws_entries']:
            print(f"    stale ws entry: {o['path']}")
        print(f"  *** Would free {format_size(total)} in orphan cleanup ***")
        return 0

    deleted, freed = clean_orphans(orphans, dry_run=False)
    print(f"  Cleaned {len(deleted)} orphan(s), freed {format_size(freed)}")
    return freed


# ============================================================
#  Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Physically purge soft-deleted sessions and unused workspaces in WorkBuddy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Session mode
  python purge.py                                    # List all soft-deleted sessions
  python purge.py --all                              # Purge ALL soft-deleted sessions
  python purge.py --older-than 30 --dry-run          # Preview purging sessions >30 days old
  python purge.py --workspace "D:\\\\work\\\\temp"      # Purge sessions from a workspace

  # Workspace mode
  python purge.py --list-workspaces                  # List all workspaces with status
  python purge.py --purge-workspace "C:\\\\Users\\\\...\\\\Claw" --dry-run  # Preview deleting a workspace
  python purge.py --purge-workspace --all-inactive   # Delete ALL inactive workspaces (DANGER!)

  # Orphan cleanup
  python purge.py --clean-orphans                    # Clean empty slug dirs and stale DB entries
  python purge.py --clean-orphans --dry-run          # Preview orphan cleanup
""",
    )

    # Session mode arguments
    parser.add_argument('--all', action='store_true',
                        help='Purge ALL soft-deleted sessions')
    parser.add_argument('--ids', nargs='+', metavar='ID',
                        help='Purge specific sessions by ID prefix')
    parser.add_argument('--older-than', type=int, metavar='DAYS',
                        help='Only purge sessions deleted more than N days ago')
    parser.add_argument('--workspace', metavar='PATH',
                        help='Only purge sessions from this workspace (session mode)')
    parser.add_argument('--min-size', type=int, metavar='KB',
                        help='Only purge sessions larger than N KB')

    # Workspace mode arguments
    parser.add_argument('--list-workspaces', action='store_true',
                        help='List all registered workspaces with active/inactive status')
    parser.add_argument('--purge-workspace', nargs='?', const='__ALL_INACTIVE__', metavar='PATH',
                        help='Physically delete a workspace and ALL its data. '
                             'Use --all-inactive to delete ALL workspaces with no active sessions.')
    parser.add_argument('--all-inactive', action='store_true',
                        help='With --purge-workspace: purge ALL workspaces with no active sessions')

    # Orphan cleanup arguments
    parser.add_argument('--clean-orphans', action='store_true',
                        help='Clean up orphaned directories and stale DB entries (empty slug dirs, '
                             'workspace dirs with 0 sessions, stale workspaces entries)')
    parser.add_argument('--no-clean-orphans', action='store_true',
                        help='Skip automatic orphan cleanup after session/workspace purge')

    # Common arguments
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without deleting')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--user-id', metavar='ID',
                        help='Filter operations to this user_id. Auto-detected from sessions.json if omitted.')
    parser.add_argument('--no-user-filter', action='store_true',
                        help='Disable user_id filtering (operate on ALL users — use with caution)')
    args = parser.parse_args()

    # Determine user_id for filtering
    user_id = args.user_id
    if not user_id and not args.no_user_filter:
        user_id = detect_current_user()
        if user_id:
            pass  # auto-detected
        else:
            print("Warning: Could not auto-detect user_id. Run with --no-user-filter to skip, or --user-id to specify.")
            sys.exit(1)

    # ============================================================
    #  ORPHAN CLEANUP MODE
    # ============================================================
    if args.clean_orphans:
        orphans = find_orphans(user_id=user_id)

        if args.dry_run:
            print("\n*** DRY RUN ***\n")
        print("=== Orphan Cleanup ===\n")
        print_orphan_list(orphans)

        total = (sum(o['size'] for o in orphans['empty_slugs']) +
                 sum(o['disk_size'] for o in orphans['orphan_workspaces']))

        if args.dry_run:
            print(f"*** DRY RUN — {format_size(total)} would be freed. Run without --dry-run to execute. ***")
            return

        if total == 0 and not orphans['stale_ws_entries']:
            print("Nothing to clean up.")
            return

        deleted, freed = clean_orphans(orphans, dry_run=False)
        print(f"\nCleaned {len(deleted)} item(s):")
        for d in deleted:
            print(f"  {d}")

        # Vacuum
        db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()

        print(f"\nDone. Freed {format_size(freed)}. Database vacuumed.")
        return

    # ============================================================
    #  WORKSPACE MODE
    # ============================================================
    if args.list_workspaces or args.purge_workspace is not None:
        workspaces = find_workspaces(user_id=user_id)

        if args.list_workspaces:
            if args.json:
                print(json.dumps(workspaces, indent=2, ensure_ascii=False))
            else:
                print(f"\n=== Registered Workspaces ({len(workspaces)}) ===\n")
                print_workspace_list(workspaces)
            return

        # --purge-workspace mode
        if args.purge_workspace == '__ALL_INACTIVE__' or args.all_inactive:
            # Purge ALL inactive workspaces
            targets = [ws for ws in workspaces if ws['active_sessions'] == 0]
            if not targets:
                print("No inactive workspaces found.")
                return
        else:
            # Purge a specific workspace by path
            target_path = os.path.normpath(args.purge_workspace)
            targets = [ws for ws in workspaces
                       if os.path.normpath(ws['path']) == target_path]
            if not targets:
                # Try matching by partial path or slug
                targets = [ws for ws in workspaces
                           if target_path.lower() in ws['path'].lower()
                           or target_path.lower() in path_to_slug(ws['path'])]
            if not targets:
                print(f"No workspace found matching: {args.purge_workspace}")
                print("Use --list-workspaces to see all registered workspaces.")
                return

        total_size = sum(ws['disk']['total'] for ws in targets)
        total_sessions = sum(ws['total_sessions'] for ws in targets)

        if args.dry_run:
            print(f"\n*** DRY RUN ***")
        print(f"\nTarget workspaces: {len(targets)}")
        print(f"Total sessions: {total_sessions}")
        print(f"Total disk: {format_size(total_size)}\n")

        for ws in targets:
            print(f"  Path: {ws['path']}")
            print(f"    Last opened: {ws['last_opened']}")
            print(f"    Active sessions: {ws['active_sessions']}")
            print(f"    Total sessions: {ws['total_sessions']} ({ws['deleted_sessions']} deleted)")
            print(f"    Path exists: {'Yes' if ws['path_exists'] else 'No'}")
            print(f"    Disk usage: {format_size(ws['disk']['total'])}")
            details = ' + '.join(f"{k}: {format_size(v)}" for k, v in ws['disk'].items()
                                 if k != 'total' and v > 0)
            if not details:
                details = "no files"
            print(f"    Breakdown: {details}")
            print()

        if args.dry_run:
            print(f"*** DRY RUN — {format_size(total_size)} would be freed. Run without --dry-run to execute. ***")
            if not args.no_clean_orphans:
                auto_clean_orphans_if_needed(user_id=user_id, dry_run=True)
            return

        # Confirm before actual deletion
        print(f"\n{'!'*60}")
        print(f"WARNING: This will PERMANENTLY DELETE {len(targets)} workspace(s)")
        print(f"and ALL {total_sessions} session(s) within them.")
        print(f"Total data: {format_size(total_size)}")
        print(f"{'!'*60}")
        print()

        print("Purging...\n")
        total_freed = 0
        for ws in targets:
            parts = physically_delete_workspace(ws['path'], user_id=user_id)
            total_freed += ws['disk']['total']
            print(f"  Deleted: {ws['path']}")
            for p in parts:
                print(f"    {p}")

        # Vacuum the database
        db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()

        print(f"\nDone. Freed {format_size(total_freed)}. Database vacuumed.")

        # Auto orphan cleanup
        if not args.no_clean_orphans:
            orphan_freed = auto_clean_orphans_if_needed(user_id=user_id)
            total_freed += orphan_freed

        print(f"\nTotal freed: {format_size(total_freed)}")
        print("Restart WorkBuddy to reflect changes.")
        return

    all_deleted = find_soft_deleted_sessions(user_id=user_id)

    if not all_deleted:
        print("No soft-deleted sessions found. Nothing to purge.\n")
        return

    # Apply filters
    targets = filter_sessions(all_deleted, args)
    total_size = sum(s['disk']['total'] for s in targets)

    # JSON output mode
    if args.json:
        output = [{
            'id': s['id'],
            'title': s['title'],
            'cwd': s['cwd'],
            'deleted_at': s['deleted_at'],
            'size_bytes': s['disk']['total'],
        } for s in targets]
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Determine action mode
    will_delete = args.all or args.ids or args.older_than is not None or args.workspace or args.min_size is not None

    if not will_delete:
        # List-only mode
        total_menu = sum(s['disk']['total'] for s in all_deleted)
        user_info = f" (user: {user_id[:8]}...)" if user_id else " (all users)"
        print(f"\nFound {len(all_deleted)} soft-deleted session(s), {format_size(total_menu)} total{user_info}\n")
        print_session_list(all_deleted, total_menu)
        print("Session purge options:")
        print("  --all                  Purge everything")
        print("  --ids 794f328e         Purge by ID prefix")
        print("  --older-than 30        Purge sessions older than 30 days")
        print("  --workspace D:\\work\\x  Purge from specific workspace")
        print("  --min-size 100         Purge sessions > 100KB")
        print("  Add --dry-run to preview first")
        print()
        print("Workspace purge options:")
        print("  --list-workspaces      List all workspaces and their status")
        print("  --purge-workspace PATH Purge an inactive workspace completely")
        print("  --purge-workspace --all-inactive  Purge ALL inactive workspaces")
        print("  --clean-orphans       Clean empty slug dirs and stale DB entries\n")
        return

    # Delete mode
    mode_parts = []
    if args.all:
        mode_parts.append("ALL")
    elif args.ids:
        mode_parts.append(f"IDs: {', '.join(args.ids)}")
    if args.older_than is not None:
        mode_parts.append(f"older than {args.older_than} days")
    if args.workspace:
        mode_parts.append(f"workspace={args.workspace}")
    if args.min_size is not None:
        mode_parts.append(f"min size={args.min_size}KB")
    mode_str = " + ".join(mode_parts)

    if args.dry_run:
        print(f"\n*** DRY RUN ***\n")
    print(f"Purge mode: {mode_str}")
    print(f"Target: {len(targets)} session(s), {format_size(total_size)}\n")

    print_session_list(targets, total_size)

    if not targets:
        print("No sessions match the criteria. Nothing to purge.")
        return

    if not args.dry_run:
        print("Purging...")
        print()

    total_freed = 0
    for s in targets:
        parts = physically_delete_session(s, dry_run=args.dry_run)
        total_freed += s['disk']['total']
        status = "would delete" if args.dry_run else "deleted"
        details = ", ".join(parts)
        size_str = format_size(s['disk']['total'])
        title_short = s['title'][:40]
        print(f"  {status}: {s['id'][:16]}... [{size_str}] {title_short}")

    if args.dry_run:
        print(f"\n*** DRY RUN — {format_size(total_freed)} would be freed. ***")

        # Preview orphan cleanup
        if not args.no_clean_orphans:
            auto_clean_orphans_if_needed(user_id=user_id, dry_run=True)
    else:
        db_path = os.path.join(WORKBUDDY_HOME, 'workbuddy.db')
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()
        print(f"\nDone. Freed {format_size(total_freed)}. Database vacuumed.")

        # Auto orphan cleanup
        if not args.no_clean_orphans:
            orphan_freed = auto_clean_orphans_if_needed(user_id=user_id)
            total_freed += orphan_freed

        print(f"\nTotal freed: {format_size(total_freed)}")
        print("Restart WorkBuddy to reflect changes.")


if __name__ == '__main__':
    main()
