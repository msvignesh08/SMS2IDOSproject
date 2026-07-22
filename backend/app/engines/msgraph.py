"""
SMS-2 IDOS — MICROSOFT 365 EXCEL CONNECTION
============================================
Pulls the plant's live workbook from the company's Microsoft account and loads
it with the SAME parser that reads the file by hand. Nothing here understands
spreadsheets: it fetches bytes and hands them to tools/history_map.py, which
already knows every quirk of this workbook — the multi-row headers, the merged
date cells, the transposed caster dates, the 127 phantom header blocks. A
second parser against Graph's workbook-range API would be a second set of
those bugs to find again.

AUTHENTICATION — app-only (client credentials)
    IT registers an Azure AD application and grants it admin consent for
    Files.Read.All (or Sites.Read.All for a SharePoint library). The server
    then authenticates as itself, with no user session to expire, which is
    what an unattended shift-change sync needs.

CONFIGURED BY ENVIRONMENT, NEVER BY THE DATABASE OR THE REPOSITORY:
    SMS2_GRAPH_TENANT         directory (tenant) ID
    SMS2_GRAPH_CLIENT_ID      application (client) ID
    SMS2_GRAPH_CLIENT_SECRET  client secret          <- never logged or shown
    SMS2_GRAPH_FILE           the workbook to read. EITHER a sharing link
                              pasted straight from SharePoint/OneDrive's
                              "Copy link" --
                                https://contoso.sharepoint.com/:x:/s/.../Ed5...
                              -- or a drive-item path:
                                drives/{drive-id}/items/{item-id}
                                drives/{drive-id}/root:/Path/To/IDOS.xlsx:
                                sites/{site-id}/drive/root:/IDOS.xlsx:
                                users/{user-id}/drive/root:/IDOS.xlsx:
                              The link is the easier of the two and is the one
                              to prefer; it only ADDRESSES the file, so admin
                              consent is still required either way.

The secret is read at call time and never returned by any function here, so it
cannot reach the API surface, the audit trail or the browser.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models as M
from ..core import plant_time

LOGIN_HOST = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 120

K_LAST_SYNC = "graph_last_sync"
K_LAST_RESULT = "graph_last_result"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def config() -> dict:
    """What is configured — WITHOUT the secret.

    Safe to hand to the admin screen: it reports whether each value is present,
    never what it is. The client secret is deliberately reduced to a boolean.
    """
    tenant, client, secret, file = (_env("SMS2_GRAPH_TENANT"),
                                    _env("SMS2_GRAPH_CLIENT_ID"),
                                    _env("SMS2_GRAPH_CLIENT_SECRET"),
                                    _env("SMS2_GRAPH_FILE"))
    missing = [n for n, v in (("SMS2_GRAPH_TENANT", tenant),
                              ("SMS2_GRAPH_CLIENT_ID", client),
                              ("SMS2_GRAPH_CLIENT_SECRET", secret),
                              ("SMS2_GRAPH_FILE", file)) if not v]
    return {
        "configured": not missing,
        "missing": missing,
        "tenant": tenant,
        "client_id": client,
        "file": file,
        "secret_set": bool(secret),          # never the secret itself
    }


def _post_form(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def get_token() -> str:
    """Client-credentials token. Raises RuntimeError with a usable message."""
    cfg = config()
    if not cfg["configured"]:
        raise RuntimeError("Microsoft 365 connection is not configured — missing "
                           + ", ".join(cfg["missing"]))
    try:
        tok = _post_form(f"{LOGIN_HOST}/{cfg['tenant']}/oauth2/v2.0/token", {
            "client_id": cfg["client_id"],
            "client_secret": _env("SMS2_GRAPH_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        # Azure's own text names the cause (bad secret, wrong tenant, consent
        # not granted). Pass it through — but never the secret we just sent.
        raise RuntimeError(f"Microsoft rejected the sign-in ({e.code}). {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Microsoft ({e.reason}). A plant "
                           f"network usually blocks outbound internet — this "
                           f"connection needs access to login.microsoftonline.com "
                           f"and graph.microsoft.com.")
    t = tok.get("access_token")
    if not t:
        raise RuntimeError("Microsoft returned no access token.")
    return t


def share_id(link: str) -> str:
    """Encode a SharePoint/OneDrive sharing URL as a Graph share ID.

    Graph's own scheme: base64 of the URL, url-safe alphabet, padding stripped,
    prefixed "u!". Lets an administrator paste the link that "Copy link" gives
    them instead of hunting a site ID and typing a path with a trailing colon —
    which was the step most likely to be got wrong.
    """
    b64 = base64.b64encode(link.strip().encode()).decode()
    return "u!" + b64.rstrip("=").replace("/", "_").replace("+", "-")


def content_url() -> str:
    """Graph URL for the workbook's bytes, from either addressing form."""
    ref = _env("SMS2_GRAPH_FILE").strip()
    if ref.lower().startswith(("http://", "https://")):
        # A sharing link ADDRESSES the file; it does not authorise it. An
        # app-only token has no user identity for "anyone with the link" to
        # apply to, so Files.Read.All/Sites.Read.All is still required.
        return f"{GRAPH}/shares/{share_id(ref)}/driveItem/content"
    return f"{GRAPH}/{ref.strip('/')}/content"


