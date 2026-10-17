"""CAD Lite: a small computer-aided dispatch app.
Python + Flask + PostgreSQL. Text-to-speech for address and nature,
SOP checklists for police / fire / medical calls, call sharing and export.
"""
import csv
import io
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime
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

CALL_TYPES = ("police", "fire", "medical", "other")
STATUSES = ("open", "dispatched", "closed")
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
    recent = q("SELECT * FROM calls WHERE status = 'closed'"
               " ORDER BY closed_at DESC NULLS LAST, id DESC LIMIT 25")
    return jsonify(active=[call_dict(r) for r in active],
                   recent=[call_dict(r) for r in recent])


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
        norm = " ".join(address.lower().split())
        dup = None
        if request.form.get("force") != "1":
            dup = q("SELECT id, call_type, nature, address, priority, status,"
                    " units, dispatcher, created_at FROM calls"
                    " WHERE status IN ('open','dispatched') AND call_type = %s"
                    " AND lower(regexp_replace(address, '\\s+', ' ', 'g')) = %s"
                    " ORDER BY id DESC LIMIT 1",
                    (ctype, norm), one=True)
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
        vals = [ctype, nature, address, priority,
                (request.form.get("caller_name") or "").strip() or None,
                (request.form.get("caller_phone") or "").strip() or None,
                (request.form.get("notes_in") or "").strip() or None,
                me["username"], addr_lat, addr_lng, addr_formatted]
        cid = q("INSERT INTO calls (call_type, nature, address, priority,"
                " caller_name, caller_phone, notes_in, dispatcher, lat, lng,"
                " addr_formatted, notified_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                + ("now()" if notify else "NULL") + ") RETURNING id",
                vals, one=True)["id"]
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


@app.route("/call/<int:call_id>")
@login_required
def call_view(call_id):
    me = current_user()
    c = _load_call(call_id)
    notes = q("SELECT * FROM call_notes WHERE call_id = %s ORDER BY id",
              (call_id,))
    responses = unit_responses([call_id]).get(call_id, [])
    for n in notes:
        n["created_at"] = n["created_at"].isoformat()
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
                           responses=responses,
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
               "other": "Call"}


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
        gk = (request.form.get("google_key") or "").strip()
        q("INSERT INTO settings (key, value) VALUES ('google_key', %s)"
          " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (gk,))
        log_audit(me["username"], "settings_saved", "call alert voice")
        return redirect(url_for("settings_page"))
    gk = get_google_key()
    fmt = get_alert_format()
    example = alert_text(
        {"call_type": "fire", "address": "1201 1st Ave, Opelika",
         "nature": "structure fire", "notified_at": None,
         "created_at": datetime.now()},
        fmt)
    return render_template("settings.html", me=me, fmt=fmt, example=example,
                           google_key=gk or "")


def get_google_key():
    try:
        r = q("SELECT value FROM settings WHERE key = 'google_key'", one=True)
        if r and (r["value"] or "").strip():
            return r["value"].strip()
    except Exception:
        pass
    return os.environ.get("GOOGLE_KEY", "").strip() or None


def validate_call_address(address):
    """Check an address against Google Maps when a key is set, otherwise
    the free OpenStreetMap geocoder. Returns
    {formatted, lat, lng, source} or None when nothing matches."""
    key = get_google_key()
    if key:
        try:
            url = ("https://maps.googleapis.com/maps/api/geocode/json?" +
                   urllib.parse.urlencode({"address": address, "key": key}))
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("results"):
                r = data["results"][0]
                loc = r["geometry"]["location"]
                return {"formatted": r["formatted_address"],
                        "lat": float(loc["lat"]), "lng": float(loc["lng"]),
                        "source": "google"}
        except Exception:
            pass
    loc = geocode_call_address(address)
    if not loc:
        return None
    return {"formatted": address, "lat": loc[0], "lng": loc[1],
            "source": "openstreetmap"}


def geocode_call_address(address):
    """Look up a call address on the free OpenStreetMap geocoder. Returns
    (lat, lng) or None. The result is cached on the call row."""
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": "1"})
    req = urllib.request.Request(
        url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
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
        try:
            url = ("https://maps.googleapis.com/maps/api/place/autocomplete/json?" +
                   urllib.parse.urlencode({"input": q_str, "key": key,
                                           "components": "country:us"}))
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK":
                out = [p["description"] for p in data.get("predictions", [])][:5]
        except Exception:
            out = []
        if not out:
            try:
                url = ("https://maps.googleapis.com/maps/api/geocode/json?" +
                       urllib.parse.urlencode({"address": q_str, "key": key}))
                with urllib.request.urlopen(url, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                out = [r["formatted_address"]
                       for r in data.get("results", [])][:5]
            except Exception:
                out = []
    if not out:
        try:
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q_str, "format": "json", "limit": 5})
            req = urllib.request.Request(
                url, headers={"User-Agent": "CAD-Lite/1.0 (dispatch aid)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out = [d["display_name"] for d in data][:5]
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
                             " Check the spelling or add the city."), 404
    return jsonify(ok=True, **v)


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
        if not loc:
            return jsonify(ok=False,
                           error="Could not find that address on the map."
                                 " Check the spelling or add the city."), 400
        q("UPDATE calls SET lat = %s, lng = %s WHERE id = %s",
          (loc[0], loc[1], call_id))
        clat, clng = loc
    else:
        clat, clng = call["lat"], call["lng"]
    units = q("SELECT username, lat, lng FROM users"
              " WHERE role = 'unit' AND unit_status = 'on_duty'"
              " AND lat IS NOT NULL AND lng IS NOT NULL")
    recs = sorted(
        ({"username": u["username"],
          "miles": round(haversine_miles(clat, clng, u["lat"], u["lng"]), 1)}
         for u in units),
        key=lambda r: r["miles"])
    log_audit(me["username"], "units_recommended", "call %s" % call_id)
    return jsonify(ok=True, recommendations=recs)


@app.route("/call/<int:call_id>/notify", methods=["POST"])
@staff_required
def call_notify(call_id):
    me = current_user()
    _load_call(call_id)
    q("UPDATE calls SET notified_at = now() WHERE id = %s"
      " AND notified_at IS NULL", (call_id,))
    log_audit(me["username"], "call_sent_to_units", "call %s" % call_id)
    if request.is_json:
        return jsonify(ok=True)
    return redirect(url_for("call_view", call_id=call_id))


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
    _load_call(call_id)
    units = (request.form.get("units") or "").strip()
    q("UPDATE calls SET units = %s WHERE id = %s", (units, call_id))
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
    if me["role"] != "unit" or not types:
        return jsonify(active=[], recent=[],
                       me_status=me["unit_status"] or "off_duty")
    ph = ",".join(["%s"] * len(types))
    active = q("SELECT * FROM calls WHERE status IN ('open','dispatched')"
               " AND call_type IN (%s) AND notified_at IS NOT NULL"
               " ORDER BY id DESC" % ph, tuple(types))
    recent = q("SELECT * FROM calls WHERE status = 'closed'"
               " AND call_type IN (%s) AND notified_at IS NOT NULL"
               " ORDER BY closed_at DESC NULLS LAST, id DESC LIMIT 10" % ph,
               tuple(types))
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
                   me_status=me["unit_status"] or "off_duty")


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
@admin_required
def audit():
    me = current_user()
    rows = q("SELECT * FROM audit ORDER BY id DESC LIMIT 200")
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return render_template("audit.html", me=me, rows=rows)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
