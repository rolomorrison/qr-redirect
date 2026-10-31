"""RA Digital - redireccionador de placas NFC/QR con panel de administración.

Cada placa tiene un ID (RA001, RA002...). El chip NFC y el QR llevan
https://qr.<dominio>/RA001 . Este servicio cuenta el escaneo y redirige al
destino actual (por ejemplo el link de reseñas de Google del local).
"""
import csv
import hmac
import io
import os
import re
import secrets
import time
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_file, session, url_for)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("INSECURE_COOKIES") != "1",
    MAX_CONTENT_LENGTH=64 * 1024,
)
DB_PATH = os.environ.get("DB_PATH", "qr.db")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
HOME_REDIRECT = os.environ.get("HOME_REDIRECT", "https://wa.me/595991400530")
TAG_RE = re.compile(r"^RA\d{3,6}$", re.I)
PLATES = ["PVC", "Acrílico", "Mesa", "Pegatina", "Otro"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '',
  notes TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags(
  id TEXT PRIMARY KEY, client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
  dest_url TEXT DEFAULT '', plate TEXT DEFAULT 'PVC', active INTEGER NOT NULL DEFAULT 1,
  notes TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tag_id TEXT NOT NULL, ts TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', ua TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS scans_tag_ts ON scans(tag_id, ts);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
  day TEXT NOT NULL, items TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0,
  paid INTEGER NOT NULL DEFAULT 0, notes TEXT DEFAULT '');
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db():
    if "db" not in g:
        d = os.path.dirname(DB_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.executescript(SCHEMA)
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c:
        c.close()


# ---------- seguridad ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect():
    if request.endpoint in ("static", "go", "home", "health"):
        return
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not hmac.compare_digest(sent, session.get("csrf", "x")):
            abort(400, "Token inválido. Recargá la página.")
    if request.path.startswith("/admin") and request.endpoint != "login" and not session.get("auth"):
        return redirect(url_for("login"))


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/admin"):
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Frame-Options"] = "DENY"
    return r


# ---------- helpers ----------
def normalize_dest(v):
    """Acepta URL completa o un Place ID de Google (ChIJ...)."""
    v = (v or "").strip()
    if not v:
        return ""
    if re.match(r"^ChIJ[\w-]{10,}$", v):
        return f"https://search.google.com/local/writereview?placeid={v}"
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    try:
        u = urlparse(v)
        host, _ = u.hostname, u.port
    except ValueError:
        raise ValueError("Destino inválido")
    if u.scheme not in ("http", "https") or not host or not re.match(r"^[\w.-]+\.[a-z]{2,}$", host, re.I):
        raise ValueError("Destino inválido")
    return v


def tag_url(tag_id):
    return f"{BASE_URL or request.host_url.rstrip('/')}/{tag_id}"


# ---------- público ----------
@app.get("/")
def home():
    return redirect(HOME_REDIRECT, 302)


@app.get("/health")
def health():
    db().execute("SELECT 1")
    return "ok"


@app.get("/<tag_id>")
def go(tag_id):
    if not TAG_RE.match(tag_id):
        abort(404)
    tag_id = tag_id.upper()
    row = db().execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
    if not row:
        abort(404)
    src = request.args.get("s", "")[:1]  # n = NFC, q = QR
    db().execute("INSERT INTO scans(tag_id,ts,source,ua) VALUES(?,?,?,?)",
                 (tag_id, now(), src, (request.user_agent.string or "")[:200]))
    db().commit()
    if row["active"] and row["dest_url"]:
        resp = redirect(row["dest_url"], 302)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return render_template("inactive.html", tag_id=tag_id, home=HOME_REDIRECT), 200


# ---------- admin ----------
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            abort(503, "Falta ADMIN_PASSWORD")
        ok = hmac.compare_digest(request.form.get("user", ""), ADMIN_USER) & \
            hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD)
        if ok:
            session.clear()
            session["auth"] = True
            session.permanent = False
            return redirect(url_for("dashboard"))
        time.sleep(1)
        flash("Usuario o clave incorrectos.")
    return render_template("login.html")


@app.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/admin")
def dashboard():
    c = db()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    stats = {
        "tags": c.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
        "assigned": c.execute("SELECT COUNT(*) FROM tags WHERE client_id IS NOT NULL AND dest_url!=''").fetchone()[0],
        "scans_month": c.execute("SELECT COUNT(*) FROM scans WHERE ts LIKE ?", (month + "%",)).fetchone()[0],
        "pending": c.execute("SELECT COUNT(*) FROM orders WHERE paid=0").fetchone()[0],
    }
    recent = c.execute("""SELECT s.tag_id,s.ts,s.source,cl.name FROM scans s
        LEFT JOIN tags t ON t.id=s.tag_id LEFT JOIN clients cl ON cl.id=t.client_id
        ORDER BY s.id DESC LIMIT 10""").fetchall()
    return render_template("dashboard.html", stats=stats, recent=recent)


@app.get("/admin/tags")
def tags():
    q = request.args.get("estado", "")
    where = {"libres": "WHERE t.client_id IS NULL", "asignadas": "WHERE t.client_id IS NOT NULL"}.get(q, "")
    rows = db().execute(f"""SELECT t.*, cl.name AS client,
        (SELECT COUNT(*) FROM scans s WHERE s.tag_id=t.id) AS n,
        (SELECT MAX(ts) FROM scans s WHERE s.tag_id=t.id) AS last
        FROM tags t LEFT JOIN clients cl ON cl.id=t.client_id {where} ORDER BY t.id""").fetchall()
    return render_template("tags.html", rows=rows, q=q)


@app.post("/admin/tags/new")
def tags_new():
    try:
        start = int(request.form["start"]); count = int(request.form["count"])
    except (KeyError, ValueError):
        flash("Números inválidos."); return redirect(url_for("tags"))
    if not (1 <= count <= 500) or not (1 <= start <= 999999 - count):
        flash("Rango fuera de límite (máx. 500)."); return redirect(url_for("tags"))
    plate = request.form.get("plate", "PVC")
    plate = plate if plate in PLATES else "Otro"
    c = db(); made = 0
    for n in range(start, start + count):
        made += c.execute("INSERT OR IGNORE INTO tags(id,plate,created_at) VALUES(?,?,?)",
                          (f"RA{n:03d}", plate, now())).rowcount
    c.commit()
    flash(f"Creadas {made} placas.")
    return redirect(url_for("tags"))


@app.route("/admin/tags/<tag_id>", methods=["GET", "POST"])
def tag_edit(tag_id):
    c = db()
    t = c.execute("SELECT * FROM tags WHERE id=?", (tag_id.upper(),)).fetchone()
    if not t:
        abort(404)
    if request.method == "POST":
        try:
            dest = normalize_dest(request.form.get("dest_url"))
        except ValueError:
            flash("El destino no es un link válido."); return redirect(request.url)
        cid = request.form.get("client_id") or None
        plate = request.form.get("plate", "PVC")
        c.execute("UPDATE tags SET client_id=?,dest_url=?,plate=?,active=?,notes=? WHERE id=?",
                  (int(cid) if cid else None, dest, plate if plate in PLATES else "Otro",
                   1 if request.form.get("active") else 0, request.form.get("notes", "")[:500], t["id"]))
        c.commit(); flash("Guardado.")
        return redirect(url_for("tag_edit", tag_id=t["id"]))
    clients = c.execute("SELECT id,name FROM clients ORDER BY name").fetchall()
    scans = c.execute("SELECT ts,source FROM scans WHERE tag_id=? ORDER BY id DESC LIMIT 20", (t["id"],)).fetchall()
    total = c.execute("SELECT COUNT(*) FROM scans WHERE tag_id=?", (t["id"],)).fetchone()[0]
    return render_template("tag_edit.html", t=t, clients=clients, scans=scans, total=total,
                           link=tag_url(t["id"]), plates=PLATES)


@app.get("/admin/qr/<tag_id>.png")
def qr_png(tag_id):
    import qrcode
    if not TAG_RE.match(tag_id):
        abort(404)
    img = qrcode.make(tag_url(tag_id.upper()) + "?s=q", box_size=14, border=3,
                      error_correction=qrcode.constants.ERROR_CORRECT_Q)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"{tag_id.upper()}.png")


