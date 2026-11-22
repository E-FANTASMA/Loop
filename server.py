import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.environ.get("LOOP_DB_PATH", ROOT / "data" / "loop.sqlite3"))
UPLOADS_DIR = Path(os.environ.get("LOOP_UPLOADS_DIR", ROOT / "data" / "uploads"))
LOOP_BACKEND = os.environ.get("LOOP_BACKEND", "sqlite").strip().lower()
if LOOP_BACKEND not in {"sqlite", "supabase"}:
    raise RuntimeError("LOOP_BACKEND must be either 'sqlite' or 'supabase'.")
SUPABASE_MODE = LOOP_BACKEND == "supabase"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = (
    os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY", "")
)
SUPABASE_SECRET_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
)
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL", "")
SUPABASE_IMAGE_BUCKET = "loop-listing-images"
CATEGORIES = {"Furniture", "Books", "Tech", "Kitchen", "Other"}
CONDITIONS = {"Like new", "Good", "Fair", "Any"}
LISTING_TYPES = {"sale", "swap", "request"}
MAX_BODY_SIZE = 4_500_000
SESSION_DAYS = 7
PASSWORD_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 64}

SEED_LISTINGS = [
    ("Oak folding desk", "Compact desk in good condition.", "Furniture", 0, "Good", "West Hall", "▰"),
    ("Calculus study guides", "Study guides with clean, readable pages.", "Books", 1500, "Like new", "Library", "▤"),
    ("Wireless headphones", "Working headphones; lightly used.", "Tech", 5000, "Good", "Arts Centre", "◖"),
    ("Two-person kettle", "Small kettle, ideal for a shared room.", "Kitchen", 0, "Good", "Riverside", "◒"),
    ("Floor desk lamp", "Warm light, works well for study.", "Furniture", 1000, "Good", "North Hall", "⌕"),
    ("Python reference set", "Two introductory programming books.", "Books", 0, "Good", "Science Block", "▥"),
    ("Compact printer", "Compact printer in working condition.", "Tech", 2500, "Fair", "East Court", "▣"),
    ("Ceramic mug and bowl", "Set of two ceramic pieces.", "Kitchen", 500, "Like new", "Garden Flats", "◡"),
]


@contextmanager
def connect_db():
    if SUPABASE_MODE:
        connection = psycopg.connect(
            SUPABASE_DB_URL,
            connect_timeout=10,
            row_factory=postgres_row,
        )
        try:
            yield PostgresConnection(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, values=()):
        query = query.replace(" LIKE ?", " ILIKE ?").replace("?", "%s")
        return self.connection.execute(query, values)


def postgres_row(cursor):
    columns = [column.name for column in cursor.description]

    def make_row(values):
        return {
            name: str(value) if isinstance(value, uuid.UUID) else value
            for name, value in zip(columns, values)
        }

    return make_row


class SupabaseError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def supabase_request(
    path, method="GET", payload=None, access_token=None, service_role=False,
    raw_body=None, content_type="application/json",
):
    key = SUPABASE_SECRET_KEY if service_role else SUPABASE_PUBLISHABLE_KEY
    headers = {"apikey": key, "Accept": "application/json"}
    authorization = access_token or (key if key.startswith("eyJ") else None)
    if authorization:
        headers["Authorization"] = f"Bearer {authorization}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type
    elif raw_body is not None:
        data = raw_body
        headers["Content-Type"] = content_type
    request = Request(f"{SUPABASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read()
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            details = {}
        message = (
            details.get("msg") or details.get("message") or details.get("error_description")
            if isinstance(details, dict) else None
        )
        raise SupabaseError(error.code, message or "Supabase rejected the request.") from error
    except URLError as error:
        raise SupabaseError(503, "Could not reach Supabase. Check the project URL and network.") from error
    except TimeoutError as error:
        raise SupabaseError(503, "Supabase did not respond before the request timed out.") from error
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupabaseError(502, "Supabase returned an unreadable response.") from error


def supabase_image_url(name):
    path = quote(name, safe="/")
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_IMAGE_BUCKET}/{path}"


