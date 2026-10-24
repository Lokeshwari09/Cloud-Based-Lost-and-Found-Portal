"""
Cloud-Based Lost and Found Portal
---------------------------------
Flask + SQLite web application.

Features
  * Report LOST or FOUND items (with optional photo)
  * Search / filter the public item board
  * Automatic "possible match" suggestions between lost and found items
  * Claim workflow (claimant submits proof -> admin approves/rejects -> item returned)
  * Admin dashboard with statistics
  * REST API (/api/items, /api/stats) and /health endpoint (for load balancers)
  * Optional cloud storage: set S3_BUCKET to store photos in AWS S3 instead of local disk

Run locally:  python app.py   ->  http://localhost:5000
"""
import os
import re
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "portal.db")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
CATEGORIES = ["Electronics", "Documents / ID", "Wallet / Purse", "Keys", "Bags",
              "Clothing", "Books / Stationery", "Jewellery", "Pets", "Other"]

S3_BUCKET = os.environ.get("S3_BUCKET")          # optional AWS S3 bucket
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024   # 5 MB uploads
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS items (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        kind          TEXT NOT NULL CHECK (kind IN ('lost','found')),
        title         TEXT NOT NULL,
        category      TEXT NOT NULL,
        description   TEXT,
        location      TEXT NOT NULL,
        event_date    TEXT NOT NULL,
        contact_name  TEXT NOT NULL,
        contact_email TEXT NOT NULL,
        image         TEXT,
        status        TEXT NOT NULL DEFAULT 'open',   -- open | claim_pending | returned
        created_at    TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS claims (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id        INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        claimant_name  TEXT NOT NULL,
        claimant_email TEXT NOT NULL,
        proof          TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
        created_at     TEXT NOT NULL
    );
    """)
    count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if count == 0:
        seed = [
            ("lost", "Black leather wallet", "Wallet / Purse", "Black bifold wallet with college ID and some cards", "Main Library", "2026-10-20", "Arun K", "arun@example.com"),
            ("found", "Black wallet near library", "Wallet / Purse", "Found a black leather wallet with an ID card on the steps", "Library entrance", "2026-10-21", "Meena S", "meena@example.com"),
            ("lost", "Blue Casio calculator", "Electronics", "Blue scientific calculator, name written on back", "Block C Room 204", "2026-10-19", "Rahul V", "rahul@example.com"),
            ("found", "Set of keys with red tag", "Keys", "Three keys on a ring with a red tag", "Canteen", "2026-10-22", "Divya R", "divya@example.com"),
            ("lost", "Grey backpack", "Bags", "Grey backpack with laptop charger and notebooks", "Bus stand", "2026-10-22", "Karthik M", "karthik@example.com"),
        ]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for s in seed:
            db.execute("""INSERT INTO items(kind,title,category,description,location,event_date,
                          contact_name,contact_email,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                       (*s, now))
    db.commit()
    db.close()


# --------------------------------------------------------------------------
# Image storage (local disk by default, AWS S3 if S3_BUCKET is set)
# --------------------------------------------------------------------------
def save_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Only png, jpg, jpeg, gif or webp images are allowed.")
    name = f"{uuid.uuid4().hex}_{secure_filename(file_storage.filename)}"
    if S3_BUCKET:
        import boto3  # imported lazily so local runs don't need AWS credentials
        boto3.client("s3", region_name=AWS_REGION).upload_fileobj(
            file_storage, S3_BUCKET, f"uploads/{name}",
            ExtraArgs={"ContentType": file_storage.mimetype})
    else:
        file_storage.save(os.path.join(UPLOAD_DIR, name))
    return name


@app.template_global()
def image_url(name):
    if not name:
        return None
    if S3_BUCKET:
        import boto3
        return boto3.client("s3", region_name=AWS_REGION).generate_presigned_url(
            "get_object", Params={"Bucket": S3_BUCKET, "Key": f"uploads/{name}"},
            ExpiresIn=3600)
    return url_for("uploaded_file", filename=name)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# --------------------------------------------------------------------------
# Matching engine: suggests found items for a lost item (and vice-versa)
# --------------------------------------------------------------------------
STOP = {"the", "a", "an", "with", "and", "of", "in", "on", "at", "near", "to",
        "is", "was", "my", "found", "lost", "has", "have", "some", "for"}


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOP and len(w) > 2}


def similarity(a, b):
    ta = tokens(f"{a['title']} {a['title']} {a['description']} {a['location']}")
    tb = tokens(f"{b['title']} {b['title']} {b['description']} {b['location']}")
    if not ta or not tb:
        return 0.0
    score = len(ta & tb) / len(ta | tb)
    if a["category"] == b["category"]:
        score += 0.25
    return round(min(score, 1.0), 2)


def find_matches(item, limit=5):
    opposite = "found" if item["kind"] == "lost" else "lost"
    rows = get_db().execute(
        "SELECT * FROM items WHERE kind=? AND status!='returned' AND id!=?",
        (opposite, item["id"])).fetchall()
    scored = [(similarity(item, r), r) for r in rows]
    return [(s, r) for s, r in sorted(scored, key=lambda x: -x[0]) if s >= 0.30][:limit]


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "")
    category = request.args.get("category", "")
    status = request.args.get("status", "")
    sql, params = "SELECT * FROM items WHERE 1=1", []
    if q:
        sql += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"
        params += [f"%{q}%"] * 3
    if kind in ("lost", "found"):
        sql += " AND kind=?"
        params.append(kind)
    if category:
        sql += " AND category=?"
        params.append(category)
    if status in ("open", "claim_pending", "returned"):
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id DESC"
    items = get_db().execute(sql, params).fetchall()
    return render_template("index.html", items=items, categories=CATEGORIES,
                           q=q, kind=kind, category=category, status=status)


