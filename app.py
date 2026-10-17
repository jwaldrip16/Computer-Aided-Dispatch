"""CAD Lite: a small computer-aided dispatch app.
Python + Flask + PostgreSQL. Text-to-speech for address and nature,
SOP checklists for police / fire / medical / traffic accident calls, call sharing and export.
"""
import csv
import io
import json
import math
import time
import os
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
import secrets
import threading

import psycopg2
import psycopg2.extras
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")

CALL_TYPES = ("police", "fire", "medical", "other", "accident")
STATUSES = ("open", "pending", "dispatched", "closed")
PRIORITIES = ("low", "routine", "urgent", "emergency")

NATURES = {
    "police": ["burglary in progress", "shots fired", "suspicious person",
               "suspicious vehicle", "disturbance / fight", "assault",
               "domestic dispute", "theft", "traffic accident with injuries",
               "traffic accident no injuries", "welfare check",
               "noise complaint"],
    "fire": ["structure fire", "vehicle fire", "brush / grass fire",
             "trash fire", "smoke smell, no fire seen", "gas leak",
             "electrical fire", "fire alarm activation", "person trapped"],
    "medical": ["chest pain", "difficulty breathing", "unconscious / not breathing",
                "cardiac arrest", "stroke symptoms", "seizure", "diabetic problem",
                "allergic reaction", "serious bleeding", "fall injury",
                "overdose", "childbirth", "poisoning", "electrocution",
                "drowning"],
    "accident": ["traffic accident with injuries", "traffic accident no injuries",
                 "unknown injuries", "hit and run", "vehicle rollover"],
    "other": ["utility outage", "water problem", "animal problem",
              "traffic hazard", "weather damage", "locked in or out", "other"],
}

DEFAULT_SOPS = {
    "police": [
        "Confirm the exact address, apartment or suite number, and nearest cross street.",
        "Get the nature of the call: crime in progress, suspicious person, accident, disturbance.",
        "Ask if there are weapons involved or anyone injured.",
        "If there is a suspect or vehicle, get a description: number of people, clothing, direction of travel, vehicle make, color, and plate.",
        "Ask if the caller is in a safe place. If not, getting to safety comes before anything else.",
        "Stay on the line with the caller when it is safe to do so.",
        "Enter the call at the right priority and assign units.",
        "Log every update in the call notes with the time.",
        "Update the call status as units arrive and work the scene.",
        "Close the call only when units confirm the scene is handled, and note the outcome.",
    ],
    "fire": [
        "Confirm the exact address and the nearest cross street.",
        "Get the nature of the fire: structure, vehicle, brush, trash, or smoke smell with no fire seen.",
        "Ask if anyone is still inside the building or vehicle, and if anyone is hurt.",
        "Ask what is burning and how big it is: one room, one floor, or the whole building.",
        "Ask about hazards: people trapped, chemicals, gas or propane tanks, downed power lines, high water.",
        "Tell the caller to get out and stay out, and not to go back inside for anything.",
        "Enter the call as Emergency priority and assign fire units.",
        "Log every update with the time, including any injuries that need medical help.",
        "Update the call status as units arrive and work the scene.",
        "Close the call when the scene is under control, and note the outcome and damage.",
    ],
    "medical": [
        "Confirm the exact address, apartment number, and a phone number to call back.",
        "Get the nature of the medical emergency and the main problem: breathing, chest pain, bleeding, unconscious, fall, overdose.",
        "Find out how many patients there are and the age of each one.",
        "Check: is the person awake? Are they breathing normally?",
        "If the person is not breathing, follow your agency's CPR and first aid instruction policy, or stay on the line and let EMS give instructions.",
        "Ask about known conditions, allergies, and medicines when the caller knows them.",
        "Ask the caller, if they can do it safely, to unlock the door, put pets away, and clear a path to the patient.",
        "Enter the call at the right priority and assign EMS units.",
        "Log every update with the time, and stay on the line until EMS arrives if the caller needs it.",
        "Close the call after units clear the scene, and note the patient outcome when it is known.",
    ],
    "accident": [
        "Confirm the exact address, direction of travel, and nearest cross street.",
        "Ask how many vehicles are involved and whether anyone is hurt.",
        "Count the injured people and get the main problems: breathing, bleeding, unconscious, trapped.",
        "Ask if anyone is trapped in a vehicle, or if there is a fire or a fuel leak.",
        "Get vehicle descriptions and, if a driver left the scene, a direction of travel.",
        "Enter the call at emergency or urgent priority as the injuries suggest.",
        "Assign both police and medical units, then press Send to unit phones.",
        "Log every update in the call notes with the time.",
        "Close the call when police and medical units clear the scene, and note the outcome.",
    ],
    "other": [
        "Confirm the exact address or location.",
        "Get the nature of the problem in the caller's own words.",
        "Ask if anyone is hurt or in danger right now. If yes, treat it as the matching police, fire, or medical call.",
        "Enter the call at the right priority and assign the right units.",
        "Log every update with the time.",
        "Close the call when the problem is handled, and note the outcome.",
    ],
}

app = Flask(__name__)

_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _sf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    try:
        if os.path.exists(_sf):
            _secret = open(_sf).read().strip()
        if not _secret:
            _secret = secrets.token_hex(32)
            open(_sf, "w").write(_secret)
    except OSError:
        _secret = secrets.token_hex(32)
app.secret_key = _secret

_init_lock = threading.Lock()
_init_done = False


def get_db():
    return psycopg2.connect(DATABASE_URL)