@app.route("/admin/clients", methods=["GET", "POST"])
def clients():
    c = db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        if name:
            c.execute("INSERT INTO clients(name,phone,notes,created_at) VALUES(?,?,?,?)",
                      (name, request.form.get("phone", "")[:40], request.form.get("notes", "")[:500], now()))
            c.commit(); flash("Cliente agregado.")
        return redirect(url_for("clients"))
    rows = c.execute("""SELECT cl.*, (SELECT COUNT(*) FROM tags t WHERE t.client_id=cl.id) AS ntags,
        (SELECT COALESCE(SUM(total),0) FROM orders o WHERE o.client_id=cl.id) AS vendido
        FROM clients cl ORDER BY cl.name""").fetchall()
    return render_template("clients.html", rows=rows)


@app.route("/admin/orders", methods=["GET", "POST"])
def orders():
    c = db()
    if request.method == "POST":
        items = request.form.get("items", "").strip()[:300]
        try:
            total = max(0, int(re.sub(r"\D", "", request.form.get("total", "0")) or 0))
        except ValueError:
            total = 0
        if items:
            cid = request.form.get("client_id") or None
            c.execute("INSERT INTO orders(client_id,day,items,total,paid,notes) VALUES(?,?,?,?,?,?)",
                      (int(cid) if cid else None, datetime.now().strftime("%Y-%m-%d"), items, total,
                       1 if request.form.get("paid") else 0, request.form.get("notes", "")[:300]))
            c.commit(); flash("Pedido registrado.")
        return redirect(url_for("orders"))
    rows = c.execute("""SELECT o.*, cl.name FROM orders o LEFT JOIN clients cl ON cl.id=o.client_id
        ORDER BY o.id DESC LIMIT 200""").fetchall()
    clients_ = c.execute("SELECT id,name FROM clients ORDER BY name").fetchall()
    return render_template("orders.html", rows=rows, clients=clients_)


