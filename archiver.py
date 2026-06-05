#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Steven Endres (rootalley)
# https://github.com/rootalley/canvas-legacy-eportfolio-archiver
"""
Canvas Legacy ePortfolio Archiver

Downloads ePortfolios from Canvas LMS via admin masquerade.

Usage:
    python archiver.py [--include-deleted]

Downloads are organized as:
    downloads/{author_name} ID {sis_id}/{eportfolio_id} {eportfolio_name}.zip

Progress is saved to archive_log.json after each download, so the script
can be interrupted and resumed safely.
"""

import argparse
import csv
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

CANVAS_URL = os.environ.get("EPORTFOLIO_ARCHIVER_CANVAS_URL", "").rstrip("/")
CANVAS_USERNAME = os.environ.get("EPORTFOLIO_ARCHIVER_CANVAS_USERNAME", "")
CANVAS_PASSWORD = os.environ.get("EPORTFOLIO_ARCHIVER_CANVAS_PASSWORD", "")
EXPORT_DIR = Path(os.environ.get("EPORTFOLIO_ARCHIVER_EXPORT_DIR", "downloads"))
CSV_PATH = Path(os.environ.get("EPORTFOLIO_ARCHIVER_CSV_PATH", "eportfolio_list.csv"))
DOWNLOAD_TIMEOUT_MS = int(os.environ.get("EPORTFOLIO_ARCHIVER_DOWNLOAD_TIMEOUT_MS", "600000"))
CANVAS_API_TOKEN = os.environ.get("EPORTFOLIO_ARCHIVER_CANVAS_API_TOKEN", "")
LOG_PATH = Path("archive_log.json")


def check_config():
    missing = []
    for var in (
        "EPORTFOLIO_ARCHIVER_CANVAS_URL",
        "EPORTFOLIO_ARCHIVER_CANVAS_USERNAME",
        "EPORTFOLIO_ARCHIVER_CANVAS_PASSWORD",
    ):
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        sys.exit("Missing required environment variables:\n  " + "\n  ".join(missing))

    password = CANVAS_PASSWORD
    masked = password[:2] + "*" * (len(password) - 2) if len(password) > 2 else "***"
    print("Configuration:")
    token_status = f"set (length: {len(CANVAS_API_TOKEN)})" if CANVAS_API_TOKEN else "not set (API calls will use browser session auth)"
    print(f"  CANVAS_URL:           {CANVAS_URL}")
    print(f"  CANVAS_USERNAME:      {CANVAS_USERNAME}")
    print(f"  CANVAS_PASSWORD:      {masked} (length: {len(password)})")
    print(f"  CANVAS_API_TOKEN:     {token_status}")
    print(f"  CSV_PATH:             {CSV_PATH}")
    print(f"  EXPORT_DIR:           {EXPORT_DIR}")
    print(f"  DOWNLOAD_TIMEOUT_MS:  {DOWNLOAD_TIMEOUT_MS}")
    print()


def safe_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "", name).strip()


def load_log() -> dict:
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text())
    return {}


def save_log(log: dict):
    LOG_PATH.write_text(json.dumps(log, indent=2))


def login(page):
    # Selectors for username and password fields across Canvas UI versions
    USERNAME_SELECTOR = "#pseudonym_session_unique_id, input[name='pseudonym_session[unique_id]'], input[type='text']:visible, input[type='email']:visible"
    PASSWORD_SELECTOR = "#pseudonym_session_password, input[name='pseudonym_session[password]'], input[type='password']:visible"

    print("Logging in...")
    page.goto(f"{CANVAS_URL}/login/canvas")
    try:
        page.wait_for_selector(PASSWORD_SELECTOR, timeout=15000)
    except Exception:
        page.screenshot(path="login_debug.png")
        raise RuntimeError(
            "Login form not found — screenshot saved to login_debug.png. "
            "Check whether the page redirected away from /login/canvas."
        )
    page.fill(USERNAME_SELECTOR, CANVAS_USERNAME)
    page.fill(PASSWORD_SELECTOR, CANVAS_PASSWORD)
    page.click("button[type=submit]")
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        page.screenshot(path="login_debug.png")
        raise RuntimeError(
            "Login did not redirect after submit — screenshot saved to login_debug.png. "
            "Check credentials or whether Canvas showed an error."
        )
    print("Logged in.\n")