def q(sql, params=(), one=False):
    """Run one query. SELECT returns rows (dicts); writes commit."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if cur.description is not None:
                rows = cur.fetchall()
                return (rows[0] if rows else None) if one else rows
            return None


def log_audit(username, action, detail=""):
    try:
        q("INSERT INTO audit (username, action, detail) VALUES (%s, %s, %s)",
          (username, action, detail[:400]))
    except Exception:
        pass


def init_db():
    """Create tables and seed the default SOPs and the first admin once."""
    global _init_done
    with _init_lock:
        if _init_done:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        schema = open(os.path.join(here, "schema.sql")).read()
        with get_db() as conn, conn.cursor() as cur:
            cur.execute(schema)
            cur.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS"
                        " page_count INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE call_pages ADD COLUMN IF NOT EXISTS"
                        " page_text TEXT")
            cur.execute("ALTER TABLE call_pages ADD COLUMN IF NOT EXISTS"
                        " audio BYTEA")
            cur.execute("SELECT count(*) AS n FROM sop_templates")
            if cur.fetchone()[0] == 0:
                for ctype, steps in DEFAULT_SOPS.items():
                    for pos, text in enumerate(steps, 1):
                        cur.execute(
                            "INSERT INTO sop_templates (call_type, position, text)"
                            " VALUES (%s, %s, %s)", (ctype, pos, text))
            if ADMIN_USER and ADMIN_PASS:
                cur.execute("SELECT count(*) AS n FROM users")
                if cur.fetchone()[0] == 0:
                    cur.execute(
                        "INSERT INTO users (username, pass_hash, role)"
                        " VALUES (%s, %s, 'admin')",
                        (ADMIN_USER, generate_password_hash(ADMIN_PASS)))
        _init_done = True


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return q("SELECT * FROM users WHERE id = %s", (uid,), one=True)


@app.before_request
def _setup():
    if DATABASE_URL:
        init_db()


def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in."), 401
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        me = current_user()
        if me["role"] != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper


def role_required(*roles):
    from functools import wraps

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            me = current_user()
            if not me:
                if request.path.startswith("/api/"):
                    return jsonify(error="Please log in."), 401
                return redirect(url_for("login", next=request.path))
            if me["role"] not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


staff_required = role_required("admin", "dispatcher")


def call_dict(row):
    d = dict(row)
    for k in ("created_at", "closed_at", "notified_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


# ---------- auth ----------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if q("SELECT count(*) AS n FROM users", one=True)["n"]:
        return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if len(u) < 3 or len(p) < 6:
            error = "Username needs at least 3 characters and password at least 6."
        else:
            q("INSERT INTO users (username, pass_hash, role) VALUES (%s, %s, 'admin')",
              (u, generate_password_hash(p)))
            log_audit(u, "setup", "created the first admin account")
            return redirect(url_for("login"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        row = q("SELECT * FROM users WHERE username = %s", (u,), one=True)
        if row and check_password_hash(row["pass_hash"], p):
            session["uid"] = row["id"]
            log_audit(u, "login")
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            if row["role"] == "unit":
                return redirect(url_for("unit_home"))
            return redirect(url_for("board"))
        error = "Wrong username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- board and calls ----------

@app.route("/")
@login_required
def board():
    me = current_user()
    return render_template("board.html", me=me, natures=NATURES,
                           alert_fmt=get_alert_format())


@app.route("/api/board")
@login_required
def api_board():
    active = q("SELECT * FROM calls WHERE status IN ('open','dispatched')"
               " ORDER BY id DESC")
    pending = q("SELECT * FROM calls WHERE status = 'pending'"
                " ORDER BY id DESC")
    recent = q("SELECT * FROM calls WHERE status = 'closed'"
               " ORDER BY closed_at DESC NULLS LAST, id DESC LIMIT 25")
    units = q("SELECT username, unit_types, unit_status, lat, lng, loc_updated"
              " FROM users WHERE role = 'unit' ORDER BY username")
    units_out = [{"username": r["username"],
                  "unit_types": r["unit_types"] or "",
                  "unit_status": r["unit_status"] or "off_duty",
                  "lat": r["lat"], "lng": r["lng"],
                  "loc_updated": r["loc_updated"].isoformat()
                  if r["loc_updated"] else None}
                 for r in units]
    resp = unit_responses([r["id"] for r in active])
    return jsonify(active=[dict(call_dict(r), responses=resp.get(r["id"], []))
                           for r in active],
                   pending=[call_dict(r) for r in pending],
                   recent=[call_dict(r) for r in recent],
                   units=units_out)


@app.route("/call/new", methods=["GET", "POST"])
@staff_required
def new_call():
    me = current_user()
    if request.method == "POST":
        ctype = request.form.get("call_type") or "other"
        if ctype not in CALL_TYPES:
            ctype = "other"
        nature = (request.form.get("nature") or "").strip() or "not given"
        address = (request.form.get("address") or "").strip()
        priority = request.form.get("priority") or "routine"
        if priority not in PRIORITIES:
            priority = "routine"
        if not address:
            return render_template("new_call.html", me=me, natures=NATURES,
                                   ctype=ctype, nature=nature, priority=priority,
                                   error="An address or location is required.")
        try:
            addr_lat = float(request.form.get("addr_lat") or "")
            addr_lng = float(request.form.get("addr_lng") or "")
        except ValueError:
            addr_lat = addr_lng = None
        addr_formatted = (request.form.get("addr_formatted") or "").strip() or None
        if addr_lat is None and address and not address[:1].isdigit():
            poi = poi_lookup_in_lee(address)
            if poi:
                addr_lat, addr_lng, addr_formatted = poi
                address = addr_formatted
        if addr_lat is None and address and address[:1].isdigit():
            v_addr = validate_call_address(address)
            if v_addr:
                addr_lat, addr_lng = v_addr["lat"], v_addr["lng"]
        norm = _addr_norm(address)
        dup = None
        if request.form.get("force") != "1":
            cands = q("SELECT id, call_type, nature, address, priority, status,"
                      " units, dispatcher, created_at, lat, lng FROM calls"
                      " WHERE status IN ('open','dispatched') AND call_type = %s"
                      " ORDER BY id DESC LIMIT 25", (ctype,))
            for cd in cands:
                if _addr_norm(cd["address"] or "") == norm:
                    dup = cd
                    break
                if (addr_lat is not None and cd["lat"] is not None and
                        haversine_miles(addr_lat, addr_lng,
                                        cd["lat"], cd["lng"]) < 0.06):
                    dup = cd
                    break
        if dup:
            return render_template("new_call.html", me=me, natures=NATURES,
                                   ctype=ctype, nature=nature, priority=priority,
                                   dup=dup,
                                   caller_name=(request.form.get("caller_name") or "").strip(),
                                   caller_phone=(request.form.get("caller_phone") or "").strip(),
                                   notes_in=(request.form.get("notes_in") or "").strip(),
                                   notify=request.form.get("notify") == "1",
                                   error="")
        notify = request.form.get("notify") == "1"
        queue = request.form.get("queue") == "1"
        vals = [ctype, nature, address, priority,
                (request.form.get("caller_name") or "").strip() or None,
                (request.form.get("caller_phone") or "").strip() or None,
                (request.form.get("notes_in") or "").strip() or None,
                me["username"], addr_lat, addr_lng, addr_formatted,
                (request.form.get("response_channel") or "").strip() or None]
        cid = q("INSERT INTO calls (call_type, nature, address, priority,"
                " caller_name, caller_phone, notes_in, dispatcher, lat, lng,"
                " addr_formatted, response_channel, status, notified_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                + ("'pending'" if queue else "'open'") + ","
                + ("now()" if notify and not queue else "NULL")
                + ") RETURNING id",
                vals, one=True)["id"]
        if queue:
            q("INSERT INTO call_notes (call_id, author, text)"
              " VALUES (%s,%s,%s)", (cid, me["username"], "Sent to pending queue."))
        log_audit(me["username"], "call_created",
                  "call %s: %s, %s, %s" % (cid, ctype, nature, address))
        return redirect(url_for("call_view", call_id=cid))
    ctype = request.args.get("type") or "police"
    if ctype not in CALL_TYPES:
        ctype = "police"
    return render_template("new_call.html", me=me, natures=NATURES,
                           ctype=ctype, nature="", priority="routine", error="")


def _load_call(call_id):
    row = q("SELECT * FROM calls WHERE id = %s", (call_id,), one=True)
    if not row:
        abort(404)
    return row


@app.route("/call/<int:call_id>/unit-status", methods=["POST"])
@staff_required
def call_unit_status(call_id):
    me = current_user()
    c = _load_call(call_id)
    username = (request.form.get("username") or "").strip()
    status = request.form.get("status") or ""
    reason = (request.form.get("reason") or "").strip() or None
    if not username or status not in UNIT_CALL_STATUSES:
        return ("Pick a unit and a status.", 400)
    if not q("SELECT 1 FROM users WHERE username = %s AND role = 'unit'",
             (username,), one=True):
        return ("That unit does not exist.", 400)
    if c["status"] == "closed":
        return ("That call is already closed.", 400)
    q("INSERT INTO unit_call_status (call_id, username, status, reason, lat, lng)"
      " VALUES (%s,%s,%s,%s,NULL,NULL)", (call_id, username, status, reason))
    label = {"enroute": "is enroute", "on_scene": "is on scene",
             "transporting": "is transporting", "clear": "cleared"}[status]
    q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
      (call_id, me["username"],
       "Dispatch set %s: %s%s" % (username, label,
                                  " (reason: %s)" % reason if reason else "")))
    log_audit(me["username"], "dispatch_unit_status",
              "call %s: %s %s" % (call_id, username, status))
    return redirect(url_for("call_view", call_id=call_id))


@app.route("/call/<int:call_id>")
@login_required
def call_view(call_id):
    me = current_user()
    c = _load_call(call_id)
    if me["role"] == "unit":
        names = [t.strip().lower() for t in (c["units"] or "").split(",")
                 if t.strip()]
        if me["username"].lower() not in names:
            return redirect(url_for("unit_home"))
    notes = q("SELECT * FROM call_notes WHERE call_id = %s ORDER BY id",
              (call_id,))
    responses = unit_responses([call_id]).get(call_id, [])
    unit_users = q("SELECT username FROM users WHERE role = 'unit'"
                   " ORDER BY username")
    for n in notes:
        n["created_at"] = n["created_at"].isoformat()
    pages = q("SELECT * FROM call_pages WHERE call_id = %s"
              " ORDER BY id DESC LIMIT 20", (call_id,))
    c["pages"] = [{"units": p["units"],
                   "id": p["id"], "call_id": p["call_id"],
                   "paged_at_str": p["paged_at"].strftime("%a %b %d, %I:%M %p"),
                   "audio": bool(p["audio"])}
                  for p in pages]
    steps = q("SELECT * FROM sop_templates WHERE call_type = %s"
              " ORDER BY position", (c["call_type"],))
    state = {s["position"]: s for s in
             q("SELECT * FROM call_sop_state WHERE call_id = %s", (call_id,))}
    checklist = []
    for s in steps:
        st = state.get(s["position"])
        checklist.append({"position": s["position"], "text": s["text"],
                          "done": bool(st and st["done_at"]),
                          "done_by": st["done_by"] if st else None,
                          "done_at": st["done_at"].isoformat() if st and st["done_at"] else None})
    shares = q("SELECT * FROM shares WHERE call_id = %s ORDER BY id DESC",
               (call_id,))
    for s in shares:
        s["url"] = "/share/" + s["token"]
    return render_template("call.html", me=me, c=call_dict(c), notes=notes,
                           responses=responses, unit_users=unit_users,
                           checklist=checklist, shares=shares,
                           type_name=c["call_type"].capitalize())


@app.route("/call/<int:call_id>/status", methods=["POST"])
@staff_required
def call_status(call_id):
    me = current_user()
    c = _load_call(call_id)
    if request.is_json:
        body = request.get_json(silent=True) or {}
        status = body.get("status")
    else:
        status = request.form.get("status")
    if status not in STATUSES:
        abort(400)
    units = c["units"]
    if request.is_json:
        units = (body.get("units") or "").strip() or units
    else:
        units = (request.form.get("units") or "").strip() or units
    if status == "closed":
        q("UPDATE calls SET status = %s, units = %s, closed_at = now()"
          " WHERE id = %s", (status, units, call_id))
    else:
        q("UPDATE calls SET status = %s, units = %s, closed_at = NULL"
          " WHERE id = %s", (status, units, call_id))
    q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
      (call_id, "system", "Status set to %s by %s" % (status, me["username"])))
    log_audit(me["username"], "status", "call %s -> %s" % (call_id, status))
    if request.is_json:
        return jsonify(ok=True, status=status, units=units)
    return redirect(url_for("call_view", call_id=call_id))


ALERT_KEYS = {"say_type": True, "say_address": True, "say_nature": True,
              "say_time": True}
TYPE_TITLES = {"police": "Police", "fire": "Fire", "medical": "Medical",
               "other": "Call",
               "accident": "Traffic accident w/ injuries"}


def get_alert_format():
    fmt = dict(ALERT_KEYS)
    for r in q("SELECT key, value FROM settings WHERE key LIKE 'alert_say_%%'"):
        k = r["key"].replace("alert_say_", "say_")
        if k in fmt:
            fmt[k] = r["value"] == "1"
    return fmt


def alert_text(call, fmt):
    parts = []
    if fmt.get("say_type"):
        if call["call_type"] == "accident":
            parts.append("Traffic accident with injuries")
        else:
            parts.append(TYPE_TITLES.get(call["call_type"], "Call") + " call")
    if fmt.get("say_address"):
        parts.append("Address: %s" % (call["address"] or ""))
    if fmt.get("say_nature"):
        parts.append("Nature: %s" % (call["nature"] or ""))
    if fmt.get("say_time"):
        when = call.get("notified_at") or call.get("created_at")
        parts.append("Dispatched at %s" % (when or "").strftime("%I:%M %p")
                     if when else "Dispatched now")
    return ". ".join(p for p in parts if p) + "."


@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    me = current_user()
    if request.method == "POST":
        for k in ALERT_KEYS:
            q("INSERT INTO settings (key, value) VALUES (%s, %s)"
              " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
              ("alert_" + k, "1" if request.form.get(k) else "0"))
        log_audit(me["username"], "settings_saved", "call alert voice")
        return redirect(url_for("settings_page"))
    fmt = get_alert_format()
    example = alert_text(
        {"call_type": "fire", "address": "1201 1st Ave, Opelika",
         "nature": "structure fire", "notified_at": None,
         "created_at": datetime.now()},
        fmt)
    return render_template("settings.html", me=me, fmt=fmt, example=example)


@app.route("/settings/reset-calls", methods=["POST"])
@admin_required
def reset_calls():
    me = current_user()
    q("DELETE FROM calls")
    q("SELECT setval('calls_id_seq', 1, false)")
    log_audit(me["username"], "reset_call_numbers",
              "all calls deleted, numbering restarted at 1")
    return redirect(url_for("settings_page"))


def get_google_key():
    return os.environ.get("GOOGLE_KEY", "").strip() or None


def _fetch_json(url, headers=None, timeout=8, retries=1):
    """GET JSON with one retry after a short pause (the free map service
    rate-limits rapid requests)."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.5)
    raise last