def download(token: str | None = None) -> tuple[str, int]:
    """Fetch the workbook to a temp file. Returns (path, bytes)."""
    token = token or get_token()
    url = content_url()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        hint = ""
        if e.code == 404:
            hint = (" SMS2_GRAPH_FILE did not resolve. If it is a sharing "
                    "link, re-copy it from the file itself; if it is a path, "
                    "check it against the examples in msgraph.py.")
        elif e.code in (401, 403):
            hint = (" The application is authenticated but not permitted to read "
                    "this file — Files.Read.All (or Sites.Read.All) needs admin "
                    "consent.")
        raise RuntimeError(f"Graph refused the download ({e.code}).{hint} {body}")
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", prefix="sms2_graph_")
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    return tmp, len(blob)


def sync(db: Session, user: M.User | None = None, dry_run: bool = False) -> dict:
    """Fetch the workbook and load it. Never raises — reports instead.

    A sync failure is an integration problem, not a production one: the plant
    keeps running on what it already has, so this returns a result the screen
    can show rather than propagating an exception.
    """
    started = plant_time.plant_now()
    tmp = None
    try:
        import openpyxl                                    # noqa: F401
        from tools.history_map import SHEETS               # noqa: F401
        from tools.import_history import Importer
        from tools.history_writer import HistoryWriter

        tmp, size = download()
        wb = openpyxl.load_workbook(tmp, data_only=True)
        missing = [s for s in SHEETS if s not in wb.sheetnames]
        if missing:
            return _finish(db, started, {
                "ok": False,
                "error": f"The workbook is missing expected sheets: {', '.join(missing)}. "
                         f"Found: {', '.join(wb.sheetnames)}."})

        imp = Importer(wb)
        imp.gather_all()
        result = {"ok": True, "bytes": size, "heats_found": len(imp.heats),
                  "unmapped_columns": len(imp.unmapped),
                  "sheets": sorted(SHEETS)}
        if dry_run:
            result["dry_run"] = True
            result["would_write"] = len(
                [n for n in imp.heats
                 if not db.query(M.Heat).filter_by(heat_no=n).first()])
            return _finish(db, started, result)

        w = HistoryWriter(db)
        result["heats_written"] = w.write(imp.heats, "Microsoft 365 workbook")
        result["skipped_existing"] = w.stats.get("heats skipped (already present)", 0)
        result["delays"] = w.stats.get("delays written", 0)
        result["materials"] = w.stats.get("material additions", 0)
        db.add(M.AuditLog(
            user_id=user.id if user else None,
            user_name=user.name if user else "Microsoft 365 sync",
            category="admin", action="graph_sync",
            detail=f"{result['heats_written']} heat(s) loaded from the Microsoft 365 "
                   f"workbook, {result['skipped_existing']} already present."))
        db.commit()
        return _finish(db, started, result)
    except Exception as e:                                  # reported, never raised
        db.rollback()
        return _finish(db, started, {"ok": False, "error": str(e)[:500]})
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)                              # never keep plant data on disk
            except OSError:
                pass


def _finish(db: Session, started: datetime, result: dict) -> dict:
    result["at"] = started.isoformat(sep=" ", timespec="seconds")
    result["seconds"] = round((plant_time.plant_now() - started).total_seconds(), 1)
    if not result.get("dry_run"):
        _set(db, K_LAST_SYNC, result["at"], "Last Microsoft 365 workbook sync")
        _set(db, K_LAST_RESULT, json.dumps(result)[:900], "Outcome of that sync")
        try:
            db.commit()
        except Exception:
            db.rollback()
    return result


def _set(db: Session, key: str, value: str, desc: str = ""):
    s = db.query(M.SystemSetting).filter_by(key=key).first()
    if s:
        s.value = value
    else:
        db.add(M.SystemSetting(key=key, value=value, description=desc))


def status(db: Session) -> dict:
    """Everything the admin screen needs — configuration state and last run."""
    cfg = config()
    last = db.query(M.SystemSetting).filter_by(key=K_LAST_RESULT).first()
    try:
        last_result = json.loads(last.value) if last and last.value else None
    except ValueError:
        last_result = None
    stamp = db.query(M.SystemSetting).filter_by(key=K_LAST_SYNC).first()
    return {**cfg,
            "last_sync": (stamp.value if stamp else None),
            "last_result": last_result}