@app.post("/admin/orders/<int:oid>/paid")
def order_paid(oid):
    db().execute("UPDATE orders SET paid=1-paid WHERE id=?", (oid,)); db().commit()
    return redirect(url_for("orders"))


@app.get("/admin/report")
def report():
    month = request.args.get("mes") or datetime.now(timezone.utc).strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", month):
        abort(400)
    rows = db().execute("""SELECT COALESCE(cl.name,'(sin asignar)') AS name, t.id AS tag_id, COUNT(s.id) AS n,
        SUM(s.source='n') AS nfc, SUM(s.source='q') AS qr
        FROM scans s JOIN tags t ON t.id=s.tag_id LEFT JOIN clients cl ON cl.id=t.client_id
        WHERE s.ts LIKE ? GROUP BY t.id ORDER BY name, t.id""", (month + "%",)).fetchall()
    return render_template("report.html", rows=rows, month=month)


@app.get("/admin/report.csv")
def report_csv():
    month = request.args.get("mes") or datetime.now(timezone.utc).strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", month):
        abort(400)
    rows = db().execute("""SELECT COALESCE(cl.name,''), t.id, COUNT(s.id)
        FROM scans s JOIN tags t ON t.id=s.tag_id LEFT JOIN clients cl ON cl.id=t.client_id
        WHERE s.ts LIKE ? GROUP BY t.id ORDER BY 1,2""", (month + "%",)).fetchall()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["cliente", "placa", "escaneos"]); w.writerows([tuple(r) for r in rows])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=escaneos-{month}.csv"})


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", 8000)))