@app.route("/report", methods=["GET", "POST"])
def report():
    if request.method == "POST":
        f = request.form
        required = ["kind", "title", "category", "location", "event_date",
                    "contact_name", "contact_email"]
        if any(not f.get(k, "").strip() for k in required) or f["kind"] not in ("lost", "found"):
            flash("Please fill in all required fields.", "error")
            return render_template("report.html", categories=CATEGORIES, form=f), 400
        try:
            image = save_image(request.files.get("image"))
        except ValueError as e:
            flash(str(e), "error")
            return render_template("report.html", categories=CATEGORIES, form=f), 400
        db = get_db()
        cur = db.execute(
            """INSERT INTO items(kind,title,category,description,location,event_date,
               contact_name,contact_email,image,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f["kind"], f["title"].strip(), f["category"], f.get("description", "").strip(),
             f["location"].strip(), f["event_date"], f["contact_name"].strip(),
             f["contact_email"].strip(), image, datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
        flash("Your report has been submitted. Check below for possible matches.", "ok")
        return redirect(url_for("detail", item_id=cur.lastrowid))
    return render_template("report.html", categories=CATEGORIES, form={})


@app.route("/item/<int:item_id>")
def detail(item_id):
    item = get_db().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return render_template("404.html"), 404
    return render_template("detail.html", item=item, matches=find_matches(item))


@app.route("/item/<int:item_id>/claim", methods=["POST"])
def claim(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item or item["status"] == "returned":
        flash("This item is no longer available for claims.", "error")
        return redirect(url_for("index"))
    f = request.form
    if not all(f.get(k, "").strip() for k in ("claimant_name", "claimant_email", "proof")):
        flash("Name, email and proof of ownership are required.", "error")
        return redirect(url_for("detail", item_id=item_id))
    db.execute("""INSERT INTO claims(item_id,claimant_name,claimant_email,proof,created_at)
                  VALUES (?,?,?,?,?)""",
               (item_id, f["claimant_name"].strip(), f["claimant_email"].strip(),
                f["proof"].strip(), datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.execute("UPDATE items SET status='claim_pending' WHERE id=?", (item_id,))
    db.commit()
    flash("Claim submitted. The admin will verify your proof and contact you.", "ok")
    return redirect(url_for("detail", item_id=item_id))


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Wrong password.", "error")
    return render_template("login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    claims = db.execute("""SELECT c.*, i.title AS item_title, i.kind AS item_kind
                           FROM claims c JOIN items i ON i.id=c.item_id
                           ORDER BY (c.status='pending') DESC, c.id DESC""").fetchall()
    return render_template("admin.html", claims=claims, stats=compute_stats())


@app.route("/admin/claim/<int:claim_id>/<action>", methods=["POST"])
@admin_required
def admin_claim(claim_id, action):
    db = get_db()
    c = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not c or action not in ("approve", "reject"):
        flash("Invalid request.", "error")
        return redirect(url_for("admin"))
    if action == "approve":
        db.execute("UPDATE claims SET status='approved' WHERE id=?", (claim_id,))
        db.execute("UPDATE items SET status='returned' WHERE id=?", (c["item_id"],))
        flash("Claim approved - item marked as returned.", "ok")
    else:
        db.execute("UPDATE claims SET status='rejected' WHERE id=?", (claim_id,))
        pending = db.execute("SELECT COUNT(*) FROM claims WHERE item_id=? AND status='pending'",
                             (c["item_id"],)).fetchone()[0]
        if pending == 0:
            db.execute("UPDATE items SET status='open' WHERE id=? AND status!='returned'",
                       (c["item_id"],))
        flash("Claim rejected.", "ok")
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/item/<int:item_id>/delete", methods=["POST"])
@admin_required
def admin_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM claims WHERE item_id=?", (item_id,))
    db.execute("DELETE FROM items WHERE id=?", (item_id,))
    db.commit()
    flash("Item deleted.", "ok")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# REST API + health check
# --------------------------------------------------------------------------
def compute_stats():
    db = get_db()
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]
    total = one("SELECT COUNT(*) FROM items")
    returned = one("SELECT COUNT(*) FROM items WHERE status='returned'")
    return {
        "total_items": total,
        "lost": one("SELECT COUNT(*) FROM items WHERE kind='lost'"),
        "found": one("SELECT COUNT(*) FROM items WHERE kind='found'"),
        "returned": returned,
        "pending_claims": one("SELECT COUNT(*) FROM claims WHERE status='pending'"),
        "recovery_rate_percent": round(100 * returned / total, 1) if total else 0.0,
        "storage": "aws-s3" if S3_BUCKET else "local-disk",
    }


@app.route("/api/items")
def api_items():
    rows = get_db().execute("SELECT * FROM items ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("contact_email")          # never expose personal data through the public API
        d.pop("contact_name")
        out.append(d)
    return jsonify(out)


@app.route("/api/stats")
def api_stats():
    return jsonify(compute_stats())


@app.route("/health")
def health():
    try:
        get_db().execute("SELECT 1")
        return jsonify(status="ok", time=datetime.now().isoformat())
    except Exception as e:  # pragma: no cover
        return jsonify(status="error", detail=str(e)), 500


@app.errorhandler(413)
def too_large(_e):
    flash("Image too large (max 5 MB).", "error")
    return redirect(url_for("report"))


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG", "1") == "1")