def _geocode_in_lee(address):
    """OpenStreetMap lookup, kept to results that really sit in Lee County,
    Alabama (the free geocoder otherwise returns fuzzy look-alikes)."""
    s, w, n, e = LEE_BBOX
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 5,
         "viewbox": "%s,%s,%s,%s" % (w, n, e, s), "bounded": "1"})
    try:
        data = _fetch_json(url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
    except Exception:
        return None
    for d in data:
        addr = d.get("address") or {}
        disp = (d.get("display_name") or "").lower()
        if _in_lee_county(county=addr.get("county"), state=addr.get("state")) \
           or ("lee county" in disp and "alabama" in disp):
            return float(d["lat"]), float(d["lon"])
    return None


def poi_lookup_in_lee(address):
    """Look up a typed business or place name (no street number) on the free
    OpenStreetMap geocoder, kept to Lee County, Alabama. Returns
    (lat, lng, name and address) or None. The name-and-address string keeps
    the business name first, then the street and city, so the unit voice says
    both."""
    s_, w, n, e = LEE_BBOX
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 5,
         "viewbox": "%s,%s,%s,%s" % (w, n, e, s_), "bounded": "1"})
    try:
        data = _fetch_json(url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
    except Exception:
        return None
    for d in data:
        addr = d.get("address") or {}
        disp = (d.get("display_name") or "").lower()
        if not (_in_lee_county(county=addr.get("county"), state=addr.get("state"))
                or ("lee county" in disp and "alabama" in disp)):
            continue
        parts = [p.strip() for p in (d.get("display_name") or "").split(",")
                 if p.strip()]
        parts = [p for p in parts
                 if p.lower() not in ("lee county", "united states")]
        short = ", ".join(parts[:5])
        try:
            return float(d["lat"]), float(d["lon"]), short
        except Exception:
            continue
    return None


def _addr_norm(text):
    """Loose address key for duplicate checks: lowercase, punctuation out,
    single spaces, and street words folded to their short form ("second" to
    "2", "2nd" to "2", "south" to "s", "road" to "rd"). Catches extra
    spaces, missing commas, and "Second Ave" instead of "2nd Ave"."""
    import re as _re
    t = (text or "").lower()
    t = _re.sub(r"[^a-z0-9 ]+", " ", t)
    words = []
    for w in t.split():
        w = _re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", w)
        w = {"first": "1", "second": "2", "third": "3", "fourth": "4",
             "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
             "ninth": "9", "tenth": "10",
             "north": "n", "south": "s", "east": "e", "west": "w",
             "northeast": "ne", "northwest": "nw",
             "southeast": "se", "southwest": "sw",
             "road": "rd", "street": "st", "avenue": "ave",
             "drive": "dr", "lane": "ln", "boulevard": "blvd",
             "circle": "cir", "parkway": "pkwy", "court": "ct",
             "highway": "hwy", "trail": "trl"}.get(w, w)
        words.append(w)
    return " ".join(words)


def validate_call_address(address):
    """Check an address against Google Maps when a key is set, otherwise
    the free OpenStreetMap geocoder. Rural roads like "1085 Lee County
    Road 166" are retried with the county added. Returns
    {formatted, lat, lng, source} or None when nothing matches."""
    key = get_google_key()
    cands = [address]
    if "lee county" not in address.lower() and "lee road" not in address.lower():
        cands.append(address + ", Lee County, AL")
    if key:
        for cand in cands:
            try:
                url = ("https://maps.googleapis.com/maps/api/geocode/json?" +
                       urllib.parse.urlencode({"address": cand, "key": key}))
                data = _fetch_json(url)
                kept = _lee_from_geocode(data.get("results", []))
                if kept:
                    loc = None
                    for r in data["results"]:
                        if r["formatted_address"] in kept:
                            loc = r["geometry"]["location"]
                            return {"formatted": r["formatted_address"],
                                    "lat": float(loc["lat"]),
                                    "lng": float(loc["lng"]),
                                    "source": "google"}
            except Exception:
                pass
    for cand in cands:
        loc = _geocode_in_lee(cand)
        if loc:
            return {"formatted": cand, "lat": loc[0], "lng": loc[1],
                    "source": "openstreetmap"}
    return None


def geocode_call_address(address):
    """Look up a call address on the free OpenStreetMap geocoder. Returns
    (lat, lng) or None. The result is cached on the call row."""
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": "1"})
    try:
        data = _fetch_json(url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
    except Exception:
        return None
    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        return None


def haversine_miles(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a)) * 0.621371


# The dispatch area is Lee County, Alabama: the address box only fills in
# addresses there.
LEE_CENTER = (32.60, -85.36)
LEE_BBOX = (32.32, -85.68, 32.83, -85.03)  # south, west, north, east


def _f(v):
    """float() that never raises."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _in_lee_county(county=None, state=None, lat=None, lng=None):
    if county is not None:
        name = county.lower().replace("county", "").strip()
        if name != "lee":
            return False
        if state:
            st = state.lower().replace(".", "").strip()
            if st not in ("al", "alabama"):
                return False
        return True
    if lat is None or lng is None:
        return False
    s, w, n, e = LEE_BBOX
    return s <= lat <= n and w <= lng <= e


def _lee_from_geocode(results):
    """Keep the geocoder results that sit in Lee County, Alabama."""
    kept = []
    for r in results:
        comp = {c["types"][0]: c["long_name"] for c in r.get("address_components", [])}
        if _in_lee_county(county=comp.get("administrative_area_level_2"),
                          state=comp.get("administrative_area_level_1")):
            kept.append(r["formatted_address"])
    return kept


@app.route("/api/address-suggest")
@staff_required
def address_suggest():
    """Address suggestions while typing. Google when a key is set (Places
    autocomplete, then Geocoding as backup), free OpenStreetMap otherwise."""
    q_str = (request.args.get("q") or "").strip()
    if len(q_str) < 3:
        return jsonify(ok=True, suggestions=[])
    out = []
    key = get_google_key()
    if key:
        preds = []
        try:
            url = ("https://maps.googleapis.com/maps/api/place/autocomplete/json?" +
                   urllib.parse.urlencode({"input": q_str, "key": key,
                                           "components": "country:us",
                                           "locationrestriction":
                                               "circle:30000@%s,%s" % LEE_CENTER}))
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK":
                preds = [p["description"] for p in data.get("predictions", [])][:5]
        except Exception:
            preds = []
        # Keep only the suggestions that really sit in Lee County, Alabama.
        for p in preds:
            try:
                url = ("https://maps.googleapis.com/maps/api/geocode/json?" +
                       urllib.parse.urlencode({"address": p, "key": key}))
                with urllib.request.urlopen(url, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                out.extend(_lee_from_geocode(data.get("results", [])))
            except Exception:
                pass
        out = out[:5]
        if not out:
            try:
                url = ("https://maps.googleapis.com/maps/api/geocode/json?" +
                       urllib.parse.urlencode({"address": q_str + ", Lee County, AL",
                                               "key": key}))
                with urllib.request.urlopen(url, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                out = _lee_from_geocode(data.get("results", []))[:5]
            except Exception:
                out = []
    if not out:
        try:
            s, w, n, e = LEE_BBOX
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q_str, "format": "json", "limit": 15,
                 "viewbox": "%s,%s,%s,%s" % (w, n, e, s), "bounded": "1"})
            data = _fetch_json(
                url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
            kept = []
            for d in data:
                addr = d.get("address") or {}
                if _in_lee_county(county=addr.get("county"),
                                  state=addr.get("state")):
                    kept.append(d["display_name"])
                elif _in_lee_county(lat=_f(d.get("lat")),
                                    lng=_f(d.get("lon"))):
                    kept.append(d["display_name"])
            out = kept[:5]
        except Exception:
            out = []
    return jsonify(ok=True, suggestions=out)


@app.route("/api/validate-address", methods=["POST"])
@staff_required
def api_validate_address():
    address = (request.form.get("address") or "").strip()
    if not address:
        return jsonify(ok=False, error="Type an address first."), 400
    v = validate_call_address(address)
    if not v:
        return jsonify(ok=False,
                       error="Could not find that address on the map."
                             " Check the spelling or add the city."
                             " You can still send the call without a map check."), 404
    return jsonify(ok=True, **v)


@app.route("/call/<int:call_id>/details", methods=["POST"])
@staff_required
def call_details(call_id):
    me = current_user()
    c = _load_call(call_id)
    updates = {}
    changes = []
    address = (request.form.get("address") or "").strip()
    if address and address != (c["address"] or "").strip():
        lat = lng = None
        try:
            lat = float(request.form.get("addr_lat") or "")
            lng = float(request.form.get("addr_lng") or "")
        except ValueError:
            lat = lng = None
        fmt = (request.form.get("addr_formatted") or "").strip() or None
        if lat is None or lng is None:
            if not address[:1].isdigit():
                poi = poi_lookup_in_lee(address)
                if poi:
                    address, lat, lng, fmt = poi[2], poi[0], poi[1], poi[2]
            if lat is None or lng is None:
                v = validate_call_address(address)
                if v:
                    lat, lng, fmt = v["lat"], v["lng"], v["formatted"]
        updates["address"] = address
        updates["lat"] = lat
        updates["lng"] = lng
        updates["addr_formatted"] = fmt
        changes.append("Address changed to %s." % (fmt or address))
    for field, label in (("caller_name", "Caller name"),
                         ("caller_phone", "Call back phone"),
                         ("response_channel", "Response channel")):
        val = (request.form.get(field) or "").strip()
        old = (c[field] or "").strip() if c[field] else ""
        if val != old:
            updates[field] = val or None
            changes.append("%s changed to %s." % (label, val or "(none)"))
    if updates:
        set_sql = ", ".join(k + " = %s" for k in updates)
        q("UPDATE calls SET " + set_sql + " WHERE id = %s",
          tuple(updates.values()) + (call_id,))
        if changes:
            q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
              (call_id, me["username"], " ".join(changes)))
        log_audit(me["username"], "call_details",
                  "call %s: %s" % (call_id, "; ".join(changes)))
    return redirect(url_for("call_view", call_id=call_id))


@app.route("/call/<int:call_id>/delete", methods=["POST"])
@admin_required
def call_delete(call_id):
    me = current_user()
    row = q("SELECT id, nature, address FROM calls WHERE id = %s", (call_id,),
            one=True)
    if not row:
        flash("Call #%s was already gone." % call_id)
        return redirect(url_for("board"))
    q("DELETE FROM calls WHERE id = %s", (call_id,))
    q("DELETE FROM call_notes WHERE call_id = %s", (call_id,))
    log_audit(me["username"], "call_deleted",
              "call %s: %s, %s" % (call_id, row["nature"], row["address"]))
    flash("Call #%s deleted." % call_id)
    return redirect(url_for("board"))


@app.route("/call/<int:call_id>/recommend", methods=["POST"])
@staff_required
def call_recommend(call_id):
    me = current_user()
    call = _load_call(call_id)
    if call["lat"] is None or call["lng"] is None:
        loc = geocode_call_address(call["address"])
        if loc:
            q("UPDATE calls SET lat = %s, lng = %s WHERE id = %s",
              (loc[0], loc[1], call_id))
            clat, clng = loc
        else:
            clat, clng = None, None
    else:
        clat, clng = call["lat"], call["lng"]
    units = q("SELECT username, lat, lng FROM users"
              " WHERE role = 'unit' AND unit_status = 'on_duty'"
              " AND lat IS NOT NULL AND lng IS NOT NULL")
    if clat is not None:
        recs = sorted(
            ({"username": u["username"],
              "miles": round(haversine_miles(clat, clng, u["lat"], u["lng"]), 1)}
             for u in units),
            key=lambda r: r["miles"])
    else:
        recs = sorted(
            ({"username": u["username"], "miles": None} for u in units),
            key=lambda r: r["username"])
    log_audit(me["username"], "units_recommended", "call %s" % call_id)
    return jsonify(ok=True, recommendations=recs)


@app.route("/call/<int:call_id>/pending", methods=["POST"])
@staff_required
def call_pending(call_id):
    me = current_user()
    c = _load_call(call_id)
    if c["status"] not in ("open", "dispatched"):
        return ("Only open or dispatched calls can go to the pending queue.", 400)
    q("UPDATE calls SET status = 'pending' WHERE id = %s", (call_id,))
    q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
      (call_id, me["username"],
       "Sent to pending queue by %s." % me["username"]))
    log_audit(me["username"], "call_pending", "call %s" % call_id)
    return redirect(url_for("call_view", call_id=call_id))


PAGE_TITLES = {"police": "Police", "fire": "Fire", "medical": "Medical",
               "other": "Call", "accident": "Traffic accident with injuries"}


def page_speech_text(c):
    """The full spoken page, the same wording the unit phones speak:
    type, address, nature, response channel, then the repeat and the time."""
    now_c = datetime.now(ZoneInfo("America/Chicago"))
    stamp = "The time is %d:%02d." % (now_c.hour % 12 or 12, now_c.minute)
    ctype = (c["call_type"] or "").strip().lower()
    first = ""
    if ctype:
        title = (PAGE_TITLES.get(ctype, ctype.title())
                 if ctype != "accident" else PAGE_TITLES["accident"])
        first = ("%s call. " % title) if ctype != "accident" else "%s. " % title
    first += "Address: %s." % (c["address"] or "")
    if (c["nature"] or "").strip():
        first += " Nature: %s." % c["nature"].strip()
    if (c["response_channel"] or "").strip():
        first += " Respond on %s." % c["response_channel"].strip()
    again = "Address: %s." % (c["address"] or "")
    if (c["nature"] or "").strip():
        again += " Nature: %s." % c["nature"].strip()
    if (c["response_channel"] or "").strip():
        again += " Respond on %s." % c["response_channel"].strip()
    return "%s %s %s" % (first, again, stamp)


def record_page_audio(page_id, c):
    """Synthesize and store an MP3 recording of this page. Never fails the
    page itself: on any error the row just keeps its text, no audio."""
    try:
        from gtts import gTTS
        import io as _io
        buf = _io.BytesIO()
        gTTS(text=page_speech_text(c), lang="en").write_to_fp(buf)
        q("UPDATE call_pages SET page_text = %s, audio = %s WHERE id = %s",
          (page_speech_text(c), buf.getvalue(), page_id))
    except Exception:
        q("UPDATE call_pages SET page_text = %s WHERE id = %s",
          (page_speech_text(c), page_id))


@app.route("/call/<int:call_id>/notify", methods=["POST"])
@staff_required
def call_notify(call_id):
    me = current_user()
    c = _load_call(call_id)
    if not (c["units"] or "").strip():
        q("UPDATE calls SET status = 'pending' WHERE id = %s"
          " AND status <> 'pending'", (call_id,))
        q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
          (call_id, me["username"], "No units available; call is pending."))
        log_audit(me["username"], "call_pending", "call %s (no units)" % call_id)
        if request.is_json:
            return jsonify(ok=True, pending=True)
        return redirect(url_for("call_view", call_id=call_id))
    q("UPDATE calls SET notified_at = now(), status = 'dispatched',"
      " page_count = COALESCE(page_count, 0) + 1 WHERE id = %s", (call_id,))
    q("INSERT INTO call_pages (call_id, units) VALUES (%s, %s)",
      (call_id, c["units"]))
    record_page_audio(q("SELECT id FROM call_pages WHERE call_id = %s"
                        " ORDER BY id DESC LIMIT 1", (call_id,))[0]["id"], c)
    log_audit(me["username"], "call_sent_to_units", "call %s" % call_id)
    if request.is_json:
        return jsonify(ok=True)
    return redirect(url_for("call_view", call_id=call_id))


@app.route("/call-page/<int:page_id>/audio")
@login_required
def page_audio(page_id):
    rows = q("SELECT audio FROM call_pages WHERE id = %s", (page_id,))
    if not rows or not rows[0]["audio"]:
        abort(404)
    return Response(rows[0]["audio"], mimetype="audio/mpeg")


@app.route("/call/<int:call_id>/note", methods=["POST"])
@staff_required
def call_note(call_id):
    me = current_user()
    _load_call(call_id)
    text = (request.form.get("text") or "").strip()
    if text:
        q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
          (call_id, me["username"], text))
        log_audit(me["username"], "note_added", "call %s" % call_id)
    return redirect(url_for("call_view", call_id=call_id))


@app.route("/call/<int:call_id>/units", methods=["POST"])
@staff_required
def call_units(call_id):
    me = current_user()
    c = _load_call(call_id)
    units = (request.form.get("units") or "").strip()
    mode = (request.form.get("mode") or "").strip()
    req = [t.strip() for t in units.split(",") if t.strip()]
    existing = [t.strip() for t in (c["units"] or "").split(",") if t.strip()]
    added = [n for n in req if n not in existing]
    if added:
        offd = q("SELECT username FROM users WHERE role = 'unit'"
                 " AND lower(username) = ANY(%s)"
                 " AND COALESCE(unit_status, 'off_duty') <> 'on_duty'",
                 ([n.lower() for n in added],))
        if offd:
            bad = ", ".join(sorted(r["username"] for r in offd))
            return ("You cannot assign an off-duty unit: %s. "
                    "Put the unit on duty first." % bad), 400
    if mode == "add" and units:
        cur = [t.strip() for t in (c["units"] or "").split(",") if t.strip()]
        for name in [t.strip() for t in units.split(",") if t.strip()]:
            if name not in cur:
                cur.append(name)
        units = ", ".join(cur)
    q("UPDATE calls SET units = %s WHERE id = %s", (units, call_id))
    if not units and c["notified_at"] and c["status"] not in ("closed", "pending"):
        q("UPDATE calls SET status = 'pending', notified_at = NULL WHERE id = %s",
          (call_id,))
        q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
          (call_id, me["username"],
           "All units removed; call sent back to the pending queue."))
        log_audit(me["username"], "call_pending",
                  "call %s (all units removed)" % call_id)
    elif units and c["notified_at"] and c["status"] != "closed":
        q("UPDATE calls SET page_count = COALESCE(page_count, 0) + 1"
          " WHERE id = %s", (call_id,))
        q("INSERT INTO call_pages (call_id, units) VALUES (%s, %s)",
          (call_id, units))
        record_page_audio(q("SELECT id FROM call_pages WHERE call_id = %s"
                            " ORDER BY id DESC LIMIT 1",
                            (call_id,))[0]["id"], c)
        q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
          (call_id, me["username"],
           "Units changed to %s; assigned units re-paged." % units))
        log_audit(me["username"], "units", "call %s units: %s (re-paged)"
                  % (call_id, units))
    else:
        log_audit(me["username"], "units", "call %s units: %s" % (call_id, units))
    return redirect(url_for("call_view", call_id=call_id))


@app.route("/call/<int:call_id>/sop", methods=["POST"])
@staff_required
def call_sop(call_id):
    me = current_user()
    _load_call(call_id)
    position = request.form.get("position", type=int)
    if position is None:
        abort(400)
    done = request.form.get("done") == "1"
    if done:
        q("INSERT INTO call_sop_state (call_id, position, done_at, done_by)"
          " VALUES (%s,%s,now(),%s)"
          " ON CONFLICT (call_id, position)"
          " DO UPDATE SET done_at = now(), done_by = EXCLUDED.done_by",
          (call_id, position, me["username"]))
    else:
        q("DELETE FROM call_sop_state WHERE call_id = %s AND position = %s",
          (call_id, position))
    return jsonify(ok=True)


# ---------- unit member pages ----------

@app.route("/unit")
@login_required
def unit_home():
    me = current_user()
    if me["role"] != "unit":
        return redirect(url_for("board"))
    types = [t for t in (me["unit_types"] or "").split(",") if t in CALL_TYPES]
    return render_template("unit.html", me=me, types=types,
                           alert_fmt=get_alert_format())


@app.route("/api/unit-board")
@login_required
def api_unit_board():
    me = current_user()
    types = [t for t in (me["unit_types"] or "").split(",") if t in CALL_TYPES]
    if me["role"] != "unit":
        return jsonify(active=[], recent=[],
                       me_status=me["unit_status"] or "off_duty")
    def mine(r):
        names = [t.strip().lower() for t in (r["units"] or "").split(",")
                 if t.strip()]
        return me["username"].lower() in names

    active = q("SELECT * FROM calls WHERE status IN ('open','dispatched')"
               " AND notified_at IS NOT NULL ORDER BY id DESC")
    active = [r for r in active if mine(r)]
    recent = q("SELECT * FROM calls WHERE status = 'closed'"
               " AND notified_at IS NOT NULL"
               " ORDER BY closed_at DESC NULLS LAST, id DESC LIMIT 50")
    recent = [r for r in recent if mine(r)][:10]
    resp = unit_responses([r["id"] for r in active])
    active_out = []
    for r in active:
        d = call_dict(r)
        entries = resp.get(r["id"], [])
        d["responses"] = entries
        for e in entries:
            if e["username"] == me["username"]:
                d["my_status"] = e["status"]
        active_out.append(d)
    return jsonify(active=active_out,
                   recent=[call_dict(r) for r in recent],
                   me_status=me["unit_status"] or "off_duty",
                   no_types=not types)


UNIT_CALL_STATUSES = ("enroute", "on_scene", "transporting", "clear")


def _haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def unit_responses(call_ids):
    """Latest status per unit per call, with driving-line miles between the
    GPS fixes recorded at each status change."""
    if not call_ids:
        return {}
    ph = ",".join(["%s"] * len(call_ids))
    rows = q("SELECT * FROM unit_call_status WHERE call_id IN (%s)"
             " ORDER BY id" % ph, tuple(call_ids))
    grouped = {}
    for row in rows:
        grouped.setdefault((row["call_id"], row["username"]), []).append(row)
    result = {}
    for (call_id, username), rows_ in grouped.items():
        last = rows_[-1]
        pts = [(r["lat"], r["lng"]) for r in rows_
               if r["lat"] is not None and r["lng"] is not None]
        miles = None
        if len(pts) >= 2:
            miles = 0.0
            for (a, b) in zip(pts, pts[1:]):
                miles += _haversine_miles(a[0], a[1], b[0], b[1])
            miles = round(miles, 1)
        result.setdefault(call_id, []).append({
            "username": username,
            "status": last["status"],
            "reason": last["reason"],
            "at": last["created_at"].isoformat(),
            "miles": miles,
        })
    return result


@app.route("/api/unit/call-status", methods=["POST"])
@login_required
def unit_call_status_set():
    me = current_user()
    if me["role"] != "unit":
        abort(403)
    body = request.get_json(silent=True) or {}
    try:
        call_id = int(body.get("call_id"))
    except (TypeError, ValueError):
        abort(400)
    status = body.get("status")
    if status not in UNIT_CALL_STATUSES:
        abort(400)
    c = _load_call(call_id)
    if c["status"] == "closed":
        return jsonify(ok=False, error="That call is already closed."), 400
    reason = (body.get("reason") or "").strip() or None
    q("INSERT INTO unit_call_status (call_id, username, status, reason, lat, lng)"
      " VALUES (%s,%s,%s,%s,%s,%s)",
      (call_id, me["username"], status, reason, me["lat"], me["lng"]))
    if status == "enroute":
        text = "%s is enroute." % me["username"]
    elif status == "on_scene":
        text = "%s is on scene." % me["username"]
    elif status == "transporting":
        text = "%s is transporting." % me["username"]
    else:
        text = "%s cleared. Reason: %s" % (me["username"], reason or "not given")
    q("INSERT INTO call_notes (call_id, author, text) VALUES (%s,%s,%s)",
      (call_id, me["username"], text))
    log_audit(me["username"], "unit_call_status",
              "call %s: %s" % (call_id, status))
    return jsonify(ok=True)


@app.route("/api/unit/status", methods=["POST"])
@login_required
def unit_status():
    me = current_user()
    if me["role"] != "unit":
        abort(403)
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status == "on_duty":
        q("UPDATE users SET unit_status = 'on_duty', lat = %s, lng = %s,"
          " loc_updated = now() WHERE id = %s",
          (body.get("lat"), body.get("lng"), me["id"]))
        log_audit(me["username"], "unit_on_duty")
    elif status == "off_duty":
        q("UPDATE users SET unit_status = 'off_duty', lat = NULL, lng = NULL,"
          " loc_updated = now() WHERE id = %s", (me["id"],))
        log_audit(me["username"], "unit_off_duty")
    else:
        # a location ping: stored only while the unit is on duty
        if me["unit_status"] == "on_duty":
            q("UPDATE users SET lat = %s, lng = %s, loc_updated = now()"
              " WHERE id = %s", (body.get("lat"), body.get("lng"), me["id"]))
    return jsonify(ok=True)


@app.route("/api/dispatch/unit-duty", methods=["POST"])
@staff_required
def dispatch_unit_duty():
    """Let dispatch put a unit on duty or off duty from the board."""
    me = current_user()
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    status = body.get("status")
    if status not in ("on_duty", "off_duty"):
        abort(400)
    u = q("SELECT * FROM users WHERE username = %s AND role = 'unit'",
          (username,), one=True)
    if not u:
        return jsonify(ok=False, error="That unit does not exist."), 400
    if status == "on_duty":
        q("UPDATE users SET unit_status = 'on_duty' WHERE username = %s",
          (username,))
        log_audit(me["username"], "unit_duty_set", "Put %s on duty" % username)
    else:
        q("UPDATE users SET unit_status = 'off_duty', lat = NULL, lng = NULL,"
          " loc_updated = now() WHERE username = %s", (username,))
        log_audit(me["username"], "unit_duty_set", "Took %s off duty" % username)
    return jsonify(ok=True)


@app.route("/units")
@role_required("admin", "dispatcher")
def units_page():
    me = current_user()
    return render_template("units.html", me=me)


@app.route("/api/units")
@role_required("admin", "dispatcher")
def api_units():
    rows = q("SELECT * FROM users WHERE role = 'unit' ORDER BY username")
    out = []
    for r in rows:
        out.append({"username": r["username"],
                    "unit_types": r["unit_types"] or "",
                    "unit_status": r["unit_status"] or "off_duty",
                    "lat": r["lat"], "lng": r["lng"],
                    "loc_updated": r["loc_updated"].isoformat() if r["loc_updated"] else None})
    return jsonify(units=out)


# ---------- SOPs ----------

@app.route("/sops")
@login_required
def sops():
    me = current_user()
    by_type = {}
    for s in q("SELECT * FROM sop_templates ORDER BY call_type, position"):
        by_type.setdefault(s["call_type"], []).append(s)
    return render_template("sops.html", me=me, sops=by_type)


@app.route("/sops/edit", methods=["POST"])
@admin_required
def sops_edit():
    me = current_user()
    ctype = request.form.get("call_type") or ""
    if ctype not in CALL_TYPES:
        abort(400)
    lines = [ln.strip() for ln in (request.form.get("steps") or "").splitlines()
             if ln.strip()]
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sop_templates WHERE call_type = %s", (ctype,))
        for pos, text in enumerate(lines, 1):
            cur.execute("INSERT INTO sop_templates (call_type, position, text)"
                        " VALUES (%s,%s,%s)", (ctype, pos, text))
    log_audit(me["username"], "sops_edited", ctype)
    return redirect(url_for("sops"))


# ---------- sharing and export ----------

@app.route("/call/<int:call_id>/share", methods=["POST"])
@staff_required
def call_share(call_id):
    me = current_user()
    _load_call(call_id)
    token = secrets.token_urlsafe(16)
    q("INSERT INTO shares (token, call_id, label, created_by)"
      " VALUES (%s,%s,%s,%s)", (token, call_id, "single call", me["username"]))
    log_audit(me["username"], "share_created", "call %s" % call_id)
    return jsonify(ok=True, url="/share/" + token)


@app.route("/share/log", methods=["POST"])
@staff_required
def share_log():
    me = current_user()
    token = secrets.token_urlsafe(16)
    q("INSERT INTO shares (token, call_id, label, created_by)"
      " VALUES (%s,NULL,%s,%s)", (token, "whole call log", me["username"]))
    log_audit(me["username"], "share_created", "whole call log")
    return jsonify(ok=True, url="/share/" + token)


@app.route("/share/<token>")
def share_view(token):
    row = q("SELECT * FROM shares WHERE token = %s", (token,), one=True)
    if not row:
        abort(404)
    if row["call_id"]:
        c = _load_call(row["call_id"])
        notes = q("SELECT * FROM call_notes WHERE call_id = %s ORDER BY id",
                  (row["call_id"],))
        for n in notes:
            n["created_at"] = n["created_at"].isoformat()
        steps = q("SELECT * FROM sop_templates WHERE call_type = %s"
                  " ORDER BY position", (c["call_type"],))
        state = {s["position"]: s for s in
                 q("SELECT * FROM call_sop_state WHERE call_id = %s",
                   (row["call_id"],))}
        checklist = []
        for s in steps:
            st = state.get(s["position"])
            checklist.append({"position": s["position"], "text": s["text"],
                              "done": bool(st and st["done_at"]),
                              "done_by": st["done_by"] if st else None})
        return render_template("share_call.html", c=call_dict(c), notes=notes,
                               checklist=checklist, type_name=c["call_type"].capitalize())
    calls = q("SELECT * FROM calls ORDER BY id DESC LIMIT 200")
    return render_template("share_log.html", calls=[call_dict(r) for r in calls])


@app.route("/export.csv")
@staff_required
def export_csv():
    me = current_user()
    rows = q("SELECT * FROM calls ORDER BY id")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "type", "nature", "priority", "address",
                "caller_name", "caller_phone", "status", "units",
                "dispatcher", "closed_at"])
    for r in rows:
        d = call_dict(r)
        w.writerow([d["id"], d["created_at"], d["call_type"], d["nature"],
                    d["priority"], d["address"], d["caller_name"] or "",
                    d["caller_phone"] or "", d["status"], d["units"] or "",
                    d["dispatcher"] or "", d["closed_at"] or ""])
    log_audit(me["username"], "export_csv", "%d calls" % len(rows))
    out = buf.getvalue()
    return Response(out, mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=calls.csv"})


# ---------- users and audit (admin) ----------

@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    me = current_user()
    error = ""
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        role = request.form.get("role") or "dispatcher"
        if role not in ("admin", "dispatcher", "unit"):
            role = "dispatcher"
        utypes = ",".join(t for t in request.form.getlist("unit_types")
                          if t in CALL_TYPES)
        if len(u) < 3 or len(p) < 6:
            error = "Username needs at least 3 characters and password at least 6."
        elif q("SELECT 1 FROM users WHERE username = %s", (u,), one=True):
            error = "That username is already used."
        elif role == "unit" and not utypes:
            error = "A unit needs at least one call type picked (police, fire, medical, other), or it will never receive calls."
        else:
            q("INSERT INTO users (username, pass_hash, role, unit_types)"
              " VALUES (%s,%s,%s,%s)",
              (u, generate_password_hash(p), role,
               utypes if role == "unit" else None))
            log_audit(me["username"], "user_created", u)
            return redirect(url_for("users"))
    return render_template("users.html", me=me,
                           users=q("SELECT id, username, role, unit_types, created_at"
                                   " FROM users ORDER BY username"), error=error)


@app.route("/user/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(uid):
    me = current_user()
    u = q("SELECT * FROM users WHERE id = %s", (uid,), one=True)
    if not u:
        abort(404)
    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        role = request.form.get("role") or ""
        password = request.form.get("password") or ""
        utypes = ",".join(t for t in request.form.getlist("unit_types")
                          if t in CALL_TYPES)
        admins = q("SELECT count(*) AS n FROM users WHERE role = 'admin'",
                   one=True)["n"]
        if len(username) < 3:
            error = "Username needs at least 3 characters."
        elif role not in ("admin", "dispatcher", "unit"):
            error = "Pick a real role."
        elif username.lower() != u["username"].lower() and \
                q("SELECT 1 FROM users WHERE lower(username) = lower(%s)"
                  " AND id <> %s", (username, uid), one=True):
            error = "That username is already used."
        elif password and len(password) < 6:
            error = "Password needs at least 6 characters."
        elif (me["id"] == u["id"] and role != "admin" and admins <= 1):
            error = "You are the only admin, so you cannot change your own role."
        elif role == "unit" and not utypes:
            error = "A unit needs at least one call type picked (police, fire, medical, other), or it will never receive calls."
        else:
            sets = ["username = %s", "role = %s",
                    "unit_types = %s" if role == "unit" else "unit_types = NULL"]
            vals = [username, role] + ([utypes] if role == "unit" else [])
            if password:
                sets.append("pass_hash = %s")
                vals.append(generate_password_hash(password))
            vals.append(uid)
            q("UPDATE users SET " + ", ".join(sets) + " WHERE id = %s", vals)
            log_audit(me["username"], "user_edit",
                      "user %s: now %s (%s)" % (uid, username, role))
            return redirect(url_for("users"))
    return render_template("user_edit.html", me=me, u=u, error=error)


@app.route("/user/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    me = current_user()
    u = q("SELECT * FROM users WHERE id = %s", (uid,), one=True)
    if not u:
        abort(404)
    if u["id"] == me["id"]:
        return jsonify(ok=False, error="You cannot delete your own account here."), 400
    if u["role"] == "admin" and \
            q("SELECT count(*) AS n FROM users WHERE role = 'admin'",
              one=True)["n"] <= 1:
        return jsonify(ok=False, error="That is the only admin."), 400
    q("DELETE FROM users WHERE id = %s", (uid,))
    log_audit(me["username"], "user_delete", u["username"])
    return jsonify(ok=True)


@app.route("/users/<int:uid>/types", methods=["POST"])
@admin_required
def user_types(uid):
    me = current_user()
    row = q("SELECT * FROM users WHERE id = %s", (uid,), one=True)
    if not row or row["role"] != "unit":
        abort(404)
    utypes = ",".join(t for t in request.form.getlist("unit_types")
                      if t in CALL_TYPES)
    q("UPDATE users SET unit_types = %s WHERE id = %s", (utypes, uid))
    log_audit(me["username"], "unit_types_changed",
              "%s: %s" % (row["username"], utypes))
    return redirect(url_for("users"))


@app.route("/audit")
@staff_required
def audit():
    import re
    me = current_user()
    rows = q("SELECT * FROM audit ORDER BY id DESC LIMIT 200")
    out = []
    for r in rows:
        d = r["detail"] or ""
        a = r["action"]
        m = re.search(r"call (\d+)", d)
        cid = m.group(1) if m else None
        def after(mark):
            return d.split(mark, 1)[1].strip() if mark in d else ""
        if a == "call_created":
            text = "Created call %s (%s)" % (cid, after(":")) if cid else "Created a call"
        elif a == "call_sent_to_units":
            text = "Sent call %s to unit phones (tone + voice)" % cid if cid else "Sent a call to unit phones"
        elif a == "call_pending":
            if "all units removed" in d and cid:
                text = ("Removed every unit from call %s; the call went back "
                        "to the pending queue" % cid)
            else:
                text = "Moved call %s to the pending queue" % cid if cid else d
        elif a == "units":
            text = "Changed units on call %s to %s" % (cid, after("units:")) if cid else "Changed units"
        elif a == "units_recommended":
            text = "Looked up the closest on-duty units for call %s" % cid
        elif a == "unit_duty_set":
            text = d or "Changed a unit's duty status"
        elif a == "dispatch_unit_status":
            text = "Dispatch set a unit status on call %s (%s)" % (cid, after(":"))
        elif a == "unit_call_status":
            text = "Unit status update on call %s (%s)" % (cid, after(":"))
        elif a == "note_added":
            text = "Added a note on call %s" % cid
        elif a == "call_details":
            text = "Edited the address or caller on call %s" % cid
        elif a == "call_deleted":
            text = "Deleted call %s" % cid
        elif a == "status":
            text = "Call %s status set to %s" % (cid, after("->")) if cid else d
        elif a == "login":
            text = "Signed in"
        elif a == "setup":
            text = "Created the first admin account"
        elif a == "settings_saved":
            text = "Saved settings (%s)" % d if d else "Saved settings"
        elif a == "reset_call_numbers":
            text = "Reset the call numbering"
        else:
            text = a.replace("_", " ") + (" (%s)" % d if d else "")
        out.append({"created_at": r["created_at"].isoformat(),
                    "who": r["username"] or "", "text": text, "cid": cid})
    return render_template("audit.html", me=me, rows=out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