def add_column_if_missing(db, table, column, definition):
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_db():
    if SUPABASE_MODE:
        required = {
            "SUPABASE_URL": SUPABASE_URL,
            "SUPABASE_PUBLISHABLE_KEY": SUPABASE_PUBLISHABLE_KEY,
            "SUPABASE_SECRET_KEY": SUPABASE_SECRET_KEY,
            "SUPABASE_DB_URL": SUPABASE_DB_URL,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Supabase configuration: {', '.join(missing)}.")
        try:
            with connect_db() as db:
                for table in ("profiles", "listings", "messages", "orders", "reports"):
                    db.execute(f"SELECT 1 FROM public.{table} LIMIT 0")
        except psycopg.errors.UndefinedTable as error:
            raise RuntimeError("Run supabase_schema.sql in the Supabase SQL Editor first.") from error
        return
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                price INTEGER NOT NULL CHECK (price >= 0),
                condition TEXT NOT NULL,
                location TEXT NOT NULL,
                seller_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '◇',
                created_at TEXT NOT NULL
            )
        """)
        add_column_if_missing(db, "listings", "owner_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL")
        add_column_if_missing(db, "listings", "image_name", "TEXT")
        add_column_if_missing(db, "listings", "status", "TEXT NOT NULL DEFAULT 'active'")
        add_column_if_missing(db, "listings", "listing_type", "TEXT NOT NULL DEFAULT 'sale'")
        add_column_if_missing(db, "listings", "swap_for", "TEXT NOT NULL DEFAULT ''")
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
                buyer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                amount INTEGER NOT NULL CHECK (amount >= 0),
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                reason TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reviewed_at TEXT,
                UNIQUE (listing_id, reporter_id)
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        seeded = db.execute("SELECT value FROM app_meta WHERE key = 'demo_seeded'").fetchone()
        if not seeded:
            now = utc_now()
            db.executemany("""
                INSERT INTO listings
                (title, description, category, price, condition, location, seller_name, phone, icon, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'Loop Demo', '', ?, ?)
            """, [(*row, now) for row in SEED_LISTINGS])
            db.execute("INSERT INTO app_meta (key, value) VALUES ('demo_seeded', 'true')")
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def clean_text(value, field, required=True, maximum=600):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > maximum:
        raise ValueError(f"{field} must be {maximum} characters or fewer.")
    return value


def validate_email(value):
    email = clean_text(value, "Email", maximum=254).lower()
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+", email):
        raise ValueError("Enter a valid email address.")
    if not email.endswith("@pau.edu.ng"):
        raise ValueError("Only email addresses ending in @pau.edu.ng are allowed.")
    return email


def validate_password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise ValueError("Password must be 12 to 128 characters.")
    return value


def hash_password(password, salt):
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        **PASSWORD_SCRYPT,
    ).hex()


def validate_listing(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    title = clean_text(payload.get("title"), "Item name", maximum=100)
    description = clean_text(payload.get("description", ""), "Description", required=False)
    category = clean_text(payload.get("category"), "Category", maximum=20)
    condition = clean_text(payload.get("condition"), "Condition", maximum=20)
    location = clean_text(payload.get("location"), "Pickup location", maximum=100)
    listing_type = clean_text(payload.get("listing_type", "sale"), "Listing type", maximum=20)
    if listing_type not in LISTING_TYPES:
        raise ValueError("Choose sale, swap, or request.")
    swap_for = clean_text(
        payload.get("swap_for", ""),
        "Desired swap item",
        required=listing_type == "swap",
        maximum=200,
    )
    try:
        price = int(payload.get("price", 0))
    except (TypeError, ValueError):
        raise ValueError("Price must be a whole number in naira.")
    if listing_type != "sale" and price != 0:
        raise ValueError("Only sale listings can have a price.")
    if price < 0 or price > 100_000_000:
        raise ValueError("Price must be between ₦0 and ₦100,000,000.")
    if category not in CATEGORIES:
        raise ValueError("Choose a valid category.")
    if condition not in CONDITIONS:
        raise ValueError("Choose a valid condition.")
    if condition == "Any" and listing_type != "request":
        raise ValueError("Any condition is only valid for item requests.")
    image = payload.get("image")
    image_bytes, image_ext = None, None
    if image:
        if not isinstance(image, str) or len(image) > 4_200_000:
            raise ValueError("Image must be a JPEG, PNG, or WebP up to 3 MB.")
        match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", image)
        if not match:
            raise ValueError("Image must be a JPEG, PNG, or WebP.")
        try:
            image_bytes = base64.b64decode(match.group(2), validate=True)
        except ValueError:
            raise ValueError("The uploaded image is invalid.")
        if len(image_bytes) > 3 * 1024 * 1024:
            raise ValueError("Image must be 3 MB or smaller.")
        image_ext = {"jpeg": "jpg", "png": "png", "webp": "webp"}[match.group(1)]
        valid_signature = (
            (image_ext == "jpg" and image_bytes.startswith(b"\xff\xd8\xff"))
            or (image_ext == "png" and image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            or (image_ext == "webp" and len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP")
        )
        if not valid_signature:
            raise ValueError("Image content does not match its file type.")
    return {
        "title": title, "description": description, "category": category,
        "price": price, "condition": condition, "location": location,
        "listing_type": listing_type, "swap_for": swap_for,
        "image_bytes": image_bytes, "image_ext": image_ext,
    }


def public_user(row):
    if row is None:
        return None
    admin_email = os.environ.get("LOOP_ADMIN_EMAIL", "").strip().casefold()
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_admin": bool(admin_email and row["email"].casefold() == admin_email),
    }


def public_listing(row, user_id=None):
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "price": row["price"],
        "listing_type": row["listing_type"],
        "swap_for": row["swap_for"],
        "condition": row["condition"],
        "location": row["location"],
        "seller_name": row["seller_name"],
        "owner_id": row["owner_id"],
        "is_owner": user_id is not None and row["owner_id"] == user_id,
        "status": row["status"],
        "image_url": (
            supabase_image_url(row["image_name"])
            if SUPABASE_MODE and row["image_name"]
            else f"/uploads/{row['image_name']}" if row["image_name"] else None
        ),
        "created_at": row["created_at"],
    }


class LoopHandler(SimpleHTTPRequestHandler):
    directory = str(ROOT)
    server_version = "Loop"
    sys_version = ""

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        image_origin = ""
        if SUPABASE_MODE:
            supabase_origin = urlparse(SUPABASE_URL)
            image_origin = f" {supabase_origin.scheme}://{supabase_origin.netloc}"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; img-src 'self' data:{image_origin}; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        super().end_headers()

    def send_json(self, data, status=200, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in [*(extra_headers or []), *getattr(self, "pending_headers", [])]:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request length.")
        if length < 1 or length > MAX_BODY_SIZE:
            raise ValueError("Request body is empty or too large.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body must contain valid JSON.")

    def handle_api_errors(self, handler):
        try:
            handler()
        except SupabaseError as error:
            self.send_json({"error": error.message}, error.status)
        except (psycopg.Error, sqlite3.Error) as error:
            print(f"Loop database request failed ({type(error).__name__}).")
            self.send_json({"error": "The database request failed. Check the server configuration and try again."}, 503)

    def read_cookie(self, name):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        return cookie[name].value if name in cookie else None

    def session(self):
        if SUPABASE_MODE:
            access_token = self.read_cookie("loop_access")
            refresh_token = self.read_cookie("loop_refresh")
            if not access_token:
                return None
            try:
                auth_user = supabase_request("/auth/v1/user", access_token=access_token)
            except SupabaseError as error:
                if error.status not in {401, 403}:
                    raise
                if not refresh_token:
                    self.expire_supabase_cookies()
                    return None
                try:
                    tokens = supabase_request(
                        "/auth/v1/token?grant_type=refresh_token",
                        method="POST",
                        payload={"refresh_token": refresh_token},
                    )
                except SupabaseError as refresh_error:
                    if refresh_error.status not in {400, 401, 403}:
                        raise
                    self.expire_supabase_cookies()
                    return None
                self.set_supabase_session(tokens)
                auth_user = tokens.get("user")
                access_token = tokens.get("access_token")
                if not auth_user or not access_token:
                    return None
            user_id = auth_user.get("id")
            if not user_id:
                return None
            with connect_db() as db:
                profile = db.execute(
                    "SELECT id, email, display_name FROM profiles WHERE id = ?", (user_id,)
                ).fetchone()
            if not profile:
                raise RuntimeError("Supabase Auth user has no matching public profile.")
            csrf_token = self.read_cookie("loop_csrf")
            if not csrf_token:
                csrf_token = secrets.token_urlsafe(32)
                self.queue_cookie("loop_csrf", csrf_token, http_only=False, max_age=SESSION_DAYS * 86400)
            self.supabase_access_token = access_token
            return {
                "id": str(profile["id"]),
                "email": profile["email"],
                "display_name": profile["display_name"],
                "csrf_token": csrf_token,
            }
        token = self.read_cookie("loop_session")
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with connect_db() as db:
            row = db.execute("""
                SELECT sessions.csrf_token, users.id, users.email, users.display_name
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """, (token_hash, utc_now())).fetchone()
        return dict(row) if row else None

    def reject_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.casefold() != self.headers.get("Host", "").casefold():
            self.send_json({"error": "Cross-origin requests are not allowed."}, 403)
            return True
        return False

    def require_user(self):
        session = self.session()
        if not session:
            self.send_json({"error": "Sign in to continue."}, 401)
            return None
        if self.command not in {"GET", "HEAD", "OPTIONS"} and (
            self.headers.get("X-Loop-CSRF") != session["csrf_token"]
            or SUPABASE_MODE and self.read_cookie("loop_csrf") != session["csrf_token"]
        ):
            self.send_json({"error": "The session token is missing or expired. Refresh and try again."}, 403)
            return None
        return session

    def require_admin(self):
        user = self.require_user()
        if not user:
            return None
        admin_email = os.environ.get("LOOP_ADMIN_EMAIL", "").strip().casefold()
        if not admin_email or user["email"].casefold() != admin_email:
            self.send_json({"error": "Admin access is not enabled for this account."}, 403)
            return None
        return user

    def queue_cookie(self, name, value, max_age, http_only=True):
        cookie = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        if not http_only:
            cookie = cookie.replace("; HttpOnly", "")
        if os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true":
            cookie += "; Secure"
        self.pending_headers = getattr(self, "pending_headers", [])
        self.pending_headers.append(("Set-Cookie", cookie))

    def set_supabase_session(self, tokens):
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token or not refresh_token:
            raise SupabaseError(502, "Supabase did not return a complete login session.")
        csrf = self.read_cookie("loop_csrf") or secrets.token_urlsafe(32)
        self.queue_cookie("loop_access", access_token, int(tokens.get("expires_in", 3600)))
        self.queue_cookie("loop_refresh", refresh_token, 60 * 60 * 24 * 30)
        self.queue_cookie("loop_csrf", csrf, 60 * 60 * 24 * 30, http_only=False)
        self.supabase_access_token = access_token
        return csrf

    def expire_supabase_cookies(self):
        for name in ("loop_access", "loop_refresh", "loop_csrf"):
            self.queue_cookie(name, "", 0, http_only=name != "loop_csrf")

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        with connect_db() as db:
            db.execute("INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                       (token_hash, user_id, csrf, expires))
        cookie = f"loop_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS * 86400}"
        if os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true":
            cookie += "; Secure"
        return csrf, ("Set-Cookie", cookie)

    def clear_session(self):
        if SUPABASE_MODE:
            access_token = getattr(self, "supabase_access_token", None)
            if access_token:
                try:
                    supabase_request("/auth/v1/logout", method="POST", access_token=access_token)
                except SupabaseError as error:
                    if error.status not in {401, 403}:
                        raise
            for name in ("loop_access", "loop_refresh", "loop_csrf"):
                self.queue_cookie(name, "", 0, http_only=name != "loop_csrf")
            return
        token = self.read_cookie("loop_session")
        if token:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with connect_db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        cookie = "loop_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        if os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true":
            cookie += "; Secure"
        return ("Set-Cookie", cookie)

    def save_image(self, image_bytes, extension):
        name = f"{uuid.uuid4().hex}.{extension}"
        if SUPABASE_MODE:
            content_type = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension]
            path = quote(f"{SUPABASE_IMAGE_BUCKET}/{name}", safe="/")
            supabase_request(
                f"/storage/v1/object/{path}",
                method="POST",
                service_role=True,
                raw_body=image_bytes,
                content_type=content_type,
            )
            return name
        path = UPLOADS_DIR / name
        path.write_bytes(image_bytes)
        return name

    def remove_image(self, name):
        if name:
            if SUPABASE_MODE:
                supabase_request(
                    f"/storage/v1/object/{SUPABASE_IMAGE_BUCKET}",
                    method="DELETE",
                    payload={"prefixes": [name]},
                    service_role=True,
                )
                return
            path = UPLOADS_DIR / name
            if path.parent.resolve() == UPLOADS_DIR.resolve() and path.exists():
                path.unlink()

    def do_GET(self):
        self.handle_api_errors(self._do_GET)

    def _do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/auth/me":
            session = self.session()
            self.send_json({"user": public_user(session), "csrf_token": session["csrf_token"] if session else None})
            return
        if path == "/api/stats":
            with connect_db() as db:
                total = db.execute(
                    "SELECT COUNT(*) AS total FROM listings WHERE status = 'active'"
                ).fetchone()["total"]
            self.send_json({"active_listings": total})
            return
        if path == "/api/orders":
            user = self.require_user()
            if not user:
                return
            self.do_GET_orders(user)
            return
        if path == "/api/admin/reports":
            user = self.require_admin()
            if not user:
                return
            profile_table = "profiles" if SUPABASE_MODE else "users"
            with connect_db() as db:
                rows = db.execute(f"""
                    SELECT reports.id, reports.listing_id, reports.reason, reports.details,
                           reports.created_at, listings.title, listings.description,
                           listings.seller_name, listings.status AS listing_status,
                           reporters.display_name AS reporter_name
                    FROM reports
                    JOIN listings ON listings.id = reports.listing_id
                    JOIN {profile_table} reporters ON reporters.id = reports.reporter_id
                    WHERE reports.status = 'open'
                    ORDER BY reports.id ASC
                """).fetchall()
            self.send_json({"reports": [dict(row) for row in rows]})
            return
        if path == "/api/listings":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0].strip()
            category = params.get("category", ["All"])[0]
            listing_type = params.get("type", ["All"])[0]
            sort = params.get("sort", ["newest"])[0]
            mine = params.get("mine", [""])[0] == "1"
            user = self.session()
            if mine and not user:
                self.send_json({"error": "Sign in to view your listings."}, 401)
                return
            order = {
                "newest": "created_at DESC, id DESC",
                "price_asc": "price ASC, created_at DESC",
                "price_desc": "price DESC, created_at DESC",
            }.get(sort, "created_at DESC, id DESC")
            clauses, values = [], []
            if not mine:
                clauses.append("status = 'active'")
            if category in CATEGORIES:
                clauses.append("category = ?")
                values.append(category)
            if listing_type in LISTING_TYPES:
                clauses.append("listing_type = ?")
                values.append(listing_type)
            if query:
                clauses.append("(title LIKE ? OR description LIKE ? OR location LIKE ? OR seller_name LIKE ?)")
                values.extend([f"%{query}%"] * 4)
            if mine:
                clauses.append("owner_id = ?")
                values.append(user["id"])
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            with connect_db() as db:
                rows = db.execute(f"SELECT * FROM listings {where} ORDER BY {order}", values).fetchall()
            self.send_json({"listings": [public_listing(row, user["id"] if user else None) for row in rows]})
            return
        if path == "/api/conversations":
            user = self.require_user()
            if not user:
                return
            profile_table = "profiles" if SUPABASE_MODE else "users"
            with connect_db() as db:
                rows = db.execute(f"""
                    SELECT m.listing_id,
                           CASE WHEN m.sender_id = ? THEN m.recipient_id ELSE m.sender_id END AS other_id,
                           l.title AS listing_title,
                           u.display_name AS other_name,
                           MAX(m.id) AS last_id,
                           SUM(CASE WHEN m.recipient_id = ? AND m.read_at IS NULL THEN 1 ELSE 0 END) AS unread
                    FROM messages m
                    JOIN listings l ON l.id = m.listing_id
                    JOIN {profile_table} u ON u.id = CASE WHEN m.sender_id = ? THEN m.recipient_id ELSE m.sender_id END
                    WHERE m.sender_id = ? OR m.recipient_id = ?
                    GROUP BY m.listing_id, other_id
                    ORDER BY last_id DESC
                """, (user["id"], user["id"], user["id"], user["id"], user["id"])).fetchall()
                conversations = []
                for row in rows:
                    last = db.execute("SELECT body, created_at FROM messages WHERE id = ?", (row["last_id"],)).fetchone()
                    conversations.append({
                        "listing_id": row["listing_id"], "other_id": row["other_id"],
                        "listing_title": row["listing_title"], "other_name": row["other_name"],
                        "last_message": last["body"], "updated_at": last["created_at"],
                        "unread": row["unread"],
                    })
            self.send_json({"conversations": conversations})
            return
        match = re.fullmatch(r"/api/conversations/(\d+)/([0-9A-Fa-f-]+)/messages", path)
        if match:
            user = self.require_user()
            if not user:
                return
            listing_id = int(match.group(1))
            other_id = match.group(2) if SUPABASE_MODE else int(match.group(2))
            profile_table = "profiles" if SUPABASE_MODE else "users"
            with connect_db() as db:
                exists = db.execute("""
                    SELECT 1 FROM messages WHERE listing_id = ? AND
                    ((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?))
                    LIMIT 1
                """, (listing_id, user["id"], other_id, other_id, user["id"])).fetchone()
                listing = db.execute("SELECT owner_id FROM listings WHERE id = ?", (listing_id,)).fetchone()
                if not listing or (not exists and listing["owner_id"] != other_id):
                    self.send_json({"error": "Conversation not found."}, 404)
                    return
                rows = db.execute(f"""
                    SELECT m.id, m.sender_id, m.recipient_id, m.body, m.created_at,
                           u.display_name AS sender_name
                    FROM messages m JOIN {profile_table} u ON u.id = m.sender_id
                    WHERE m.listing_id = ? AND
                    ((m.sender_id = ? AND m.recipient_id = ?) OR (m.sender_id = ? AND m.recipient_id = ?))
                    ORDER BY m.id
                """, (listing_id, user["id"], other_id, other_id, user["id"])).fetchall()
                db.execute("""
                    UPDATE messages SET read_at = ?
                    WHERE listing_id = ? AND sender_id = ? AND recipient_id = ? AND read_at IS NULL
                """, (utc_now(), listing_id, other_id, user["id"]))
            self.send_json({"messages": [dict(row) for row in rows]})
            return
        image_match = re.fullmatch(r"/uploads/([a-f0-9]{32}\.(?:jpg|png|webp))", path)
        if image_match:
            image_path = UPLOADS_DIR / image_match.group(1)
            if not image_path.is_file():
                self.send_error(404, "Image not found.")
                return
            mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[image_path.suffix[1:]]
            data = image_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        static_files = {"/": "index.html", "/index.html": "index.html",
                        "/styles.css": "styles.css", "/script.js": "script.js"}
        filename = static_files.get(path)
        if not filename:
            self.send_error(404, "Not found.")
            return
        self.path = "/" + filename
        super().do_GET()

    def do_POST(self):
        self.handle_api_errors(self._do_POST)

    def _do_POST(self):
        if self.reject_origin():
            return
        path = urlparse(self.path).path
        if path == "/api/auth/verify-otp":
            self.handle_verify_otp()
            return
        if path == "/api/auth/register":
            self.handle_register()
            return
        if path == "/api/auth/login":
            self.handle_login()
            return
        if path == "/api/auth/logout":
            user = self.require_user()
            if not user:
                return
            header = self.clear_session()
            self.send_json({"ok": True}, extra_headers=[header] if header else None)
            return
        if path == "/api/listings":
            user = self.require_user()
            if not user:
                return
            try:
                listing = validate_listing(self.read_json())
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            image_name = self.save_image(listing["image_bytes"], listing["image_ext"]) if listing["image_bytes"] else None
            with connect_db() as db:
                cursor = db.execute("""
                    INSERT INTO listings
                        (title, description, category, price, condition, location, seller_name, phone,
                         owner_id, icon, image_name, status, listing_type, swap_for, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, '◇', ?, 'active', ?, ?, ?)
                    RETURNING id
                    """, (listing["title"], listing["description"], listing["category"], listing["price"],
                          listing["condition"], listing["location"], user["display_name"], user["id"],
                          image_name, listing["listing_type"], listing["swap_for"], utc_now()))
                row = db.execute("SELECT * FROM listings WHERE id = ?", (cursor.fetchone()["id"],)).fetchone()
            self.send_json({"listing": public_listing(row, user["id"])}, 201)
            return
        if path == "/api/messages":
            self.handle_message()
            return
        if path == "/api/reports":
            self.handle_report()
            return
        if path == "/api/orders":
            self.handle_order()
            return
        report_match = re.fullmatch(r"/api/admin/reports/(\d+)", path)
        if report_match:
            self.handle_report_review(int(report_match.group(1)))
            return
        self.send_json({"error": "Not found."}, 404)

    def handle_register(self):
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            name = clean_text(payload.get("display_name"), "Name", maximum=60)
            email = validate_email(payload.get("email"))
            password = validate_password(payload.get("password"))
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        if SUPABASE_MODE:
            try:
                result = supabase_request(
                    "/auth/v1/signup",
                    method="POST",
                    payload={
                        "email": email,
                        "password": password,
                        "data": {"display_name": name},
                    },
                )
            except SupabaseError as error:
                self.send_json({"error": error.message}, 409 if error.status == 409 else error.status)
                return
            auth_user = result.get("user")
            if not auth_user:
                self.send_json({"error": "Supabase did not create an account."}, 502)
                return
            if "identities" in auth_user and not auth_user["identities"]:
                self.send_json({"error": "An account with that email already exists."}, 409)
                return
            access_token = result.get("access_token")
            refresh_token = result.get("refresh_token")
            if not access_token or not refresh_token:
                self.send_json({
                    "verification_required": True,
                    "message": "Check your PAU email for the 6-digit code, then enter it here.",
                }, 202)
                return
            csrf = self.set_supabase_session(result)
            user = self.supabase_public_user(auth_user)
            self.send_json({"user": user, "csrf_token": csrf}, 201)
            return
        salt = secrets.token_bytes(16).hex()
        password_hash = hash_password(password, salt)
        try:
            with connect_db() as db:
                cursor = db.execute("""
                    INSERT INTO users (email, display_name, password_salt, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                """, (email, name, salt, password_hash, utc_now()))
                user_id = cursor.fetchone()["id"]
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            self.send_json({"error": "An account with that email already exists."}, 409)
            return
        csrf, cookie = self.create_session(user_id)
        self.send_json({
            "user": public_user({"id": user_id, "email": email, "display_name": name}),
            "csrf_token": csrf,
        }, 201, [cookie])

    def handle_verify_otp(self):
        if not SUPABASE_MODE:
            self.send_json({"error": "Email code verification requires Supabase Auth."}, 400)
            return
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            email = validate_email(payload.get("email"))
            token = clean_text(payload.get("token"), "Verification code", maximum=6)
            if not re.fullmatch(r"[0-9]{6}", token):
                raise ValueError("Enter the 6-digit code from your email.")
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        try:
            result = supabase_request(
                "/auth/v1/verify",
                method="POST",
                payload={"email": email, "token": token, "type": "email"},
            )
        except SupabaseError as error:
            self.send_json({"error": error.message}, error.status)
            return
        auth_user = result.get("user")
        if not auth_user or not result.get("access_token") or not result.get("refresh_token"):
            self.send_json({"error": "Supabase did not return a verified login session."}, 502)
            return
        csrf = self.set_supabase_session(result)
        self.send_json({"user": self.supabase_public_user(auth_user), "csrf_token": csrf})

    def handle_login(self):
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            email = validate_email(payload.get("email"))
            password = payload.get("password")
            if not isinstance(password, str) or not password:
                raise ValueError("Email and password are required.")
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        if SUPABASE_MODE:
            try:
                result = supabase_request(
                    "/auth/v1/token?grant_type=password",
                    method="POST",
                    payload={"email": email, "password": password},
                )
            except SupabaseError as error:
                status = 401 if error.status in {400, 401} else error.status
                self.send_json({"error": "Email or password is incorrect." if status == 401 else error.message}, status)
                return
            auth_user = result.get("user")
            if not auth_user:
                self.send_json({"error": "Supabase did not return the signed-in user."}, 502)
                return
            csrf = self.set_supabase_session(result)
            self.send_json({"user": self.supabase_public_user(auth_user), "csrf_token": csrf})
            return
        with connect_db() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not secrets.compare_digest(hash_password(password, row["password_salt"]), row["password_hash"]):
            self.send_json({"error": "Email or password is incorrect."}, 401)
            return
        csrf, cookie = self.create_session(row["id"])
        self.send_json({"user": public_user(row), "csrf_token": csrf}, extra_headers=[cookie])

    def supabase_public_user(self, auth_user):
        user_id = auth_user.get("id")
        if not user_id:
            raise RuntimeError("Supabase Auth returned a user without an ID.")
        with connect_db() as db:
            profile = db.execute(
                "SELECT id, email, display_name FROM profiles WHERE id = ?", (user_id,)
            ).fetchone()
        if not profile:
            raise RuntimeError("Supabase Auth user has no matching public profile.")
        return public_user(profile)

    def handle_message(self):
        user = self.require_user()
        if not user:
            return
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            listing_id = int(payload.get("listing_id"))
            if SUPABASE_MODE:
                recipient_id = clean_text(payload.get("recipient_id"), "Recipient", maximum=36)
                if not re.fullmatch(
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                    recipient_id,
                ):
                    raise ValueError("Choose a valid conversation participant.")
            else:
                recipient_id = int(payload.get("recipient_id"))
            body = clean_text(payload.get("body"), "Message", maximum=1200)
        except (TypeError, ValueError) as error:
            self.send_json({"error": str(error) or "Invalid message."}, 400)
            return
        if recipient_id == user["id"]:
            self.send_json({"error": "You cannot message yourself."}, 400)
            return
        with connect_db() as db:
            listing = db.execute("SELECT owner_id, status FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not listing:
                self.send_json({"error": "Listing not found."}, 404)
                return
            prior = db.execute("""
                SELECT 1 FROM messages WHERE listing_id = ? AND
                ((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?))
                LIMIT 1
            """, (listing_id, user["id"], recipient_id, recipient_id, user["id"])).fetchone()
            is_buyer_starting = (
                listing["owner_id"] == recipient_id
                and listing["status"] == "active"
                and not prior
            )
            allowed = prior is not None or is_buyer_starting
            if not allowed:
                self.send_json({"error": "You cannot message this listing participant."}, 403)
                return
            cursor = db.execute("""
                INSERT INTO messages (listing_id, sender_id, recipient_id, body, created_at)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
            """, (listing_id, user["id"], recipient_id, body, utc_now()))
            row = db.execute("SELECT id, sender_id, recipient_id, body, created_at FROM messages WHERE id = ?",
                             (cursor.fetchone()["id"],)).fetchone()
        self.send_json({"message": dict(row)}, 201)

    def handle_order(self):
        user = self.require_user()
        if not user:
            return
        try:
            payload = self.read_json()
            listing_id = int(payload.get("listing_id")) if isinstance(payload, dict) else 0
        except (TypeError, ValueError):
            self.send_json({"error": "Choose a valid listing."}, 400)
            return
        with connect_db() as db:
            if not SUPABASE_MODE:
                db.execute("BEGIN IMMEDIATE")
            lock = " FOR UPDATE" if SUPABASE_MODE else ""
            listing = db.execute(f"SELECT * FROM listings WHERE id = ?{lock}", (listing_id,)).fetchone()
            if not listing:
                self.send_json({"error": "Listing not found."}, 404)
                return
            if listing["owner_id"] == user["id"]:
                self.send_json({"error": "You cannot buy your own listing."}, 400)
                return
            if listing["status"] != "active" or listing["owner_id"] is None:
                self.send_json({"error": "This sample or sold listing cannot be purchased."}, 409)
                return
            if listing["listing_type"] != "sale":
                self.send_json({"error": "Only sale listings can be purchased. Message the owner to discuss this listing."}, 409)
                return
            now = utc_now()
            cursor = db.execute("""
                INSERT INTO orders (listing_id, buyer_id, seller_id, amount, status, created_at)
                VALUES (?, ?, ?, ?, 'simulated_paid', ?)
                RETURNING id
            """, (listing_id, user["id"], listing["owner_id"], listing["price"], now))
            update = db.execute(
                "UPDATE listings SET status = 'sold' WHERE id = ? AND status = 'active'", (listing_id,)
            )
            if update.rowcount != 1:
                self.send_json({"error": "This listing is no longer available."}, 409)
                return
            profile_table = "profiles" if SUPABASE_MODE else "users"
            order = db.execute(f"""
                SELECT orders.id, orders.listing_id, orders.amount, orders.status, orders.created_at,
                       listings.title, sellers.display_name AS seller_name,
                       buyers.display_name AS buyer_name
                FROM orders JOIN listings ON listings.id = orders.listing_id
                JOIN {profile_table} sellers ON sellers.id = orders.seller_id
                JOIN {profile_table} buyers ON buyers.id = orders.buyer_id
                WHERE orders.id = ?
            """, (cursor.fetchone()["id"],)).fetchone()
        self.send_json({
            "order": dict(order),
            "notice": "Demo checkout only — no money was charged or transferred.",
        }, 201)

    def handle_report(self):
        user = self.require_user()
        if not user:
            return
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            listing_id = int(payload.get("listing_id"))
            reason = clean_text(payload.get("reason"), "Report reason", maximum=30)
            details = clean_text(payload.get("details", ""), "Details", required=False, maximum=500)
        except (TypeError, ValueError) as error:
            self.send_json({"error": str(error) or "Invalid report."}, 400)
            return
        if reason not in {"spam", "fraud", "prohibited", "other"}:
            self.send_json({"error": "Choose a valid report reason."}, 400)
            return
        with connect_db() as db:
            listing = db.execute(
                "SELECT owner_id, status FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if not listing or listing["status"] != "active":
                self.send_json({"error": "This listing cannot be reported."}, 404)
                return
            if listing["owner_id"] is None:
                self.send_json({"error": "Sample listings cannot be reported."}, 400)
                return
            if listing["owner_id"] == user["id"]:
                self.send_json({"error": "You cannot report your own listing."}, 400)
                return
            cursor = db.execute("""
                INSERT INTO reports (listing_id, reporter_id, reason, details, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (listing_id, reporter_id) DO NOTHING
                RETURNING id
            """, (listing_id, user["id"], reason, details, utc_now()))
            report_row = cursor.fetchone()
            if not report_row:
                self.send_json({"error": "You have already reported this listing."}, 409)
                return
        self.send_json({"report_id": report_row["id"], "status": "open"}, 201)

    def handle_report_review(self, report_id):
        user = self.require_admin()
        if not user:
            return
        try:
            payload = self.read_json()
            action = payload.get("action") if isinstance(payload, dict) else None
            if action not in {"hide", "dismiss"}:
                raise ValueError("Choose hide or dismiss.")
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        with connect_db() as db:
            report = db.execute("SELECT listing_id, status FROM reports WHERE id = ?", (report_id,)).fetchone()
            if not report:
                self.send_json({"error": "Report not found."}, 404)
                return
            if report["status"] != "open":
                self.send_json({"error": "This report has already been reviewed."}, 409)
                return
            if action == "hide":
                cursor = db.execute(
                    "UPDATE listings SET status = 'hidden' WHERE id = ? AND status = 'active'",
                    (report["listing_id"],),
                )
                if cursor.rowcount != 1:
                    self.send_json({"error": "This listing is no longer active and cannot be hidden."}, 409)
                    return
            db.execute("""
                UPDATE reports SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?
            """, ("resolved" if action == "hide" else "dismissed", user["id"], utc_now(), report_id))
        self.send_json({"ok": True, "action": action})

    def do_PUT(self):
        self.handle_api_errors(self._do_PUT)

    def _do_PUT(self):
        if self.reject_origin():
            return
        user = self.require_user()
        if not user:
            return
        match = re.fullmatch(r"/api/listings/(\d+)", urlparse(self.path).path)
        if not match:
            self.send_json({"error": "Not found."}, 404)
            return
        try:
            listing = validate_listing(self.read_json())
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        listing_id = int(match.group(1))
        with connect_db() as db:
            current = db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not current:
                self.send_json({"error": "Listing not found."}, 404)
                return
            if current["owner_id"] != user["id"]:
                self.send_json({"error": "Only the listing owner can edit this item."}, 403)
                return
            if current["status"] != "active":
                self.send_json({"error": "Sold listings cannot be edited."}, 409)
                return
            image_name = current["image_name"]
            if listing["image_bytes"]:
                image_name = self.save_image(listing["image_bytes"], listing["image_ext"])
            db.execute("""
                UPDATE listings SET title=?, description=?, category=?, price=?, condition=?,
                location=?, listing_type=?, swap_for=?, image_name=? WHERE id=?
            """, (listing["title"], listing["description"], listing["category"], listing["price"],
                  listing["condition"], listing["location"], listing["listing_type"],
                  listing["swap_for"], image_name, listing_id))
            row = db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if listing["image_bytes"] and current["image_name"]:
            self.remove_image(current["image_name"])
        self.send_json({"listing": public_listing(row, user["id"])})

    def do_DELETE(self):
        self.handle_api_errors(self._do_DELETE)

    def _do_DELETE(self):
        if self.reject_origin():
            return
        user = self.require_user()
        if not user:
            return
        match = re.fullmatch(r"/api/listings/(\d+)", urlparse(self.path).path)
        if not match:
            self.send_json({"error": "Not found."}, 404)
            return
        listing_id = int(match.group(1))
        with connect_db() as db:
            current = db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not current:
                self.send_json({"error": "Listing not found."}, 404)
                return
            if current["owner_id"] != user["id"]:
                self.send_json({"error": "Only the listing owner can delete this item."}, 403)
                return
            if current["status"] != "active":
                self.send_json({"error": "Sold listings cannot be deleted."}, 409)
                return
            db.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
        self.remove_image(current["image_name"])
        self.send_json({"ok": True})

    def do_GET_orders(self, user):
        profile_table = "profiles" if SUPABASE_MODE else "users"
        with connect_db() as db:
            rows = db.execute(f"""
                SELECT orders.id, orders.listing_id, orders.amount, orders.status, orders.created_at,
                       listings.title, sellers.display_name AS seller_name,
                       buyers.display_name AS buyer_name, orders.buyer_id, orders.seller_id
                FROM orders JOIN listings ON listings.id = orders.listing_id
                JOIN {profile_table} sellers ON sellers.id = orders.seller_id
                JOIN {profile_table} buyers ON buyers.id = orders.buyer_id
                WHERE orders.buyer_id = ? OR orders.seller_id = ?
                ORDER BY orders.id DESC
            """, (user["id"], user["id"])).fetchall()
        self.send_json({"orders": [dict(row) for row in rows]})

    def do_OPTIONS(self):
        self.send_error(405, "Cross-origin requests are not supported.")

    def do_HEAD(self):
        path = unquote(urlparse(self.path).path)
        static_files = {"/": "index.html", "/index.html": "index.html",
                        "/styles.css": "styles.css", "/script.js": "script.js"}
        filename = static_files.get(path)
        if not filename:
            self.send_error(404)
            return
        self.path = "/" + filename
        super().do_HEAD()

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")
if __name__ == "__main__":
    initialize_db()
    host = os.environ.get("LOOP_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("LOOP_PORT", "8000")))
    server = ThreadingHTTPServer((host, port), LoopHandler)
    database = "Supabase Postgres" if SUPABASE_MODE else f"SQLite: {DB_PATH}"
    print(f"Loop is running at http://{host}:{port} (database: {database})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Loop server.")
    finally:
        server.server_close()
