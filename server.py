"""
중동중학교 커뮤니티 서버 — 정적 파일 + SQLite + 이메일 인증 로그인
(app.py 에서 분리한 인증 API 사용)
"""
import base64
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import jwt
import requests
from flask import Flask, jsonify, redirect, request, send_from_directory
from flask_cors import CORS

try:
    import resend
except ImportError:
    resend = None

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "community.db"
AUTH_SESSION_FILE = ROOT / "auth_sessions.json"

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")
CORS(app, supports_credentials=True)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "jdms_community_secret")
JWT_SECRET = os.environ.get("JWT_SECRET", "jdms_community_jwt_secret")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:5000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://127.0.0.1:5000")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "corerepublix@gmail.com")
KST = timezone(timedelta(hours=9))

if RESEND_API_KEY and resend:
    resend.api_key = RESEND_API_KEY


# ── DB ──────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                author_email TEXT NOT NULL,
                author_name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'free',
                class_id TEXT,
                likes INTEGER NOT NULL DEFAULT 0,
                views INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (author_email) REFERENCES users(email)
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                author_email TEXT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
            """
        )


def row_to_post(row, comments=None):
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "content": row["content"],
        "author": row["author_name"],
        "authorEmail": row["author_email"],
        "category": row["category"],
        "classId": row["class_id"],
        "likes": row["likes"],
        "views": row["views"],
        "createdAt": row["created_at"],
        "comments": comments or [],
    }


# ── Auth sessions (JSON, app.py 와 동일 방식) ─────────────────
def load_auth_sessions():
    if AUTH_SESSION_FILE.exists():
        try:
            return json.loads(AUTH_SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_auth_sessions(sessions):
    AUTH_SESSION_FILE.write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def issue_access_token(email, name):
    payload = {
        "email": email,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_user_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.cookies.get("jdms_token") or ""
    if not token:
        return None
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {"email": data["email"], "name": data.get("name", data["email"].split("@")[0])}
    except Exception:
        return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_user_from_request()
        if not user:
            return jsonify({"error": "로그인이 필요합니다."}), 401
        request.jdms_user = user
        return fn(*args, **kwargs)

    return wrapper


# ── 인증 API (app.py 에서 이전) ───────────────────────────────
@app.route("/api/auth/send-link", methods=["POST"])
def send_auth_link():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    device_id = data.get("device_id")
    bypass = data.get("bypass", False)

    if not email:
        return jsonify({"success": False, "error": "이메일을 입력해 주세요."}), 400
    if not device_id:
        return jsonify({"success": False, "error": "device_id가 필요합니다."}), 400
    if not bypass and not email.endswith("@joongdong.ms.kr"):
        return jsonify({"success": False, "error": "학교 이메일(@joongdong.ms.kr)만 사용할 수 있습니다."}), 400

    try:
        payload = {
            "email": email,
            "device_id": device_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

        sessions = load_auth_sessions()
        sessions[device_id] = {
            "email": email,
            "status": "pending",
            "expires_at": (datetime.now(KST) + timedelta(minutes=10)).isoformat(),
        }
        save_auth_sessions(sessions)

        verify_link = f"{BACKEND_URL}/api/auth/verify?token={token}"
        email_html = f"""
        <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #eee;border-radius:10px;">
            <h2 style="color:#000;">JDMS 커뮤니티 로그인</h2>
            <p>아래 버튼을 눌러 로그인을 완료해 주세요.</p>
            <a href="{verify_link}" style="display:inline-block;padding:12px 24px;background:#000;color:#fff;text-decoration:none;border-radius:12px;font-weight:bold;margin:20px 0;">로그인 완료하기</a>
            <p style="font-size:12px;color:#888;">10분간 유효합니다.</p>
        </div>
        """

        sent = False
        if RESEND_API_KEY and resend:
            try:
                from_addr = FROM_EMAIL if "@" in FROM_EMAIL else "onboarding@resend.dev"
                resend.Emails.send(
                    {
                        "from": f"JDMS Community <{from_addr}>",
                        "to": [email],
                        "subject": "[JDMS 커뮤니티] 로그인 인증 링크",
                        "html": email_html,
                    }
                )
                sent = True
            except Exception:
                pass

        if not sent and SENDGRID_API_KEY:
            sg_data = {
                "personalizations": [
                    {"to": [{"email": email}], "subject": "[JDMS 커뮤니티] 로그인 인증 링크"}
                ],
                "from": {"email": FROM_EMAIL, "name": "JDMS Community"},
                "content": [{"type": "text/html", "value": email_html}],
            }
            requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=sg_data,
                timeout=15,
            )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/auth/poll")
def poll_auth():
    device_id = request.args.get("device_id")
    if not device_id:
        return jsonify({"status": "error"}), 400

    sessions = load_auth_sessions()
    session = sessions.get(device_id)
    if not session:
        return jsonify({"status": "not_found"})
    if session.get("status") == "completed":
        data = session.get("user_data", {})
        del sessions[device_id]
        save_auth_sessions(sessions)
        return jsonify({"status": "completed", "userData": data})
    return jsonify({"status": "pending"})


@app.route("/api/auth/verify")
def verify_token():
    token = request.args.get("token")
    if not token:
        return "인증 토큰이 없습니다.", 400
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        email = payload.get("email")
        device_id = payload.get("device_id")
        name = email.split("@")[0].upper() if email else "USER"

        sessions = load_auth_sessions()
        if device_id not in sessions:
            return "유효하지 않거나 만료된 세션입니다.", 403

        access = issue_access_token(email, name)
        user_data = {
            "email": email,
            "name": name,
            "token": access,
            "loggedInAt": datetime.now(timezone.utc).isoformat(),
        }

        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)",
                (email, name, datetime.now(timezone.utc).isoformat()),
            )

        sessions[device_id]["status"] = "completed"
        sessions[device_id]["user_data"] = user_data
        save_auth_sessions(sessions)

        encoded = base64.urlsafe_b64encode(json.dumps(user_data).encode()).decode()
        return redirect(f"{FRONTEND_URL}/login.html?auth_success=true&data={encoded}")
    except jwt.ExpiredSignatureError:
        return "링크가 만료되었습니다. (10분 경과)", 401
    except jwt.InvalidTokenError:
        return "유효하지 않은 인증 링크입니다.", 401


@app.route("/api/auth/me")
def auth_me():
    user = get_user_from_request()
    if not user:
        return jsonify({"loggedIn": False})
    return jsonify({"loggedIn": True, "user": user})


# ── 게시글 API ─────────────────────────────────────────────────
@app.route("/api/posts", methods=["GET"])
def list_posts():
    category = request.args.get("category")
    class_id = request.args.get("classId")
    limit = min(int(request.args.get("limit", 50)), 100)

    sql = "SELECT * FROM posts WHERE 1=1"
    params = []
    if class_id:
        sql += " AND class_id = ?"
        params.append(class_id)
    else:
        sql += " AND (class_id IS NULL OR class_id = '')"
    if category and category != "all":
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        posts = []
        for row in rows:
            comments = conn.execute(
                "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC",
                (row["id"],),
            ).fetchall()
            c_list = [
                {
                    "id": str(c["id"]),
                    "author": c["author_name"],
                    "content": c["content"],
                    "createdAt": c["created_at"],
                }
                for c in comments
            ]
            posts.append(row_to_post(row, c_list))
    return jsonify({"posts": posts})


@app.route("/api/posts/<int:post_id>", methods=["GET"])
def get_post(post_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not row:
            return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
        conn.execute("UPDATE posts SET views = views + 1 WHERE id = ?", (post_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        comments = conn.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC",
            (post_id,),
        ).fetchall()
        c_list = [
            {
                "id": str(c["id"]),
                "author": c["author_name"],
                "content": c["content"],
                "createdAt": c["created_at"],
            }
            for c in comments
        ]
    return jsonify({"post": row_to_post(row, c_list)})


@app.route("/api/posts", methods=["POST"])
@login_required
def create_post():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    category = data.get("category") or "free"
    class_id = data.get("classId") or None
    user = request.jdms_user

    if not title or not content:
        return jsonify({"error": "제목과 내용을 입력해 주세요."}), 400
    if class_id == "":
        class_id = None

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)",
            (user["email"], user["name"], now),
        )
        cur = conn.execute(
            """INSERT INTO posts (title, content, author_email, author_name, category, class_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, content, user["email"], user["name"], category, class_id, now),
        )
        post_id = cur.lastrowid
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return jsonify({"post": row_to_post(row, [])}), 201


@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
@login_required
def create_comment(post_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    user = request.jdms_user
    if not content:
        return jsonify({"error": "댓글 내용을 입력해 주세요."}), 400

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            return jsonify({"error": "게시글을 찾을 수 없습니다."}), 404
        cur = conn.execute(
            """INSERT INTO comments (post_id, author_email, author_name, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (post_id, user["email"], user["name"], content, now),
        )
        c = conn.execute("SELECT * FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(
        {
            "comment": {
                "id": str(c["id"]),
                "author": c["author_name"],
                "content": c["content"],
                "createdAt": c["created_at"],
            }
        }
    ), 201


# ── 정적 파일 ─────────────────────────────────────────────────
@app.route("/")
def serve_index():
    return send_from_directory(ROOT, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    target = ROOT / path
    if target.is_file():
        return send_from_directory(ROOT, path)
    if (ROOT / (path + ".html")).is_file():
        return send_from_directory(ROOT, path + ".html")
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