def masquerade(page, user_id: str):
    page.goto(f"{CANVAS_URL}/users/{user_id}/masquerade")
    # The confirm button is a React-rendered <a data-method="post"> — wait for it explicitly
    try:
        page.wait_for_selector('a[data-method="post"][href*="masquerade"]', timeout=10000)
        page.click('a[data-method="post"][href*="masquerade"]')
    except Exception:
        page.screenshot(path="masquerade_debug.png")
        raise RuntimeError(
            "Masquerade confirm button not found — screenshot saved to masquerade_debug.png."
        )
    page.wait_for_load_state("networkidle")


def stop_masquerade(page, user_id: str, quiet: bool = False):
    try:
        # The masquerade bar link uses Rails UJS data-method="delete"
        stop = page.locator(f'a[href*="{user_id}/masquerade"], a:has-text("Stop acting as")')
        if stop.count() > 0:
            stop.first.click()
            page.wait_for_load_state("networkidle")
        else:
            # Fallback: issue DELETE directly via the browser session
            page.request.fetch(
                f"{CANVAS_URL}/users/{user_id}/masquerade",
                method="DELETE",
            )
    except Exception as e:
        if not quiet:
            print(f"  Warning: masquerade stop issue: {e}")


_user_cache: dict[str, tuple[str, str]] = {}
_shutdown = False


def _handle_sigint(signum, frame):
    global _shutdown
    _shutdown = True
    print(
        "\nInterrupt received — canceling current download and saving progress.",
        flush=True,
    )


def api_headers() -> dict:
    if CANVAS_API_TOKEN:
        return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    return {}


def lookup_user(page, user_id: str) -> tuple[str, str]:
    """Returns (sortable_name, sis_user_id). Results are cached per user_id."""
    if user_id in _user_cache:
        return _user_cache[user_id]
    resp = page.request.get(
        f"{CANVAS_URL}/api/v1/users/{user_id}",
        headers=api_headers(),
    )
    if resp.ok:
        data = resp.json()
        sortable_name = data.get("sortable_name") or ""
        sis_user_id = data.get("sis_user_id") or ""
    else:
        print(f"  Warning: user lookup failed for {user_id} (HTTP {resp.status}); falling back to Canvas ID")
        sortable_name = ""
        sis_user_id = ""
    _user_cache[user_id] = (sortable_name, sis_user_id)
    return sortable_name, sis_user_id


def user_folder_name(sortable_name: str, sis_user_id: str, canvas_user_id: str) -> str:
    name_part = safe_name(sortable_name)
    if sis_user_id:
        return f"{name_part} ID {sis_user_id}"
    return f"{name_part} USER {canvas_user_id}"


def restore_eportfolio(page, eportfolio_id: str):
    resp = page.request.put(
        f"{CANVAS_URL}/api/v1/eportfolios/{eportfolio_id}/restore",
        headers=api_headers(),
    )
    if not resp.ok:
        raise RuntimeError(f"Restore API returned HTTP {resp.status}: {resp.text()}")
    print("  Restored deleted ePortfolio.")


def delete_eportfolio(page, eportfolio_id: str):
    resp = page.request.delete(
        f"{CANVAS_URL}/api/v1/eportfolios/{eportfolio_id}",
        headers=api_headers(),
    )
    if not resp.ok:
        raise RuntimeError(f"Re-delete API returned HTTP {resp.status}: {resp.text()}")
    print("  Re-deleted ePortfolio.")


def delete_eportfolio_direct(eportfolio_id: str):
    """Re-delete via urllib when the Playwright browser is no longer available."""
    if not CANVAS_API_TOKEN:
        print(f"  Warning: cannot re-delete ePortfolio {eportfolio_id} — no API token set.")
        return
    req = urllib.request.Request(
        f"{CANVAS_URL}/api/v1/eportfolios/{eportfolio_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req):
            print("  Re-deleted ePortfolio (direct HTTP).")
    except urllib.error.URLError as e:
        print(f"  Warning: could not re-delete ePortfolio {eportfolio_id}: {e}")


def export_portfolio(page, eportfolio_id: str, dest_path: Path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    page.goto(f"{CANVAS_URL}/eportfolios/{eportfolio_id}")
    page.wait_for_load_state("networkidle")
    # Canvas JS intercepts this link, handles async ZIP generation, then triggers the download
    print("  Waiting for download (Canvas is generating the ZIP)...")
    with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
        page.click(".download_eportfolio_link")
    dl_info.value.save_as(dest_path)


def process_row(page, row: dict, log: dict) -> str:
    eportfolio_id = row["eportfolio_id"].strip()
    author_id = row["author_id"].strip()
    author_name = row["author_name"].strip()
    eportfolio_name = row["eportfolio_name"].strip()
    is_deleted = row.get("workflow_state", "").strip() == "deleted"

    key = eportfolio_id

    sortable_name, sis_user_id = lookup_user(page, author_id)
    if not sortable_name:
        sortable_name = author_name
    folder = user_folder_name(sortable_name, sis_user_id, author_id)
    dest_path = EXPORT_DIR / folder / f"{eportfolio_id} {safe_name(eportfolio_name)}.zip"

    if dest_path.exists():
        print("  Skipping (file already exists on disk)")
        log[key] = {"status": "success", "file": str(dest_path), "author": author_name, "author_id": author_id}
        return "skipped"

    restored = False
    deleted_after_restore = False
    masquerade_stopped = False
    try:
        if is_deleted:
            restore_eportfolio(page, eportfolio_id)
            restored = True

        masquerade(page, author_id)
        export_portfolio(page, eportfolio_id, dest_path)
        stop_masquerade(page, author_id)
        masquerade_stopped = True

        if restored:
            delete_eportfolio(page, eportfolio_id)
            deleted_after_restore = True

        log[key] = {
            "status": "success",
            "file": str(dest_path),
            "author": author_name,
            "author_id": author_id,
        }
        print(f"  Saved: {dest_path}")
        return "success"

    except Exception as e:
        if _shutdown:
            # Download was canceled by user interrupt; don't log as error so it's retried on resume.
            print("  Canceled.")
            return "canceled"
        else:
            log[key] = {
                "status": "error",
                "error": str(e),
                "author": author_name,
                "author_id": author_id,
                "eportfolio_name": eportfolio_name,
            }
            print(f"  Error: {e}")
            return "error"

    finally:
        # Re-delete any restored portfolio that wasn't already re-deleted.
        # Only stop masquerade if we haven't already done so on the happy path.
        # Use quiet=True so a closed browser (e.g. after Ctrl+C) doesn't print spurious warnings.
        if not masquerade_stopped:
            stop_masquerade(page, author_id, quiet=True)
        if restored and not deleted_after_restore:
            try:
                delete_eportfolio(page, eportfolio_id)
            except Exception:
                # Browser may be gone (e.g. Ctrl+C); fall back to direct HTTP.
                delete_eportfolio_direct(eportfolio_id)


def main():
    parser = argparse.ArgumentParser(description="Archive Canvas ePortfolios")
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Also process ePortfolios with workflow_state=deleted",
    )
    args = parser.parse_args()

    check_config()
    signal.signal(signal.SIGINT, _handle_sigint)

    if not CSV_PATH.exists():
        sys.exit(f"CSV not found: {CSV_PATH}")

    log = load_log()

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    active = [r for r in rows if r.get("workflow_state", "").strip() == "active"]
    deleted = [r for r in rows if r.get("workflow_state", "").strip() == "deleted"]

    pool = active + deleted if args.include_deleted else active
    seen_ids: set[str] = set()
    work = []
    for r in pool:
        eid = r["eportfolio_id"].strip()
        if eid not in seen_ids:
            seen_ids.add(eid)
            work.append(r)
    duplicates = len(pool) - len(work)

    print(f"CSV: {len(rows)} total — {len(active)} active, {len(deleted)} deleted")
    print(f"Filtering: {duplicates} duplicate row{'s' if duplicates != 1 else ''} removed; {len(work)} unique ePortfolio{'s' if len(work) != 1 else ''} remaining")
    print(f"Processing: {len(work)} ePortfolio{'s' if len(work) != 1 else ''} (--include-deleted={args.include_deleted})\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        login(page)

        counts = {"success": 0, "error": 0, "skipped": 0, "canceled": 0}
        for i, row in enumerate(work, 1):
            if _shutdown:
                print("Stopping — re-run to resume from where you left off.")
                break
            print(
                f"[{i}/{len(work)}] {row['eportfolio_name']}"
                f" (portfolio={row['eportfolio_id']}, user={row['author_id']})"
            )
            result = process_row(page, row, log)
            counts[result] += 1
            save_log(log)

        browser.close()

    print(f"\n{'=' * 50}")
    parts = [f"{counts['success']} downloaded", f"{counts['error']} errors", f"{counts['skipped']} skipped"]
    if counts["canceled"]:
        parts.append(f"{counts['canceled']} canceled")
    print("Done — " + ", ".join(parts))
    if counts["error"]:
        print(f"Review errors in {LOG_PATH}")


if __name__ == "__main__":
    main()
