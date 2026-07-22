"""
FocusPlan — Schedule planner with login system, admin panel and 3 languages.
Run:  python app.py   →  http://127.0.0.1:5001
"""
import os
import re
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, date
from functools import wraps

from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "planner.db")

# ---- persistent data dir for cloud hosts (Render disks etc.) --------------
# Set DATA_DIR=/var/data (a mounted persistent disk) and the database plus all
# uploads survive every redeploy. Locally, leave DATA_DIR unset — nothing changes.
DATA_DIR = os.environ.get("DATA_DIR")
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "planner.db")
    for sub, target in (("avatars", os.path.join(BASE_DIR, "static", "avatars")),
                        ("fonts", os.path.join(BASE_DIR, "static", "fonts")),
                        ("groupfiles", os.path.join(BASE_DIR, "groupfiles")),
                        ("dmfiles", os.path.join(BASE_DIR, "dmfiles")),
                        ("postfiles", os.path.join(BASE_DIR, "postfiles"))):
        real = os.path.join(DATA_DIR, sub)
        os.makedirs(real, exist_ok=True)
        if not os.path.islink(target):
            if os.path.isdir(target) and not os.listdir(target):
                os.rmdir(target)
            if not os.path.exists(target):
                os.symlink(real, target)

from datetime import timedelta as _td

APP_VERSION = "6.0"   # shown in the footer — bump this with each release

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")
app.config["PERMANENT_SESSION_LIFETIME"] = _td(days=60)  # stay signed in like a real app
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB uploads (photos & group files)

AVATAR_EXTS = ("png", "jpg", "jpeg", "webp", "gif")


def avatar_url(user_id):
    """Return the static URL of a user's profile photo, or None."""
    folder = os.path.join(BASE_DIR, "static", "avatars")
    for ext in AVATAR_EXTS:
        p = os.path.join(folder, f"{user_id}.{ext}")
        if os.path.exists(p):
            return url_for("static", filename=f"avatars/{user_id}.{ext}",
                           v=int(os.path.getmtime(p)))
    return None


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        is_admin      INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        last_login    TEXT
    );
    CREATE TABLE IF NOT EXISTS plans (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        details    TEXT DEFAULT '',
        plan_type  TEXT NOT NULL CHECK(plan_type IN ('short','long')),
        priority   TEXT NOT NULL CHECK(priority IN ('high','medium','low')),
        due_date   TEXT,
        done       INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS quotes (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        text_en TEXT NOT NULL,
        text_ar TEXT DEFAULT '',
        text_ku TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS activity (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day     TEXT NOT NULL,
        PRIMARY KEY (user_id, day)
    );
    CREATE TABLE IF NOT EXISTS habits (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS habit_checks (
        habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        day      TEXT NOT NULL,
        PRIMARY KEY (habit_id, day)
    );
    CREATE TABLE IF NOT EXISTS notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT DEFAULT '',
        content    TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS stories (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content    TEXT NOT NULL,
        bg         INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS story_views (
        story_id   INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (story_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS tt_shares (
        code       TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS homework (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subject    TEXT DEFAULT '',
        title      TEXT NOT NULL,
        details    TEXT DEFAULT '',
        due_date   TEXT DEFAULT '',
        done       INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS exams (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subject   TEXT NOT NULL,
        exam_date TEXT NOT NULL,
        note      TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS timetable (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day        INTEGER NOT NULL,           -- 0=Sat 1=Sun ... 6=Fri
        subject    TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time   TEXT DEFAULT '',
        room       TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS flashcards (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subject  TEXT NOT NULL,
        question TEXT NOT NULL,
        answer   TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS friendships (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        to_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status     TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted')),
        created_at TEXT NOT NULL,
        UNIQUE(from_id, to_id)
    );
    CREATE TABLE IF NOT EXISTS groups (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        description   TEXT DEFAULT '',
        owner_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        frequency     TEXT NOT NULL DEFAULT 'weekly'
                      CHECK(frequency IN ('weekly','biweekly','monthly')),
        first_meeting TEXT,
        created_at    TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS group_members (
        group_id  INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at TEXT NOT NULL,
        PRIMARY KEY (group_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS group_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS group_plans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id    INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        details     TEXT DEFAULT '',
        target_date TEXT,
        pinned      INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL,
        actor      TEXT DEFAULT '',
        link       TEXT DEFAULT '',
        is_read    INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dms (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        to_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content    TEXT NOT NULL,
        is_read    INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS badges (
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code      TEXT NOT NULL,
        earned_at TEXT NOT NULL,
        PRIMARY KEY (user_id, code)
    );
    CREATE TABLE IF NOT EXISTS group_files (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        stored     TEXT NOT NULL,
        orig_name  TEXT NOT NULL,
        size       INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS group_decks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subject    TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(group_id, user_id, subject)
    );
    CREATE TABLE IF NOT EXISTS fonts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        stored     TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS msg_reactions (
        message_id INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji      TEXT NOT NULL,
        PRIMARY KEY (message_id, user_id, emoji)
    );
    CREATE TABLE IF NOT EXISTS dm_reactions (
        msg_id     INTEGER NOT NULL REFERENCES dms(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji      TEXT NOT NULL,
        PRIMARY KEY (msg_id, user_id, emoji)
    );
    CREATE TABLE IF NOT EXISTS push_subs (
        endpoint   TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        p256dh     TEXT NOT NULL,
        auth       TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS polls (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        question   TEXT NOT NULL,
        closed     INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS poll_options (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        text    TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS poll_votes (
        poll_id   INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (poll_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS challenges (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        target     INTEGER NOT NULL,
        start_day  TEXT NOT NULL,
        end_day    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS personal_challenges (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        target     INTEGER NOT NULL,
        start_day  TEXT NOT NULL,
        end_day    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS semesters (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS semester_courses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        credits     REAL NOT NULL DEFAULT 3,
        letter      TEXT NOT NULL DEFAULT 'A',
        points      REAL NOT NULL DEFAULT 4.0
    );
    CREATE TABLE IF NOT EXISTS posts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        content    TEXT NOT NULL,
        category   TEXT NOT NULL DEFAULT 'other',
        image      TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS post_likes (
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (post_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS post_comments (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS feedback (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        message    TEXT NOT NULL,
        rating     INTEGER,
        resolved   INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS duels (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        to_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status     TEXT NOT NULL DEFAULT 'pending'
                   CHECK(status IN ('pending','active','done')),
        start_day  TEXT,
        end_day    TEXT,
        winner_id  INTEGER,
        created_at TEXT NOT NULL
    );
    """)
    # migrations for databases created by earlier versions
    for stmt in ("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'dark'",
                 "ALTER TABLE users ADD COLUMN accent TEXT",
                 "ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''",
                 "ALTER TABLE plans ADD COLUMN done_at TEXT",
                 "ALTER TABLE users ADD COLUMN edu_level TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN institution TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN college TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN department TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN stage TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN job_title TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN job_field TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN daily_goal INTEGER DEFAULT 3",
                 "ALTER TABLE group_messages ADD COLUMN reply_to INTEGER",
                 "ALTER TABLE plans ADD COLUMN repeat TEXT DEFAULT ''",
                 "ALTER TABLE dms ADD COLUMN kind TEXT DEFAULT 'text'",
                 "ALTER TABLE dms ADD COLUMN stored TEXT DEFAULT ''",
                 "ALTER TABLE dms ADD COLUMN orig_name TEXT DEFAULT ''",
                 "ALTER TABLE dms ADD COLUMN reply_to INTEGER",
                 "ALTER TABLE dms ADD COLUMN deleted INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN last_seen TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'en'",
                 "ALTER TABLE group_messages ADD COLUMN deleted INTEGER DEFAULT 0",
                 "ALTER TABLE group_messages ADD COLUMN kind TEXT DEFAULT 'text'",
                 "ALTER TABLE group_messages ADD COLUMN stored TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN plus INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN studying_until TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN studying_label TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN middle_name TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN last_name TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN school_level TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN grade TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN college_kind TEXT DEFAULT ''",
                 "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN profile_v INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN allow_dm_all INTEGER DEFAULT 1",
                 "ALTER TABLE users ADD COLUMN is_private INTEGER DEFAULT 0",
                 "ALTER TABLE dms ADD COLUMN pinned INTEGER DEFAULT 0",
                 "ALTER TABLE group_messages ADD COLUMN pinned INTEGER DEFAULT 0",
                 """CREATE TABLE IF NOT EXISTS chat_clears (
                     user_id INTEGER NOT NULL, kind TEXT NOT NULL,
                     target_id INTEGER NOT NULL, cleared_id INTEGER NOT NULL DEFAULT 0,
                     UNIQUE(user_id, kind, target_id))""",
                 """CREATE TABLE IF NOT EXISTS chat_mutes (
                     user_id INTEGER NOT NULL, kind TEXT NOT NULL,
                     target_id INTEGER NOT NULL,
                     UNIQUE(user_id, kind, target_id))""",
                 """CREATE TABLE IF NOT EXISTS follows (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                     followed_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                     created_at TEXT NOT NULL,
                     UNIQUE(follower_id, followed_id))"""):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    os.makedirs(os.path.join(BASE_DIR, "static", "avatars"), exist_ok=True)
    # group files live OUTSIDE static so downloads always pass the membership check
    os.makedirs(os.path.join(BASE_DIR, "groupfiles"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "dmfiles"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "postfiles"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "static", "fonts"), exist_ok=True)
    # default settings
    defaults = {
        "site_name": "FocusPlan",
        "tagline_en": "Plan it. Do it. Own your time.",
        "tagline_ar": "خطّط. نفّذ. امتلك وقتك.",
        "tagline_ku": "پلان دابنێ. جێبەجێی بکە. کاتەکەت بەڕێوە ببە.",
        "accent_color": "#7c5cff",
        "allow_registration": "1",
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))
    # seed the editable registration option lists (admin can change them later)
    _REG_DEFAULTS = {
        "reg_universities": "\n".join([
            "🏛️ | Salahaddin University-Erbil | زانکۆی سەڵاحەدین - هەولێر | جامعة صلاح الدين - أربيل",
            "🏛️ | University of Sulaimani | زانکۆی سلێمانی | جامعة السليمانية",
            "🏛️ | University of Duhok | زانکۆی دهۆک | جامعة دهوك",
            "🏛️ | University of Zakho | زانکۆی زاخۆ | جامعة زاخو",
            "🏛️ | Koya University | زانکۆی کۆیە | جامعة كوية",
            "🏛️ | Soran University | زانکۆی سۆران | جامعة سوران",
            "🏛️ | University of Raparin | زانکۆی ڕاپەڕین | جامعة رابرين",
            "🏛️ | University of Halabja | زانکۆی هەڵەبجە | جامعة حلبجة",
            "🏛️ | University of Garmian | زانکۆی گەرمیان | جامعة كرميان",
            "🏛️ | Charmo University | زانکۆی چەرموو | جامعة جرمو",
            "🏢 | Erbil Polytechnic University | زانکۆی پۆلیتەکنیکی هەولێر | جامعة أربيل التقنية",
            "🏢 | Sulaimani Polytechnic University | زانکۆی پۆلیتەکنیکی سلێمانی | جامعة السليمانية التقنية",
            "🏢 | Duhok Polytechnic University | زانکۆی پۆلیتەکنیکی دهۆک | جامعة دهوك التقنية",
            "🏛️ | University of Kurdistan Hewlêr | زانکۆی کوردستان هەولێر | جامعة كردستان أربيل",
            "🌍 | American University of Iraq Sulaimani | زانکۆی ئەمریکی عێراق - سلێمانی | الجامعة الأمريكية في العراق - السليمانية",
            "🌍 | American University of Kurdistan | زانکۆی ئەمریکی کوردستان | الجامعة الأمريكية في كردستان",
            "🌍 | Tishk International University | زانکۆی نێودەوڵەتی تیشک | جامعة تيشك الدولية",
            "🏛️ | Cihan University Erbil | زانکۆی جیهان - هەولێر | جامعة جيهان - أربيل",
            "🏛️ | Cihan University Sulaimaniya | زانکۆی جیهان - سلێمانی | جامعة جيهان - السليمانية",
            "🏛️ | Cihan University Duhok | زانکۆی جیهان - دهۆک | جامعة جيهان - دهوك",
            "🏛️ | Knowledge University | زانکۆی نۆلج | جامعة المعرفة",
            "🏛️ | Catholic University in Erbil | زانکۆی کاسۆلیکی هەولێر | الجامعة الكاثوليكية في أربيل",
            "🏛️ | Lebanese French University | زانکۆی لوبنانی فەرەنسی | الجامعة اللبنانية الفرنسية",
            "🏛️ | Bayan University | زانکۆی بایان | جامعة بيان",
            "🔬 | Komar University of Science and Technology | زانکۆی کۆمار بۆ زانست و تەکنەلۆژیا | جامعة كومار للعلوم والتكنولوجيا",
            "🏛️ | University of Human Development | زانکۆی گەشەپێدانی مرۆیی | جامعة التنمية البشرية",
            "🌍 | Qaiwan International University | زانکۆی نێودەوڵەتی قەیوان | جامعة قيوان الدولية",
            "🏛️ | Nawroz University | زانکۆی نەورۆز | جامعة نوروز"]),
        "reg_colleges": "\n".join([
            "🩺 | Medicine | پزیشکی | الطب",
            "🦷 | Dentistry | ددانسازی | طب الأسنان",
            "💊 | Pharmacy | دەرمانسازی | الصيدلة",
            "🩹 | Nursing | پەرستاری | التمريض",
            "🏥 | Health Sciences | زانستە تەندروستییەکان | العلوم الصحية",
            "🐾 | Veterinary Medicine | پزیشکی ڤێتێرنەری | الطب البيطري",
            "⚙️ | Engineering | ئەندازیاری | الهندسة",
            "🔬 | Science | زانست | العلوم",
            "💻 | Computer Science & IT | زانستی کۆمپیوتەر و ئایتی | علوم الحاسوب وتكنولوجيا المعلومات",
            "🌾 | Agriculture | کشتوکاڵ | الزراعة",
            "🎓 | Education | پەروەردە | التربية",
            "✏️ | Basic Education | پەروەردەی بنەڕەتی | التربية الأساسية",
            "🏃 | Physical Education & Sport Sciences | پەروەردەی وەرزشی | التربية الرياضية",
            "🗣️ | Languages | زمان | اللغات",
            "🎨 | Arts & Humanities | ئاداب و زانستە مرۆییەکان | الآداب والعلوم الإنسانية",
            "⚖️ | Law | یاسا | القانون",
            "🏛️ | Political Science | زانستە سیاسییەکان | العلوم السياسية",
            "📊 | Administration & Economics | کارگێڕی و ئابووری | الإدارة والاقتصاد",
            "🎭 | Fine Arts | هونەرە جوانەکان | الفنون الجميلة",
            "🕌 | Islamic Sciences | زانستە ئیسلامییەکان | العلوم الإسلامية",
            "📺 | Media & Communication | ڕاگەیاندن | الإعلام والاتصال",
            "📐 | Architecture | تەلارسازی | العمارة",
            "🧳 | Tourism | گەشتوگوزار | السياحة"]),
        "reg_departments": "\n".join([
            "Medicine: General Medicine|پزیشکی گشتی|الطب العام",
            "Dentistry: Dentistry|ددانسازی|طب الأسنان",
            "Pharmacy: Pharmacy|دەرمانسازی|الصيدلة",
            "Nursing: Nursing|پەرستاری|التمريض",
            "Health Sciences: Medical Laboratory Science|زانستی تاقیگەی پزیشکی|علوم المختبرات الطبية, Physiotherapy|چارەسەری سروشتی|العلاج الطبيعي, Radiology|تیشک|الأشعة, Anesthesia|بێهۆشکردن|التخدير, Public Health|تەندروستی گشتی|الصحة العامة, Nutrition|خۆراک|التغذية, Optometry|چاودێری چاو|البصريات",
            "Veterinary Medicine: Veterinary Medicine|پزیشکی ڤێتێرنەری|الطب البيطري",
            "Engineering: Civil|شارستانی|المدنية, Electrical|کارەبا|الكهربائية, Mechanical|میکانیک|الميكانيكية, Architectural|تەلارسازی|العمارة, Software|سۆفتوێر|البرمجيات, Computer|کۆمپیوتەر|الحاسوب, Chemical|کیمیایی|الكيميائية, Petroleum|نەوت|النفط, Water Resources|سەرچاوەکانی ئاو|الموارد المائية, Surveying|ڕووپێوی|المساحة, Aviation|فڕۆکەوانی|الطيران, Mechatronics|میکاترۆنیکس|الميكاترونكس",
            "Science: Mathematics|ماتماتیک|الرياضيات, Physics|فیزیا|الفيزياء, Chemistry|کیمیا|الكيمياء, Biology|زیندەزانی|الأحياء, Geology|زەویناسی|الجيولوجيا, Environmental Science|زانستی ژینگە|علوم البيئة, Statistics|ئامار|الإحصاء",
            "Computer Science & IT: Computer Science|زانستی کۆمپیوتەر|علوم الحاسوب, Information Technology|تەکنەلۆژیای زانیاری|تكنولوجيا المعلومات, Software Engineering|ئەندازیاری سۆفتوێر|هندسة البرمجيات, Cybersecurity|ئاسایشی سایبەری|الأمن السيبراني, Artificial Intelligence|ژیریی دەستکرد|الذكاء الاصطناعي, Computer Networks|تۆڕی کۆمپیوتەر|شبكات الحاسوب, Information Systems|سیستەمی زانیاری|نظم المعلومات",
            "Agriculture: Plant Production|بەرهەمی ڕووەک|الإنتاج النباتي, Animal Production|بەرهەمی ئاژەڵ|الإنتاج الحيواني, Food Science|زانستی خۆراک|علوم الأغذية, Forestry|دارستان|الغابات, Horticulture|باخداری|البستنة, Soil & Water|خاک و ئاو|التربة والمياه",
            "Education: Kurdish Language|زمانی کوردی|اللغة الكردية, English Language|زمانی ئینگلیزی|اللغة الإنجليزية, Arabic Language|زمانی عەرەبی|اللغة العربية, Mathematics|ماتماتیک|الرياضيات, Physics|فیزیا|الفيزياء, Chemistry|کیمیا|الكيمياء, Biology|زیندەزانی|الأحياء, History|مێژوو|التاريخ, Geography|جوگرافیا|الجغرافيا, Psychology|دەروونناسی|علم النفس, Special Education|پەروەردەی تایبەت|التربية الخاصة",
            "Basic Education: General Science|زانستە گشتییەکان|العلوم العامة, Social Science|زانستە کۆمەڵایەتییەکان|العلوم الاجتماعية, Kurdish Language|زمانی کوردی|اللغة الكردية, English Language|زمانی ئینگلیزی|اللغة الإنجليزية, Mathematics|ماتماتیک|الرياضيات, Kindergarten|باخچەی منداڵان|رياض الأطفال",
            "Physical Education & Sport Sciences: Physical Education|پەروەردەی وەرزشی|التربية الرياضية",
            "Languages: Kurdish|کوردی|الكردية, English|ئینگلیزی|الإنجليزية, Arabic|عەرەبی|العربية, Persian|فارسی|الفارسية, French|فەرەنسی|الفرنسية, German|ئەڵمانی|الألمانية, Turkish|تورکی|التركية, Translation|وەرگێڕان|الترجمة",
            "Arts & Humanities: History|مێژوو|التاريخ, Geography|جوگرافیا|الجغرافيا, Archaeology|شوێنەوارناسی|الآثار, Philosophy|فەلسەفە|الفلسفة, Psychology|دەروونناسی|علم النفس, Sociology|کۆمەڵناسی|علم الاجتماع, Social Work|کاری کۆمەڵایەتی|الخدمة الاجتماعية, Anthropology|مرۆڤناسی|الأنثروبولوجيا",
            "Law: Law|یاسا|القانون",
            "Political Science: Political Science|زانستە سیاسییەکان|العلوم السياسية, International Relations|پەیوەندییە نێودەوڵەتییەکان|العلاقات الدولية, Diplomacy|دیپلۆماسی|الدبلوماسية",
            "Administration & Economics: Business Administration|کارگێڕی کار|إدارة الأعمال, Accounting|ژمێریاری|المحاسبة, Economics|ئابووری|الاقتصاد, Finance & Banking|دارایی و بانک|المالية والمصارف, Marketing|بازاڕدۆزی|التسويق, Statistics & Informatics|ئامار و زانیاری|الإحصاء والمعلوماتية, Tourism Administration|کارگێڕی گەشتوگوزار|إدارة السياحة, Management Information Systems|سیستەمی زانیاری کارگێڕی|نظم المعلومات الإدارية",
            "Fine Arts: Music|مۆسیقا|الموسيقى, Theatre|شانۆ|المسرح, Cinema|سینەما|السينما, Painting|وێنەکێشان|الرسم, Design|دیزاین|التصميم, Sculpture|پەیکەرتاشی|النحت",
            "Islamic Sciences: Islamic Studies|خوێندنە ئیسلامییەکان|الدراسات الإسلامية, Sharia|شەریعە|الشريعة, Usul al-Din|ئوسولی دین|أصول الدين",
            "Media & Communication: Journalism|ڕۆژنامەگەری|الصحافة, Media|ڕاگەیاندن|الإعلام, Public Relations|پەیوەندییە گشتییەکان|العلاقات العامة, Digital Media|میدیای دیجیتاڵ|الإعلام الرقمي",
            "Architecture: Architecture|تەلارسازی|العمارة, Interior Design|دیزاینی ناوەوە|التصميم الداخلي",
            "Tourism: Tourism Management|کارگێڕی گەشتوگوزار|إدارة السياحة, Hotel Management|کارگێڕی هوتێل|إدارة الفنادق"]),
        "reg_jobs": "\n".join([
            "Teacher", "Lecturer", "Doctor", "Nurse", "Pharmacist", "Engineer",
            "Lawyer", "Judge", "Accountant", "Government Employee",
            "Police / Security Forces", "Peshmerga", "Business Owner",
            "Shop Owner", "Trader", "Farmer", "Driver", "Craftsman",
            "Freelancer", "Programmer / IT", "Designer", "Journalist", "Artist",
            "Athlete", "Housewife", "Retired", "Job Seeker", "Other"]),
    }
    for k, v in _REG_DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))
    # upgrade plain (English-only) option lines to the emoji|en|ku|ar format
    for k in ("reg_universities", "reg_colleges", "reg_departments"):
        row = db.execute("SELECT value FROM settings WHERE key = ?", (k,)).fetchone()
        if row and "|" not in row[0]:
            rich = {}
            for line in _REG_DEFAULTS[k].split("\n"):
                if k == "reg_departments":
                    if ":" in line:
                        rich[line.split(":", 1)[0].strip()] = line.strip()
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2:
                    rich[parts[1]] = line.strip()
            def _mkey(l):
                return (l.split(":", 1)[0].strip()
                        if k == "reg_departments" and ":" in l else l.strip())
            merged = [rich.get(_mkey(l), l.strip())
                      for l in row[0].split("\n") if l.strip()]
            db.execute("UPDATE settings SET value = ? WHERE key = ?",
                       ("\n".join(merged), k))
    # generate the push-notification (VAPID) keypair once, keep it in settings
    if not db.execute("SELECT 1 FROM settings WHERE key = 'vapid_private'").fetchone():
        try:
            from py_vapid import Vapid02, b64urlencode
            from cryptography.hazmat.primitives import serialization
            _v = Vapid02()
            _v.generate_keys()
            _pub = _v.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint)
            _priv = _v.private_key.private_numbers().private_value.to_bytes(32, "big")
            db.execute("INSERT INTO settings(key, value) VALUES('vapid_private', ?)",
                       (b64urlencode(_priv),))
            db.execute("INSERT INTO settings(key, value) VALUES('vapid_public', ?)",
                       (b64urlencode(_pub),))
        except Exception:
            pass  # push libs missing — the site still works, just without push
    else:
        # v1.7 stored the key as PEM, which the push library can't sign with —
        # convert it in place so pushes reach closed apps too
        _row = db.execute("SELECT value FROM settings "
                          "WHERE key = 'vapid_private'").fetchone()
        if _row and _row[0].startswith("-----BEGIN"):
            try:
                from py_vapid import b64urlencode
                from cryptography.hazmat.primitives import serialization
                _pk = serialization.load_pem_private_key(_row[0].encode(),
                                                         password=None)
                _priv = _pk.private_numbers().private_value.to_bytes(32, "big")
                db.execute("UPDATE settings SET value = ? WHERE key = 'vapid_private'",
                           (b64urlencode(_priv),))
            except Exception:
                pass
    # default motivational quotes
    if db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0:
        seed_quotes = [
            ("The secret of getting ahead is getting started.",
             "سر التقدم هو أن تبدأ.",
             "نهێنی پێشکەوتن دەستپێکردنە."),
            ("A goal without a plan is just a wish.",
             "الهدف بدون خطة مجرد أمنية.",
             "ئامانج بەبێ پلان تەنها هیوایەکە."),
            ("Small steps every day lead to big results.",
             "خطوات صغيرة كل يوم تقود إلى نتائج كبيرة.",
             "هەنگاوی بچووک هەموو ڕۆژێک دەگاتە ئەنجامی گەورە."),
            ("Discipline is choosing what you want most over what you want now.",
             "الانضباط هو اختيار ما تريده أكثر على ما تريده الآن.",
             "دیسیپلین هەڵبژاردنی ئەوەیە کە زۆرترین دەتەوێت بەسەر ئەوەی ئێستا دەتەوێت."),
            ("Your future is created by what you do today, not tomorrow.",
             "مستقبلك يُصنع بما تفعله اليوم، لا غدًا.",
             "داهاتووت بەو کارە دروست دەبێت کە ئەمڕۆ دەیکەیت، نەک سبەینێ."),
            ("Focus on being productive instead of busy.",
             "ركّز على أن تكون منتجًا بدلاً من أن تكون مشغولًا.",
             "سەرنج بدە لەسەر بەرهەمدار بوون نەک سەرقاڵ بوون."),
        ]
        db.executemany("INSERT INTO quotes(text_en, text_ar, text_ku) VALUES(?,?,?)",
                       seed_quotes)
    # seed admin account
    if not db.execute("SELECT 1 FROM users WHERE username = ?", ("sharo",)).fetchone():
        db.execute(
            "INSERT INTO users(username, password_hash, is_admin, created_at) VALUES(?,?,1,?)",
            ("sharo", generate_password_hash("Sharo@2006"),
             datetime.utcnow().isoformat(timespec="seconds")))
    db.commit()
    db.close()


# ---------------------------------------------------------------- i18n
LANGS = {"en": "English", "ar": "العربية", "ku": "کوردی"}
RTL_LANGS = {"ar", "ku"}

T = {
    "en": {
        "login": "Log in", "register": "Create account", "logout": "Log out",
        "username": "Username", "password": "Password",
        "confirm_password": "Confirm password",
        "welcome_back": "Welcome back", "no_account": "No account yet?",
        "have_account": "Already have an account?",
        "dashboard": "My Schedule", "admin_panel": "Admin Panel",
        "short_term": "Short-term plans", "long_term": "Long-term plans",
        "short_hint": "Today · this week", "long_hint": "Months · big goals",
        "new_plan": "Add a new plan", "title": "Title",
        "details": "Details (optional)", "priority": "Priority",
        "p_high": "Important", "p_medium": "Normal", "p_low": "Someday / minor",
        "type": "Duration", "t_short": "Short-term", "t_long": "Long-term",
        "due": "Due date", "add": "Add plan", "done": "Done", "undo": "Undo",
        "delete": "Delete", "empty_short": "No short-term plans yet — start small, start now.",
        "empty_long": "No long-term plans yet — where do you want to be in a year?",
        "progress": "completed", "users": "Users", "settings": "Site settings",
        "quotes": "Motivational quotes", "site_name": "Site name",
        "tagline": "Tagline", "accent": "Accent color", "save": "Save",
        "created": "Created", "last_login": "Last login", "plans_count": "Plans",
        "role": "Role", "admin": "Admin", "user": "User", "actions": "Actions",
        "make_admin": "Make admin", "remove_admin": "Remove admin",
        "reset_pw": "Reset password", "new_password": "New password",
        "delete_user": "Delete", "add_quote": "Add quote",
        "quote_en": "Quote (English)", "quote_ar": "Quote (Arabic)",
        "quote_ku": "Quote (Kurdish)", "never": "Never",
        "allow_reg": "Allow new registrations",
        "err_login": "Wrong username or password.",
        "err_user_exists": "That username is already taken.",
        "err_pw_match": "Passwords do not match.",
        "err_pw_short": "Password must be at least 6 characters.",
        "err_username": "Username: 3–20 letters, numbers, _ or . (no dot at start/end).",
        "err_reg_closed": "Registration is currently closed.",
        "ok_registered": "Account created — welcome!",
        "ok_saved": "Saved.", "ok_deleted": "Deleted.",
        "confirm_del_plan": "Delete this plan?",
        "confirm_del_user": "Delete this user and all their plans?",
        "overdue": "Overdue", "today": "Today",
        "important_first": "Important things stand out. Small things stay small.",
        "you": "you",
    },
    "ar": {
        "login": "تسجيل الدخول", "register": "إنشاء حساب", "logout": "تسجيل الخروج",
        "username": "اسم المستخدم", "password": "كلمة المرور",
        "confirm_password": "تأكيد كلمة المرور",
        "welcome_back": "أهلاً بعودتك", "no_account": "ليس لديك حساب؟",
        "have_account": "لديك حساب بالفعل؟",
        "dashboard": "جدولي", "admin_panel": "لوحة الإدارة",
        "short_term": "خطط قصيرة المدى", "long_term": "خطط طويلة المدى",
        "short_hint": "اليوم · هذا الأسبوع", "long_hint": "أشهر · أهداف كبيرة",
        "new_plan": "أضف خطة جديدة", "title": "العنوان",
        "details": "التفاصيل (اختياري)", "priority": "الأولوية",
        "p_high": "مهم", "p_medium": "عادي", "p_low": "لاحقًا / ثانوي",
        "type": "المدة", "t_short": "قصير المدى", "t_long": "طويل المدى",
        "due": "تاريخ الاستحقاق", "add": "إضافة", "done": "تم", "undo": "تراجع",
        "delete": "حذف", "empty_short": "لا توجد خطط قصيرة بعد — ابدأ صغيرًا، ابدأ الآن.",
        "empty_long": "لا توجد خطط طويلة بعد — أين تريد أن تكون بعد سنة؟",
        "progress": "مكتمل", "users": "المستخدمون", "settings": "إعدادات الموقع",
        "quotes": "عبارات تحفيزية", "site_name": "اسم الموقع",
        "tagline": "الشعار", "accent": "اللون الرئيسي", "save": "حفظ",
        "created": "تاريخ الإنشاء", "last_login": "آخر دخول", "plans_count": "الخطط",
        "role": "الدور", "admin": "مدير", "user": "مستخدم", "actions": "إجراءات",
        "make_admin": "ترقية لمدير", "remove_admin": "إزالة الإدارة",
        "reset_pw": "إعادة تعيين كلمة المرور", "new_password": "كلمة مرور جديدة",
        "delete_user": "حذف", "add_quote": "إضافة عبارة",
        "quote_en": "العبارة (إنجليزي)", "quote_ar": "العبارة (عربي)",
        "quote_ku": "العبارة (كردي)", "never": "أبدًا",
        "allow_reg": "السماح بالتسجيلات الجديدة",
        "err_login": "اسم المستخدم أو كلمة المرور غير صحيحة.",
        "err_user_exists": "اسم المستخدم مأخوذ بالفعل.",
        "err_pw_match": "كلمتا المرور غير متطابقتين.",
        "err_pw_short": "كلمة المرور يجب أن تكون 6 أحرف على الأقل.",
        "err_username": "اسم المستخدم: 3–20 حرفًا أو رقمًا أو _ أو . (بدون نقطة في البداية/النهاية).",
        "err_reg_closed": "التسجيل مغلق حاليًا.",
        "ok_registered": "تم إنشاء الحساب — أهلاً بك!",
        "ok_saved": "تم الحفظ.", "ok_deleted": "تم الحذف.",
        "confirm_del_plan": "حذف هذه الخطة؟",
        "confirm_del_user": "حذف هذا المستخدم وجميع خططه؟",
        "overdue": "متأخر", "today": "اليوم",
        "important_first": "الأشياء المهمة تبرز. والصغيرة تبقى صغيرة.",
        "you": "أنت",
    },
    "ku": {
        "login": "چوونەژوورەوە", "register": "دروستکردنی هەژمار", "logout": "دەرچوون",
        "username": "ناوی بەکارهێنەر", "password": "وشەی نهێنی",
        "confirm_password": "دووبارەکردنەوەی وشەی نهێنی",
        "welcome_back": "بەخێربێیتەوە", "no_account": "هەژمارت نییە؟",
        "have_account": "پێشتر هەژمارت هەیە؟",
        "dashboard": "خشتەکەم", "admin_panel": "پانێڵی بەڕێوەبەر",
        "short_term": "پلانی کورتخایەن", "long_term": "پلانی درێژخایەن",
        "short_hint": "ئەمڕۆ · ئەم هەفتەیە", "long_hint": "مانگەکان · ئامانجە گەورەکان",
        "new_plan": "پلانێکی نوێ زیاد بکە", "title": "ناونیشان",
        "details": "وردەکاری (ئارەزوومەندانە)", "priority": "گرنگی",
        "p_high": "گرنگ", "p_medium": "ئاسایی", "p_low": "دواتر / لاوەکی",
        "type": "ماوە", "t_short": "کورتخایەن", "t_long": "درێژخایەن",
        "due": "بەرواری کۆتایی", "add": "زیادکردن", "done": "تەواو", "undo": "گەڕاندنەوە",
        "delete": "سڕینەوە", "empty_short": "هێشتا پلانی کورتخایەن نییە — بە بچووک دەست پێ بکە، ئێستا دەست پێ بکە.",
        "empty_long": "هێشتا پلانی درێژخایەن نییە — دەتەوێت ساڵێکی تر لە کوێ بیت؟",
        "progress": "تەواوبوو", "users": "بەکارهێنەران", "settings": "ڕێکخستنەکانی ماڵپەڕ",
        "quotes": "وتە هاندەرەکان", "site_name": "ناوی ماڵپەڕ",
        "tagline": "دروشم", "accent": "ڕەنگی سەرەکی", "save": "پاشەکەوتکردن",
        "created": "دروستکراوە", "last_login": "دوایین چوونەژوورەوە", "plans_count": "پلانەکان",
        "role": "ڕۆڵ", "admin": "بەڕێوەبەر", "user": "بەکارهێنەر", "actions": "کردارەکان",
        "make_admin": "بکە بە بەڕێوەبەر", "remove_admin": "لابردنی بەڕێوەبەرایەتی",
        "reset_pw": "ڕێکخستنەوەی وشەی نهێنی", "new_password": "وشەی نهێنی نوێ",
        "delete_user": "سڕینەوە", "add_quote": "زیادکردنی وتە",
        "quote_en": "وتە (ئینگلیزی)", "quote_ar": "وتە (عەرەبی)",
        "quote_ku": "وتە (کوردی)", "never": "هەرگیز",
        "allow_reg": "ڕێگەدان بە تۆمارکردنی نوێ",
        "err_login": "ناوی بەکارهێنەر یان وشەی نهێنی هەڵەیە.",
        "err_user_exists": "ئەم ناوە پێشتر گیراوە.",
        "err_pw_match": "وشە نهێنییەکان وەک یەک نین.",
        "err_pw_short": "وشەی نهێنی دەبێت لانیکەم ٦ پیت بێت.",
        "err_username": "ناوی بەکارهێنەر: ٣–٢٠ پیت، ژمارە، _ یان . (خاڵ لە سەرەتا/کۆتایی نابێت).",
        "err_reg_closed": "تۆمارکردن لە ئێستادا داخراوە.",
        "ok_registered": "هەژمارەکە دروستکرا — بەخێربێیت!",
        "ok_saved": "پاشەکەوتکرا.", "ok_deleted": "سڕایەوە.",
        "confirm_del_plan": "ئەم پلانە بسڕدرێتەوە؟",
        "confirm_del_user": "ئەم بەکارهێنەرە و هەموو پلانەکانی بسڕدرێنەوە؟",
        "overdue": "دواکەوتوو", "today": "ئەمڕۆ",
        "important_first": "شتە گرنگەکان دەردەکەون. بچووکەکان بچووک دەمێننەوە.",
        "you": "تۆ",
    },
}


# --- v2 feature strings ---
EXTRA = {
    "en": {
        "focus": "Focus", "habits": "Habits", "notes": "Notes",
        "university": "University",
        "streak": "day streak", "streak_msg": "Keep it going — don't break the chain!",
        "focus_time": "Focus", "break_time": "Break", "long_break": "Long break",
        "start": "Start", "pause": "Pause", "reset": "Reset",
        "sessions_done": "sessions completed today",
        "pomodoro_hint": "25 minutes of deep focus, then a 5-minute break. After 4 rounds, take a long break.",
        "new_habit": "New habit", "add_habit": "Add habit",
        "empty_habits": "No habits yet — add something small like “Read 10 minutes”.",
        "done_today": "Done today ✓", "mark_today": "Mark today",
        "last_7": "Last 7 days",
        "new_note": "New note", "note_title": "Title", "note_content": "Write here…",
        "empty_notes": "No notes yet — your private space for thoughts and ideas.",
        "exams": "Exam countdown", "exam_subject": "Subject", "exam_date": "Exam date",
        "exam_note": "Note (room, chapters…)", "add_exam": "Add exam",
        "days_left": "days left", "exam_today": "TODAY!", "exam_tomorrow": "Tomorrow!",
        "exam_passed": "finished",
        "empty_exams": "No exams added — add one and watch the countdown.",
        "timetable": "Class timetable", "day": "Day", "start_t": "Starts",
        "end_t": "Ends", "room": "Room", "add_class": "Add class",
        "empty_day": "—",
        "day_sat": "Saturday", "day_sun": "Sunday", "day_mon": "Monday",
        "day_tue": "Tuesday", "day_wed": "Wednesday", "day_thu": "Thursday",
        "day_fri": "Friday",
        "flashcards": "Flashcards", "fc_subject": "Subject",
        "fc_question": "Question (front)", "fc_answer": "Answer (back)",
        "add_card": "Add card", "study": "Study", "show_answer": "Show answer",
        "next_card": "Next", "prev_card": "Previous", "card": "Card",
        "empty_cards": "No flashcards yet — great for memorizing definitions and formulas.",
        "gpa_calc": "GPA calculator", "course": "Course", "credits": "Credits",
        "grade": "Grade", "add_row": "Add course", "your_gpa": "Your GPA",
        "gpa_hint": "4.0 scale · nothing is saved, it's a quick calculator",
        "theme_toggle": "Light / dark", "my_color": "My color",
        "celebrate_high": "Excellent! You finished something important! 🎉",
        "celebrate_normal": "Nice work! One step closer. ✨",
    },
    "ar": {
        "focus": "تركيز", "habits": "العادات", "notes": "ملاحظات",
        "university": "الجامعة",
        "streak": "يوم متواصل", "streak_msg": "استمر — لا تكسر السلسلة!",
        "focus_time": "تركيز", "break_time": "استراحة", "long_break": "استراحة طويلة",
        "start": "ابدأ", "pause": "إيقاف", "reset": "إعادة",
        "sessions_done": "جلسات اليوم",
        "pomodoro_hint": "٢٥ دقيقة تركيز عميق ثم ٥ دقائق استراحة. بعد ٤ جولات خذ استراحة طويلة.",
        "new_habit": "عادة جديدة", "add_habit": "إضافة عادة",
        "empty_habits": "لا عادات بعد — أضف شيئًا صغيرًا مثل «اقرأ ١٠ دقائق».",
        "done_today": "تم اليوم ✓", "mark_today": "سجّل اليوم",
        "last_7": "آخر ٧ أيام",
        "new_note": "ملاحظة جديدة", "note_title": "العنوان", "note_content": "اكتب هنا…",
        "empty_notes": "لا ملاحظات بعد — مساحتك الخاصة للأفكار.",
        "exams": "عدّاد الامتحانات", "exam_subject": "المادة", "exam_date": "تاريخ الامتحان",
        "exam_note": "ملاحظة (القاعة، الفصول…)", "add_exam": "إضافة امتحان",
        "days_left": "يوم متبقٍ", "exam_today": "اليوم!", "exam_tomorrow": "غدًا!",
        "exam_passed": "انتهى",
        "empty_exams": "لا امتحانات — أضف امتحانًا وراقب العدّاد.",
        "timetable": "جدول المحاضرات", "day": "اليوم", "start_t": "يبدأ",
        "end_t": "ينتهي", "room": "القاعة", "add_class": "إضافة محاضرة",
        "empty_day": "—",
        "day_sat": "السبت", "day_sun": "الأحد", "day_mon": "الاثنين",
        "day_tue": "الثلاثاء", "day_wed": "الأربعاء", "day_thu": "الخميس",
        "day_fri": "الجمعة",
        "flashcards": "بطاقات المراجعة", "fc_subject": "المادة",
        "fc_question": "السؤال (الوجه)", "fc_answer": "الجواب (الظهر)",
        "add_card": "إضافة بطاقة", "study": "مراجعة", "show_answer": "أظهر الجواب",
        "next_card": "التالي", "prev_card": "السابق", "card": "بطاقة",
        "empty_cards": "لا بطاقات بعد — ممتازة لحفظ التعاريف والقوانين.",
        "gpa_calc": "حاسبة المعدل", "course": "المادة", "credits": "الوحدات",
        "grade": "الدرجة", "add_row": "إضافة مادة", "your_gpa": "معدلك",
        "gpa_hint": "مقياس ٤٫٠ · لا يُحفظ شيء، حاسبة سريعة فقط",
        "theme_toggle": "فاتح / داكن", "my_color": "لوني",
        "celebrate_high": "ممتاز! أنجزت شيئًا مهمًا! 🎉",
        "celebrate_normal": "أحسنت! خطوة أقرب. ✨",
    },
    "ku": {
        "focus": "سەرنج", "habits": "خووەکان", "notes": "تێبینییەکان",
        "university": "زانکۆ",
        "streak": "ڕۆژی بەردەوام", "streak_msg": "بەردەوام بە — زنجیرەکە مەپچڕێنە!",
        "focus_time": "سەرنج", "break_time": "پشوو", "long_break": "پشووی درێژ",
        "start": "دەستپێکردن", "pause": "وەستان", "reset": "ڕێکخستنەوە",
        "sessions_done": "دانیشتنی ئەمڕۆ",
        "pomodoro_hint": "٢٥ خولەک سەرنجی قووڵ، پاشان ٥ خولەک پشوو. دوای ٤ جار پشووی درێژ وەربگرە.",
        "new_habit": "خووی نوێ", "add_habit": "زیادکردنی خوو",
        "empty_habits": "هێشتا خوو نییە — شتێکی بچووک زیاد بکە وەک «١٠ خولەک خوێندنەوە».",
        "done_today": "ئەمڕۆ تەواو ✓", "mark_today": "ئەمڕۆ تۆمار بکە",
        "last_7": "دوایین ٧ ڕۆژ",
        "new_note": "تێبینی نوێ", "note_title": "ناونیشان", "note_content": "لێرە بنووسە…",
        "empty_notes": "هێشتا تێبینی نییە — شوێنی تایبەتی خۆت بۆ بیرۆکەکان.",
        "exams": "ژمێرەری تاقیکردنەوەکان", "exam_subject": "بابەت",
        "exam_date": "بەرواری تاقیکردنەوە",
        "exam_note": "تێبینی (هۆڵ، بەشەکان…)", "add_exam": "زیادکردنی تاقیکردنەوە",
        "days_left": "ڕۆژ ماوە", "exam_today": "ئەمڕۆیە!", "exam_tomorrow": "سبەینێیە!",
        "exam_passed": "تەواو بوو",
        "empty_exams": "تاقیکردنەوە نییە — یەکێک زیاد بکە و ژمێرەرەکە ببینە.",
        "timetable": "خشتەی وانەکان", "day": "ڕۆژ", "start_t": "دەست پێدەکات",
        "end_t": "تەواو دەبێت", "room": "هۆڵ", "add_class": "زیادکردنی وانە",
        "empty_day": "—",
        "day_sat": "شەممە", "day_sun": "یەکشەممە", "day_mon": "دووشەممە",
        "day_tue": "سێشەممە", "day_wed": "چوارشەممە", "day_thu": "پێنجشەممە",
        "day_fri": "هەینی",
        "flashcards": "کارتی بیرهێنانەوە", "fc_subject": "بابەت",
        "fc_question": "پرسیار (ڕوو)", "fc_answer": "وەڵام (پشت)",
        "add_card": "زیادکردنی کارت", "study": "خوێندن", "show_answer": "وەڵام پیشان بدە",
        "next_card": "دواتر", "prev_card": "پێشتر", "card": "کارت",
        "empty_cards": "هێشتا کارت نییە — زۆر باشە بۆ لەبەرکردنی پێناسە و یاساکان.",
        "gpa_calc": "ژمێرەری تێکڕا (GPA)", "course": "بابەت", "credits": "یەکەکان",
        "grade": "نمرە", "add_row": "زیادکردنی بابەت", "your_gpa": "تێکڕاکەت",
        "gpa_hint": "پێوانەی ٤٫٠ · هیچ پاشەکەوت ناکرێت، تەنها ژمێرەرێکی خێرایە",
        "theme_toggle": "ڕووناک / تاریک", "my_color": "ڕەنگەکەم",
        "celebrate_high": "نایاب! شتێکی گرنگت تەواو کرد! 🎉",
        "celebrate_normal": "ئافەرین! هەنگاوێک نزیکتر. ✨",
    },
}
for _l, _d in EXTRA.items():
    T[_l].update(_d)

# --- v3 social strings ---
SOCIAL = {
    "en": {
        "friends": "Friends", "groups": "Groups",
        "add_friend": "Add a friend", "friend_hint": "Type their exact username",
        "send_request": "Send request", "incoming_requests": "Friend requests",
        "sent_requests": "Sent requests", "accept": "Accept", "decline": "Decline",
        "cancel": "Cancel", "remove_friend": "Remove",
        "no_friends": "No friends yet — ask them for their username and add them!",
        "err_user_not_found": "No user with that username.",
        "err_self_friend": "That's you! Add someone else.",
        "err_already_friends": "You are already friends.",
        "err_request_pending": "A request already exists between you two.",
        "ok_request_sent": "Request sent!",
        "my_groups": "My groups", "create_group": "Create a group",
        "group_name": "Group name", "group_desc": "What is this group about? (optional)",
        "frequency": "Meets", "weekly": "Every week", "biweekly": "Every 2 weeks",
        "monthly": "Every month", "first_meeting": "First meeting date",
        "next_meeting": "Next meeting", "members": "Members", "owner": "Owner",
        "add_member": "Add a friend to the group", "add_to_group": "Add",
        "only_friends_hint": "You can only add your own friends",
        "no_addable": "All your friends are already here (or add friends first).",
        "ok_member_added": "Added to the group!",
        "discussion": "Discussion", "write_message": "Write a message…", "send": "Send",
        "no_messages": "No messages yet — say hello! 👋",
        "group_plans": "Group plans", "gplan_title": "What is the plan?",
        "gplan_date": "For which date?", "add_gplan": "Add group plan",
        "pin": "Pin", "unpin": "Unpin", "pinned": "PINNED",
        "no_gplans": "No group plans yet — pin what your group should do next.",
        "by": "by", "leave_group": "Leave group", "delete_group": "Delete group",
        "confirm_leave": "Leave this group?",
        "confirm_del_group": "Delete this group with all its messages and plans?",
        "group_settings": "Group settings", "no_groups": "No groups yet — create one with your friends!",
        "days_short": "days", "meeting_today": "Meeting is TODAY!",
    },
    "ar": {
        "friends": "الأصدقاء", "groups": "المجموعات",
        "add_friend": "أضف صديقًا", "friend_hint": "اكتب اسم المستخدم بدقة",
        "send_request": "إرسال طلب", "incoming_requests": "طلبات الصداقة",
        "sent_requests": "الطلبات المرسلة", "accept": "قبول", "decline": "رفض",
        "cancel": "إلغاء", "remove_friend": "إزالة",
        "no_friends": "لا أصدقاء بعد — اطلب اسم المستخدم منهم وأضفهم!",
        "err_user_not_found": "لا يوجد مستخدم بهذا الاسم.",
        "err_self_friend": "هذا أنت! أضف شخصًا آخر.",
        "err_already_friends": "أنتما صديقان بالفعل.",
        "err_request_pending": "يوجد طلب بينكما بالفعل.",
        "ok_request_sent": "تم إرسال الطلب!",
        "my_groups": "مجموعاتي", "create_group": "إنشاء مجموعة",
        "group_name": "اسم المجموعة", "group_desc": "ما موضوع هذه المجموعة؟ (اختياري)",
        "frequency": "الاجتماع", "weekly": "كل أسبوع", "biweekly": "كل أسبوعين",
        "monthly": "كل شهر", "first_meeting": "تاريخ أول اجتماع",
        "next_meeting": "الاجتماع القادم", "members": "الأعضاء", "owner": "المالك",
        "add_member": "أضف صديقًا إلى المجموعة", "add_to_group": "إضافة",
        "only_friends_hint": "يمكنك إضافة أصدقائك فقط",
        "no_addable": "كل أصدقائك هنا بالفعل (أو أضف أصدقاء أولًا).",
        "ok_member_added": "تمت الإضافة إلى المجموعة!",
        "discussion": "النقاش", "write_message": "اكتب رسالة…", "send": "إرسال",
        "no_messages": "لا رسائل بعد — قل مرحبًا! 👋",
        "group_plans": "خطط المجموعة", "gplan_title": "ما الخطة؟",
        "gplan_date": "لأي تاريخ؟", "add_gplan": "إضافة خطة للمجموعة",
        "pin": "تثبيت", "unpin": "إلغاء التثبيت", "pinned": "مثبّت",
        "no_gplans": "لا خطط بعد — ثبّت ما يجب أن تفعله مجموعتك.",
        "by": "بواسطة", "leave_group": "مغادرة المجموعة", "delete_group": "حذف المجموعة",
        "confirm_leave": "مغادرة هذه المجموعة؟",
        "confirm_del_group": "حذف هذه المجموعة مع كل رسائلها وخططها؟",
        "group_settings": "إعدادات المجموعة", "no_groups": "لا مجموعات بعد — أنشئ واحدة مع أصدقائك!",
        "days_short": "يوم", "meeting_today": "الاجتماع اليوم!",
    },
    "ku": {
        "friends": "هاوڕێکان", "groups": "گروپەکان",
        "add_friend": "هاوڕێیەک زیاد بکە", "friend_hint": "ناوی بەکارهێنەرەکەی بە وردی بنووسە",
        "send_request": "ناردنی داواکاری", "incoming_requests": "داواکارییەکانی هاوڕێیەتی",
        "sent_requests": "داواکارییە نێردراوەکان", "accept": "وەرگرتن", "decline": "ڕەتکردنەوە",
        "cancel": "هەڵوەشاندنەوە", "remove_friend": "لابردن",
        "no_friends": "هێشتا هاوڕێت نییە — ناوی بەکارهێنەرەکەیان لێ بپرسە و زیادیان بکە!",
        "err_user_not_found": "هیچ بەکارهێنەرێک بەم ناوە نییە.",
        "err_self_friend": "ئەوە تۆیت! کەسێکی تر زیاد بکە.",
        "err_already_friends": "ئێوە پێشتر هاوڕێن.",
        "err_request_pending": "پێشتر داواکارییەک لە نێوانتاندا هەیە.",
        "ok_request_sent": "داواکارییەکە نێردرا!",
        "my_groups": "گروپەکانم", "create_group": "دروستکردنی گروپ",
        "group_name": "ناوی گروپ", "group_desc": "ئەم گروپە دەربارەی چییە؟ (ئارەزوومەندانە)",
        "frequency": "کۆبوونەوە", "weekly": "هەموو هەفتەیەک", "biweekly": "هەر دوو هەفتە",
        "monthly": "هەموو مانگێک", "first_meeting": "بەرواری یەکەم کۆبوونەوە",
        "next_meeting": "کۆبوونەوەی داهاتوو", "members": "ئەندامان", "owner": "خاوەن",
        "add_member": "هاوڕێیەک بۆ گروپەکە زیاد بکە", "add_to_group": "زیادکردن",
        "only_friends_hint": "تەنها دەتوانیت هاوڕێکانی خۆت زیاد بکەیت",
        "no_addable": "هەموو هاوڕێکانت لێرەن (یان سەرەتا هاوڕێ زیاد بکە).",
        "ok_member_added": "بۆ گروپەکە زیادکرا!",
        "discussion": "گفتوگۆ", "write_message": "نامەیەک بنووسە…", "send": "ناردن",
        "no_messages": "هێشتا نامە نییە — سڵاو بکە! 👋",
        "group_plans": "پلانەکانی گروپ", "gplan_title": "پلانەکە چییە؟",
        "gplan_date": "بۆ چ بەروارێک؟", "add_gplan": "زیادکردنی پلانی گروپ",
        "pin": "چەسپاندن", "unpin": "لابردنی چەسپاندن", "pinned": "چەسپێنراو",
        "no_gplans": "هێشتا پلانی گروپ نییە — ئەوە بچەسپێنە کە گروپەکەت دەبێت بیکات.",
        "by": "لەلایەن", "leave_group": "جێهێشتنی گروپ", "delete_group": "سڕینەوەی گروپ",
        "confirm_leave": "ئەم گروپە جێبهێڵیت؟",
        "confirm_del_group": "ئەم گروپە بسڕدرێتەوە لەگەڵ هەموو نامە و پلانەکانی؟",
        "group_settings": "ڕێکخستنەکانی گروپ", "no_groups": "هێشتا گروپ نییە — لەگەڵ هاوڕێکانت یەکێک دروست بکە!",
        "days_short": "ڕۆژ", "meeting_today": "کۆبوونەوەکە ئەمڕۆیە!",
    },
}
for _l, _d in SOCIAL.items():
    T[_l].update(_d)

# --- v4 profile strings ---
PROFILE = {
    "en": {
        "profile": "My profile", "edit_profile": "Edit profile",
        "full_name": "Full name", "email_addr": "Email", "bio": "About me",
        "photo": "Profile photo", "upload_hint": "PNG, JPG, WEBP or GIF — up to 3 MB",
        "search_people": "Find people", "search": "Search",
        "search_hint": "Search by username or full name",
        "no_results": "No one found with that name.",
        "joined": "Joined", "view_profile": "View profile",
        "pw_keep": "New password (leave empty to keep your current one)",
        "friends_ok": "Friends ✓", "pending_dots": "Request pending…",
        "completed_plans": "plans completed",
        "ok_profile": "Profile updated!",
        "err_photo_type": "Photo must be PNG, JPG, WEBP or GIF.",
        "optional": "optional",
    },
    "ar": {
        "profile": "ملفي الشخصي", "edit_profile": "تعديل الملف الشخصي",
        "full_name": "الاسم الكامل", "email_addr": "البريد الإلكتروني", "bio": "نبذة عني",
        "photo": "الصورة الشخصية", "upload_hint": "PNG أو JPG أو WEBP أو GIF — حتى ٣ م.ب",
        "search_people": "ابحث عن أشخاص", "search": "بحث",
        "search_hint": "ابحث باسم المستخدم أو الاسم الكامل",
        "no_results": "لم يُعثر على أحد بهذا الاسم.",
        "joined": "انضم في", "view_profile": "عرض الملف",
        "pw_keep": "كلمة مرور جديدة (اتركها فارغة للاحتفاظ بالحالية)",
        "friends_ok": "أصدقاء ✓", "pending_dots": "الطلب قيد الانتظار…",
        "completed_plans": "خطة منجزة",
        "ok_profile": "تم تحديث الملف!",
        "err_photo_type": "الصورة يجب أن تكون PNG أو JPG أو WEBP أو GIF.",
        "optional": "اختياري",
    },
    "ku": {
        "profile": "پرۆفایلەکەم", "edit_profile": "دەستکاری پرۆفایل",
        "full_name": "ناوی تەواو", "email_addr": "ئیمەیڵ", "bio": "دەربارەم",
        "photo": "وێنەی پرۆفایل", "upload_hint": "PNG یان JPG یان WEBP یان GIF — تا ٣ م.ب",
        "search_people": "گەڕان بۆ کەسان", "search": "گەڕان",
        "search_hint": "بە ناوی بەکارهێنەر یان ناوی تەواو بگەڕێ",
        "no_results": "کەس بەم ناوە نەدۆزرایەوە.",
        "joined": "بەشداربوو لە", "view_profile": "بینینی پرۆفایل",
        "pw_keep": "وشەی نهێنی نوێ (بەتاڵی بهێڵەوە بۆ هێشتنەوەی ئێستا)",
        "friends_ok": "هاوڕێن ✓", "pending_dots": "داواکاری چاوەڕوانە…",
        "completed_plans": "پلانی تەواوکراو",
        "ok_profile": "پرۆفایل نوێکرایەوە!",
        "err_photo_type": "وێنەکە دەبێت PNG یان JPG یان WEBP یان GIF بێت.",
        "optional": "ئارەزوومەندانە",
    },
}
for _l, _d in PROFILE.items():
    T[_l].update(_d)

# --- v5 strings: notifications, DMs, leaderboard, badges, files, decks ---
V5 = {
    "en": {
        "notifications": "Notifications", "no_notifications": "You're all caught up! 🎉",
        "reminders": "Reminders",
        "ntf_friend_req": "sent you a friend request",
        "ntf_friend_acc": "accepted your friend request",
        "ntf_follow": "started following you",
        "ntf_group_msg": "New messages in", "ntf_group_add": "You were added to",
        "ntf_badge": "You earned a badge:", "ntf_dm": "New message from",
        "ntf_exam": "Exam coming up:", "ntf_meeting": "Group meeting soon:",
        "messages_t": "Messages", "no_convos": "No conversations yet — message a friend!",
        "open_chat": "Open chat", "leaderboard": "Leaderboard",
        "this_week": "This week", "points": "points",
        "lb_hint": "10 pts per completed plan · 5 pts per habit check · 3 pts per streak day · resets every Monday",
        "badges_t": "Badges",
        "badge_first_plan_n": "First Step", "badge_first_plan_d": "Complete your first plan",
        "badge_plans_10_n": "Doer", "badge_plans_10_d": "Complete 10 plans",
        "badge_plans_50_n": "Champion", "badge_plans_50_d": "Complete 50 plans",
        "badge_streak_7_n": "On Fire", "badge_streak_7_d": "7-day streak",
        "badge_streak_30_n": "Unstoppable", "badge_streak_30_d": "30-day streak",
        "badge_first_group_n": "Team Builder", "badge_first_group_d": "Create a group",
        "badge_friends_5_n": "Social Butterfly", "badge_friends_5_d": "Make 5 friends",
        "badge_habit_7_n": "Consistent", "badge_habit_7_d": "7-day habit streak",
        "group_files": "Shared files", "upload_file": "Upload",
        "file_hint": "PDF, images, documents, zip — up to 10 MB",
        "download": "Download", "err_file_type": "That file type is not allowed.",
        "shared_decks": "Shared flashcard decks", "share_deck": "Share one of my decks",
        "share": "Share", "unshare": "Unshare", "no_decks_own": "Create flashcards in University first.",
        "no_shared_decks": "No shared decks yet — share yours so the group can study together.",
        "cards_n": "cards", "activity_today": "Friends' activity today",
        "no_activity": "No friend activity yet today — be the first! 💪",
        "f_plans": "plans", "f_habits": "habits",
    },
    "ar": {
        "notifications": "الإشعارات", "no_notifications": "لا جديد لديك! 🎉",
        "reminders": "تذكيرات",
        "ntf_friend_req": "أرسل لك طلب صداقة",
        "ntf_friend_acc": "قبل طلب صداقتك",
        "ntf_follow": "بدأ بمتابعتك",
        "ntf_group_msg": "رسائل جديدة في", "ntf_group_add": "تمت إضافتك إلى",
        "ntf_badge": "حصلت على وسام:", "ntf_dm": "رسالة جديدة من",
        "ntf_exam": "امتحان قريب:", "ntf_meeting": "اجتماع المجموعة قريبًا:",
        "messages_t": "الرسائل", "no_convos": "لا محادثات بعد — راسل صديقًا!",
        "open_chat": "افتح المحادثة", "leaderboard": "لوحة الصدارة",
        "this_week": "هذا الأسبوع", "points": "نقطة",
        "lb_hint": "١٠ نقاط لكل خطة منجزة · ٥ لكل عادة · ٣ لكل يوم متواصل · تُصفَّر كل اثنين",
        "badges_t": "الأوسمة",
        "badge_first_plan_n": "الخطوة الأولى", "badge_first_plan_d": "أنجز أول خطة",
        "badge_plans_10_n": "منجز", "badge_plans_10_d": "أنجز ١٠ خطط",
        "badge_plans_50_n": "بطل", "badge_plans_50_d": "أنجز ٥٠ خطة",
        "badge_streak_7_n": "مشتعل", "badge_streak_7_d": "٧ أيام متواصلة",
        "badge_streak_30_n": "لا يُوقف", "badge_streak_30_d": "٣٠ يومًا متواصلًا",
        "badge_first_group_n": "باني الفريق", "badge_first_group_d": "أنشئ مجموعة",
        "badge_friends_5_n": "اجتماعي", "badge_friends_5_d": "كوّن ٥ صداقات",
        "badge_habit_7_n": "منضبط", "badge_habit_7_d": "عادة لمدة ٧ أيام",
        "group_files": "الملفات المشتركة", "upload_file": "رفع",
        "file_hint": "PDF وصور ومستندات وzip — حتى ١٠ م.ب",
        "download": "تنزيل", "err_file_type": "نوع الملف غير مسموح.",
        "shared_decks": "بطاقات مشتركة", "share_deck": "شارك إحدى مجموعاتي",
        "share": "مشاركة", "unshare": "إلغاء المشاركة", "no_decks_own": "أنشئ بطاقات في صفحة الجامعة أولًا.",
        "no_shared_decks": "لا بطاقات مشتركة بعد — شارك بطاقاتك ليدرس الجميع معًا.",
        "cards_n": "بطاقة", "activity_today": "نشاط الأصدقاء اليوم",
        "no_activity": "لا نشاط للأصدقاء اليوم — كن الأول! 💪",
        "f_plans": "خطط", "f_habits": "عادات",
    },
    "ku": {
        "notifications": "ئاگادارکردنەوەکان", "no_notifications": "هیچ نوێ نییە! 🎉",
        "reminders": "بیرخستنەوەکان",
        "ntf_friend_req": "داواکاری هاوڕێیەتی بۆ ناردیت",
        "ntf_friend_acc": "داواکاری هاوڕێیەتیەکەتی وەرگرت",
        "ntf_follow": "دەستی کرد بە فۆڵۆکردنت",
        "ntf_group_msg": "نامەی نوێ لە", "ntf_group_add": "زیادکرایت بۆ",
        "ntf_badge": "خەڵاتێکت بەدەست هێنا:", "ntf_dm": "نامەی نوێ لە",
        "ntf_exam": "تاقیکردنەوە نزیکە:", "ntf_meeting": "کۆبوونەوەی گروپ نزیکە:",
        "messages_t": "نامەکان", "no_convos": "هێشتا گفتوگۆ نییە — نامە بۆ هاوڕێیەک بنێرە!",
        "open_chat": "کردنەوەی گفتوگۆ", "leaderboard": "خشتەی پێشەنگەکان",
        "this_week": "ئەم هەفتەیە", "points": "خاڵ",
        "lb_hint": "١٠ خاڵ بۆ هەر پلانێکی تەواو · ٥ بۆ هەر خووێک · ٣ بۆ هەر ڕۆژی بەردەوام · هەموو دووشەممەیەک سفر دەبێتەوە",
        "badges_t": "خەڵاتەکان",
        "badge_first_plan_n": "یەکەم هەنگاو", "badge_first_plan_d": "یەکەم پلانت تەواو بکە",
        "badge_plans_10_n": "کارامە", "badge_plans_10_d": "١٠ پلان تەواو بکە",
        "badge_plans_50_n": "پاڵەوان", "badge_plans_50_d": "٥٠ پلان تەواو بکە",
        "badge_streak_7_n": "گڕگرتوو", "badge_streak_7_d": "٧ ڕۆژی بەردەوام",
        "badge_streak_30_n": "ڕاناوەستێت", "badge_streak_30_d": "٣٠ ڕۆژی بەردەوام",
        "badge_first_group_n": "دروستکەری تیم", "badge_first_group_d": "گروپێک دروست بکە",
        "badge_friends_5_n": "کۆمەڵایەتی", "badge_friends_5_d": "٥ هاوڕێ پەیدا بکە",
        "badge_habit_7_n": "بەردەوام", "badge_habit_7_d": "خووێک بۆ ٧ ڕۆژ",
        "group_files": "فایلە هاوبەشەکان", "upload_file": "بارکردن",
        "file_hint": "PDF و وێنە و بەڵگەنامە و zip — تا ١٠ م.ب",
        "download": "داگرتن", "err_file_type": "ئەم جۆرە فایلە ڕێگەپێدراو نییە.",
        "shared_decks": "کارتە هاوبەشەکان", "share_deck": "یەکێک لە کۆمەڵەکانم هاوبەش بکە",
        "share": "هاوبەشکردن", "unshare": "لابردنی هاوبەشی", "no_decks_own": "سەرەتا لە پەڕەی زانکۆ کارت دروست بکە.",
        "no_shared_decks": "هێشتا کۆمەڵەی هاوبەش نییە — کۆمەڵەکانت هاوبەش بکە بۆ ئەوەی گروپەکە پێکەوە بخوێنن.",
        "cards_n": "کارت", "activity_today": "چالاکی هاوڕێکان ئەمڕۆ",
        "no_activity": "هێشتا چالاکی هاوڕێ نییە ئەمڕۆ — تۆ یەکەم بە! 💪",
        "f_plans": "پلان", "f_habits": "خوو",
    },
}
for _l, _d in V5.items():
    T[_l].update(_d)

# --- v6 navigation strings ---
NAV = {
    "en": {"home": "Home", "planning": "Planning", "social": "Social",
           "menu": "Menu", "account": "Account", "language": "Language",
           "created_by": "Created by"},
    "ar": {"home": "الرئيسية", "planning": "التخطيط", "social": "اجتماعي",
           "menu": "القائمة", "account": "الحساب", "language": "اللغة",
           "created_by": "صُنع بواسطة"},
    "ku": {"home": "سەرەکی", "planning": "پلاندانان", "social": "کۆمەڵایەتی",
           "menu": "لیستە", "account": "هەژمار", "language": "زمان",
           "created_by": "دروستکراوە لەلایەن"},
}
for _l, _d in NAV.items():
    T[_l].update(_d)

# --- v7 tools strings ---
TOOLS = {
    "en": {
        "tools": "Tools", "poster_maker": "Poster maker", "cv_builder": "CV builder",
        "essay_checker": "Essay checker", "citations_t": "Citations", "quiz_mode": "Quiz",
        "ai_assistant": "AI Assistant",
        "p_title": "Title", "p_subtitle": "Subtitle", "p_body": "Text (one line per row)",
        "p_emoji": "Big emoji", "p_template": "Style", "p_download": "Download PNG",
        "cv_title": "Field / job title", "cv_contact": "Contact (email · city)",
        "cv_summary": "Short summary about you",
        "cv_education": "Education (one per line: years — degree, place)",
        "cv_experience": "Experience / activities (one per line)",
        "cv_skills": "Skills (comma separated)", "cv_langs": "Languages (comma separated)",
        "cv_print": "Print / Save as PDF",
        "e_paste": "Paste your essay here…", "e_analyze": "Analyze",
        "e_words": "words", "e_sentences": "sentences", "e_paragraphs": "paragraphs",
        "e_avg": "avg words / sentence", "e_long": "Very long sentences (split them)",
        "e_rep": "Most repeated words", "e_tips": "Tips",
        "tip_long": "Some sentences are very long — split them for clarity.",
        "tip_para": "Very few paragraphs — break your essay into more paragraphs.",
        "tip_rep": "You repeat some words a lot — try synonyms.",
        "tip_short": "The essay is quite short — develop your ideas further.",
        "tip_good": "Structure looks good — nice balance! ✓",
        "c_type": "Source type", "c_book": "Book", "c_web": "Website", "c_journal": "Journal article",
        "c_author": "Author(s) (Family, F.)", "c_year": "Year", "c_title2": "Title",
        "c_publisher": "Publisher", "c_url": "URL", "c_site": "Website name",
        "c_journal_n": "Journal name", "c_pages": "Pages", "c_generate": "Generate",
        "c_copy": "Copy", "c_copied": "Copied!",
        "q_start": "Start quiz", "q_need": "You need at least 4 flashcards to build a quiz — create some in University → Flashcards.",
        "q_score": "Your score", "q_restart": "Try again", "q_correct": "Correct! 🎉",
        "q_wrong": "Wrong — the answer was:", "q_of": "of",
        "ai_task": "What should the AI do?", "ai_rate": "Rate my essay (score + feedback)",
        "ai_sum": "Summarize this text", "ai_explain": "Explain this simply",
        "ai_improve": "Improve my writing", "ai_input": "Paste your text or question…",
        "ai_run": "Ask AI", "ai_result": "Result",
        "ai_not_conf": "AI is not configured yet. The site admin must add an API key in the Admin Panel → Site settings.",
        "ai_key": "AI API key (Anthropic)", "ai_model": "AI model",
        "ai_key_hint": "Get a key at console.anthropic.com — paid per use",
        "ai_err": "AI error:",
    },
    "ar": {
        "tools": "الأدوات", "poster_maker": "صانع الملصقات", "cv_builder": "منشئ السيرة الذاتية",
        "essay_checker": "مدقق المقالات", "citations_t": "المراجع", "quiz_mode": "اختبار",
        "ai_assistant": "المساعد الذكي",
        "p_title": "العنوان", "p_subtitle": "العنوان الفرعي", "p_body": "النص (سطر لكل فقرة)",
        "p_emoji": "إيموجي كبير", "p_template": "النمط", "p_download": "تنزيل PNG",
        "cv_title": "المجال / المسمى الوظيفي", "cv_contact": "التواصل (بريد · مدينة)",
        "cv_summary": "نبذة قصيرة عنك",
        "cv_education": "التعليم (سطر لكل شهادة: السنوات — الشهادة، المكان)",
        "cv_experience": "الخبرات / الأنشطة (سطر لكل واحدة)",
        "cv_skills": "المهارات (مفصولة بفواصل)", "cv_langs": "اللغات (مفصولة بفواصل)",
        "cv_print": "طباعة / حفظ PDF",
        "e_paste": "الصق مقالك هنا…", "e_analyze": "تحليل",
        "e_words": "كلمة", "e_sentences": "جملة", "e_paragraphs": "فقرة",
        "e_avg": "متوسط الكلمات / جملة", "e_long": "جمل طويلة جدًا (قسّمها)",
        "e_rep": "أكثر الكلمات تكرارًا", "e_tips": "نصائح",
        "tip_long": "بعض الجمل طويلة جدًا — قسّمها للوضوح.",
        "tip_para": "فقرات قليلة جدًا — قسّم مقالك إلى فقرات أكثر.",
        "tip_rep": "تكرر بعض الكلمات كثيرًا — جرّب المرادفات.",
        "tip_short": "المقال قصير — طوّر أفكارك أكثر.",
        "tip_good": "البنية تبدو جيدة — توازن ممتاز! ✓",
        "c_type": "نوع المصدر", "c_book": "كتاب", "c_web": "موقع إلكتروني", "c_journal": "مقالة علمية",
        "c_author": "المؤلف(ون) (العائلة، الأول.)", "c_year": "السنة", "c_title2": "العنوان",
        "c_publisher": "الناشر", "c_url": "الرابط", "c_site": "اسم الموقع",
        "c_journal_n": "اسم المجلة", "c_pages": "الصفحات", "c_generate": "إنشاء",
        "c_copy": "نسخ", "c_copied": "تم النسخ!",
        "q_start": "ابدأ الاختبار", "q_need": "تحتاج ٤ بطاقات على الأقل لإنشاء اختبار — أنشئها في الجامعة ← البطاقات.",
        "q_score": "نتيجتك", "q_restart": "حاول مجددًا", "q_correct": "صحيح! 🎉",
        "q_wrong": "خطأ — الجواب كان:", "q_of": "من",
        "ai_task": "ماذا يفعل الذكاء الاصطناعي؟", "ai_rate": "قيّم مقالي (درجة + ملاحظات)",
        "ai_sum": "لخّص هذا النص", "ai_explain": "اشرح هذا ببساطة",
        "ai_improve": "حسّن كتابتي", "ai_input": "الصق نصك أو سؤالك…",
        "ai_run": "اسأل الذكاء الاصطناعي", "ai_result": "النتيجة",
        "ai_not_conf": "الذكاء الاصطناعي غير مفعّل بعد. على مدير الموقع إضافة مفتاح API في لوحة الإدارة ← إعدادات الموقع.",
        "ai_key": "مفتاح API للذكاء الاصطناعي (Anthropic)", "ai_model": "نموذج الذكاء الاصطناعي",
        "ai_key_hint": "احصل على مفتاح من console.anthropic.com — مدفوع حسب الاستخدام",
        "ai_err": "خطأ الذكاء الاصطناعي:",
    },
    "ku": {
        "tools": "ئامرازەکان", "poster_maker": "دروستکەری پۆستەر", "cv_builder": "دروستکەری سیڤی",
        "essay_checker": "پشکنەری وتار", "citations_t": "سەرچاوەکان", "quiz_mode": "تاقیکردنەوە",
        "ai_assistant": "یاریدەدەری زیرەک",
        "p_title": "ناونیشان", "p_subtitle": "ژێرناونیشان", "p_body": "دەق (هێڵێک بۆ هەر دێڕێک)",
        "p_emoji": "ئیمۆجی گەورە", "p_template": "شێواز", "p_download": "داگرتنی PNG",
        "cv_title": "بوار / ناونیشانی کار", "cv_contact": "پەیوەندی (ئیمەیڵ · شار)",
        "cv_summary": "پوختەیەکی کورت دەربارەت",
        "cv_education": "خوێندن (هێڵێک بۆ هەر بڕوانامەیەک: ساڵەکان — بڕوانامە، شوێن)",
        "cv_experience": "ئەزموون / چالاکییەکان (هێڵێک بۆ هەر یەکێک)",
        "cv_skills": "لێهاتووییەکان (بە کۆما جیاکراوە)", "cv_langs": "زمانەکان (بە کۆما جیاکراوە)",
        "cv_print": "چاپکردن / پاشەکەوت وەک PDF",
        "e_paste": "وتارەکەت لێرە دابنێ…", "e_analyze": "شیکردنەوە",
        "e_words": "وشە", "e_sentences": "ڕستە", "e_paragraphs": "بڕگە",
        "e_avg": "تێکڕای وشە / ڕستە", "e_long": "ڕستە زۆر درێژەکان (دابەشیان بکە)",
        "e_rep": "زۆرترین وشەی دووبارەکراوە", "e_tips": "ئامۆژگارییەکان",
        "tip_long": "هەندێک ڕستە زۆر درێژن — بۆ ڕوونی دابەشیان بکە.",
        "tip_para": "بڕگە زۆر کەمن — وتارەکەت بکە بە بڕگەی زیاتر.",
        "tip_rep": "هەندێک وشە زۆر دووبارە دەکەیتەوە — هاوواتا تاقی بکەرەوە.",
        "tip_short": "وتارەکە کورتە — بیرۆکەکانت زیاتر گەشە پێ بدە.",
        "tip_good": "پێکهاتەکە باش دیارە — هاوسەنگییەکی جوان! ✓",
        "c_type": "جۆری سەرچاوە", "c_book": "کتێب", "c_web": "ماڵپەڕ", "c_journal": "وتاری زانستی",
        "c_author": "نووسەر(ان) (خێزان، یەکەم.)", "c_year": "ساڵ", "c_title2": "ناونیشان",
        "c_publisher": "بڵاوکەرەوە", "c_url": "بەستەر", "c_site": "ناوی ماڵپەڕ",
        "c_journal_n": "ناوی گۆڤار", "c_pages": "پەڕەکان", "c_generate": "دروستکردن",
        "c_copy": "کۆپی", "c_copied": "کۆپی کرا!",
        "q_start": "دەستپێکردنی تاقیکردنەوە", "q_need": "لانیکەم ٤ کارتت پێویستە بۆ تاقیکردنەوە — لە زانکۆ ← کارتەکان دروستیان بکە.",
        "q_score": "نمرەکەت", "q_restart": "دووبارە هەوڵ بدەرەوە", "q_correct": "ڕاستە! 🎉",
        "q_wrong": "هەڵەیە — وەڵامەکە ئەمە بوو:", "q_of": "لە",
        "ai_task": "زیرەکی دەستکرد چی بکات؟", "ai_rate": "وتارەکەم هەڵبسەنگێنە (نمرە + تێبینی)",
        "ai_sum": "ئەم دەقە پوخت بکەرەوە", "ai_explain": "ئەمە بە سادەیی ڕوون بکەرەوە",
        "ai_improve": "نووسینەکەم باشتر بکە", "ai_input": "دەق یان پرسیارەکەت لێرە دابنێ…",
        "ai_run": "پرسیار لە AI بکە", "ai_result": "ئەنجام",
        "ai_not_conf": "زیرەکی دەستکرد هێشتا ڕێکنەخراوە. بەڕێوەبەری ماڵپەڕ دەبێت کلیلی API زیاد بکات لە پانێڵی بەڕێوەبەر ← ڕێکخستنەکان.",
        "ai_key": "کلیلی API ی زیرەکی دەستکرد (Anthropic)", "ai_model": "مۆدێلی AI",
        "ai_key_hint": "کلیل لە console.anthropic.com وەربگرە — بەپێی بەکارهێنان پارەی دەوێت",
        "ai_err": "هەڵەی AI:",
    },
}
for _l, _d in TOOLS.items():
    T[_l].update(_d)

# --- v8 strings: coming soon, fonts, pro tools ---
V8 = {
    "en": {
        "coming_soon": "Coming soon 🔜",
        "coming_soon_sub": "This feature will be available very soon. Stay tuned!",
        "font": "Font", "my_fonts": "My fonts",
        "add_font": "Add your own font (TTF / OTF / WOFF)", "upload_font": "Add font",
        "err_font_type": "Font must be a TTF, OTF, WOFF or WOFF2 file.",
        "size": "Size", "s_a4": "A4 Poster", "s_sq": "Square (post)", "s_story": "Story (9:16)",
        "pattern": "Decoration", "pt_circles": "Circles", "pt_grid": "Grid",
        "pt_waves": "Waves", "pt_none": "Clean",
        "custom_colors": "Colors", "text_color": "Text color", "title_size": "Title size",
        "cv_template": "Template", "cvt_classic": "Classic", "cvt_modern": "Modern",
        "cvt_minimal": "Minimal",
        "e_score": "Writing score",
    },
    "ar": {
        "coming_soon": "قريبًا 🔜",
        "coming_soon_sub": "هذه الميزة ستتوفر قريبًا جدًا. ترقبوا!",
        "font": "الخط", "my_fonts": "خطوطي",
        "add_font": "أضف خطك الخاص (TTF / OTF / WOFF)", "upload_font": "إضافة خط",
        "err_font_type": "الخط يجب أن يكون ملف TTF أو OTF أو WOFF أو WOFF2.",
        "size": "الحجم", "s_a4": "ملصق A4", "s_sq": "مربع (منشور)", "s_story": "ستوري (9:16)",
        "pattern": "الزخرفة", "pt_circles": "دوائر", "pt_grid": "شبكة",
        "pt_waves": "أمواج", "pt_none": "بسيط",
        "custom_colors": "الألوان", "text_color": "لون النص", "title_size": "حجم العنوان",
        "cv_template": "القالب", "cvt_classic": "كلاسيكي", "cvt_modern": "عصري",
        "cvt_minimal": "بسيط",
        "e_score": "درجة الكتابة",
    },
    "ku": {
        "coming_soon": "بەم زووانە 🔜",
        "coming_soon_sub": "ئەم تایبەتمەندییە بەم زووانە بەردەست دەبێت. چاوەڕوان بن!",
        "font": "فۆنت", "my_fonts": "فۆنتەکانم",
        "add_font": "فۆنتی خۆت زیاد بکە (TTF / OTF / WOFF)", "upload_font": "زیادکردنی فۆنت",
        "err_font_type": "فۆنتەکە دەبێت فایلی TTF یان OTF یان WOFF یان WOFF2 بێت.",
        "size": "قەبارە", "s_a4": "پۆستەری A4", "s_sq": "چوارگۆشە (پۆست)", "s_story": "ستۆری (9:16)",
        "pattern": "ڕازاندنەوە", "pt_circles": "بازنەکان", "pt_grid": "تۆڕ",
        "pt_waves": "شەپۆلەکان", "pt_none": "سادە",
        "custom_colors": "ڕەنگەکان", "text_color": "ڕەنگی دەق", "title_size": "قەبارەی ناونیشان",
        "cv_template": "قاڵب", "cvt_classic": "کلاسیک", "cvt_modern": "مۆدێرن",
        "cvt_minimal": "سادە",
        "e_score": "نمرەی نووسین",
    },
}
for _l, _d in V8.items():
    T[_l].update(_d)

# --- v9 education/work strings ---
V9 = {
    "en": {
        "edu_info": "Education / Work", "i_am": "I am…",
        "lvl_school": "School student", "lvl_bachelor": "University student",
        "lvl_master": "Master's student", "lvl_phd": "PhD student",
        "lvl_professor": "Professor / Teacher", "lvl_graduate": "Graduated — working",
        "institution": "University / School", "college": "College / Faculty",
        "department": "Department", "stage": "Stage / Year",
        "job_title": "Job", "job_field": "Field",
        "prefer_not": "— prefer not to say —",
    },
    "ar": {
        "edu_info": "الدراسة / العمل", "i_am": "أنا…",
        "lvl_school": "طالب مدرسة", "lvl_bachelor": "طالب جامعة",
        "lvl_master": "طالب ماجستير", "lvl_phd": "طالب دكتوراه",
        "lvl_professor": "أستاذ / مدرّس", "lvl_graduate": "خريج — أعمل",
        "institution": "الجامعة / المدرسة", "college": "الكلية",
        "department": "القسم", "stage": "المرحلة / السنة",
        "job_title": "الوظيفة", "job_field": "المجال",
        "prefer_not": "— أفضّل عدم الذكر —",
    },
    "ku": {
        "edu_info": "خوێندن / کار", "i_am": "من…",
        "lvl_school": "قوتابی قوتابخانە", "lvl_bachelor": "قوتابی زانکۆ",
        "lvl_master": "قوتابی ماستەر", "lvl_phd": "قوتابی دکتۆرا",
        "lvl_professor": "مامۆستا / پرۆفیسۆر", "lvl_graduate": "دەرچوو — کار دەکەم",
        "institution": "زانکۆ / قوتابخانە", "college": "کۆلێژ",
        "department": "بەش", "stage": "قۆناغ / ساڵ",
        "job_title": "کار", "job_field": "بوار",
        "prefer_not": "— نامەوێت بیڵێم —",
    },
}
for _l, _d in V9.items():
    T[_l].update(_d)

# --- v10 motivation strings ---
V10 = {
    "en": {
        "reply": "Reply", "replying_to": "Replying to", "cancel_reply": "✕",
        "polls_t": "Polls", "new_poll": "New poll", "poll_q": "Question",
        "poll_opts": "Options (one per line)", "create": "Create",
        "votes_n": "votes", "close_poll": "Close", "reopen_poll": "Reopen",
        "closed_p": "Closed",
        "challenges_t": "Group challenges", "new_challenge": "New challenge",
        "ch_title": "Challenge title (e.g. Finish 25 plans together)",
        "ch_target": "Target (completed plans)", "ch_days": "Days",
        "ch_done": "Challenge completed! 🎉", "ch_by": "by",
        "ch_left": "days left", "ch_of": "of",
        "group_lb": "This week in this group",
        "level": "Level", "xp": "XP", "to_next": "to level",
        "daily_goal": "Daily goal", "today_t": "Today",
        "week_report": "This week", "vs_last": "vs last week",
    },
    "ar": {
        "reply": "رد", "replying_to": "رد على", "cancel_reply": "✕",
        "polls_t": "استطلاعات", "new_poll": "استطلاع جديد", "poll_q": "السؤال",
        "poll_opts": "الخيارات (سطر لكل خيار)", "create": "إنشاء",
        "votes_n": "صوت", "close_poll": "إغلاق", "reopen_poll": "إعادة فتح",
        "closed_p": "مغلق",
        "challenges_t": "تحديات المجموعة", "new_challenge": "تحدٍ جديد",
        "ch_title": "عنوان التحدي (مثال: ننجز ٢٥ خطة معًا)",
        "ch_target": "الهدف (خطط منجزة)", "ch_days": "الأيام",
        "ch_done": "اكتمل التحدي! 🎉", "ch_by": "بواسطة",
        "ch_left": "يوم متبقٍ", "ch_of": "من",
        "group_lb": "هذا الأسبوع في هذه المجموعة",
        "level": "المستوى", "xp": "نقاط الخبرة", "to_next": "إلى المستوى",
        "daily_goal": "الهدف اليومي", "today_t": "اليوم",
        "week_report": "هذا الأسبوع", "vs_last": "مقارنة بالأسبوع الماضي",
    },
    "ku": {
        "reply": "وەڵام", "replying_to": "وەڵام بۆ", "cancel_reply": "✕",
        "polls_t": "ڕاپرسییەکان", "new_poll": "ڕاپرسی نوێ", "poll_q": "پرسیار",
        "poll_opts": "بژاردەکان (هێڵێک بۆ هەر یەکێک)", "create": "دروستکردن",
        "votes_n": "دەنگ", "close_poll": "داخستن", "reopen_poll": "کردنەوە",
        "closed_p": "داخراوە",
        "challenges_t": "چالێنجەکانی گروپ", "new_challenge": "چالێنجی نوێ",
        "ch_title": "ناونیشانی چالێنج (نموونە: پێکەوە ٢٥ پلان تەواو بکەین)",
        "ch_target": "ئامانج (پلانی تەواوکراو)", "ch_days": "ڕۆژەکان",
        "ch_done": "چالێنجەکە تەواو بوو! 🎉", "ch_by": "لەلایەن",
        "ch_left": "ڕۆژ ماوە", "ch_of": "لە",
        "group_lb": "ئەم هەفتەیە لەم گروپەدا",
        "level": "ئاست", "xp": "خاڵی ئەزموون", "to_next": "بۆ ئاستی",
        "daily_goal": "ئامانجی ڕۆژانە", "today_t": "ئەمڕۆ",
        "week_report": "ئەم هەفتەیە", "vs_last": "بەراورد بە هەفتەی پێشوو",
    },
}
for _l, _d in V10.items():
    T[_l].update(_d)

# --- v11 strings ---
V11 = {
    "en": {
        "personal_ch": "My challenges", "repeat_t": "Repeat",
        "r_none": "No repeat", "r_daily": "Daily", "r_weekly": "Weekly",
        "grade_book": "Grade book", "semester": "Semester",
        "new_semester": "Add semester (e.g. Year 2 — Fall)",
        "sem_gpa": "Semester GPA", "cum_gpa": "Cumulative GPA",
        "duels_t": "Duels", "duel_btn": "⚔️ Challenge to a duel",
        "duel_pending": "Duel invitation", "duel_vs": "vs",
        "duel_won": "won the duel! 🏆", "duel_tie": "Draw!",
        "ntf_duel_req": "challenged you to a duel ⚔️",
        "ntf_duel_acc": "accepted your duel ⚔️",
        "ntf_duel_end": "Duel finished — winner:",
        "guide_t": "Guide",
    },
    "ar": {
        "personal_ch": "تحدياتي", "repeat_t": "التكرار",
        "r_none": "بدون تكرار", "r_daily": "يوميًا", "r_weekly": "أسبوعيًا",
        "grade_book": "سجل الدرجات", "semester": "الفصل الدراسي",
        "new_semester": "أضف فصلًا (مثال: السنة ٢ — الخريف)",
        "sem_gpa": "معدل الفصل", "cum_gpa": "المعدل التراكمي",
        "duels_t": "المبارزات", "duel_btn": "⚔️ تحدَّ للمبارزة",
        "duel_pending": "دعوة مبارزة", "duel_vs": "ضد",
        "duel_won": "فاز بالمبارزة! 🏆", "duel_tie": "تعادل!",
        "ntf_duel_req": "تحداك في مبارزة ⚔️",
        "ntf_duel_acc": "قبل مبارزتك ⚔️",
        "ntf_duel_end": "انتهت المبارزة — الفائز:",
        "guide_t": "الدليل",
    },
    "ku": {
        "personal_ch": "چالێنجەکانم", "repeat_t": "دووبارەبوونەوە",
        "r_none": "بێ دووبارەبوونەوە", "r_daily": "ڕۆژانە", "r_weekly": "هەفتانە",
        "grade_book": "تۆماری نمرەکان", "semester": "وەرز",
        "new_semester": "وەرزێک زیاد بکە (نموونە: ساڵی ٢ — پاییز)",
        "sem_gpa": "تێکڕای وەرز", "cum_gpa": "تێکڕای گشتی",
        "duels_t": "ملمالانێکان", "duel_btn": "⚔️ بانگهێشتی ملمالانێ بکە",
        "duel_pending": "بانگهێشتی ملمالانێ", "duel_vs": "دژ بە",
        "duel_won": "لە ملمالانێکە بردیەوە! 🏆", "duel_tie": "یەکسان!",
        "ntf_duel_req": "بانگهێشتی کردیت بۆ ملمالانێ ⚔️",
        "ntf_duel_acc": "ملمالانێکەتی قبوڵ کرد ⚔️",
        "ntf_duel_end": "ملمالانێکە تەواو بوو — براوە:",
        "guide_t": "ڕێبەر",
    },
}
for _l, _d in V11.items():
    T[_l].update(_d)

# --- v12 strings: posts, support, pro messaging ---
V12 = {
    "en": {
        "posts_t": "Posts", "new_post": "New post", "post_cat": "Category",
        "cat_research": "Research", "cat_science": "Science", "cat_tech": "Technology",
        "cat_ai": "AI", "cat_other": "Other", "cat_all": "All",
        "publish": "Publish", "post_img": "Image (optional)",
        "comments_t": "Comments", "write_comment": "Write a comment…",
        "no_posts": "No posts yet — share your first research, idea, or discovery!",
        "support_t": "Support", "contact_admins": "Contact the admins",
        "your_msg": "Your message — a question, a problem, or an idea",
        "rate_app": "Rate the app", "my_tickets": "My messages",
        "resolved_t": "Answered ✓", "open_t": "Waiting",
        "thanks_fb": "Thank you! Your message reached the admins. 💜",
        "avg_rating": "Average rating", "ratings_n": "ratings",
        "feedback_t": "Support inbox & ratings",
        "attach_t": "Attach a photo or file", "record_v": "Record a voice message",
        "recording": "Recording… tap to send", "voice_msg": "Voice message",
    },
    "ar": {
        "posts_t": "المنشورات", "new_post": "منشور جديد", "post_cat": "التصنيف",
        "cat_research": "بحث", "cat_science": "علوم", "cat_tech": "تقنية",
        "cat_ai": "ذكاء اصطناعي", "cat_other": "أخرى", "cat_all": "الكل",
        "publish": "نشر", "post_img": "صورة (اختياري)",
        "comments_t": "التعليقات", "write_comment": "اكتب تعليقًا…",
        "no_posts": "لا منشورات بعد — شارك أول بحث أو فكرة أو اكتشاف لك!",
        "support_t": "الدعم", "contact_admins": "تواصل مع الإدارة",
        "your_msg": "رسالتك — سؤال أو مشكلة أو فكرة",
        "rate_app": "قيّم التطبيق", "my_tickets": "رسائلي",
        "resolved_t": "تم الرد ✓", "open_t": "قيد الانتظار",
        "thanks_fb": "شكرًا! وصلت رسالتك إلى الإدارة. 💜",
        "avg_rating": "متوسط التقييم", "ratings_n": "تقييم",
        "feedback_t": "صندوق الدعم والتقييمات",
        "attach_t": "أرفق صورة أو ملفًا", "record_v": "سجّل رسالة صوتية",
        "recording": "جارٍ التسجيل… اضغط للإرسال", "voice_msg": "رسالة صوتية",
    },
    "ku": {
        "posts_t": "پۆستەکان", "new_post": "پۆستی نوێ", "post_cat": "پۆل",
        "cat_research": "توێژینەوە", "cat_science": "زانست", "cat_tech": "تەکنەلۆژیا",
        "cat_ai": "زیرەکی دەستکرد", "cat_other": "هیتر", "cat_all": "هەموو",
        "publish": "بڵاوکردنەوە", "post_img": "وێنە (ئارەزوومەندانە)",
        "comments_t": "کۆمێنتەکان", "write_comment": "کۆمێنتێک بنووسە…",
        "no_posts": "هێشتا پۆست نییە — یەکەم توێژینەوە یان بیرۆکەت بڵاو بکەرەوە!",
        "support_t": "پشتگیری", "contact_admins": "پەیوەندی بە بەڕێوەبەران",
        "your_msg": "نامەکەت — پرسیارێک، کێشەیەک، یان بیرۆکەیەک",
        "rate_app": "هەڵسەنگاندنی ئەپەکە", "my_tickets": "نامەکانم",
        "resolved_t": "وەڵام دراوە ✓", "open_t": "چاوەڕوانە",
        "thanks_fb": "سوپاس! نامەکەت گەیشتە بەڕێوەبەران. 💜",
        "avg_rating": "تێکڕای هەڵسەنگاندن", "ratings_n": "هەڵسەنگاندن",
        "feedback_t": "سندوقی پشتگیری و هەڵسەنگاندنەکان",
        "attach_t": "وێنە یان فایل هاوپێچ بکە", "record_v": "نامەی دەنگی تۆمار بکە",
        "recording": "تۆمارکردن… دابگرە بۆ ناردن", "voice_msg": "نامەی دەنگی",
    },
}
for _l, _d in V12.items():
    T[_l].update(_d)

V13 = {
    "en": {
        "online": "Online", "last_seen": "Last seen",
        "msg_deleted": "Message deleted",
        "del_confirm": "Delete this message for everyone?",
    },
    "ar": {
        "online": "متصل الآن", "last_seen": "آخر ظهور",
        "msg_deleted": "تم حذف الرسالة",
        "del_confirm": "حذف هذه الرسالة للجميع؟",
    },
    "ku": {
        "online": "ئۆنلاینە", "last_seen": "دوایین بینین",
        "msg_deleted": "نامەکە سڕایەوە",
        "del_confirm": "ئەم نامەیە بۆ هەمووان بسڕدرێتەوە؟",
    },
}
for _l, _d in V13.items():
    T[_l].update(_d)

V14 = {
    "en": {
        "ntf_deadline": "Plan due today:", "ntf_overdue": "Plan overdue:",
        "ntf_exam_soon": "Exam coming up:",
        "enable_notifs": "Enable notifications",
        "new_msg_toast": "New message",
    },
    "ar": {
        "ntf_deadline": "خطة تستحق اليوم:", "ntf_overdue": "خطة متأخرة:",
        "ntf_exam_soon": "امتحان قريب:",
        "enable_notifs": "تفعيل الإشعارات",
        "new_msg_toast": "رسالة جديدة",
    },
    "ku": {
        "ntf_deadline": "پلانێک ئەمڕۆ کۆتایی دێت:", "ntf_overdue": "پلانێک دواکەوتووە:",
        "ntf_exam_soon": "تاقیکردنەوە نزیکە:",
        "enable_notifs": "چالاککردنی ئاگادارکردنەوەکان",
        "new_msg_toast": "نامەی نوێ",
    },
}
for _l, _d in V14.items():
    T[_l].update(_d)

V15 = {
    "en": {
        "inst_title": "Install the app",
        "inst_sub": "Faster, fullscreen, with notifications — right on your home screen.",
        "inst_b1": "Instant notifications",
        "inst_b2": "Opens in one tap",
        "inst_b3": "Feels like a real app",
        "inst_btn": "Install", "inst_later": "Not now",
        "inst_ios1": "Tap the Share button", "inst_ios2": "below, then choose",
        "inst_ios3": "Add to Home Screen",
    },
    "ar": {
        "inst_title": "ثبّت التطبيق",
        "inst_sub": "أسرع، بملء الشاشة، مع إشعارات — مباشرة على شاشتك الرئيسية.",
        "inst_b1": "إشعارات فورية",
        "inst_b2": "يفتح بلمسة واحدة",
        "inst_b3": "إحساس تطبيق حقيقي",
        "inst_btn": "تثبيت", "inst_later": "ليس الآن",
        "inst_ios1": "اضغط زر المشاركة", "inst_ios2": "بالأسفل، ثم اختر",
        "inst_ios3": "إضافة إلى الشاشة الرئيسية",
    },
    "ku": {
        "inst_title": "ئەپەکە دابمەزرێنە",
        "inst_sub": "خێراتر، پڕ بە شاشە، لەگەڵ ئاگادارکردنەوەکان — ڕاستەوخۆ لەسەر شاشەی سەرەکیت.",
        "inst_b1": "ئاگادارکردنەوەی خێرا",
        "inst_b2": "بە یەک دەستلێدان دەکرێتەوە",
        "inst_b3": "هەستی ئەپێکی ڕاستەقینە",
        "inst_btn": "دامەزراندن", "inst_later": "ئێستا نا",
        "inst_ios1": "دوگمەی هاوبەشکردن دابگرە", "inst_ios2": "لە خوارەوە، پاشان هەڵبژێرە",
        "inst_ios3": "زیادکردن بۆ شاشەی سەرەکی",
    },
}
for _l, _d in V15.items():
    T[_l].update(_d)

V16 = {
    "en": {"show_more": "Show more", "show_less": "Show less"},
    "ar": {"show_more": "عرض المزيد", "show_less": "عرض أقل"},
    "ku": {"show_more": "زیاتر پیشان بدە", "show_less": "کەمتر پیشان بدە"},
}
for _l, _d in V16.items():
    T[_l].update(_d)

V17 = {
    "en": {
        "ntfask_title": "Turn on notifications",
        "ntfask_sub": "So you never miss a message, a deadline, or an exam — even when the app is closed.",
        "ntfask_b1": "New messages", "ntfask_b2": "Deadline warnings", "ntfask_b3": "Exam reminders",
        "ntfask_btn": "Allow notifications",
        "push_test_ok": "Notifications are working! You're all set 🎉",
    },
    "ar": {
        "ntfask_title": "فعّل الإشعارات",
        "ntfask_sub": "حتى لا تفوتك رسالة أو موعد نهائي أو امتحان — حتى عندما يكون التطبيق مغلقًا.",
        "ntfask_b1": "رسائل جديدة", "ntfask_b2": "تنبيهات المواعيد", "ntfask_b3": "تذكير الامتحانات",
        "ntfask_btn": "السماح بالإشعارات",
        "push_test_ok": "الإشعارات تعمل! كل شيء جاهز 🎉",
    },
    "ku": {
        "ntfask_title": "ئاگادارکردنەوەکان چالاک بکە",
        "ntfask_sub": "بۆ ئەوەی هیچ نامەیەک، کۆتا وادەیەک یان تاقیکردنەوەیەکت لە دەست نەچێت — تەنانەت کاتێک ئەپەکە داخراوە.",
        "ntfask_b1": "نامەی نوێ", "ntfask_b2": "ئاگاداری کۆتا وادە", "ntfask_b3": "بیرخەرەوەی تاقیکردنەوە",
        "ntfask_btn": "ڕێگە بە ئاگادارکردنەوەکان بدە",
        "push_test_ok": "ئاگادارکردنەوەکان کار دەکەن! هەموو شتێک ئامادەیە 🎉",
    },
}
for _l, _d in V17.items():
    T[_l].update(_d)

V18 = {
    "en": {
        "hw_t": "Homework", "hw_add": "Add homework", "hw_subject": "Subject",
        "hw_title_l": "What do you have to do?", "hw_details": "Notes (optional)",
        "hw_due": "Due date", "no_hw": "No homework yet — lucky you! 🎉",
        "hw_left": "left", "ntf_homework": "Homework waiting:",
    },
    "ar": {
        "hw_t": "الواجبات", "hw_add": "أضف واجبًا", "hw_subject": "المادة",
        "hw_title_l": "ما الذي عليك فعله؟", "hw_details": "ملاحظات (اختياري)",
        "hw_due": "موعد التسليم", "no_hw": "لا واجبات بعد — محظوظ! 🎉",
        "hw_left": "متبقٍ", "ntf_homework": "واجبات بانتظارك:",
    },
    "ku": {
        "hw_t": "ئەرکەکان", "hw_add": "ئەرک زیاد بکە", "hw_subject": "بابەت",
        "hw_title_l": "چی دەبێت بکەیت؟", "hw_details": "تێبینی (ئارەزوومەندانە)",
        "hw_due": "کۆتا وادە", "no_hw": "هێشتا ئەرک نییە — بەختەوەری! 🎉",
        "hw_left": "ماوە", "ntf_homework": "ئەرکەکان چاوەڕێتن:",
    },
}
for _l, _d in V18.items():
    T[_l].update(_d)

V19 = {
    "en": {
        "stories_t": "Stories", "story_add": "Your story", "story_new": "New story",
        "story_write": "Share what you're studying, a win, or a thought…",
        "story_post": "Post story", "story_views": "views", "story_gone": "24h",
        "studying_now": "Studying now", "study_together": "Study together",
        "nobody_studying": "No friends studying right now — be the first! ⏱",
        "tt_share": "Share timetable", "tt_code": "Class code",
        "tt_import_btn": "Import", "tt_imported": "Timetable imported! 🎉",
        "tt_badcode": "Code not found.",
        "tt_share_hint": "Give this code to your classmates — they type it below and get your exact timetable:",
        "sponsor_by": "Sponsored by", "sponsor_t": "Sponsor banner",
        "sponsor_name_l": "Sponsor name", "sponsor_url_l": "Sponsor link (https://…)",
        "sponsor_img_l": "Sponsor logo", "sponsor_on": "Show sponsor banner",
        "make_plus": "Give ⭐ Plus", "remove_plus": "Remove ⭐ Plus",
        "ntf_weekly": "Your weekly report:", "ntf_mention": "mentioned you 📣",
        "search_msgs": "Search messages…", "search_none": "No matches",
        "join_now": "Join now", "on_kurdroom": "is on",
    },
    "ar": {
        "stories_t": "القصص", "story_add": "قصتك", "story_new": "قصة جديدة",
        "story_write": "شارك ما تدرسه أو إنجازًا أو فكرة…",
        "story_post": "نشر القصة", "story_views": "مشاهدة", "story_gone": "٢٤س",
        "studying_now": "يدرس الآن", "study_together": "ادرسوا معًا",
        "nobody_studying": "لا أصدقاء يدرسون الآن — كن الأول! ⏱",
        "tt_share": "مشاركة الجدول", "tt_code": "رمز الصف",
        "tt_import_btn": "استيراد", "tt_imported": "تم استيراد الجدول! 🎉",
        "tt_badcode": "الرمز غير موجود.",
        "tt_share_hint": "أعطِ هذا الرمز لزملائك — يكتبونه بالأسفل ويحصلون على جدولك نفسه:",
        "sponsor_by": "برعاية", "sponsor_t": "لافتة الراعي",
        "sponsor_name_l": "اسم الراعي", "sponsor_url_l": "رابط الراعي (https://…)",
        "sponsor_img_l": "شعار الراعي", "sponsor_on": "إظهار لافتة الراعي",
        "make_plus": "منح ⭐ بلس", "remove_plus": "إزالة ⭐ بلس",
        "ntf_weekly": "تقريرك الأسبوعي:", "ntf_mention": "أشار إليك 📣",
        "search_msgs": "ابحث في الرسائل…", "search_none": "لا نتائج",
        "join_now": "انضم الآن", "on_kurdroom": "موجود على",
    },
    "ku": {
        "stories_t": "چیرۆکەکان", "story_add": "چیرۆکەکەت", "story_new": "چیرۆکی نوێ",
        "story_write": "ئەوەی دەیخوێنیت، سەرکەوتنێک یان بیرۆکەیەک بڵاو بکەرەوە…",
        "story_post": "بڵاوکردنەوە", "story_views": "بینین", "story_gone": "٢٤ک",
        "studying_now": "ئێستا دەخوێنێت", "study_together": "پێکەوە بخوێنن",
        "nobody_studying": "هیچ هاوڕێیەک ئێستا ناخوێنێت — تۆ یەکەم بە! ⏱",
        "tt_share": "هاوبەشکردنی خشتە", "tt_code": "کۆدی پۆل",
        "tt_import_btn": "هێنان", "tt_imported": "خشتەکە هێنرا! 🎉",
        "tt_badcode": "کۆدەکە نەدۆزرایەوە.",
        "tt_share_hint": "ئەم کۆدە بدە بە هاوپۆلەکانت — لە خوارەوە دەینووسن و هەمان خشتەی تۆیان بۆ دێت:",
        "sponsor_by": "بە پاڵپشتی", "sponsor_t": "بانەری پاڵپشت",
        "sponsor_name_l": "ناوی پاڵپشت", "sponsor_url_l": "لینکی پاڵپشت (https://…)",
        "sponsor_img_l": "لۆگۆی پاڵپشت", "sponsor_on": "پیشاندانی بانەری پاڵپشت",
        "make_plus": "پێدانی ⭐ پڵەس", "remove_plus": "لابردنی ⭐ پڵەس",
        "ntf_weekly": "ڕاپۆرتی هەفتانەت:", "ntf_mention": "ئاماژەی پێکردیت 📣",
        "search_msgs": "لە نامەکاندا بگەڕێ…", "search_none": "هیچ نەدۆزرایەوە",
        "join_now": "ئێستا بەشدار بە", "on_kurdroom": "لەسەر",
    },
}
for _l, _d in V19.items():
    T[_l].update(_d)

V20 = {
    "en": {
        "plus_t": "KurdRoom Plus", "plus_hero": "Go Plus. Shine everywhere. ⭐",
        "plus_sub": "One small payment — a golden experience across the whole app.",
        "plus_active": "You are a Plus member! Enjoy your gold star ⭐",
        "perk1_t": "Gold star badge", "perk1_d": "A ⭐ next to your name in chats, groups, messages, and your profile — everyone sees it.",
        "perk2_t": "Double XP", "perk2_d": "Every plan, habit, and badge counts twice. Level up 2× faster than everyone else.",
        "perk3_t": "Exclusive story styles", "perk3_d": "3 premium story backgrounds nobody else can post with.",
        "perk4_t": "Priority support", "perk4_d": "Your support messages jump to the top of the admin's inbox.",
        "perk5_t": "Early access", "perk5_d": "New features reach Plus members first.",
        "perk6_t": "Support KurdRoom", "perk6_d": "You keep the platform free for thousands of students. 💜",
        "pay_how": "How to get Plus", "pay_step1": "Send the amount to this number with FIB or FastPay:",
        "pay_step2": "Tap the app you use — the number is copied automatically:",
        "pay_step3": "After sending, tap the button below — the admin activates your ⭐ (usually within hours):",
        "i_paid": "✅ I sent the payment", "paid_sent": "Got it! The admin was notified — your ⭐ is coming soon.",
        "copy_num": "Copy number", "copied": "Copied!",
        "plus_price": "5,000 IQD / month · or 45,000 IQD / year (save 25%)",
    },
    "ar": {
        "plus_t": "كوردروم بلس", "plus_hero": "انتقل إلى بلس. تألّق في كل مكان. ⭐",
        "plus_sub": "دفعة صغيرة واحدة — تجربة ذهبية في التطبيق كله.",
        "plus_active": "أنت عضو بلس! استمتع بنجمتك الذهبية ⭐",
        "perk1_t": "شارة النجمة الذهبية", "perk1_d": "⭐ بجانب اسمك في الدردشات والمجموعات والرسائل وملفك — يراها الجميع.",
        "perk2_t": "نقاط خبرة مضاعفة", "perk2_d": "كل خطة وعادة وشارة تُحسب مرتين. ارتقِ بمستواك أسرع مرتين من الجميع.",
        "perk3_t": "خلفيات قصص حصرية", "perk3_d": "3 خلفيات قصص مميزة لا يستطيع غيرك النشر بها.",
        "perk4_t": "دعم بأولوية", "perk4_d": "رسائل دعمك تقفز إلى أعلى صندوق الإدارة.",
        "perk5_t": "وصول مبكر", "perk5_d": "الميزات الجديدة تصل أعضاء بلس أولًا.",
        "perk6_t": "ادعم كوردروم", "perk6_d": "أنت تُبقي المنصة مجانية لآلاف الطلاب. 💜",
        "pay_how": "كيف تحصل على بلس", "pay_step1": "أرسل المبلغ إلى هذا الرقم عبر FIB أو FastPay:",
        "pay_step2": "اضغط على التطبيق الذي تستخدمه — يُنسخ الرقم تلقائيًا:",
        "pay_step3": "بعد الإرسال اضغط الزر بالأسفل — تفعّل الإدارة نجمتك ⭐ (عادة خلال ساعات):",
        "i_paid": "✅ أرسلت المبلغ", "paid_sent": "وصلنا! تم إشعار الإدارة — نجمتك ⭐ قادمة قريبًا.",
        "copy_num": "نسخ الرقم", "copied": "تم النسخ!",
        "plus_price": "5,000 دينار / شهر · أو 45,000 دينار / سنة (وفّر 25٪)",
    },
    "ku": {
        "plus_t": "کوردڕووم پڵەس", "plus_hero": "بڕۆ بۆ پڵەس. لە هەموو شوێنێک بدرەوشێوە. ⭐",
        "plus_sub": "یەک پارەدانی بچووک — ئەزموونێکی ئاڵتوونی لە هەموو ئەپەکەدا.",
        "plus_active": "تۆ ئەندامی پڵەسیت! چێژ لە ئەستێرە ئاڵتوونیەکەت وەربگرە ⭐",
        "perk1_t": "نیشانەی ئەستێرەی ئاڵتوونی", "perk1_d": "⭐ لەتەنیشت ناوت لە چات و گروپ و نامەکان و پرۆفایلەکەت — هەمووان دەیبینن.",
        "perk2_t": "دوو هێندە XP", "perk2_d": "هەر پلان و خوو و نیشانەیەک دوو جار دەژمێردرێت. دوو هێندە خێراتر پلە بەرز بکەرەوە.",
        "perk3_t": "شێوازی چیرۆکی تایبەت", "perk3_d": "3 پاشبنەمای چیرۆکی پرێمیۆم کە کەسی تر ناتوانێت پۆستیان بکات.",
        "perk4_t": "پشتگیری بە پێشینە", "perk4_d": "نامەکانی پشتگیریت دەچنە سەرەوەی سندوقی بەڕێوەبەر.",
        "perk5_t": "دەستپێگەیشتنی زوو", "perk5_d": "تایبەتمەندییە نوێیەکان یەکەمجار دەگەنە ئەندامانی پڵەس.",
        "perk6_t": "پشتگیری کوردڕووم بکە", "perk6_d": "تۆ پلاتفۆرمەکە بەخۆڕایی دەهێڵیتەوە بۆ هەزاران خوێندکار. 💜",
        "pay_how": "چۆن پڵەس بەدەست بهێنیت", "pay_step1": "بڕەکە بنێرە بۆ ئەم ژمارەیە بە FIB یان FastPay:",
        "pay_step2": "ئەو ئەپە دابگرە کە بەکاریدەهێنیت — ژمارەکە خۆکارانە کۆپی دەبێت:",
        "pay_step3": "دوای ناردن دوگمەکەی خوارەوە دابگرە — بەڕێوەبەر ئەستێرەکەت چالاک دەکات ⭐ (زۆربەی کات لە چەند کاتژمێرێکدا):",
        "i_paid": "✅ پارەکەم نارد", "paid_sent": "گەیشت! بەڕێوەبەر ئاگادار کرایەوە — ئەستێرەکەت ⭐ بەم زووانە دێت.",
        "copy_num": "کۆپیکردنی ژمارە", "copied": "کۆپی کرا!",
        "plus_price": "5,000 دینار / مانگ · یان 45,000 دینار / ساڵ (25٪ کەمتر)",
    },
}
for _l, _d in V20.items():
    T[_l].update(_d)

V21 = {
    "en": {
        "tool_studyplan": "Study Plan Generator", "tool_sum2": "AI Summarizer",
        "tool_present": "Presentation Builder", "tool_translate": "Academic Translator",
        "tool_predict": "Exam Question Predictor",
        "gate_t": "This is a Plus tool", "gate_sub": "Exclusive to ⭐ Plus members — unlock it and 4 more premium tools, double XP, the gold badge, and everything else.",
        "gate_cta": "Get Plus ⭐",
        "sp_perday": "Subjects per day", "sp_gen": "✨ Generate my plan",
        "sp_save": "✅ Add to my planner", "sp_preview": "Your revision schedule",
        "sp_none": "Add your upcoming exams in University first — the plan is built from them.",
        "sp_revise": "Revise", "sp_created": "revision sessions added to your planner! 🎉",
        "sum2_ph": "Paste the lecture, chapter, or article here…",
        "present_ph": "Write your presentation topic (e.g. Renewable energy in Kurdistan)…",
        "translate_ph": "Paste the text to translate and polish…",
        "predict_ph": "Paste the chapter or your notes — get the questions your teacher is most likely to ask…",
        "translate_to": "Translate to", "run_t": "Run ✨", "result_t": "Result",
    },
    "ar": {
        "tool_studyplan": "مولّد خطة المذاكرة", "tool_sum2": "الملخّص الذكي",
        "tool_present": "منشئ العروض التقديمية", "tool_translate": "المترجم الأكاديمي",
        "tool_predict": "متنبّئ أسئلة الامتحان",
        "gate_t": "هذه أداة بلس", "gate_sub": "حصرية لأعضاء ⭐ بلس — افتحها مع 4 أدوات مميزة أخرى ونقاط مضاعفة والشارة الذهبية وكل شيء آخر.",
        "gate_cta": "احصل على بلس ⭐",
        "sp_perday": "مواد في اليوم", "sp_gen": "✨ أنشئ خطتي",
        "sp_save": "✅ أضِف إلى مخططي", "sp_preview": "جدول مراجعتك",
        "sp_none": "أضف امتحاناتك القادمة في صفحة الجامعة أولًا — الخطة تُبنى منها.",
        "sp_revise": "مراجعة", "sp_created": "جلسة مراجعة أُضيفت إلى مخططك! 🎉",
        "sum2_ph": "الصق المحاضرة أو الفصل أو المقال هنا…",
        "present_ph": "اكتب موضوع عرضك التقديمي…",
        "translate_ph": "الصق النص للترجمة والتحسين…",
        "predict_ph": "الصق الفصل أو ملاحظاتك — واحصل على الأسئلة الأكثر احتمالًا…",
        "translate_to": "ترجم إلى", "run_t": "شغّل ✨", "result_t": "النتيجة",
    },
    "ku": {
        "tool_studyplan": "دروستکەری پلانی خوێندن", "tool_sum2": "کورتکەرەوەی زیرەک",
        "tool_present": "دروستکەری پرێزەنتەیشن", "tool_translate": "وەرگێڕی ئەکادیمی",
        "tool_predict": "پێشبینیکەری پرسیاری تاقیکردنەوە",
        "gate_t": "ئەمە ئامرازێکی پڵەسە", "gate_sub": "تایبەتە بە ئەندامانی ⭐ پڵەس — بیکەرەوە لەگەڵ 4 ئامرازی پرێمیۆمی تر و XP ی دووهێندە و نیشانە ئاڵتوونیەکە و هەموو شتێکی تر.",
        "gate_cta": "پڵەس وەربگرە ⭐",
        "sp_perday": "بابەت لە ڕۆژێکدا", "sp_gen": "✨ پلانەکەم دروست بکە",
        "sp_save": "✅ زیادی بکە بۆ پلانەکانم", "sp_preview": "خشتەی پێداچوونەوەکەت",
        "sp_none": "سەرەتا تاقیکردنەوە داهاتووەکانت لە پەڕەی زانکۆ زیاد بکە — پلانەکە لەوانەوە دروست دەبێت.",
        "sp_revise": "پێداچوونەوە", "sp_created": "دانیشتنی پێداچوونەوە زیادکرا بۆ پلانەکانت! 🎉",
        "sum2_ph": "وانەکە یان بەشەکە یان وتارەکە لێرە دابنێ…",
        "present_ph": "بابەتی پرێزەنتەیشنەکەت بنووسە…",
        "translate_ph": "دەقەکە دابنێ بۆ وەرگێڕان و جوانکردن…",
        "predict_ph": "بەشەکە یان تێبینیەکانت دابنێ — ئەو پرسیارانە وەربگرە کە زۆرترین ئەگەری هاتنیان هەیە…",
        "translate_to": "وەربگێڕە بۆ", "run_t": "کاری پێبکە ✨", "result_t": "ئەنجام",
    },
}
for _l, _d in V21.items():
    T[_l].update(_d)

V22 = {
    "en": {
        "pay_method_q": "What payment method did you choose?",
        "pay_plan_q": "Which plan did you pay for?",
        "plan_month": "5,000 IQD / Monthly", "plan_year": "45,000 IQD / Yearly",
        "submit_t": "Submit",
        "pay_success": "Payment successful ✅ — wait for the activation, your ⭐ is coming soon!",
        "ntf_plus_wait": "Payment received for review:",
        "ntf_plus_on": "Your ⭐ Plus is now active! Enjoy!",
        "about_t": "About Us", "about_l": "About Us text",
        "social_l": "Links (Instagram · Facebook · Website · Email)",
        "fib_link_l": "FIB payment link (from your FIB app: Request → Share link)",
        "fp_link_l": "FastPay payment link (optional)",
        "fib_qr_l": "FIB QR code image (share/save 'My QR' from the FIB app, upload it here)",
        "scan_qr": "…or scan this QR with the FIB app:",
        "plus_phone_l": "Payment phone number",
        "del_img": "🗑 Delete current image",
        "contact_t": "Find us",
    },
    "ar": {
        "pay_method_q": "ما طريقة الدفع التي اخترتها؟",
        "pay_plan_q": "أي خطة دفعت؟",
        "plan_month": "5,000 دينار / شهري", "plan_year": "45,000 دينار / سنوي",
        "submit_t": "إرسال",
        "pay_success": "تم الدفع بنجاح ✅ — انتظر التفعيل، نجمتك ⭐ قادمة قريبًا!",
        "ntf_plus_wait": "دفعة قيد المراجعة:",
        "ntf_plus_on": "تم تفعيل ⭐ بلس الخاص بك! استمتع!",
        "about_t": "من نحن", "about_l": "نص من نحن",
        "social_l": "الروابط (إنستغرام · فيسبوك · الموقع · البريد)",
        "fib_link_l": "رابط دفع FIB (من تطبيقك: طلب → مشاركة الرابط)",
        "fp_link_l": "رابط دفع FastPay (اختياري)",
        "fib_qr_l": "صورة رمز QR لـ FIB (شارك/احفظ 'رمزي' من التطبيق وارفعه هنا)",
        "scan_qr": "…أو امسح هذا الرمز بتطبيق FIB:",
        "plus_phone_l": "رقم هاتف الدفع",
        "del_img": "🗑 حذف الصورة الحالية",
        "contact_t": "تواصل معنا",
    },
    "ku": {
        "pay_method_q": "کام شێوازی پارەدانت هەڵبژارد؟",
        "pay_plan_q": "پارەت بۆ کام پلان دا؟",
        "plan_month": "5,000 دینار / مانگانە", "plan_year": "45,000 دینار / ساڵانە",
        "submit_t": "ناردن",
        "pay_success": "پارەدان سەرکەوتوو بوو ✅ — چاوەڕێی چالاککردن بە، ئەستێرەکەت ⭐ بەم زووانە دێت!",
        "ntf_plus_wait": "پارەدانێک بۆ پێداچوونەوە:",
        "ntf_plus_on": "⭐ پڵەسەکەت چالاک کرا! چێژی لێ ببینە!",
        "about_t": "دەربارەی ئێمە", "about_l": "دەقی دەربارەی ئێمە",
        "social_l": "لینکەکان (ئینستاگرام · فەیسبووک · ماڵپەڕ · ئیمەیڵ)",
        "fib_link_l": "لینکی پارەدانی FIB (لە ئەپەکەتەوە: داواکردن → هاوبەشکردنی لینک)",
        "fp_link_l": "لینکی پارەدانی FastPay (ئارەزوومەندانە)",
        "fib_qr_l": "وێنەی کۆدی QR ی FIB (لە ئەپەکە 'My QR' هاوبەش بکە/پاشەکەوتی بکە و لێرە باری بکە)",
        "scan_qr": "…یان ئەم کۆدە بە ئەپی FIB سکان بکە:",
        "plus_phone_l": "ژمارەی تەلەفۆنی پارەدان",
        "del_img": "🗑 سڕینەوەی وێنەی ئێستا",
        "contact_t": "بمانبیننەوە",
    },
}
for _l, _d in V22.items():
    T[_l].update(_d)

V23 = {
    "en": {
        "first_name_l": "First name", "middle_name_l": "Second name",
        "last_name_l": "Last name",
        "email_hint2": "Required — used only for verification & password reset. Nobody ever sees it.",
        "step_account": "Account", "step_edu": "Your study", "step_pw": "Security",
        "edu_q": "What describes you best?",
        "wl_school": "School student", "wl_uni": "University student",
        "wl_master": "Master's", "wl_phd": "PhD", "wl_prof": "Professor",
        "wl_other": "Work / Other",
        "school_name_l": "Your school's name", "school_lvl_l": "School stage",
        "s_elem": "Elementary", "s_inter": "Intermediate", "s_high": "High school",
        "grade_l": "Grade", "uni_l": "Your university",
        "ck_l": "College or institute?", "ck_college": "College",
        "ck_institute": "Institute", "college_l": "Your college",
        "dept_l": "Department", "inst_name_l": "Institute name",
        "inst_dept_l": "Institute department", "job_l": "Your work",
        "choose_l": "Choose…", "next_t": "Next", "back_t": "Back",
        "create_acc": "Create account 🚀",
        "pw_rules_t": "A strong password has:",
        "rule_len": "At least 8 characters", "rule_upper": "An UPPERCASE letter",
        "rule_lower": "a lowercase letter", "rule_num": "A number (0-9)",
        "rule_sym": "A symbol !@#$ (bonus)",
        "err_fill": "Please complete the highlighted fields.",
        "err_email": "Please enter a valid email address.",
        "err_email_used": "This email is already registered.",
        "err_pw_weak": "Password too weak — follow the checklist.",
        "err_email_send": "Email could not be sent — please contact the admin.",
        "verify_t": "Verify your email", "verify_sent": "We sent a 6-digit code to",
        "verify_code_l": "Enter the code", "verify_btn": "Verify ✓",
        "resend_code": "Resend code", "code_resent": "A new code was sent 📩",
        "err_code": "Wrong code — check your email and try again.",
        "code_expired": "The code expired — please start again.",
        "verify_subject": "Your verification code",
        "verify_body": "Welcome! Your verification code is:",
        "reset_body": "Your password reset code is:",
        "forgot_t": "Forgot password?",
        "forgot_hint": "Type your account's email — we'll send you a reset code.",
        "send_code": "Send code 📩",
        "code_sent_maybe": "If that email exists, a code is on its way 📩",
        "reset_t": "Set a new password", "pw_reset_ok": "Password changed — log in! ✅",
        "complete_t": "Welcome back! One quick step",
        "complete_sub": "KurdRoom got a big upgrade — please confirm your details once. Your username stays yours.",
        "stats_t": "Statistics", "open_stats": "📊 Full statistics",
        "st_users": "All users", "st_plus": "Plus members",
        "st_verified": "Verified emails", "st_active": "Active this week",
        "st_completed": "Completed profiles", "st_bylevel": "By education level",
        "st_unis": "Universities", "st_colleges": "Colleges",
        "st_deps": "Departments", "st_school": "School stages",
        "st_grades": "Grades", "st_jobs": "Jobs", "st_growth": "New users per month",
        "smtp_t": "Email sending (SMTP)",
        "regopts_t": "Registration options (one per line)",
    },
    "ar": {
        "first_name_l": "الاسم الأول", "middle_name_l": "الاسم الثاني",
        "last_name_l": "اسم العائلة",
        "email_hint2": "مطلوب — يُستخدم فقط للتحقق واستعادة كلمة المرور. لا يراه أحد أبدًا.",
        "step_account": "الحساب", "step_edu": "دراستك", "step_pw": "الأمان",
        "edu_q": "ما الذي يصفك أفضل؟",
        "wl_school": "طالب مدرسة", "wl_uni": "طالب جامعة",
        "wl_master": "ماجستير", "wl_phd": "دكتوراه", "wl_prof": "أستاذ",
        "wl_other": "عمل / أخرى",
        "school_name_l": "اسم مدرستك", "school_lvl_l": "المرحلة الدراسية",
        "s_elem": "ابتدائية", "s_inter": "متوسطة", "s_high": "إعدادية",
        "grade_l": "الصف", "uni_l": "جامعتك",
        "ck_l": "كلية أم معهد؟", "ck_college": "كلية",
        "ck_institute": "معهد", "college_l": "كليتك",
        "dept_l": "القسم", "inst_name_l": "اسم المعهد",
        "inst_dept_l": "قسم المعهد", "job_l": "عملك",
        "choose_l": "اختر…", "next_t": "التالي", "back_t": "رجوع",
        "create_acc": "أنشئ الحساب 🚀",
        "pw_rules_t": "كلمة المرور القوية تحتوي:",
        "rule_len": "8 أحرف على الأقل", "rule_upper": "حرف كبير (A-Z)",
        "rule_lower": "حرف صغير (a-z)", "rule_num": "رقم (0-9)",
        "rule_sym": "رمز !@#$ (إضافي)",
        "err_fill": "يرجى إكمال الحقول المحددة.",
        "err_email": "يرجى إدخال بريد إلكتروني صحيح.",
        "err_email_used": "هذا البريد مسجل بالفعل.",
        "err_pw_weak": "كلمة المرور ضعيفة — اتبع القائمة.",
        "err_email_send": "تعذر إرسال البريد — تواصل مع الإدارة.",
        "verify_t": "تحقق من بريدك", "verify_sent": "أرسلنا رمزًا من 6 أرقام إلى",
        "verify_code_l": "أدخل الرمز", "verify_btn": "تحقق ✓",
        "resend_code": "إعادة إرسال الرمز", "code_resent": "تم إرسال رمز جديد 📩",
        "err_code": "رمز خاطئ — تحقق من بريدك وحاول مجددًا.",
        "code_expired": "انتهت صلاحية الرمز — ابدأ من جديد.",
        "verify_subject": "رمز التحقق الخاص بك",
        "verify_body": "مرحبًا! رمز التحقق الخاص بك هو:",
        "reset_body": "رمز استعادة كلمة المرور هو:",
        "forgot_t": "نسيت كلمة المرور؟",
        "forgot_hint": "اكتب بريد حسابك — سنرسل لك رمز الاستعادة.",
        "send_code": "أرسل الرمز 📩",
        "code_sent_maybe": "إذا كان البريد موجودًا، فالرمز في الطريق 📩",
        "reset_t": "كلمة مرور جديدة", "pw_reset_ok": "تم تغيير كلمة المرور — سجّل الدخول! ✅",
        "complete_t": "أهلًا بعودتك! خطوة سريعة",
        "complete_sub": "حصل كوردروم على تحديث كبير — أكّد بياناتك مرة واحدة. اسم المستخدم يبقى لك.",
        "stats_t": "الإحصائيات", "open_stats": "📊 الإحصائيات الكاملة",
        "st_users": "كل المستخدمين", "st_plus": "أعضاء بلس",
        "st_verified": "بريد موثّق", "st_active": "نشط هذا الأسبوع",
        "st_completed": "ملفات مكتملة", "st_bylevel": "حسب المستوى الدراسي",
        "st_unis": "الجامعات", "st_colleges": "الكليات",
        "st_deps": "الأقسام", "st_school": "المراحل المدرسية",
        "st_grades": "الصفوف", "st_jobs": "الأعمال", "st_growth": "مستخدمون جدد شهريًا",
        "smtp_t": "إرسال البريد (SMTP)",
        "regopts_t": "خيارات التسجيل (واحد في كل سطر)",
    },
    "ku": {
        "first_name_l": "ناوی یەکەم", "middle_name_l": "ناوی دووەم",
        "last_name_l": "ناوی خێزان",
        "email_hint2": "پێویستە — تەنها بۆ پشتڕاستکردنەوە و گەڕاندنەوەی وشەی نهێنی بەکاردێت. هیچ کەس نایبینێت.",
        "step_account": "هەژمار", "step_edu": "خوێندنەکەت", "step_pw": "پاراستن",
        "edu_q": "کامیان باشتر باست دەکات؟",
        "wl_school": "خوێندکاری قوتابخانە", "wl_uni": "خوێندکاری زانکۆ",
        "wl_master": "ماستەر", "wl_phd": "دکتۆرا", "wl_prof": "پرۆفیسۆر",
        "wl_other": "کار / هیتر",
        "school_name_l": "ناوی قوتابخانەکەت", "school_lvl_l": "قۆناغی خوێندن",
        "s_elem": "سەرەتایی", "s_inter": "ناوەندی", "s_high": "ئامادەیی",
        "grade_l": "پۆل", "uni_l": "زانکۆکەت",
        "ck_l": "کۆلێژ یان پەیمانگا؟", "ck_college": "کۆلێژ",
        "ck_institute": "پەیمانگا", "college_l": "کۆلێژەکەت",
        "dept_l": "بەش", "inst_name_l": "ناوی پەیمانگا",
        "inst_dept_l": "بەشی پەیمانگا", "job_l": "کارەکەت",
        "choose_l": "هەڵبژێرە…", "next_t": "دواتر", "back_t": "گەڕانەوە",
        "create_acc": "هەژمار دروست بکە 🚀",
        "pw_rules_t": "وشەی نهێنی بەهێز ئەمانەی تێدایە:",
        "rule_len": "لانیکەم 8 پیت", "rule_upper": "پیتێکی گەورە (A-Z)",
        "rule_lower": "پیتێکی بچووک (a-z)", "rule_num": "ژمارەیەک (0-9)",
        "rule_sym": "هێمایەک !@#$ (زیادە)",
        "err_fill": "تکایە خانە دیاریکراوەکان پڕ بکەرەوە.",
        "err_email": "تکایە ئیمەیڵێکی دروست بنووسە.",
        "err_email_used": "ئەم ئیمەیڵە پێشتر تۆمارکراوە.",
        "err_pw_weak": "وشەی نهێنی لاوازە — لیستەکە جێبەجێ بکە.",
        "err_email_send": "ئیمەیڵ نەنێردرا — پەیوەندی بە بەڕێوەبەر بکە.",
        "verify_t": "ئیمەیڵەکەت پشتڕاست بکەرەوە",
        "verify_sent": "کۆدێکی 6 ژمارەییمان نارد بۆ",
        "verify_code_l": "کۆدەکە بنووسە", "verify_btn": "پشتڕاستکردنەوە ✓",
        "resend_code": "دووبارە ناردنی کۆد", "code_resent": "کۆدێکی نوێ نێردرا 📩",
        "err_code": "کۆدەکە هەڵەیە — ئیمەیڵەکەت بپشکنە و دووبارە هەوڵ بدە.",
        "code_expired": "کۆدەکە بەسەرچوو — تکایە لە سەرەتاوە دەست پێ بکەرەوە.",
        "verify_subject": "کۆدی پشتڕاستکردنەوەکەت",
        "verify_body": "بەخێربێیت! کۆدی پشتڕاستکردنەوەکەت ئەمەیە:",
        "reset_body": "کۆدی گەڕاندنەوەی وشەی نهێنی ئەمەیە:",
        "forgot_t": "وشەی نهێنیت لەبیر چووە؟",
        "forgot_hint": "ئیمەیڵی هەژمارەکەت بنووسە — کۆدی گەڕاندنەوەت بۆ دەنێرین.",
        "send_code": "کۆد بنێرە 📩",
        "code_sent_maybe": "ئەگەر ئەو ئیمەیڵە هەبێت، کۆدەکە لە ڕێگایە 📩",
        "reset_t": "وشەی نهێنی نوێ",
        "pw_reset_ok": "وشەی نهێنی گۆڕدرا — بچۆ ژوورەوە! ✅",
        "complete_t": "بەخێربێیتەوە! یەک هەنگاوی خێرا",
        "complete_sub": "کوردڕووم نوێکردنەوەیەکی گەورەی وەرگرت — تەنها جارێک زانیارییەکانت پشتڕاست بکەرەوە. ناوی بەکارهێنەرەکەت هەر هی تۆیە.",
        "stats_t": "ئامارەکان", "open_stats": "📊 ئاماری تەواو",
        "st_users": "هەموو بەکارهێنەران", "st_plus": "ئەندامانی پڵەس",
        "st_verified": "ئیمەیڵی پشتڕاستکراو", "st_active": "چالاک لەم هەفتەیە",
        "st_completed": "پرۆفایلی تەواوکراو", "st_bylevel": "بەپێی ئاستی خوێندن",
        "st_unis": "زانکۆکان", "st_colleges": "کۆلێژەکان",
        "st_deps": "بەشەکان", "st_school": "قۆناغەکانی قوتابخانە",
        "st_grades": "پۆلەکان", "st_jobs": "کارەکان",
        "st_growth": "بەکارهێنەری نوێ لە مانگدا",
        "smtp_t": "ناردنی ئیمەیڵ (SMTP)",
        "regopts_t": "هەڵبژاردەکانی تۆمارکردن (هەر یەکە لە دێڕێکدا)",
    },
}
for _l, _d in V23.items():
    T[_l].update(_d)

V24 = {
    "en": {
        "ferr_req": "This field is required",
        "ferr_email": "Please enter a valid email address",
        "ferr_short": "This is too short",
        "ferr_bad": "Please check this field",
        "kp_search": "Search…",
        "kp_none": "No results found",
        "regopts_hint": "Format: emoji | English | کوردی | عربي — only the English name is required. Example: 🩺 | Medicine | پزیشکی | الطب",
        "kc_t": "Are you sure?",
        "kc_yes": "Yes, do it",
        "kc_hint": "This action cannot be undone.",
    },
    "ar": {
        "ferr_req": "هذا الحقل مطلوب",
        "ferr_email": "يرجى إدخال بريد إلكتروني صحيح",
        "ferr_short": "قصير جدًا",
        "ferr_bad": "يرجى التحقق من هذا الحقل",
        "kp_search": "ابحث…",
        "kp_none": "لا توجد نتائج",
        "regopts_hint": "الصيغة: إيموجي | English | کوردی | عربي — الاسم الإنجليزي فقط إلزامي. مثال: 🩺 | Medicine | پزیشکی | الطب",
        "kc_t": "هل أنت متأكد؟",
        "kc_yes": "نعم، متأكد",
        "kc_hint": "لا يمكن التراجع عن هذا الإجراء.",
    },
    "ku": {
        "ferr_req": "ئەم خانەیە پێویستە پڕ بکرێتەوە",
        "ferr_email": "تکایە ئیمەیڵێکی دروست بنووسە",
        "ferr_short": "زۆر کورتە",
        "ferr_bad": "تکایە ئەم خانەیە بپشکنە",
        "kp_search": "بگەڕێ…",
        "kp_none": "هیچ ئەنجامێک نەدۆزرایەوە",
        "regopts_hint": "شێواز: ئیمۆجی | English | کوردی | عربي — تەنها ناوی ئینگلیزی پێویستە. نموونە: 🩺 | Medicine | پزیشکی | الطب",
        "kc_t": "دڵنیایت؟",
        "kc_yes": "بەڵێ، دڵنیام",
        "kc_hint": "ئەم کردارە ناگەڕێتەوە.",
    },
}
for _l, _d in V24.items():
    T[_l].update(_d)

V25 = {
    "en": {
        "chat_set_t": "Chat settings",
        "pinned_t": "Pinned messages", "pin_t": "Pin", "unpin_t": "Unpin",
        "no_pins": "No pinned messages yet",
        "mute_t": "Mute notifications", "unmute_t": "Unmute notifications",
        "clear_t": "Clear conversation (only for you)",
        "clear_q": "Clear this conversation? It disappears only for you — the other side keeps it.",
        "members_t": "Group members",
        "privacy_sec": "Privacy & messages",
        "allow_dm_t": "People can send you messages even if they aren't your friends",
        "private_acc_t": "Private account",
        "private_acc_hint": "Only your friends can see your posts, activity and details",
        "followers_t": "Followers", "following_t": "Following", "posts_t": "Posts",
        "follow_b": "Follow", "following_b": "Following",
        "private_note": "This account is private — send a friend request to see their profile",
        "no_posts": "No posts yet",
    },
    "ar": {
        "chat_set_t": "إعدادات المحادثة",
        "pinned_t": "الرسائل المثبتة", "pin_t": "تثبيت", "unpin_t": "إلغاء التثبيت",
        "no_pins": "لا توجد رسائل مثبتة بعد",
        "mute_t": "كتم الإشعارات", "unmute_t": "إلغاء كتم الإشعارات",
        "clear_t": "مسح المحادثة (لك فقط)",
        "clear_q": "مسح هذه المحادثة؟ ستختفي لديك فقط — الطرف الآخر يحتفظ بها.",
        "members_t": "أعضاء المجموعة",
        "privacy_sec": "الخصوصية والرسائل",
        "allow_dm_t": "يمكن للناس مراسلتك حتى لو لم يكونوا أصدقاءك",
        "private_acc_t": "حساب خاص",
        "private_acc_hint": "أصدقاؤك فقط يمكنهم رؤية منشوراتك ونشاطك وتفاصيلك",
        "followers_t": "المتابِعون", "following_t": "يتابع", "posts_t": "المنشورات",
        "follow_b": "متابعة", "following_b": "تتابعه",
        "private_note": "هذا الحساب خاص — أرسل طلب صداقة لرؤية ملفه الشخصي",
        "no_posts": "لا توجد منشورات بعد",
    },
    "ku": {
        "chat_set_t": "ڕێکخستنەکانی چات",
        "pinned_t": "پەیامە هەڵواسراوەکان", "pin_t": "هەڵواسین", "unpin_t": "لابردنی هەڵواسین",
        "no_pins": "هێشتا هیچ پەیامێک هەڵنەواسراوە",
        "mute_t": "بێدەنگکردنی ئاگادارییەکان", "unmute_t": "کردنەوەی ئاگادارییەکان",
        "clear_t": "سڕینەوەی گفتوگۆ (تەنها بۆ تۆ)",
        "clear_q": "ئەم گفتوگۆیە بسڕدرێتەوە؟ تەنها لای تۆ نامێنێت — لای بەرامبەر دەمێنێتەوە.",
        "members_t": "ئەندامانی گروپ",
        "privacy_sec": "تایبەتمەندی و پەیامەکان",
        "allow_dm_t": "خەڵک دەتوانن پەیامت بۆ بنێرن تەنانەت ئەگەر هاوڕێت نەبن",
        "private_acc_t": "هەژماری تایبەت",
        "private_acc_hint": "تەنها هاوڕێکانت دەتوانن پۆست و چالاکی و زانیارییەکانت ببینن",
        "followers_t": "فۆڵۆوەرەکان", "following_t": "فۆڵۆکراوەکان", "posts_t": "پۆستەکان",
        "follow_b": "فۆڵۆکردن", "following_b": "فۆڵۆکراوە",
        "private_note": "ئەم هەژمارە تایبەتە — داواکاری هاوڕێیەتی بنێرە بۆ بینینی پرۆفایلەکەی",
        "no_posts": "هێشتا هیچ پۆستێک نییە",
    },
}
for _l, _d in V25.items():
    T[_l].update(_d)



USERNAME_RE = r"(?!\.)(?!.*\.\.)[A-Za-z0-9_.]{3,20}(?<!\.)"

EDU_LEVELS = ("school", "bachelor", "master", "phd", "professor", "graduate")


def save_edu_fields(uid):
    """Store the optional education/work info from the current form."""
    lvl = request.form.get("edu_level", "")
    if lvl and lvl not in EDU_LEVELS:
        lvl = ""
    db = get_db()
    db.execute("UPDATE users SET edu_level=?, institution=?, college=?, department=?, "
               "stage=?, job_title=?, job_field=? WHERE id = ?",
               (lvl, request.form.get("institution", "").strip()[:80],
                request.form.get("college", "").strip()[:80],
                request.form.get("department", "").strip()[:80],
                request.form.get("stage", "").strip()[:30],
                request.form.get("job_title", "").strip()[:80],
                request.form.get("job_field", "").strip()[:80], uid))


def tr(key):
    lang = session.get("lang", "en")
    return T.get(lang, T["en"]).get(key, T["en"].get(key, key))


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.context_processor
def inject_globals():
    lang = session.get("lang", "en")
    s = get_settings()
    tagline = s.get(f"tagline_{lang}") or s.get("tagline_en", "")
    cu = current_user()
    theme = (cu["theme"] if cu and cu["theme"] else "dark")
    accent = (cu["accent"] if cu and cu["accent"] else s.get("accent_color", "#7c5cff"))
    pending = unread_n = unread_d = 0
    if cu:
        db = get_db()
        pending = db.execute(
            "SELECT COUNT(*) FROM friendships WHERE to_id = ? AND status='pending'",
            (cu["id"],)).fetchone()[0]
        unread_n = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (cu["id"],)).fetchone()[0]
        unread_d = db.execute(
            "SELECT COUNT(*) FROM dms WHERE to_id = ? AND is_read = 0",
            (cu["id"],)).fetchone()[0]
    logo = None
    logo_path = os.path.join(BASE_DIR, "static", "aikurd-logo.png")
    if os.path.exists(logo_path):
        logo = url_for("static", filename="aikurd-logo.png",
                       v=int(os.path.getmtime(logo_path)))
    sponsor_img = None
    sp_path = os.path.join(BASE_DIR, "static", "avatars", "sponsor.png")
    if os.path.exists(sp_path):
        sponsor_img = url_for("static", filename="avatars/sponsor.png",
                              v=int(os.path.getmtime(sp_path)))
    fib_qr_img = None
    qr_path = os.path.join(BASE_DIR, "static", "avatars", "fibqr.png")
    if os.path.exists(qr_path):
        fib_qr_img = url_for("static", filename="avatars/fibqr.png",
                             v=int(os.path.getmtime(qr_path)))
    return dict(t=tr, lang=lang, is_rtl=lang in RTL_LANGS, langs=LANGS,
                settings=s, tagline=tagline, today=date.today().isoformat(),
                cu=cu, theme=theme, accent=accent, pending_requests=pending,
                unread_notifs=unread_n, unread_dms=unread_d,
                av=avatar_url, BADGES=BADGES, site_logo=logo,
                sponsor_img=sponsor_img, fib_qr_img=fib_qr_img,
                app_version=APP_VERSION)


# ---------------------------------------------------------------- helpers
def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.before_request
def touch_last_seen():
    """Keep users.last_seen fresh (throttled to one write per minute)."""
    uid = session.get("user_id")
    if uid is None:
        return
    now = datetime.utcnow()
    last = session.get("_seen")
    try:
        fresh = last and (now - datetime.fromisoformat(last)).total_seconds() < 60
    except ValueError:
        fresh = False
    if not fresh:
        db = get_db()
        db.execute("UPDATE users SET last_seen = ? WHERE id = ?",
                   (now.isoformat(timespec="seconds"), uid))
        db.commit()
        session["_seen"] = now.isoformat(timespec="seconds")


@app.before_request
def force_complete_profile():
    uid = session.get("user_id")
    if uid is None:
        return
    ep = request.endpoint or ""
    if ep in ("complete_profile", "logout", "set_lang", "static", "service_worker",
              "favicon", "robots_txt", "sitemap_xml", "login", "register",
              "register_verify", "forgot", "reset_pw_page", "about",
              "api_pings") or ep.startswith("push_"):
        return
    row = get_db().execute("SELECT profile_v FROM users WHERE id = ?",
                           (uid,)).fetchone()
    if row and (row["profile_v"] or 0) < 2:
        return redirect(url_for("complete_profile"))


@app.route("/welcome-back", methods=["GET", "POST"])
def complete_profile():
    uid = session.get("user_id")
    if uid is None:
        return redirect(url_for("login"))
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if u is None:
        session.clear()
        return redirect(url_for("login"))
    if request.method == "POST":
        f = request.form
        first = f.get("first_name", "").strip()[:40]
        middle = f.get("middle_name", "").strip()[:40]
        last = f.get("last_name", "").strip()[:40]
        email = f.get("email", "").strip().lower()[:100]
        edu, edu_ok = parse_edu_wizard(f)
        err = None
        if not (first and middle and last):
            err = "err_fill"
        elif not re.fullmatch(EMAIL_RE, email):
            err = "err_email"
        elif db.execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE AND "
                        "email != '' AND id != ?", (email, uid)).fetchone():
            err = "err_email_used"
        elif not edu_ok:
            err = "err_fill"
        if err:
            flash(tr(err), "error")
            return render_template("complete_profile.html", u=u, old=f,
                                   **_wizard_ctx())
        full = " ".join(x for x in (first, middle, last) if x)
        db.execute("UPDATE users SET first_name=?, middle_name=?, last_name=?, "
                   "full_name=?, email=?, edu_level=?, institution=?, "
                   "school_level=?, grade=?, college=?, department=?, stage=?, "
                   "job_title=?, college_kind=?, profile_v=2 WHERE id=?",
                   (first, middle, last, full, email, edu["edu_level"],
                    edu["institution"], edu["school_level"], edu["grade"],
                    edu["college"], edu["department"], edu["stage"],
                    edu["job_title"], edu["college_kind"], uid))
        db.commit()
        flash(tr("ok_saved"), "ok")
        return redirect(url_for("dashboard"))
    return render_template("complete_profile.html", u=u, old=None, **_wizard_ctx())


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("login"))
        if current_user() is None:
            # stale login cookie pointing at an account that no longer exists
            # (e.g. the database was replaced) — log out cleanly instead of crashing
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if not user["is_admin"]:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@app.template_filter("mentions")
def mentions_filter(text):
    """Escape, then paint @usernames in the accent color."""
    from markupsafe import Markup, escape
    esc = str(escape(text or ""))
    esc = re.sub(r"@([A-Za-z0-9_.]{3,20})",
                 r'<span class="mention">@\1</span>', esc)
    return Markup(esc)


NOTIF_ICONS = {"friend_req": "👥", "friend_acc": "🤝", "group_msg": "💬",
               "group_add": "➕", "badge": "🏅", "dm": "✉️", "duel_req": "⚔️",
               "duel_acc": "⚔️", "duel_end": "🏆", "deadline": "⏰",
               "overdue": "🚨", "exam_soon": "📚", "homework": "📝",
               "weekly": "🏆", "mention": "📣", "plus_wait": "💳", "plus_on": "⭐",
               "follow": "➕"}


def push_text(lang, kind, actor):
    """Build the push body in the recipient's language (mirrors notifications page)."""
    tt = T.get(lang) or T["en"]
    txt = tt.get("ntf_" + kind, "")
    if kind == "badge":
        return f"{txt} {tt.get('badge_' + actor + '_n', actor)}"
    if kind in ("group_msg", "group_add", "duel_end", "deadline", "overdue",
                "exam_soon", "homework", "weekly", "plus_wait"):
        return f"{txt} “{actor}”"
    if kind == "plus_on":
        return txt
    return f"{actor} {txt}"


def _do_push(subs, payload, priv):
    """Deliver one payload to many subscriptions (runs in a background thread)."""
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return
    dead = []
    for s in subs:
        try:
            webpush({"endpoint": s["endpoint"],
                     "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                    data=json.dumps(payload), vapid_private_key=priv,
                    vapid_claims={"sub": "mailto:admin@aikurd.org"},
                    ttl=120, headers={"Urgency": "high"})
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (403, 404, 410):
                dead.append(s["endpoint"])
        except Exception:
            pass
    if dead:
        try:
            con = sqlite3.connect(DB_PATH)
            con.executemany("DELETE FROM push_subs WHERE endpoint = ?",
                            [(d,) for d in dead])
            con.commit()
            con.close()
        except Exception:
            pass


def push_to_user(uid, kind, actor="", link=""):
    """Fire a real push notification to every device this user subscribed."""
    db = get_db()
    subs = [dict(r) for r in db.execute(
        "SELECT * FROM push_subs WHERE user_id = ?", (uid,))]
    if not subs:
        return
    s = get_settings()
    priv = s.get("vapid_private", "")
    if not priv:
        return
    u = db.execute("SELECT lang FROM users WHERE id = ?", (uid,)).fetchone()
    lang = (u["lang"] if u and u["lang"] else "en")
    payload = {"title": f"{NOTIF_ICONS.get(kind, '🔔')} {s.get('site_name', 'KurdRoom')}",
               "body": push_text(lang, kind, actor),
               "url": link or "/", "tag": kind}
    threading.Thread(target=_do_push, args=(subs, payload, priv),
                     daemon=True).start()


def clear_notifs(*kinds, prefix=None):
    """Mark notifications read once the user has actually seen the thing
    they point at (opened the chat, the dashboard, the exams page…)."""
    uid = session.get("user_id")
    if uid is None:
        return
    q = "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0"
    args = [uid]
    if kinds:
        q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args += list(kinds)
    if prefix:
        q += " AND link LIKE ?"
        args.append(prefix + "%")
    db = get_db()
    cur = db.execute(q, args)
    if cur.rowcount:
        db.commit()


def notify(uid, kind, actor="", link=""):
    """Queue a notification (caller commits). Chat kinds collapse into one unread row."""
    db = get_db()
    if kind in ("group_msg", "dm"):
        db.execute("DELETE FROM notifications WHERE user_id = ? AND kind = ? "
                   "AND link = ? AND is_read = 0", (uid, kind, link))
    db.execute("INSERT INTO notifications(user_id, kind, actor, link, created_at) "
               "VALUES(?,?,?,?,?)",
               (uid, kind, actor, link, datetime.utcnow().isoformat(timespec="seconds")))
    try:
        push_to_user(uid, kind, actor, link)   # instant push to phone/desktop
    except Exception:
        pass


def chat_cleared_id(uid, kind, target_id):
    row = get_db().execute("SELECT cleared_id FROM chat_clears WHERE user_id = ? "
                           "AND kind = ? AND target_id = ?",
                           (uid, kind, target_id)).fetchone()
    return row["cleared_id"] if row else 0


def chat_muted(uid, kind, target_id):
    return get_db().execute("SELECT 1 FROM chat_mutes WHERE user_id = ? AND "
                            "kind = ? AND target_id = ?",
                            (uid, kind, target_id)).fetchone() is not None


def is_following(a, b):
    return get_db().execute("SELECT 1 FROM follows WHERE follower_id = ? AND "
                            "followed_id = ?", (a, b)).fetchone() is not None


def make_follow(a, b):
    get_db().execute("INSERT OR IGNORE INTO follows(follower_id, followed_id, "
                     "created_at) VALUES(?,?,?)",
                     (a, b, datetime.utcnow().isoformat(timespec="seconds")))


BADGES = {
    "first_plan": "🌱", "plans_10": "⚡", "plans_50": "🏆",
    "streak_7": "🔥", "streak_30": "🚀", "first_group": "🤝",
    "friends_5": "🦋", "habit_7": "💎",
}


def award_badges(uid):
    """Check thresholds and hand out any newly earned badges."""
    db = get_db()
    have = {r["code"] for r in
            db.execute("SELECT code FROM badges WHERE user_id = ?", (uid,))}
    earned = []
    done = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1",
                      (uid,)).fetchone()[0]
    if done >= 1:
        earned.append("first_plan")
    if done >= 10:
        earned.append("plans_10")
    if done >= 50:
        earned.append("plans_50")
    s = user_streak(uid)
    if s >= 7:
        earned.append("streak_7")
    if s >= 30:
        earned.append("streak_30")
    if db.execute("SELECT 1 FROM groups WHERE owner_id = ?", (uid,)).fetchone():
        earned.append("first_group")
    if len(get_friend_ids(uid)) >= 5:
        earned.append("friends_5")
    if any(habit_streak(h["id"]) >= 7 for h in
           db.execute("SELECT id FROM habits WHERE user_id = ?", (uid,)).fetchall()):
        earned.append("habit_7")
    for code in earned:
        if code not in have:
            db.execute("INSERT OR IGNORE INTO badges(user_id, code, earned_at) "
                       "VALUES(?,?,?)",
                       (uid, code, datetime.utcnow().isoformat(timespec="seconds")))
            notify(uid, "badge", actor=code)
    db.commit()


REACTION_EMOJIS = ["❤️", "👍", "😂", "🔥", "🎉", "😮"]


def user_xp(uid):
    """XP from everything the user has done; level grows as sqrt(xp)."""
    db = get_db()
    plans = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1",
                       (uid,)).fetchone()[0]
    habits = db.execute("SELECT COUNT(*) FROM habit_checks hc JOIN habits h "
                        "ON h.id = hc.habit_id WHERE h.user_id = ?", (uid,)).fetchone()[0]
    bcount = db.execute("SELECT COUNT(*) FROM badges WHERE user_id = ?",
                        (uid,)).fetchone()[0]
    pch_done = sum(1 for c in my_challenges(uid) if c["done"])
    xp = plans * 20 + habits * 10 + bcount * 50 + user_streak(uid) * 5 + pch_done * 100
    plus_row = db.execute("SELECT plus FROM users WHERE id = ?", (uid,)).fetchone()
    if plus_row and plus_row["plus"]:
        xp *= 2                     # ⭐ Plus perk: double XP
    level = int((xp / 100) ** 0.5) + 1
    floor_xp = 100 * (level - 1) ** 2
    next_xp = 100 * level ** 2
    pct = int((xp - floor_xp) * 100 / max(1, next_xp - floor_xp))
    return dict(xp=xp, level=level, pct=pct, next_xp=next_xp)


def week_window():
    from datetime import timedelta
    monday = date.today() - timedelta(days=date.today().weekday())
    return monday.isoformat(), (monday - timedelta(days=7)).isoformat()


def week_counts(uid, start_iso, end_iso=None):
    """Completed plans + habit checks in [start, end) — end None = open."""
    db = get_db()
    if end_iso:
        p = db.execute("SELECT COUNT(*) FROM plans WHERE user_id=? AND done=1 AND "
                       "substr(done_at,1,10) >= ? AND substr(done_at,1,10) < ?",
                       (uid, start_iso, end_iso)).fetchone()[0]
        h = db.execute("SELECT COUNT(*) FROM habit_checks hc JOIN habits ha ON "
                       "ha.id=hc.habit_id WHERE ha.user_id=? AND hc.day >= ? AND hc.day < ?",
                       (uid, start_iso, end_iso)).fetchone()[0]
    else:
        p = db.execute("SELECT COUNT(*) FROM plans WHERE user_id=? AND done=1 AND "
                       "substr(done_at,1,10) >= ?", (uid, start_iso)).fetchone()[0]
        h = db.execute("SELECT COUNT(*) FROM habit_checks hc JOIN habits ha ON "
                       "ha.id=hc.habit_id WHERE ha.user_id=? AND hc.day >= ?",
                       (uid, start_iso)).fetchone()[0]
    return p, h


def plans_done_between(uid, start_iso, end_iso):
    return get_db().execute(
        "SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1 AND "
        "substr(done_at,1,10) >= ? AND substr(done_at,1,10) <= ?",
        (uid, start_iso, end_iso)).fetchone()[0]


def my_challenges(uid):
    out = []
    for ch in get_db().execute(
            "SELECT * FROM personal_challenges WHERE user_id = ? ORDER BY id DESC "
            "LIMIT 6", (uid,)).fetchall():
        total = plans_done_between(uid, ch["start_day"], ch["end_day"])
        try:
            days_left = (date.fromisoformat(ch["end_day"]) - date.today()).days
        except ValueError:
            days_left = None
        out.append(dict(id=ch["id"], title=ch["title"], target=ch["target"],
                        total=total, pct=min(100, int(total * 100 / max(1, ch["target"]))),
                        days_left=days_left, done=total >= ch["target"]))
    return out


def finish_due_duels(uid):
    """Close any of this user's active duels whose time is up; notify both sides."""
    db = get_db()
    today_s = date.today().isoformat()
    for d in db.execute("SELECT * FROM duels WHERE status='active' AND end_day < ? "
                        "AND (from_id = ? OR to_id = ?)",
                        (today_s, uid, uid)).fetchall():
        a = plans_done_between(d["from_id"], d["start_day"], d["end_day"])
        b = plans_done_between(d["to_id"], d["start_day"], d["end_day"])
        winner = d["from_id"] if a > b else d["to_id"] if b > a else None
        db.execute("UPDATE duels SET status='done', winner_id = ? WHERE id = ?",
                   (winner, d["id"]))
        for uid2 in (d["from_id"], d["to_id"]):
            wname = ""
            if winner:
                w = db.execute("SELECT username FROM users WHERE id = ?",
                               (winner,)).fetchone()
                wname = w["username"] if w else ""
            notify(uid2, "duel_end", actor=wname, link=url_for("friends"))
    db.commit()


def challenge_progress(ch):
    """Total completed plans by all group members inside the challenge window."""
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM plans p JOIN group_members gm ON gm.user_id = p.user_id "
        "AND gm.group_id = ? WHERE p.done = 1 AND substr(p.done_at,1,10) >= ? "
        "AND substr(p.done_at,1,10) <= ?",
        (ch["group_id"], ch["start_day"], ch["end_day"])).fetchone()[0]
    pct = min(100, int(total * 100 / max(1, ch["target"])))
    days_left = None
    try:
        days_left = (date.fromisoformat(ch["end_day"]) - date.today()).days
    except ValueError:
        pass
    return total, pct, days_left


def user_badges(uid):
    return get_db().execute("SELECT * FROM badges WHERE user_id = ? ORDER BY earned_at",
                            (uid,)).fetchall()


def record_activity(uid):
    db = get_db()
    db.execute("INSERT OR IGNORE INTO activity(user_id, day) VALUES(?,?)",
               (uid, date.today().isoformat()))
    db.commit()


def _streak_from_days(days):
    """Consecutive-day streak ending today (or yesterday, so it isn't lost at midnight)."""
    from datetime import timedelta
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def user_streak(uid):
    rows = get_db().execute("SELECT day FROM activity WHERE user_id = ?", (uid,)).fetchall()
    return _streak_from_days({r["day"] for r in rows})


def habit_streak(habit_id):
    rows = get_db().execute("SELECT day FROM habit_checks WHERE habit_id = ?",
                            (habit_id,)).fetchall()
    return _streak_from_days({r["day"] for r in rows})


def get_friend_ids(uid):
    rows = get_db().execute(
        "SELECT from_id, to_id FROM friendships WHERE status='accepted' "
        "AND (from_id = ? OR to_id = ?)", (uid, uid)).fetchall()
    return {r["from_id"] if r["to_id"] == uid else r["to_id"] for r in rows}


def are_friends(a, b):
    return b in get_friend_ids(a)


def is_group_member(group_id, uid):
    return get_db().execute(
        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
        (group_id, uid)).fetchone() is not None


def member_group_or_403(group_id):
    g_row = get_db().execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if g_row is None:
        abort(404)
    if not is_group_member(group_id, session["user_id"]):
        abort(403)
    return g_row


def upcoming_meeting(first_meeting, frequency):
    """Next meeting date >= today, stepping by the group's frequency."""
    from datetime import timedelta
    if not first_meeting:
        return None
    try:
        d = date.fromisoformat(first_meeting)
    except ValueError:
        return None
    step = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(frequency, 7)
    while d < date.today():
        d += timedelta(days=step)
    return d


def random_quote():
    lang = session.get("lang", "en")
    row = get_db().execute("SELECT * FROM quotes ORDER BY RANDOM() LIMIT 1").fetchone()
    if row is None:
        return ""
    return row[f"text_{lang}"] or row["text_en"]


# ---------------------------------------------------------------- routes
@app.route("/lang/<code>")
def set_lang(code):
    if code in LANGS:
        session["lang"] = code
        if session.get("user_id"):
            db = get_db()
            db.execute("UPDATE users SET lang = ? WHERE id = ?",
                       (code, session["user_id"]))
            db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?",
                                (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["user_id"] = user["id"]
            db = get_db()
            db.execute("UPDATE users SET last_login = ? WHERE id = ?",
                       (datetime.utcnow().isoformat(timespec="seconds"), user["id"]))
            db.commit()
            return redirect(url_for("dashboard"))
        flash(tr("err_login"), "error")
    return render_template("login.html", quote=random_quote())


def send_email(to, subject, body):
    """Send a plain email using the SMTP settings from the admin panel."""
    st = get_settings()
    host = (st.get("smtp_host") or "").strip()
    user = (st.get("smtp_user") or "").strip()
    pw = st.get("smtp_pass") or ""
    if not host or not user:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = (st.get("smtp_from") or "").strip() or user
        msg["To"] = to
        port = int((st.get("smtp_port") or "587").strip() or 587)
        with smtplib.SMTP(host, port, timeout=20) as srv:
            srv.starttls()
            srv.login(user, pw)
            srv.send_message(msg)
        return True
    except Exception:
        return False


def smtp_ready():
    st = get_settings()
    return bool((st.get("smtp_host") or "").strip() and
                (st.get("smtp_user") or "").strip())


def _looks_emoji(seg):
    """True when a pipe segment is an emoji/symbol, not a name."""
    seg = seg.strip()
    if not seg or len(seg) > 6:
        return False
    return all(not ch.isalnum() for ch in seg if ch not in " \ufe0f\u200d")


def _parse_opt(raw):
    """'emoji | English | Kurdish | Arabic' -> dict (all but English optional)."""
    parts = [p.strip() for p in raw.split("|")]
    e = ""
    if parts and _looks_emoji(parts[0]):
        e = parts[0]
        parts = parts[1:]
    if not parts or not parts[0]:
        return None
    en = parts[0]
    ku = parts[1] if len(parts) > 1 and parts[1] else en
    ar = parts[2] if len(parts) > 2 and parts[2] else en
    return {"v": en, "ku": ku, "ar": ar, "e": e}


def reg_options_rich():
    st = get_settings()

    def plist(key):
        out = []
        for line in (st.get(key) or "").split("\n"):
            o = _parse_opt(line) if line.strip() else None
            if o:
                out.append(o)
        return out

    unis = plist("reg_universities")
    colleges = plist("reg_colleges")
    jobs = plist("reg_jobs")
    deps = {}
    for line in (st.get("reg_departments") or "").split("\n"):
        if ":" in line:
            c, rest = line.split(":", 1)
            co = _parse_opt(c)
            if not co:
                continue
            deps[co["v"]] = [o for o in (_parse_opt(x) for x in rest.split(","))
                             if o]
    return unis, colleges, deps, jobs


def reg_options():
    """Canonical (English) values — used for validation and storage."""
    unis, colleges, deps, jobs = reg_options_rich()
    return ([u["v"] for u in unis], [c["v"] for c in colleges],
            {k: [d["v"] for d in v] for k, v in deps.items()},
            [j["v"] for j in jobs])


def loc_opt(name):
    """Localize a stored canonical option name for the current language."""
    if not name:
        return name
    lang = session.get("lang", "en")
    if lang == "en":
        return name
    cache = getattr(g, "_loc_opt", None)
    if cache is None:
        cache = {}
        unis, colleges, deps, jobs = reg_options_rich()
        for lst in (unis, colleges, jobs):
            for o in lst:
                cache[o["v"]] = o
        for v in deps.values():
            for o in v:
                cache.setdefault(o["v"], o)
        g._loc_opt = cache
    o = cache.get(name)
    return o[lang] if o else name


app.jinja_env.globals["loc_opt"] = loc_opt


EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"


def strong_pw(pw):
    return (len(pw) >= 8 and re.search(r"[A-Z]", pw)
            and re.search(r"[a-z]", pw) and re.search(r"[0-9]", pw))


def parse_edu_wizard(form):
    """Validate the education step. Returns (data, ok)."""
    lvl = form.get("edu_level", "")
    d = dict(edu_level="", institution="", school_level="", grade="", college="",
             department="", stage="", job_title="", college_kind="")
    if lvl == "school":
        d["edu_level"] = "school"
        d["institution"] = form.get("school_name", "").strip()[:100]
        d["school_level"] = form.get("school_level", "")
        d["grade"] = (form.get("grade", "") or "").strip()[:4]
        rng = {"elementary": (1, 6), "intermediate": (7, 9),
               "high": (10, 12)}.get(d["school_level"])
        try:
            g = int(d["grade"])
        except ValueError:
            g = -1
        if not d["institution"] or not rng or not rng[0] <= g <= rng[1]:
            return d, False
    elif lvl in ("university", "master", "phd", "professor"):
        d["edu_level"] = "bachelor" if lvl == "university" else lvl
        unis, colleges, deps, _ = reg_options()
        d["institution"] = form.get("university", "").strip()[:120]
        kind = form.get("college_kind", "")
        d["college_kind"] = kind
        if d["institution"] not in unis or kind not in ("college", "institute"):
            return d, False
        if kind == "college":
            d["college"] = form.get("college", "").strip()[:120]
            d["department"] = form.get("department", "").strip()[:120]
            if d["college"] not in colleges or                     d["department"] not in deps.get(d["college"], []):
                return d, False
        else:
            d["college"] = form.get("institute_name", "").strip()[:120]
            d["department"] = form.get("institute_dept", "").strip()[:120]
            if not d["college"] or not d["department"]:
                return d, False
        if lvl == "university":
            d["stage"] = form.get("stage", "").strip()[:40]
            if not d["stage"]:
                return d, False
    elif lvl == "others":
        d["edu_level"] = "graduate"
        _, _, _, jobs = reg_options()
        d["job_title"] = form.get("job", "").strip()[:80]
        if d["job_title"] not in jobs:
            return d, False
    else:
        return d, False
    return d, True


def _wizard_ctx():
    unis, colleges, deps, jobs = reg_options_rich()
    lang = session.get("lang", "en")

    def pack(lst):
        return [{"v": o["v"], "l": o.get(lang) or o["v"], "e": o["e"]}
                for o in lst]

    return dict(unis=pack(unis), colleges=pack(colleges),
                deps={k: pack(v) for k, v in deps.items()}, jobs=pack(jobs),
                uni_vals=[o["v"] for o in unis],
                job_vals=[o["v"] for o in jobs])


@app.route("/register", methods=["GET", "POST"])
def register():
    if get_settings().get("allow_registration") != "1":
        flash(tr("err_reg_closed"), "error")
        return render_template("register.html", quote=random_quote(), closed=True,
                               **_wizard_ctx())
    if request.method == "POST":
        db = get_db()
        f = request.form
        username = f.get("username", "").strip()
        first = f.get("first_name", "").strip()[:40]
        middle = f.get("middle_name", "").strip()[:40]
        last = f.get("last_name", "").strip()[:40]
        email = f.get("email", "").strip().lower()[:100]
        password = f.get("password", "")
        edu, edu_ok = parse_edu_wizard(f)
        err = None
        if not re.fullmatch(USERNAME_RE, username):
            err = "err_username"
        elif db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
                        (username,)).fetchone():
            err = "err_user_exists"
        elif not (first and middle and last):
            err = "err_fill"
        elif not re.fullmatch(EMAIL_RE, email):
            err = "err_email"
        elif db.execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE AND "
                        "email != ''", (email,)).fetchone():
            err = "err_email_used"
        elif not edu_ok:
            err = "err_fill"
        elif not strong_pw(password):
            err = "err_pw_weak"
        if err:
            flash(tr(err), "error")
            return render_template("register.html", quote=random_quote(),
                                   closed=False, old=f, **_wizard_ctx())
        pending = dict(username=username, first=first, middle=middle, last=last,
                       email=email, pw_hash=generate_password_hash(password), **edu)
        if smtp_ready():
            code = f"{secrets.randbelow(900000) + 100000}"
            if send_email(email, tr("verify_subject"),
                          tr("verify_body") + " " + code):
                session["preg"] = pending
                session["preg_code"] = code
                session["preg_t"] = time.time()
                session["preg_tries"] = 0
                session["preg_resends"] = 0
                return redirect(url_for("register_verify"))
            flash(tr("err_email_send"), "error")
            return render_template("register.html", quote=random_quote(),
                                   closed=False, old=f, **_wizard_ctx())
        # no SMTP configured -> create the account directly
        return _create_user_from_pending(pending, verified=False)
    return render_template("register.html", quote=random_quote(), closed=False,
                           old=None, **_wizard_ctx())


def _create_user_from_pending(p, verified):
    db = get_db()
    full = " ".join(x for x in (p["first"], p["middle"], p["last"]) if x)
    try:
        cur = db.execute(
            "INSERT INTO users(username, password_hash, created_at, full_name, "
            "email, first_name, middle_name, last_name, edu_level, institution, "
            "school_level, grade, college, department, stage, job_title, "
            "college_kind, email_verified, profile_v, lang) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["username"], p["pw_hash"],
             datetime.utcnow().isoformat(timespec="seconds"), full, p["email"],
             p["first"], p["middle"], p["last"], p["edu_level"], p["institution"],
             p["school_level"], p["grade"], p["college"], p["department"],
             p["stage"], p["job_title"], p["college_kind"],
             1 if verified else 0, 2, session.get("lang", "en")))
    except sqlite3.IntegrityError:
        flash(tr("err_user_exists"), "error")
        return redirect(url_for("register"))
    db.commit()
    session.clear()
    session.permanent = True
    session["user_id"] = cur.lastrowid
    flash(tr("ok_registered"), "ok")
    return redirect(url_for("dashboard"))


@app.route("/register/verify", methods=["GET", "POST"])
def register_verify():
    p = session.get("preg")
    if not p:
        return redirect(url_for("register"))
    if time.time() - session.get("preg_t", 0) > 900:
        session.pop("preg", None)
        flash(tr("code_expired"), "error")
        return redirect(url_for("register"))
    if request.method == "POST":
        if request.form.get("resend"):
            if session.get("preg_resends", 0) < 3:
                code = f"{secrets.randbelow(900000) + 100000}"
                if send_email(p["email"], tr("verify_subject"),
                              tr("verify_body") + " " + code):
                    session["preg_code"] = code
                    session["preg_t"] = time.time()
                    session["preg_resends"] = session.get("preg_resends", 0) + 1
                    flash(tr("code_resent"), "ok")
            return redirect(url_for("register_verify"))
        code = request.form.get("code", "").strip()
        session["preg_tries"] = session.get("preg_tries", 0) + 1
        if session["preg_tries"] > 6:
            session.pop("preg", None)
            flash(tr("code_expired"), "error")
            return redirect(url_for("register"))
        if code == session.get("preg_code"):
            return _create_user_from_pending(p, verified=True)
        flash(tr("err_code"), "error")
    return render_template("verify.html", email=p["email"], mode="register")


# ------------------------------------------------------------- forgot password
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not smtp_ready():
            flash(tr("err_email_send"), "error")
            return redirect(url_for("forgot"))
        u = get_db().execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE "
                             "AND email != ''", (email,)).fetchone()
        if u:
            code = f"{secrets.randbelow(900000) + 100000}"
            if send_email(email, tr("verify_subject"),
                          tr("reset_body") + " " + code):
                session["fp_uid"] = u["id"]
                session["fp_code"] = code
                session["fp_t"] = time.time()
                session["fp_tries"] = 0
        # always claim success so nobody can probe which emails exist
        flash(tr("code_sent_maybe"), "ok")
        return redirect(url_for("reset_pw_page"))
    return render_template("forgot.html")


@app.route("/reset", methods=["GET", "POST"])
def reset_pw_page():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        pw = request.form.get("password", "")
        uid = session.get("fp_uid")
        ok_time = time.time() - session.get("fp_t", 0) < 900
        session["fp_tries"] = session.get("fp_tries", 0) + 1
        if not uid or not ok_time or session["fp_tries"] > 6:
            session.pop("fp_uid", None)
            flash(tr("code_expired"), "error")
            return redirect(url_for("forgot"))
        if code != session.get("fp_code"):
            flash(tr("err_code"), "error")
        elif not strong_pw(pw):
            flash(tr("err_pw_weak"), "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(pw), uid))
            db.commit()
            for k in ("fp_uid", "fp_code", "fp_t", "fp_tries"):
                session.pop(k, None)
            flash(tr("pw_reset_ok"), "ok")
            return redirect(url_for("login"))
    return render_template("reset.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    clear_notifs("deadline", "overdue")   # seen the plans -> warnings are done
    rows = get_db().execute(
        "SELECT * FROM plans WHERE user_id = ? ORDER BY done ASC, "
        "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "COALESCE(due_date,'9999') ASC, created_at DESC", (user["id"],)).fetchall()
    short_plans = [r for r in rows if r["plan_type"] == "short"]
    long_plans = [r for r in rows if r["plan_type"] == "long"]
    total = len(rows)
    done = sum(1 for r in rows if r["done"])
    pct = int(done * 100 / total) if total else 0
    # friends' activity today
    feed = []
    db = get_db()
    today_s = date.today().isoformat()
    for fid in sorted(get_friend_ids(user["id"])):
        fr = db.execute("SELECT id, username FROM users WHERE id = ?", (fid,)).fetchone()
        if fr is None:
            continue
        p_n = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1 "
                         "AND substr(done_at,1,10) = ?", (fid, today_s)).fetchone()[0]
        h_n = db.execute("SELECT COUNT(*) FROM habit_checks hc JOIN habits h "
                         "ON h.id = hc.habit_id WHERE h.user_id = ? AND hc.day = ?",
                         (fid, today_s)).fetchone()[0]
        b_new = [r["code"] for r in db.execute(
            "SELECT code FROM badges WHERE user_id = ? AND substr(earned_at,1,10) = ?",
            (fid, today_s))]
        if p_n or h_n or b_new:
            feed.append(dict(user=fr, plans=p_n, habits=h_n, badges=b_new))
    feed.sort(key=lambda x: x["plans"] + x["habits"], reverse=True)
    # daily goal ring + weekly report
    today_done = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1 "
                            "AND substr(done_at,1,10) = ?",
                            (user["id"], today_s)).fetchone()[0]
    goal = user["daily_goal"] or 3
    wk_start, lastwk_start = week_window()
    wp, wh = week_counts(user["id"], wk_start)
    lp, lh = week_counts(user["id"], lastwk_start, wk_start)
    return render_template("dashboard.html", user=user, short_plans=short_plans,
                           long_plans=long_plans, quote=random_quote(),
                           total=total, done_count=done, pct=pct,
                           streak=user_streak(user["id"]), feed=feed,
                           today_done=today_done, goal=goal,
                           ring_pct=min(100, int(today_done * 100 / max(1, goal))),
                           wk=dict(plans=wp, habits=wh),
                           lastwk=dict(plans=lp, habits=lh),
                           xpinfo=user_xp(user["id"]),
                           pchallenges=my_challenges(user["id"]),
                           stories=story_strip(user["id"]))


@app.route("/plan/add", methods=["POST"])
@login_required
def plan_add():
    title = request.form.get("title", "").strip()
    if not title:
        return redirect(url_for("dashboard"))
    details = request.form.get("details", "").strip()
    plan_type = request.form.get("plan_type", "short")
    if plan_type not in ("short", "long"):
        plan_type = "short"
    priority = request.form.get("priority", "medium")
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    repeat = request.form.get("repeat", "")
    if repeat not in ("daily", "weekly"):
        repeat = ""
    due = request.form.get("due_date") or None
    db = get_db()
    db.execute("INSERT INTO plans(user_id, title, details, plan_type, priority, "
               "due_date, created_at, repeat) VALUES(?,?,?,?,?,?,?,?)",
               (session["user_id"], title, details, plan_type, priority, due,
                datetime.utcnow().isoformat(timespec="seconds"), repeat))
    db.commit()
    return redirect(url_for("dashboard"))


def own_plan_or_404(plan_id):
    row = get_db().execute("SELECT * FROM plans WHERE id = ? AND user_id = ?",
                           (plan_id, session["user_id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/plan/<int:plan_id>/toggle", methods=["POST"])
@login_required
def plan_toggle(plan_id):
    row = own_plan_or_404(plan_id)
    db = get_db()
    now_done = 0 if row["done"] else 1
    db.execute("UPDATE plans SET done = ?, done_at = ? WHERE id = ?",
               (now_done,
                datetime.utcnow().isoformat(timespec="seconds") if now_done else None,
                plan_id))
    if now_done and (row["repeat"] or ""):
        # recurring plan: spawn the next occurrence automatically
        from datetime import timedelta
        step = 1 if row["repeat"] == "daily" else 7
        next_due = (date.today() + timedelta(days=step)).isoformat()
        db.execute("INSERT INTO plans(user_id, title, details, plan_type, priority, "
                   "due_date, created_at, repeat) VALUES(?,?,?,?,?,?,?,?)",
                   (row["user_id"], row["title"], row["details"], row["plan_type"],
                    row["priority"], next_due,
                    datetime.utcnow().isoformat(timespec="seconds"), row["repeat"]))
    db.commit()
    if now_done:
        record_activity(session["user_id"])
        award_badges(session["user_id"])
        big = "1" if row["priority"] == "high" else "0"
        return redirect(url_for("dashboard", celebrate=1, big=big))
    return redirect(url_for("dashboard"))


@app.route("/plan/<int:plan_id>/delete", methods=["POST"])
@login_required
def plan_delete(plan_id):
    own_plan_or_404(plan_id)
    db = get_db()
    db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    db.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- personalization
@app.route("/prefs/theme", methods=["POST"])
@login_required
def prefs_theme():
    db = get_db()
    cur = db.execute("SELECT theme FROM users WHERE id = ?",
                     (session["user_id"],)).fetchone()
    new = "light" if (cur["theme"] or "dark") == "dark" else "dark"
    db.execute("UPDATE users SET theme = ? WHERE id = ?", (new, session["user_id"]))
    db.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/prefs/accent", methods=["POST"])
@login_required
def prefs_accent():
    accent = request.form.get("accent", "").strip()
    import re
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = None
    db = get_db()
    db.execute("UPDATE users SET accent = ? WHERE id = ?", (accent, session["user_id"]))
    db.commit()
    return redirect(request.referrer or url_for("dashboard"))


# ---------------------------------------------------------------- focus timer
@app.route("/focus")
@login_required
def focus():
    return render_template("focus.html", user=current_user(), quote=random_quote(),
                           studying=studying_friends(session["user_id"]))


# ---------------------------------------------------------------- habits
@app.route("/habits")
@login_required
def habits():
    from datetime import timedelta
    db = get_db()
    uid = session["user_id"]
    rows = db.execute("SELECT * FROM habits WHERE user_id = ? ORDER BY id",
                      (uid,)).fetchall()
    week = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    items = []
    for h in rows:
        checks = {r["day"] for r in db.execute(
            "SELECT day FROM habit_checks WHERE habit_id = ?", (h["id"],))}
        items.append(dict(id=h["id"], name=h["name"],
                          streak=_streak_from_days(checks),
                          week=[(d, d in checks) for d in week],
                          done_today=date.today().isoformat() in checks))
    return render_template("habits.html", user=current_user(), habits=items,
                           quote=random_quote())


@app.route("/habit/add", methods=["POST"])
@login_required
def habit_add():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO habits(user_id, name, created_at) VALUES(?,?,?)",
                   (session["user_id"], name,
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("habits"))


def own_habit_or_404(habit_id):
    row = get_db().execute("SELECT * FROM habits WHERE id = ? AND user_id = ?",
                           (habit_id, session["user_id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/habit/<int:habit_id>/toggle", methods=["POST"])
@login_required
def habit_toggle(habit_id):
    own_habit_or_404(habit_id)
    db = get_db()
    today = date.today().isoformat()
    hit = db.execute("SELECT 1 FROM habit_checks WHERE habit_id = ? AND day = ?",
                     (habit_id, today)).fetchone()
    if hit:
        db.execute("DELETE FROM habit_checks WHERE habit_id = ? AND day = ?",
                   (habit_id, today))
    else:
        db.execute("INSERT INTO habit_checks(habit_id, day) VALUES(?,?)",
                   (habit_id, today))
        record_activity(session["user_id"])
    db.commit()
    if not hit:
        award_badges(session["user_id"])
    return redirect(url_for("habits"))


@app.route("/habit/<int:habit_id>/delete", methods=["POST"])
@login_required
def habit_delete(habit_id):
    own_habit_or_404(habit_id)
    db = get_db()
    db.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    db.commit()
    return redirect(url_for("habits"))


# ---------------------------------------------------------------- notes
@app.route("/notes")
@login_required
def notes():
    rows = get_db().execute(
        "SELECT * FROM notes WHERE user_id = ? ORDER BY updated_at DESC",
        (session["user_id"],)).fetchall()
    return render_template("notes.html", user=current_user(), notes=rows,
                           quote=random_quote())


@app.route("/note/add", methods=["POST"])
@login_required
def note_add():
    db = get_db()
    db.execute("INSERT INTO notes(user_id, title, content, updated_at) VALUES(?,?,?,?)",
               (session["user_id"], request.form.get("title", "").strip(),
                request.form.get("content", "").strip(),
                datetime.utcnow().isoformat(timespec="seconds")))
    db.commit()
    return redirect(url_for("notes"))


def own_note_or_404(note_id):
    row = get_db().execute("SELECT * FROM notes WHERE id = ? AND user_id = ?",
                           (note_id, session["user_id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/note/<int:note_id>/update", methods=["POST"])
@login_required
def note_update(note_id):
    own_note_or_404(note_id)
    db = get_db()
    db.execute("UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
               (request.form.get("title", "").strip(),
                request.form.get("content", "").strip(),
                datetime.utcnow().isoformat(timespec="seconds"), note_id))
    db.commit()
    flash(tr("ok_saved"), "ok")
    return redirect(url_for("notes"))


@app.route("/note/<int:note_id>/delete", methods=["POST"])
@login_required
def note_delete(note_id):
    own_note_or_404(note_id)
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()
    return redirect(url_for("notes"))


# ---------------------------------------------------------------- university
DAY_KEYS = ["day_sat", "day_sun", "day_mon", "day_tue", "day_wed", "day_thu", "day_fri"]


@app.route("/university")
@login_required
def university():
    db = get_db()
    uid = session["user_id"]
    clear_notifs("exam_soon", "homework")  # seen the page -> reminders done
    hw_rows = db.execute(
        "SELECT * FROM homework WHERE user_id = ? ORDER BY done ASC, "
        "CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date ASC, id DESC",
        (uid,)).fetchall()
    today_iso = date.today().isoformat()
    homework = [dict(h, overdue=(h["due_date"] and h["due_date"] < today_iso
                                 and not h["done"]),
                     due_today=(h["due_date"] == today_iso and not h["done"]))
                for h in hw_rows]
    exams = db.execute("SELECT * FROM exams WHERE user_id = ? ORDER BY exam_date",
                       (uid,)).fetchall()
    today_d = date.today()
    exam_items = []
    for e in exams:
        try:
            left = (date.fromisoformat(e["exam_date"]) - today_d).days
        except ValueError:
            left = None
        exam_items.append(dict(id=e["id"], subject=e["subject"], note=e["note"],
                               exam_date=e["exam_date"], left=left))
    classes = db.execute("SELECT * FROM timetable WHERE user_id = ? "
                         "ORDER BY day, start_time", (uid,)).fetchall()
    days_used = sorted({c["day"] for c in classes} | {1, 2, 3, 4, 5})  # Sun–Thu always
    grid = {d: [c for c in classes if c["day"] == d] for d in days_used}
    cards = db.execute("SELECT * FROM flashcards WHERE user_id = ? ORDER BY subject, id",
                       (uid,)).fetchall()
    subjects = sorted({c["subject"] for c in cards})
    fc_data = [dict(s=c["subject"], q=c["question"], a=c["answer"]) for c in cards]
    return render_template("university.html", user=current_user(), exams=exam_items,
                           grid=grid, day_keys=DAY_KEYS, cards=cards,
                           subjects=subjects, fc_data=fc_data, homework=homework,
                           quote=random_quote())


# ---------------------------------------------------------------- homework
@app.route("/homework/add", methods=["POST"])
@login_required
def homework_add():
    title = request.form.get("title", "").strip()
    if title:
        db = get_db()
        db.execute("INSERT INTO homework(user_id, subject, title, details, due_date, "
                   "created_at) VALUES(?,?,?,?,?,?)",
                   (session["user_id"], request.form.get("subject", "").strip()[:60],
                    title[:120], request.form.get("details", "").strip()[:300],
                    request.form.get("due_date", "").strip()[:10],
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("university") + "#homework")


@app.route("/homework/<int:hw_id>/toggle", methods=["POST"])
@login_required
def homework_toggle(hw_id):
    db = get_db()
    db.execute("UPDATE homework SET done = 1 - done WHERE id = ? AND user_id = ?",
               (hw_id, session["user_id"]))
    db.commit()
    return redirect(url_for("university") + "#homework")


@app.route("/homework/<int:hw_id>/delete", methods=["POST"])
@login_required
def homework_delete(hw_id):
    db = get_db()
    db.execute("DELETE FROM homework WHERE id = ? AND user_id = ?",
               (hw_id, session["user_id"]))
    db.commit()
    return redirect(url_for("university") + "#homework")


# ---------------------------------------------------------------- KurdRoom Plus
PLUS_PHONE = "+9647518962161"


@app.route("/plus")
@login_required
def plus_page():
    qr_path = os.path.join(BASE_DIR, "static", "avatars", "fibqr.png")
    fib_qr = None
    if os.path.exists(qr_path):
        fib_qr = url_for("static", filename="avatars/fibqr.png",
                         v=int(os.path.getmtime(qr_path)))
    phone = (get_settings().get("plus_phone") or "").strip() or PLUS_PHONE
    return render_template("plus.html", user=current_user(), phone=phone,
                           fib_qr=fib_qr)


@app.route("/plus/paid", methods=["POST"])
@login_required
def plus_paid():
    method = request.form.get("method", "")[:20] or "?"
    plan = request.form.get("plan", "")[:20] or "?"
    plan_label = {"monthly": "5,000 / Monthly",
                  "yearly": "45,000 / Yearly"}.get(plan, plan)
    db = get_db()
    db.execute("INSERT INTO feedback(user_id, message, rating, created_at) "
               "VALUES(?,?,?,?)",
               (session["user_id"],
                f"💳⭐ PLUS PAYMENT — {plan_label} via {method} to "
                f"{(get_settings().get('plus_phone') or '').strip() or PLUS_PHONE}. "
                "Please check and activate!", None,
                datetime.utcnow().isoformat(timespec="seconds")))
    notify(session["user_id"], "plus_wait", actor=f"{plan_label} · {method}",
           link=url_for("plus_page"))
    db.commit()
    flash(tr("pay_success"), "ok")
    return redirect(url_for("plus_page"))


@app.route("/about")
def about():
    return render_template("about.html", user=current_user())


# ---------------------------------------------------------------- stories (24h)
STORY_CUTOFF_H = 24


def _story_cutoff():
    return (datetime.utcnow() - _td(hours=STORY_CUTOFF_H)).isoformat(timespec="seconds")


def story_strip(uid):
    """People to show in the stories bar: me first, then friends with stories."""
    db = get_db()
    cutoff = _story_cutoff()
    out = []
    for fid in [uid] + sorted(get_friend_ids(uid)):
        ids = [r[0] for r in db.execute(
            "SELECT id FROM stories WHERE user_id = ? AND created_at > ?",
            (fid, cutoff))]
        if not ids and fid != uid:
            continue
        unseen = False
        if ids:
            ph = ",".join("?" * len(ids))
            seen = {r[0] for r in db.execute(
                f"SELECT story_id FROM story_views WHERE user_id = ? "
                f"AND story_id IN ({ph})", [uid] + ids)}
            unseen = any(i not in seen for i in ids)
        u = db.execute("SELECT id, username, full_name FROM users WHERE id = ?",
                       (fid,)).fetchone()
        out.append(dict(id=fid, username=u["username"],
                        name=u["full_name"] or u["username"],
                        n=len(ids), unseen=unseen, me=(fid == uid)))
    return out


@app.route("/story/add", methods=["POST"])
@login_required
def story_add():
    content = request.form.get("content", "").strip()
    bg = request.form.get("bg", type=int) or 1
    if content:
        db = get_db()
        max_bg = 9 if current_user()["plus"] else 6   # ⭐ Plus: 3 exclusive styles
        db.execute("INSERT INTO stories(user_id, content, bg, created_at) "
                   "VALUES(?,?,?,?)",
                   (session["user_id"], content[:220], min(max(bg, 1), max_bg),
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/stories")
@login_required
def stories_json():
    db = get_db()
    uid = session["user_id"]
    cutoff = _story_cutoff()
    out = []
    for p in story_strip(uid):
        rows = db.execute(
            "SELECT * FROM stories WHERE user_id = ? AND created_at > ? "
            "ORDER BY id ASC", (p["id"], cutoff)).fetchall()
        if not rows:
            continue
        items = []
        for s in rows:
            item = {"id": s["id"], "content": s["content"], "bg": s["bg"],
                    "ts": s["created_at"]}
            if p["me"]:
                item["views"] = db.execute(
                    "SELECT COUNT(*) FROM story_views WHERE story_id = ?",
                    (s["id"],)).fetchone()[0]
            items.append(item)
        out.append({"uid": p["id"], "username": p["username"], "name": p["name"],
                    "me": p["me"], "stories": items})
    return {"people": out}


@app.route("/story/<int:story_id>/seen", methods=["POST"])
@login_required
def story_seen(story_id):
    db = get_db()
    s = db.execute("SELECT user_id FROM stories WHERE id = ?", (story_id,)).fetchone()
    if s and s["user_id"] != session["user_id"] \
            and are_friends(session["user_id"], s["user_id"]):
        db.execute("INSERT OR IGNORE INTO story_views(story_id, user_id) VALUES(?,?)",
                   (story_id, session["user_id"]))
        db.commit()
    return {"ok": 1}


@app.route("/story/<int:story_id>/delete", methods=["POST"])
@login_required
def story_delete(story_id):
    db = get_db()
    db.execute("DELETE FROM stories WHERE id = ? AND user_id = ?",
               (story_id, session["user_id"]))
    db.commit()
    return {"ok": 1}


# ---------------------------------------------------------------- live studying
@app.route("/focus/ping", methods=["POST"])
@login_required
def focus_ping():
    minutes = request.form.get("minutes", 0, type=int)
    label = request.form.get("label", "").strip()[:40]
    until = "" if minutes <= 0 else \
        (datetime.utcnow() + _td(minutes=min(minutes, 240))).isoformat(timespec="seconds")
    db = get_db()
    db.execute("UPDATE users SET studying_until = ?, studying_label = ? WHERE id = ?",
               (until, label if until else "", session["user_id"]))
    db.commit()
    return {"ok": 1}


def studying_friends(uid):
    db = get_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    out = []
    for fid in sorted(get_friend_ids(uid)):
        u = db.execute("SELECT id, username, full_name, studying_until, "
                       "studying_label FROM users WHERE id = ?", (fid,)).fetchone()
        if u and u["studying_until"] and u["studying_until"] > now:
            out.append(u)
    return out


# ---------------------------------------------------------------- timetable share
@app.route("/timetable/share", methods=["POST"])
@login_required
def timetable_share():
    db = get_db()
    uid = session["user_id"]
    if not db.execute("SELECT 1 FROM timetable WHERE user_id = ?", (uid,)).fetchone():
        return redirect(url_for("university"))
    db.execute("DELETE FROM tt_shares WHERE user_id = ?", (uid,))
    code = secrets.token_hex(3).upper()
    db.execute("INSERT INTO tt_shares(code, user_id, created_at) VALUES(?,?,?)",
               (code, uid, datetime.utcnow().isoformat(timespec="seconds")))
    db.commit()
    flash(tr("tt_share_hint") + "  📋 " + code, "ok")
    return redirect(url_for("university") + "#timetable")


@app.route("/timetable/import", methods=["POST"])
@login_required
def timetable_import():
    code = request.form.get("code", "").strip().upper()
    db = get_db()
    uid = session["user_id"]
    share = db.execute("SELECT * FROM tt_shares WHERE code = ?", (code,)).fetchone()
    if not share or share["user_id"] == uid:
        flash(tr("tt_badcode"), "error")
    else:
        db.execute("DELETE FROM timetable WHERE user_id = ?", (uid,))
        for c in db.execute("SELECT * FROM timetable WHERE user_id = ?",
                            (share["user_id"],)).fetchall():
            db.execute("INSERT INTO timetable(user_id, day, subject, room, "
                       "start_time, end_time) VALUES(?,?,?,?,?,?)",
                       (uid, c["day"], c["subject"], c["room"],
                        c["start_time"], c["end_time"]))
        db.commit()
        flash(tr("tt_imported"), "ok")
    return redirect(url_for("university") + "#timetable")


def group_extras(group_id):
    """Files and shared decks for a group page."""
    db = get_db()
    files = db.execute(
        "SELECT f.*, u.username FROM group_files f JOIN users u ON u.id = f.user_id "
        "WHERE f.group_id = ? ORDER BY f.id DESC", (group_id,)).fetchall()
    decks = []
    for d in db.execute(
            "SELECT d.*, u.username FROM group_decks d JOIN users u ON u.id = d.user_id "
            "WHERE d.group_id = ? ORDER BY d.id", (group_id,)).fetchall():
        cards = db.execute(
            "SELECT question, answer FROM flashcards WHERE user_id = ? AND subject = ? "
            "ORDER BY id", (d["user_id"], d["subject"])).fetchall()
        decks.append(dict(id=d["id"], subject=d["subject"], username=d["username"],
                          user_id=d["user_id"],
                          cards=[dict(q=c["question"], a=c["answer"]) for c in cards]))
    return files, decks


@app.route("/exam/add", methods=["POST"])
@login_required
def exam_add():
    subject = request.form.get("subject", "").strip()
    exam_date = request.form.get("exam_date", "")
    if subject and exam_date:
        db = get_db()
        db.execute("INSERT INTO exams(user_id, subject, exam_date, note) VALUES(?,?,?,?)",
                   (session["user_id"], subject, exam_date,
                    request.form.get("note", "").strip()))
        db.commit()
    return redirect(url_for("university"))


@app.route("/exam/<int:exam_id>/delete", methods=["POST"])
@login_required
def exam_delete(exam_id):
    db = get_db()
    db.execute("DELETE FROM exams WHERE id = ? AND user_id = ?",
               (exam_id, session["user_id"]))
    db.commit()
    return redirect(url_for("university"))


@app.route("/class/add", methods=["POST"])
@login_required
def class_add():
    subject = request.form.get("subject", "").strip()
    try:
        day = int(request.form.get("day", "1"))
    except ValueError:
        day = 1
    start = request.form.get("start_time", "")
    if subject and start and 0 <= day <= 6:
        db = get_db()
        db.execute("INSERT INTO timetable(user_id, day, subject, start_time, end_time, room)"
                   " VALUES(?,?,?,?,?,?)",
                   (session["user_id"], day, subject, start,
                    request.form.get("end_time", ""), request.form.get("room", "").strip()))
        db.commit()
    return redirect(url_for("university"))


@app.route("/class/<int:class_id>/delete", methods=["POST"])
@login_required
def class_delete(class_id):
    db = get_db()
    db.execute("DELETE FROM timetable WHERE id = ? AND user_id = ?",
               (class_id, session["user_id"]))
    db.commit()
    return redirect(url_for("university"))


@app.route("/flashcard/add", methods=["POST"])
@login_required
def flashcard_add():
    subject = request.form.get("subject", "").strip()
    q = request.form.get("question", "").strip()
    a = request.form.get("answer", "").strip()
    if subject and q and a:
        db = get_db()
        db.execute("INSERT INTO flashcards(user_id, subject, question, answer) "
                   "VALUES(?,?,?,?)", (session["user_id"], subject, q, a))
        db.commit()
    return redirect(url_for("university"))


@app.route("/flashcard/<int:card_id>/delete", methods=["POST"])
@login_required
def flashcard_delete(card_id):
    db = get_db()
    db.execute("DELETE FROM flashcards WHERE id = ? AND user_id = ?",
               (card_id, session["user_id"]))
    db.commit()
    return redirect(url_for("university"))


# ---------------------------------------------------------------- profiles
def friendship_status(me, other_id):
    """'self' | 'friends' | 'pending_out' | 'pending_in' | 'none' (+ row id)."""
    if me == other_id:
        return "self", None
    row = get_db().execute(
        "SELECT * FROM friendships WHERE (from_id = ? AND to_id = ?) "
        "OR (from_id = ? AND to_id = ?)",
        (me, other_id, other_id, me)).fetchone()
    if row is None:
        return "none", None
    if row["status"] == "accepted":
        return "friends", row["id"]
    return ("pending_out" if row["from_id"] == me else "pending_in"), row["id"]


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    uid = session["user_id"]
    if request.method == "POST":
        f = request.form
        first = f.get("first_name", "").strip()[:40]
        middle = f.get("middle_name", "").strip()[:40]
        last = f.get("last_name", "").strip()[:40]
        email = f.get("email", "").strip().lower()[:100]
        edu, edu_ok = parse_edu_wizard(f)
        err = None
        if not (first and middle and last):
            err = "err_fill"
        elif not re.fullmatch(EMAIL_RE, email):
            err = "err_email"
        elif db.execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE AND "
                        "email != '' AND id != ?", (email, uid)).fetchone():
            err = "err_email_used"
        elif not edu_ok:
            err = "err_fill"
        if err:
            flash(tr(err), "error")
            return redirect(url_for("profile"))
        full = " ".join(x for x in (first, middle, last) if x)
        db.execute("UPDATE users SET first_name=?, middle_name=?, last_name=?, "
                   "full_name=?, email=?, bio=?, edu_level=?, institution=?, "
                   "school_level=?, grade=?, college=?, department=?, stage=?, "
                   "job_title=?, college_kind=?, is_private=?, allow_dm_all=? "
                   "WHERE id=?",
                   (first, middle, last, full, email,
                    f.get("bio", "").strip()[:300], edu["edu_level"],
                    edu["institution"], edu["school_level"], edu["grade"],
                    edu["college"], edu["department"], edu["stage"],
                    edu["job_title"], edu["college_kind"],
                    1 if f.get("is_private") else 0,
                    1 if f.get("allow_dm_all") else 0, uid))
        # optional username change (must stay unique, same rules as registration)
        new_un = request.form.get("username", "").strip()
        if new_un and new_un != current_user()["username"]:
            if not re.fullmatch(USERNAME_RE, new_un):
                flash(tr("err_username"), "error")
            else:
                try:
                    db.execute("UPDATE users SET username = ? WHERE id = ?",
                               (new_un, uid))
                except sqlite3.IntegrityError:
                    flash(tr("err_user_exists"), "error")
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if len(new_pw) < 6:
                flash(tr("err_pw_short"), "error")
            else:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(new_pw), uid))
        photo = request.files.get("photo")
        if photo and photo.filename:
            ext = photo.filename.rsplit(".", 1)[-1].lower()
            if ext in AVATAR_EXTS:
                folder = os.path.join(BASE_DIR, "static", "avatars")
                for old in AVATAR_EXTS:  # remove any previous photo
                    old_p = os.path.join(folder, f"{uid}.{old}")
                    if os.path.exists(old_p):
                        os.remove(old_p)
                photo.save(os.path.join(folder, f"{uid}.{ext}"))
            else:
                flash(tr("err_photo_type"), "error")
        db.commit()
        flash(tr("ok_profile"), "ok")
        return redirect(url_for("profile"))
    done_count = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1",
                            (uid,)).fetchone()[0]
    return render_template("profile.html", user=current_user(),
                           streak=user_streak(uid), done_count=done_count,
                           earned=user_badges(uid), xpinfo=user_xp(uid),
                           **_wizard_ctx())


# ---------------------------------------------------------------- chat tools
@app.route("/chat/clear", methods=["POST"])
@login_required
def chat_clear():
    """Hide the whole conversation for ME only (the other side keeps it)."""
    db = get_db()
    uid = session["user_id"]
    kind = request.form.get("kind")
    target = request.form.get("target", type=int)
    if kind not in ("dm", "group") or not target:
        abort(400)
    if kind == "dm":
        mx = db.execute("SELECT COALESCE(MAX(id),0) FROM dms WHERE "
                        "(from_id=? AND to_id=?) OR (from_id=? AND to_id=?)",
                        (uid, target, target, uid)).fetchone()[0]
    else:
        member_group_or_403(target)
        mx = db.execute("SELECT COALESCE(MAX(id),0) FROM group_messages "
                        "WHERE group_id=?", (target,)).fetchone()[0]
    db.execute("INSERT INTO chat_clears(user_id, kind, target_id, cleared_id) "
               "VALUES(?,?,?,?) ON CONFLICT(user_id, kind, target_id) "
               "DO UPDATE SET cleared_id=?", (uid, kind, target, mx, mx))
    db.commit()
    return {"ok": 1}


@app.route("/chat/mute", methods=["POST"])
@login_required
def chat_mute():
    db = get_db()
    uid = session["user_id"]
    kind = request.form.get("kind")
    target = request.form.get("target", type=int)
    if kind not in ("dm", "group") or not target:
        abort(400)
    if chat_muted(uid, kind, target):
        db.execute("DELETE FROM chat_mutes WHERE user_id=? AND kind=? AND "
                   "target_id=?", (uid, kind, target))
        muted = 0
    else:
        db.execute("INSERT OR IGNORE INTO chat_mutes(user_id, kind, target_id) "
                   "VALUES(?,?,?)", (uid, kind, target))
        muted = 1
    db.commit()
    return {"muted": muted}


@app.route("/chat/pin", methods=["POST"])
@login_required
def chat_pin():
    db = get_db()
    uid = session["user_id"]
    kind = request.form.get("kind")
    mid = request.form.get("msg_id", type=int)
    if kind not in ("dm", "group") or not mid:
        abort(400)
    if kind == "dm":
        m = db.execute("SELECT * FROM dms WHERE id=? AND (from_id=? OR to_id=?)",
                       (mid, uid, uid)).fetchone()
        if not m:
            abort(403)
        db.execute("UPDATE dms SET pinned=? WHERE id=?",
                   (0 if m["pinned"] else 1, mid))
        pinned = 0 if m["pinned"] else 1
    else:
        m = db.execute("SELECT * FROM group_messages WHERE id=?", (mid,)).fetchone()
        if not m:
            abort(404)
        member_group_or_403(m["group_id"])
        db.execute("UPDATE group_messages SET pinned=? WHERE id=?",
                   (0 if m["pinned"] else 1, mid))
        pinned = 0 if m["pinned"] else 1
    db.commit()
    return {"pinned": pinned}


@app.route("/chat/pins")
@login_required
def chat_pins():
    db = get_db()
    uid = session["user_id"]
    kind = request.args.get("kind")
    target = request.args.get("target", type=int)
    out = []
    if kind == "dm":
        cleared = chat_cleared_id(uid, "dm", target)
        rows = db.execute(
            "SELECT m.*, u.username FROM dms m JOIN users u ON u.id=m.from_id "
            "WHERE m.pinned=1 AND m.deleted IS NOT 1 AND m.id > ? AND "
            "((m.from_id=? AND m.to_id=?) OR (m.from_id=? AND m.to_id=?)) "
            "ORDER BY m.id DESC LIMIT 40",
            (cleared, uid, target, target, uid)).fetchall()
    elif kind == "group":
        member_group_or_403(target)
        cleared = chat_cleared_id(uid, "group", target)
        rows = db.execute(
            "SELECT m.*, u.username FROM group_messages m "
            "JOIN users u ON u.id=m.user_id WHERE m.pinned=1 AND "
            "m.deleted IS NOT 1 AND m.group_id=? AND m.id > ? "
            "ORDER BY m.id DESC LIMIT 40", (target, cleared)).fetchall()
    else:
        abort(400)
    for m in rows:
        txt = (m["content"] or "")[:120] or \
            {"image": "📷", "voice": "🎤", "file": "📄"}.get(m["kind"] or "", "")
        out.append({"id": m["id"], "who": m["username"], "text": txt,
                    "ts": m["created_at"]})
    return {"pins": out}


# ---------------------------------------------------------------- follows
@app.route("/follow/<username>", methods=["POST"])
@login_required
def follow_toggle(username):
    db = get_db()
    uid = session["user_id"]
    person = db.execute("SELECT * FROM users WHERE username = ?",
                        (username,)).fetchone()
    if person is None or person["id"] == uid:
        abort(404)
    if is_following(uid, person["id"]):
        db.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?",
                   (uid, person["id"]))
    else:
        # private accounts can only be reached by friendship
        if person["is_private"] and not are_friends(uid, person["id"]):
            abort(403)
        make_follow(uid, person["id"])
        notify(person["id"], "follow", actor=current_user()["username"],
               link=url_for("user_profile", username=current_user()["username"]))
    db.commit()
    return redirect(url_for("user_profile", username=username))


def _can_view_profile(viewer_id, person):
    if not person["is_private"]:
        return True
    if viewer_id is None:
        return False
    return viewer_id == person["id"] or are_friends(viewer_id, person["id"])


@app.route("/u/<username>/followers")
@app.route("/u/<username>/following")
@login_required
def follow_list(username):
    db = get_db()
    person = db.execute("SELECT * FROM users WHERE username = ?",
                        (username,)).fetchone()
    if person is None:
        abort(404)
    mode = "followers" if request.path.endswith("/followers") else "following"
    if not _can_view_profile(session.get("user_id"), person):
        flash(tr("private_note"), "error")
        return redirect(url_for("user_profile", username=username))
    if mode == "followers":
        rows = db.execute(
            "SELECT u.* FROM follows f JOIN users u ON u.id=f.follower_id "
            "WHERE f.followed_id=? ORDER BY f.id DESC", (person["id"],)).fetchall()
    else:
        rows = db.execute(
            "SELECT u.* FROM follows f JOIN users u ON u.id=f.followed_id "
            "WHERE f.follower_id=? ORDER BY f.id DESC", (person["id"],)).fetchall()
    return render_template("follow_list.html", user=current_user(), person=person,
                           rows=rows, mode=mode)


@app.route("/u/<username>")
def user_profile(username):
    # PUBLIC page — students can share kurdroom.aikurd.org/u/name anywhere
    db = get_db()
    person = db.execute("SELECT * FROM users WHERE username = ?",
                        (username,)).fetchone()
    if person is None:
        abort(404)
    uid = session.get("user_id")
    if uid:
        status, fid = friendship_status(uid, person["id"])
    else:
        status, fid = None, None
    done_count = db.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1",
                            (person["id"],)).fetchone()[0]
    followers_n = db.execute("SELECT COUNT(*) FROM follows WHERE followed_id = ?",
                             (person["id"],)).fetchone()[0]
    following_n = db.execute("SELECT COUNT(*) FROM follows WHERE follower_id = ?",
                             (person["id"],)).fetchone()[0]
    posts_n = db.execute("SELECT COUNT(*) FROM posts WHERE user_id = ?",
                         (person["id"],)).fetchone()[0]
    can_view = _can_view_profile(uid, person)
    posts = []
    if can_view:
        posts = db.execute("SELECT * FROM posts WHERE user_id = ? "
                           "ORDER BY id DESC LIMIT 30", (person["id"],)).fetchall()
    following = bool(uid and is_following(uid, person["id"]))
    return render_template("user_profile.html", user=current_user(), person=person,
                           status=status, fid=fid, streak=user_streak(person["id"]),
                           done_count=done_count, earned=user_badges(person["id"]),
                           xpinfo=user_xp(person["id"]),
                           followers_n=followers_n, following_n=following_n,
                           posts_n=posts_n, can_view=can_view, posts=posts,
                           following=following)


# ---------------------------------------------------------------- friends
@app.route("/friends")
@login_required
def friends():
    db = get_db()
    uid = session["user_id"]
    clear_notifs("friend_req", "friend_acc", "duel_req", "duel_acc", "duel_end")
    finish_due_duels(uid)
    # duels involving me
    duels = []
    for d in db.execute(
            "SELECT * FROM duels WHERE (from_id = ? OR to_id = ?) "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
            "id DESC LIMIT 8", (uid, uid)).fetchall():
        u_from = db.execute("SELECT id, username FROM users WHERE id = ?",
                            (d["from_id"],)).fetchone()
        u_to = db.execute("SELECT id, username FROM users WHERE id = ?",
                          (d["to_id"],)).fetchone()
        if not u_from or not u_to:
            continue
        item = dict(id=d["id"], status=d["status"], from_u=u_from, to_u=u_to,
                    mine_incoming=d["to_id"] == uid and d["status"] == "pending",
                    end_day=d["end_day"], winner_id=d["winner_id"])
        if d["status"] in ("active", "done") and d["start_day"]:
            a = plans_done_between(d["from_id"], d["start_day"], d["end_day"])
            b = plans_done_between(d["to_id"], d["start_day"], d["end_day"])
            item.update(a=a, b=b,
                        a_pct=int(a * 100 / max(1, a + b)) if a + b else 50,
                        days_left=(date.fromisoformat(d["end_day"]) - date.today()).days
                        if d["status"] == "active" else None)
        duels.append(item)
    q = request.args.get("q", "").strip()
    results = []
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT * FROM users WHERE (username LIKE ? OR full_name LIKE ?) "
            "AND id != ? ORDER BY username LIMIT 12", (like, like, uid)).fetchall()
        for r in rows:
            status, fid = friendship_status(uid, r["id"])
            results.append(dict(r, status=status, fid=fid))
    incoming = db.execute(
        "SELECT f.id, u.id AS from_id, u.username FROM friendships f "
        "JOIN users u ON u.id = f.from_id "
        "WHERE f.to_id = ? AND f.status='pending' ORDER BY f.created_at DESC",
        (uid,)).fetchall()
    outgoing = db.execute(
        "SELECT f.id, u.username FROM friendships f JOIN users u ON u.id = f.to_id "
        "WHERE f.from_id = ? AND f.status='pending' ORDER BY f.created_at DESC",
        (uid,)).fetchall()
    my_friends = db.execute("""
        SELECT f.id AS fid, u.id AS uid, u.username, u.last_login
        FROM friendships f
        JOIN users u ON u.id = CASE WHEN f.from_id = ? THEN f.to_id ELSE f.from_id END
        WHERE f.status='accepted' AND (f.from_id = ? OR f.to_id = ?)
        ORDER BY u.username""", (uid, uid, uid)).fetchall()
    return render_template("friends.html", user=current_user(), incoming=incoming,
                           outgoing=outgoing, my_friends=my_friends,
                           q=q, results=results, quote=random_quote(), duels=duels)


@app.route("/friend/request", methods=["POST"])
@login_required
def friend_request():
    uid = session["user_id"]
    username = request.form.get("username", "").strip()
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if target is None:
        flash(tr("err_user_not_found"), "error")
    elif target["id"] == uid:
        flash(tr("err_self_friend"), "error")
    else:
        existing = db.execute(
            "SELECT * FROM friendships WHERE (from_id = ? AND to_id = ?) "
            "OR (from_id = ? AND to_id = ?)",
            (uid, target["id"], target["id"], uid)).fetchone()
        if existing:
            flash(tr("err_already_friends" if existing["status"] == "accepted"
                     else "err_request_pending"), "error")
        else:
            db.execute("INSERT INTO friendships(from_id, to_id, created_at) VALUES(?,?,?)",
                       (uid, target["id"],
                        datetime.utcnow().isoformat(timespec="seconds")))
            notify(target["id"], "friend_req", actor=current_user()["username"],
                   link=url_for("friends"))
            db.commit()
            flash(tr("ok_request_sent"), "ok")
    return redirect(url_for("friends"))


@app.route("/friend/<int:fid>/accept", methods=["POST"])
@login_required
def friend_accept(fid):
    db = get_db()
    row = db.execute("SELECT * FROM friendships WHERE id = ? AND to_id = ? "
                     "AND status='pending'", (fid, session["user_id"])).fetchone()
    if row:
        db.execute("UPDATE friendships SET status='accepted' WHERE id = ?", (fid,))
        make_follow(row["from_id"], session["user_id"])   # friends follow each other
        make_follow(session["user_id"], row["from_id"])
        notify(row["from_id"], "friend_acc", actor=current_user()["username"],
               link=url_for("user_profile", username=current_user()["username"]))
        db.commit()
        award_badges(session["user_id"])
        award_badges(row["from_id"])
    return redirect(url_for("friends"))


@app.route("/friend/<int:fid>/decline", methods=["POST"])
@login_required
def friend_decline(fid):
    """Decline an incoming request, cancel an outgoing one, or remove a friend."""
    db = get_db()
    db.execute("DELETE FROM friendships WHERE id = ? AND (to_id = ? OR from_id = ?)",
               (fid, session["user_id"], session["user_id"]))
    db.commit()
    return redirect(url_for("friends"))


# ---------------------------------------------------------------- groups
@app.route("/groups")
@login_required
def groups():
    uid = session["user_id"]
    rows = get_db().execute("""
        SELECT g.*, u.username AS owner_name,
               (SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.id) AS member_count
        FROM groups g
        JOIN group_members gm ON gm.group_id = g.id AND gm.user_id = ?
        JOIN users u ON u.id = g.owner_id
        ORDER BY g.created_at DESC""", (uid,)).fetchall()
    items = []
    for g_row in rows:
        nm = upcoming_meeting(g_row["first_meeting"], g_row["frequency"])
        items.append(dict(g_row, next_meeting=nm.isoformat() if nm else None,
                          days_to_meeting=(nm - date.today()).days if nm else None))
    return render_template("groups.html", user=current_user(), groups=items,
                           quote=random_quote())


@app.route("/group/create", methods=["POST"])
@login_required
def group_create():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("groups"))
    freq = request.form.get("frequency", "weekly")
    if freq not in ("weekly", "biweekly", "monthly"):
        freq = "weekly"
    db = get_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = db.execute(
        "INSERT INTO groups(name, description, owner_id, frequency, first_meeting, "
        "created_at) VALUES(?,?,?,?,?,?)",
        (name, request.form.get("description", "").strip(), session["user_id"],
         freq, request.form.get("first_meeting") or None, now))
    db.execute("INSERT INTO group_members(group_id, user_id, joined_at) VALUES(?,?,?)",
               (cur.lastrowid, session["user_id"], now))
    db.commit()
    award_badges(session["user_id"])
    return redirect(url_for("group_page", group_id=cur.lastrowid))


@app.route("/group/<int:group_id>")
@login_required
def group_page(group_id):
    g_row = member_group_or_403(group_id)
    db = get_db()
    clear_notifs("group_msg", "group_add", "mention", prefix=request.path)
    members = db.execute(
        "SELECT u.id, u.username FROM group_members gm JOIN users u ON u.id = gm.user_id "
        "WHERE gm.group_id = ? ORDER BY u.username", (group_id,)).fetchall()
    g_cleared = chat_cleared_id(session["user_id"], "group", group_id)
    messages = db.execute(
        "SELECT m.*, u.username, u.plus AS uplus FROM group_messages m "
        "JOIN users u ON u.id = m.user_id "
        "WHERE m.group_id = ? AND m.id > ? ORDER BY m.id DESC LIMIT 100",
        (group_id, g_cleared)).fetchall()
    gplans = db.execute(
        "SELECT p.*, u.username FROM group_plans p JOIN users u ON u.id = p.user_id "
        "WHERE p.group_id = ? ORDER BY p.pinned DESC, "
        "COALESCE(p.target_date,'9999') ASC, p.id DESC", (group_id,)).fetchall()
    nm = upcoming_meeting(g_row["first_meeting"], g_row["frequency"])
    # friends of mine who are not yet members (for the add-member dropdown)
    friend_ids = get_friend_ids(session["user_id"])
    member_ids = {m["id"] for m in members}
    addable = [db.execute("SELECT id, username FROM users WHERE id = ?", (fid,)).fetchone()
               for fid in sorted(friend_ids - member_ids)]
    files, decks = group_extras(group_id)
    my_subjects = sorted({r["subject"] for r in db.execute(
        "SELECT DISTINCT subject FROM flashcards WHERE user_id = ?",
        (session["user_id"],))})
    # --- chat extras: reactions + quoted replies ---
    msg_ids = [m["id"] for m in messages]
    reactions = {}
    if msg_ids:
        ph = ",".join("?" * len(msg_ids))
        for r in db.execute(
                f"SELECT message_id, emoji, COUNT(*) AS n, "
                f"SUM(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS mine "
                f"FROM msg_reactions WHERE message_id IN ({ph}) "
                f"GROUP BY message_id, emoji",
                [session["user_id"]] + msg_ids).fetchall():
            reactions.setdefault(r["message_id"], []).append(
                dict(emoji=r["emoji"], n=r["n"], mine=bool(r["mine"])))
    quotes = {}
    for m in messages:
        if m["reply_to"]:
            q = db.execute("SELECT gm.content, u.username FROM group_messages gm "
                           "JOIN users u ON u.id = gm.user_id WHERE gm.id = ?",
                           (m["reply_to"],)).fetchone()
            if q:
                quotes[m["id"]] = dict(username=q["username"],
                                       content=q["content"][:90])
    # --- polls ---
    polls = []
    for p in db.execute("SELECT p.*, u.username FROM polls p JOIN users u ON "
                        "u.id = p.user_id WHERE p.group_id = ? ORDER BY p.id DESC "
                        "LIMIT 6", (group_id,)).fetchall():
        opts = db.execute("SELECT * FROM poll_options WHERE poll_id = ?",
                          (p["id"],)).fetchall()
        votes = db.execute("SELECT option_id, COUNT(*) AS n FROM poll_votes "
                           "WHERE poll_id = ? GROUP BY option_id", (p["id"],)).fetchall()
        vmap = {v["option_id"]: v["n"] for v in votes}
        total_v = sum(vmap.values())
        my_v = db.execute("SELECT option_id FROM poll_votes WHERE poll_id = ? AND "
                          "user_id = ?", (p["id"], session["user_id"])).fetchone()
        polls.append(dict(id=p["id"], question=p["question"], username=p["username"],
                          closed=p["closed"], user_id=p["user_id"], total=total_v,
                          my_vote=my_v["option_id"] if my_v else None,
                          options=[dict(id=o["id"], text=o["text"], n=vmap.get(o["id"], 0),
                                        pct=int(vmap.get(o["id"], 0) * 100 / max(1, total_v)))
                                   for o in opts]))
    # --- challenges ---
    chs = []
    for ch in db.execute("SELECT c.*, u.username FROM challenges c JOIN users u ON "
                         "u.id = c.user_id WHERE c.group_id = ? ORDER BY c.id DESC "
                         "LIMIT 4", (group_id,)).fetchall():
        total, pct, days_left = challenge_progress(ch)
        chs.append(dict(id=ch["id"], title=ch["title"], target=ch["target"],
                        username=ch["username"], user_id=ch["user_id"],
                        total=total, pct=pct, days_left=days_left,
                        done=total >= ch["target"]))
    # --- group leaderboard (this week) + member levels ---
    wk_start, _ = week_window()
    glb = []
    levels = {}
    for m in members:
        p_n, h_n = week_counts(m["id"], wk_start)
        streak = user_streak(m["id"])
        glb.append(dict(id=m["id"], username=m["username"],
                        points=p_n * 10 + h_n * 5 + streak * 3,
                        plans=p_n, habits=h_n, streak=streak))
        levels[m["id"]] = user_xp(m["id"])["level"]
    glb.sort(key=lambda r: r["points"], reverse=True)
    return render_template("group.html", user=current_user(), g=g_row, members=members,
                           messages=list(reversed(messages)), gplans=gplans,
                           addable=[a for a in addable if a],
                           next_meeting=nm.isoformat() if nm else None,
                           days_to_meeting=(nm - date.today()).days if nm else None,
                           is_owner=g_row["owner_id"] == session["user_id"],
                           files=files, decks=decks, my_subjects=my_subjects,
                           reactions=reactions, quotes=quotes, polls=polls,
                           challenges=chs, glb=glb, levels=levels,
                           avatars={m["id"]: avatar_url(m["id"]) for m in members},
                           muted=chat_muted(session["user_id"], "group", group_id),
                           REACTIONS=REACTION_EMOJIS)


@app.route("/group/<int:group_id>/message", methods=["POST"])
@login_required
def group_message(group_id):
    from werkzeug.utils import secure_filename
    member_group_or_403(group_id)
    content = request.form.get("content", "").strip()
    f = request.files.get("file")
    voice_ext = ""
    if f and f.filename and request.form.get("kind") == "voice":
        voice_ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if voice_ext not in ("webm", "m4a", "ogg", "wav", "mp3"):
            voice_ext = ""
    if content or voice_ext:
        db = get_db()
        reply_to = request.form.get("reply_to") or None
        if reply_to:
            ok = db.execute("SELECT 1 FROM group_messages WHERE id = ? AND group_id = ?",
                            (reply_to, group_id)).fetchone()
            if not ok:
                reply_to = None
        cur = db.execute(
            "INSERT INTO group_messages(group_id, user_id, content, created_at, "
            "reply_to, kind) VALUES(?,?,?,?,?,?)",
            (group_id, session["user_id"], content[:500],
             datetime.utcnow().isoformat(timespec="seconds"), reply_to,
             "voice" if voice_ext else "text"))
        if voice_ext:
            stored = f"gv{cur.lastrowid}_"                      f"{secure_filename(f.filename) or 'voice.' + voice_ext}"
            f.save(os.path.join(BASE_DIR, "groupfiles", stored))
            new_name = transcode_voice(stored, "groupfiles")
            if new_name:
                stored = new_name
            db.execute("UPDATE group_messages SET stored = ? WHERE id = ?",
                       (stored, cur.lastrowid))
        g_row = db.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
        link = url_for("group_page", group_id=group_id)
        # @mentions ping the mentioned member directly
        mentioned = set()
        for mu in set(re.findall(r"@([A-Za-z0-9_.]{3,20})", content)):
            row = db.execute(
                "SELECT u.id FROM users u JOIN group_members gm ON "
                "gm.user_id = u.id AND gm.group_id = ? WHERE u.username = ?",
                (group_id, mu)).fetchone()
            if row and row["id"] != session["user_id"]:
                mentioned.add(row["id"])
                notify(row["id"], "mention",
                       actor=current_user()["username"], link=link)
        for m in db.execute("SELECT user_id FROM group_members WHERE group_id = ? "
                            "AND user_id != ?", (group_id, session["user_id"])):
            if m["user_id"] not in mentioned and \
                    not chat_muted(m["user_id"], "group", group_id):
                notify(m["user_id"], "group_msg", actor=g_row["name"], link=link)
        db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": 1}
    return redirect(url_for("group_page", group_id=group_id) + "#chat")


@app.route("/gvoice/<int:msg_id>")
@login_required
def group_voice(msg_id):
    db = get_db()
    m = db.execute("SELECT * FROM group_messages WHERE id = ?", (msg_id,)).fetchone()
    if m is None or m["kind"] != "voice" or not m["stored"]:
        abort(404)
    if not is_group_member(m["group_id"], session["user_id"]):
        abort(403)
    if m["stored"].lower().endswith(".webm"):
        new_name = transcode_voice(m["stored"], "groupfiles")
        if new_name:
            db.execute("UPDATE group_messages SET stored = ? WHERE id = ?",
                       (new_name, msg_id))
            db.commit()
            m = db.execute("SELECT * FROM group_messages WHERE id = ?",
                           (msg_id,)).fetchone()
    from flask import send_from_directory
    ext = m["stored"].rsplit(".", 1)[-1].lower() if "." in m["stored"] else ""
    mime = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "webm": "audio/webm",
            "ogg": "audio/ogg", "wav": "audio/wav"}.get(ext)
    return send_from_directory(os.path.join(BASE_DIR, "groupfiles"), m["stored"],
                               as_attachment=False, mimetype=mime,
                               download_name=m["stored"])


@app.route("/group/<int:group_id>/chat_poll")
@login_required
def group_chat_poll(group_id):
    """Live group chat: messages newer than ?after=<id>, reactions, deletions."""
    member_group_or_403(group_id)
    db = get_db()
    uid = session["user_id"]
    after = max(request.args.get("after", 0, type=int),
                chat_cleared_id(uid, "group", group_id))
    clear_notifs("group_msg", prefix=url_for("group_page", group_id=group_id))
    rows = db.execute(
        "SELECT m.*, u.username, u.plus AS uplus, "
        "r.content AS r_content, ru.username AS r_username "
        "FROM group_messages m JOIN users u ON u.id = m.user_id "
        "LEFT JOIN group_messages r ON r.id = m.reply_to "
        "LEFT JOIN users ru ON ru.id = r.user_id "
        "WHERE m.group_id = ? AND m.id > ? ORDER BY m.id ASC LIMIT 100",
        (group_id, after)).fetchall()
    # the visible window = last 100 messages: reactions + deletions can change there
    win = [r[0] for r in db.execute(
        "SELECT id FROM group_messages WHERE group_id = ? ORDER BY id DESC LIMIT 100",
        (group_id,))]
    reacts = {}
    if win:
        ph = ",".join("?" * len(win))
        for r in db.execute(
                f"SELECT message_id, emoji, COUNT(*) AS n, "
                f"MAX(user_id = ?) AS me FROM msg_reactions "
                f"WHERE message_id IN ({ph}) GROUP BY message_id, emoji",
                [uid] + win):
            reacts.setdefault(str(r["message_id"]), []).append(
                {"e": r["emoji"], "n": r["n"], "me": bool(r["me"])})
    dels = [r[0] for r in db.execute(
        "SELECT id FROM group_messages WHERE group_id = ? AND deleted = 1 "
        "ORDER BY id DESC LIMIT 200", (group_id,))]
    lv_cache = {}

    def lv(u):
        if u not in lv_cache:
            lv_cache[u] = user_xp(u)["level"]
        return lv_cache[u]
    return {"msgs": [{"id": m["id"], "uid": m["user_id"], "me": m["user_id"] == uid,
                      "name": m["username"] + (" ⭐" if m["uplus"] else ""),
                      "level": lv(m["user_id"]),
                      "kind": m["kind"] or "text", "stored": bool(m["stored"]),
                      "content": m["content"] or "",
                      "at": (m["created_at"] or "")[5:16].replace("T", " "),
                      "ts": m["created_at"] or "",
                      "gone": bool(m["deleted"]),
                      "reply": ({"id": m["reply_to"], "name": m["r_username"] or "",
                                 "text": (m["r_content"] or "")[:90]}
                                if m["reply_to"] else None)} for m in rows],
            "reacts": reacts, "del": dels}


@app.route("/group/<int:group_id>/add_member", methods=["POST"])
@login_required
def group_add_member(group_id):
    member_group_or_403(group_id)
    try:
        new_id = int(request.form.get("user_id", "0"))
    except ValueError:
        new_id = 0
    # you may only add YOUR OWN friends
    if new_id and are_friends(session["user_id"], new_id) \
            and not is_group_member(group_id, new_id):
        db = get_db()
        db.execute("INSERT INTO group_members(group_id, user_id, joined_at) VALUES(?,?,?)",
                   (group_id, new_id, datetime.utcnow().isoformat(timespec="seconds")))
        g_row = db.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
        notify(new_id, "group_add", actor=g_row["name"],
               link=url_for("group_page", group_id=group_id))
        db.commit()
        flash(tr("ok_member_added"), "ok")
    return redirect(url_for("group_page", group_id=group_id))


@app.route("/group/<int:group_id>/remove_member/<int:member_id>", methods=["POST"])
@login_required
def group_remove_member(group_id, member_id):
    g_row = member_group_or_403(group_id)
    if g_row["owner_id"] != session["user_id"] or member_id == g_row["owner_id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
               (group_id, member_id))
    db.commit()
    return redirect(url_for("group_page", group_id=group_id))


@app.route("/group/<int:group_id>/leave", methods=["POST"])
@login_required
def group_leave(group_id):
    g_row = member_group_or_403(group_id)
    if g_row["owner_id"] == session["user_id"]:
        return redirect(url_for("group_page", group_id=group_id))  # owner deletes instead
    db = get_db()
    db.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
               (group_id, session["user_id"]))
    db.commit()
    return redirect(url_for("groups"))


@app.route("/group/<int:group_id>/delete", methods=["POST"])
@login_required
def group_delete(group_id):
    g_row = member_group_or_403(group_id)
    if g_row["owner_id"] != session["user_id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    db.commit()
    return redirect(url_for("groups"))


@app.route("/group/<int:group_id>/settings", methods=["POST"])
@login_required
def group_settings(group_id):
    g_row = member_group_or_403(group_id)
    if g_row["owner_id"] != session["user_id"]:
        abort(403)
    freq = request.form.get("frequency", g_row["frequency"])
    if freq not in ("weekly", "biweekly", "monthly"):
        freq = g_row["frequency"]
    db = get_db()
    db.execute("UPDATE groups SET frequency = ?, first_meeting = ? WHERE id = ?",
               (freq, request.form.get("first_meeting") or None, group_id))
    db.commit()
    flash(tr("ok_saved"), "ok")
    return redirect(url_for("group_page", group_id=group_id))


# ---------------------------------------------------------------- chat extras
@app.route("/msg/<int:msg_id>/react", methods=["POST"])
@login_required
def msg_react(msg_id):
    db = get_db()
    m = db.execute("SELECT * FROM group_messages WHERE id = ?", (msg_id,)).fetchone()
    if m is None or not is_group_member(m["group_id"], session["user_id"]):
        abort(403)
    emoji = request.form.get("emoji", "")
    if emoji in REACTION_EMOJIS:
        hit = db.execute("SELECT 1 FROM msg_reactions WHERE message_id=? AND user_id=? "
                         "AND emoji=?", (msg_id, session["user_id"], emoji)).fetchone()
        if hit:
            db.execute("DELETE FROM msg_reactions WHERE message_id=? AND user_id=? "
                       "AND emoji=?", (msg_id, session["user_id"], emoji))
        else:
            db.execute("INSERT INTO msg_reactions(message_id, user_id, emoji) "
                       "VALUES(?,?,?)", (msg_id, session["user_id"], emoji))
        db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": 1}
    return redirect(url_for("group_page", group_id=m["group_id"]) + "#chat")


@app.route("/msg/<int:msg_id>/delete", methods=["POST"])
@login_required
def msg_delete(msg_id):
    db = get_db()
    m = db.execute("SELECT * FROM group_messages WHERE id = ?", (msg_id,)).fetchone()
    if m is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (m["group_id"],)).fetchone()
    if m["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    # soft delete so every member's open chat replaces it live
    if m["kind"] == "voice" and m["stored"]:
        try:
            os.remove(os.path.join(BASE_DIR, "groupfiles", m["stored"]))
        except OSError:
            pass
    db.execute("UPDATE group_messages SET deleted = 1, content = '', stored = '' "
               "WHERE id = ?", (msg_id,))
    db.execute("DELETE FROM msg_reactions WHERE message_id = ?", (msg_id,))
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": 1}
    return redirect(url_for("group_page", group_id=m["group_id"]) + "#chat")


# ---------------------------------------------------------------- polls
@app.route("/group/<int:group_id>/poll/create", methods=["POST"])
@login_required
def poll_create(group_id):
    member_group_or_403(group_id)
    q = request.form.get("question", "").strip()
    opts = [o.strip() for o in request.form.get("options", "").split("\n") if o.strip()]
    if q and len(opts) >= 2:
        db = get_db()
        cur = db.execute("INSERT INTO polls(group_id, user_id, question, created_at) "
                         "VALUES(?,?,?,?)", (group_id, session["user_id"], q[:150],
                                             datetime.utcnow().isoformat(timespec="seconds")))
        for o in opts[:8]:
            db.execute("INSERT INTO poll_options(poll_id, text) VALUES(?,?)",
                       (cur.lastrowid, o[:80]))
        db.commit()
    return redirect(url_for("group_page", group_id=group_id) + "#polls")


@app.route("/poll/<int:poll_id>/vote", methods=["POST"])
@login_required
def poll_vote(poll_id):
    db = get_db()
    p = db.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if p is None or not is_group_member(p["group_id"], session["user_id"]):
        abort(403)
    if not p["closed"]:
        try:
            opt = int(request.form.get("option_id", "0"))
        except ValueError:
            opt = 0
        ok = db.execute("SELECT 1 FROM poll_options WHERE id = ? AND poll_id = ?",
                        (opt, poll_id)).fetchone()
        if ok:
            db.execute("INSERT OR REPLACE INTO poll_votes(poll_id, option_id, user_id) "
                       "VALUES(?,?,?)", (poll_id, opt, session["user_id"]))
            db.commit()
    return redirect(url_for("group_page", group_id=p["group_id"]) + "#polls")


@app.route("/poll/<int:poll_id>/close", methods=["POST"])
@login_required
def poll_close(poll_id):
    db = get_db()
    p = db.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if p is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (p["group_id"],)).fetchone()
    if p["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    db.execute("UPDATE polls SET closed = 1 - closed WHERE id = ?", (poll_id,))
    db.commit()
    return redirect(url_for("group_page", group_id=p["group_id"]) + "#polls")


# ---------------------------------------------------------------- challenges
@app.route("/group/<int:group_id>/challenge/create", methods=["POST"])
@login_required
def challenge_create(group_id):
    member_group_or_403(group_id)
    from datetime import timedelta
    title = request.form.get("title", "").strip()
    try:
        target = max(1, min(10000, int(request.form.get("target", "10"))))
        days = max(1, min(60, int(request.form.get("days", "7"))))
    except ValueError:
        target, days = 10, 7
    if title:
        db = get_db()
        db.execute("INSERT INTO challenges(group_id, user_id, title, target, start_day, "
                   "end_day, created_at) VALUES(?,?,?,?,?,?,?)",
                   (group_id, session["user_id"], title[:100], target,
                    date.today().isoformat(),
                    (date.today() + timedelta(days=days)).isoformat(),
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("group_page", group_id=group_id) + "#challenges")


@app.route("/challenge/<int:ch_id>/delete", methods=["POST"])
@login_required
def challenge_delete(ch_id):
    db = get_db()
    ch = db.execute("SELECT * FROM challenges WHERE id = ?", (ch_id,)).fetchone()
    if ch is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (ch["group_id"],)).fetchone()
    if ch["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    db.execute("DELETE FROM challenges WHERE id = ?", (ch_id,))
    db.commit()
    return redirect(url_for("group_page", group_id=ch["group_id"]) + "#challenges")


# ---------------------------------------------------------------- personal challenges
@app.route("/pchallenge/add", methods=["POST"])
@login_required
def pchallenge_add():
    from datetime import timedelta
    title = request.form.get("title", "").strip()
    try:
        target = max(1, min(10000, int(request.form.get("target", "10"))))
        days = max(1, min(60, int(request.form.get("days", "7"))))
    except ValueError:
        target, days = 10, 7
    if title:
        db = get_db()
        db.execute("INSERT INTO personal_challenges(user_id, title, target, start_day, "
                   "end_day, created_at) VALUES(?,?,?,?,?,?)",
                   (session["user_id"], title[:100], target, date.today().isoformat(),
                    (date.today() + timedelta(days=days)).isoformat(),
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("dashboard"))


@app.route("/pchallenge/<int:pc_id>/delete", methods=["POST"])
@login_required
def pchallenge_delete(pc_id):
    db = get_db()
    db.execute("DELETE FROM personal_challenges WHERE id = ? AND user_id = ?",
               (pc_id, session["user_id"]))
    db.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- grade book
GRADE_POINTS = [("A", 4.0), ("A-", 3.7), ("B+", 3.3), ("B", 3.0), ("B-", 2.7),
                ("C+", 2.3), ("C", 2.0), ("C-", 1.7), ("D+", 1.3), ("D", 1.0),
                ("F", 0.0)]


@app.route("/grades")
@login_required
def grades():
    db = get_db()
    uid = session["user_id"]
    sems = []
    all_pts = all_cr = 0.0
    for s in db.execute("SELECT * FROM semesters WHERE user_id = ? ORDER BY id",
                        (uid,)).fetchall():
        courses = db.execute("SELECT * FROM semester_courses WHERE semester_id = ? "
                             "ORDER BY id", (s["id"],)).fetchall()
        pts = sum(c["points"] * c["credits"] for c in courses)
        cr = sum(c["credits"] for c in courses)
        all_pts += pts
        all_cr += cr
        sems.append(dict(id=s["id"], name=s["name"], courses=courses,
                         gpa=round(pts / cr, 2) if cr else None, credits=cr))
    cum = round(all_pts / all_cr, 2) if all_cr else None
    return render_template("grades.html", user=current_user(), sems=sems,
                           cum=cum, total_credits=all_cr, GRADES=GRADE_POINTS)


@app.route("/semester/add", methods=["POST"])
@login_required
def semester_add():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO semesters(user_id, name, created_at) VALUES(?,?,?)",
                   (session["user_id"], name[:60],
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("grades"))


@app.route("/semester/<int:sem_id>/delete", methods=["POST"])
@login_required
def semester_delete(sem_id):
    db = get_db()
    db.execute("DELETE FROM semesters WHERE id = ? AND user_id = ?",
               (sem_id, session["user_id"]))
    db.commit()
    return redirect(url_for("grades"))


@app.route("/course/add", methods=["POST"])
@login_required
def course_add():
    db = get_db()
    sem = db.execute("SELECT * FROM semesters WHERE id = ? AND user_id = ?",
                     (request.form.get("semester_id", 0), session["user_id"])).fetchone()
    name = request.form.get("name", "").strip()
    if sem and name:
        letter = request.form.get("letter", "A")
        points = dict(GRADE_POINTS).get(letter, 4.0)
        try:
            credits = max(0.5, min(12, float(request.form.get("credits", "3"))))
        except ValueError:
            credits = 3
        db.execute("INSERT INTO semester_courses(semester_id, name, credits, letter, "
                   "points) VALUES(?,?,?,?,?)", (sem["id"], name[:60], credits,
                                                 letter, points))
        db.commit()
    return redirect(url_for("grades"))


@app.route("/course/<int:c_id>/delete", methods=["POST"])
@login_required
def course_delete(c_id):
    db = get_db()
    db.execute("DELETE FROM semester_courses WHERE id = ? AND semester_id IN "
               "(SELECT id FROM semesters WHERE user_id = ?)",
               (c_id, session["user_id"]))
    db.commit()
    return redirect(url_for("grades"))


# ---------------------------------------------------------------- duels
@app.route("/duel/challenge/<username>", methods=["POST"])
@login_required
def duel_challenge(username):
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    uid = session["user_id"]
    if target is None or target["id"] == uid or not are_friends(uid, target["id"]):
        abort(403)
    open_d = db.execute("SELECT 1 FROM duels WHERE status IN ('pending','active') AND "
                        "((from_id=? AND to_id=?) OR (from_id=? AND to_id=?))",
                        (uid, target["id"], target["id"], uid)).fetchone()
    if not open_d:
        db.execute("INSERT INTO duels(from_id, to_id, created_at) VALUES(?,?,?)",
                   (uid, target["id"], datetime.utcnow().isoformat(timespec="seconds")))
        notify(target["id"], "duel_req", actor=current_user()["username"],
               link=url_for("friends"))
        db.commit()
        flash(tr("ok_request_sent"), "ok")
    return redirect(request.referrer or url_for("friends"))


@app.route("/duel/<int:duel_id>/accept", methods=["POST"])
@login_required
def duel_accept(duel_id):
    from datetime import timedelta
    db = get_db()
    d = db.execute("SELECT * FROM duels WHERE id = ? AND to_id = ? AND status='pending'",
                   (duel_id, session["user_id"])).fetchone()
    if d:
        db.execute("UPDATE duels SET status='active', start_day=?, end_day=? WHERE id=?",
                   (date.today().isoformat(),
                    (date.today() + timedelta(days=7)).isoformat(), duel_id))
        notify(d["from_id"], "duel_acc", actor=current_user()["username"],
               link=url_for("friends"))
        db.commit()
    return redirect(url_for("friends"))


@app.route("/duel/<int:duel_id>/decline", methods=["POST"])
@login_required
def duel_decline(duel_id):
    db = get_db()
    db.execute("DELETE FROM duels WHERE id = ? AND status='pending' AND "
               "(to_id = ? OR from_id = ?)",
               (duel_id, session["user_id"], session["user_id"]))
    db.commit()
    return redirect(url_for("friends"))


# ---------------------------------------------------------------- guide
@app.route("/guide")
@login_required
def guide():
    return render_template("guide.html", user=current_user())


@app.route("/prefs/daily_goal", methods=["POST"])
@login_required
def prefs_daily_goal():
    try:
        g = max(1, min(20, int(request.form.get("goal", "3"))))
    except ValueError:
        g = 3
    db = get_db()
    db.execute("UPDATE users SET daily_goal = ? WHERE id = ?", (g, session["user_id"]))
    db.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- group plans
@app.route("/group/<int:group_id>/plan/add", methods=["POST"])
@login_required
def gplan_add(group_id):
    member_group_or_403(group_id)
    title = request.form.get("title", "").strip()
    if title:
        db = get_db()
        db.execute("INSERT INTO group_plans(group_id, user_id, title, details, "
                   "target_date, created_at) VALUES(?,?,?,?,?,?)",
                   (group_id, session["user_id"], title,
                    request.form.get("details", "").strip(),
                    request.form.get("target_date") or None,
                    datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(url_for("group_page", group_id=group_id) + "#plans")


@app.route("/gplan/<int:plan_id>/pin", methods=["POST"])
@login_required
def gplan_pin(plan_id):
    db = get_db()
    p = db.execute("SELECT * FROM group_plans WHERE id = ?", (plan_id,)).fetchone()
    if p is None or not is_group_member(p["group_id"], session["user_id"]):
        abort(403)
    db.execute("UPDATE group_plans SET pinned = ? WHERE id = ?",
               (0 if p["pinned"] else 1, plan_id))
    db.commit()
    return redirect(url_for("group_page", group_id=p["group_id"]) + "#plans")


@app.route("/gplan/<int:plan_id>/delete", methods=["POST"])
@login_required
def gplan_delete(plan_id):
    db = get_db()
    p = db.execute("SELECT * FROM group_plans WHERE id = ?", (plan_id,)).fetchone()
    if p is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (p["group_id"],)).fetchone()
    # author or group owner may delete
    if p["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    db.execute("DELETE FROM group_plans WHERE id = ?", (plan_id,))
    db.commit()
    return redirect(url_for("group_page", group_id=p["group_id"]) + "#plans")


# ---------------------------------------------------------------- notifications
@app.route("/notifications")
@login_required
def notifications():
    from datetime import timedelta
    db = get_db()
    uid = session["user_id"]
    stored = db.execute("SELECT * FROM notifications WHERE user_id = ? "
                        "ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
    # dynamic reminders: exams within 3 days, meetings within 1 day
    reminders = []
    for e in db.execute("SELECT * FROM exams WHERE user_id = ?", (uid,)):
        try:
            left = (date.fromisoformat(e["exam_date"]) - date.today()).days
        except ValueError:
            continue
        if 0 <= left <= 3:
            reminders.append(dict(kind="exam", actor=e["subject"], left=left,
                                  link=url_for("university")))
    for g_row in db.execute(
            "SELECT g.* FROM groups g JOIN group_members m ON m.group_id = g.id "
            "AND m.user_id = ?", (uid,)):
        nm = upcoming_meeting(g_row["first_meeting"], g_row["frequency"])
        if nm and (nm - date.today()).days <= 1:
            reminders.append(dict(kind="meeting", actor=g_row["name"],
                                  left=(nm - date.today()).days,
                                  link=url_for("group_page", group_id=g_row["id"])))
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (uid,))
    db.commit()
    return render_template("notifications.html", user=current_user(),
                           stored=stored, reminders=reminders)


# ---------------------------------------------------------------- direct messages
@app.route("/messages")
@login_required
def messages():
    db = get_db()
    uid = session["user_id"]
    convos = []
    for fid in sorted(get_friend_ids(uid)):
        friend = db.execute("SELECT id, username, full_name, last_seen, plus "
                            "FROM users WHERE id = ?", (fid,)).fetchone()
        if friend is None:
            continue
        online = False
        if friend["last_seen"]:
            try:
                online = (datetime.utcnow() - datetime.fromisoformat(
                    friend["last_seen"])).total_seconds() < 120
            except ValueError:
                pass
        last = db.execute(
            "SELECT * FROM dms WHERE (from_id = ? AND to_id = ?) "
            "OR (from_id = ? AND to_id = ?) ORDER BY id DESC LIMIT 1",
            (uid, fid, fid, uid)).fetchone()
        unread = db.execute("SELECT COUNT(*) FROM dms WHERE from_id = ? AND to_id = ? "
                            "AND is_read = 0", (fid, uid)).fetchone()[0]
        convos.append(dict(friend=friend, last=last, unread=unread, online=online))
    convos.sort(key=lambda c: c["last"]["id"] if c["last"] else 0, reverse=True)
    return render_template("messages.html", user=current_user(), convos=convos)


IMG_EXTS = ("png", "jpg", "jpeg", "webp", "gif")
DM_FILE_EXTS = IMG_EXTS + ("pdf", "txt", "md", "docx", "xlsx", "pptx", "zip",
                           "webm", "m4a", "mp3", "ogg", "wav")


@app.route("/messages/<username>", methods=["GET", "POST"])
@login_required
def dm_thread(username):
    from werkzeug.utils import secure_filename
    db = get_db()
    uid = session["user_id"]
    friend = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if friend is None:
        abort(404)
    if not are_friends(uid, friend["id"]) and friend["id"] != uid and \
            not friend["allow_dm_all"]:
        abort(403)
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        f = request.files.get("file")
        reply_to = request.form.get("reply_to", type=int)
        if reply_to:
            ok = db.execute("SELECT 1 FROM dms WHERE id = ? AND ((from_id = ? AND "
                            "to_id = ?) OR (from_id = ? AND to_id = ?))",
                            (reply_to, uid, friend["id"], friend["id"], uid)).fetchone()
            if not ok:
                reply_to = None
        sent = False
        now = datetime.utcnow().isoformat(timespec="seconds")
        if f and f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext in DM_FILE_EXTS:
                if request.form.get("kind") == "voice" or ext in ("webm", "m4a", "ogg", "wav"):
                    kind = "voice"
                elif ext in IMG_EXTS:
                    kind = "image"
                else:
                    kind = "file"
                cur = db.execute("INSERT INTO dms(from_id, to_id, content, created_at, "
                                 "kind, orig_name, reply_to) VALUES(?,?,?,?,?,?,?)",
                                 (uid, friend["id"], content[:500], now, kind,
                                  f.filename[:100], reply_to))
                stored = f"{cur.lastrowid}_{secure_filename(f.filename) or 'file.' + ext}"
                f.save(os.path.join(BASE_DIR, "dmfiles", stored))
                if kind == "voice":
                    new_name = transcode_voice(stored)
                    if new_name:
                        stored = new_name   # mp3 plays on iPhone + Android alike
                db.execute("UPDATE dms SET stored = ? WHERE id = ?",
                           (stored, cur.lastrowid))
                sent = True
            else:
                flash(tr("err_file_type"), "error")
        elif content:
            db.execute("INSERT INTO dms(from_id, to_id, content, created_at, reply_to) "
                       "VALUES(?,?,?,?,?)", (uid, friend["id"], content[:500], now,
                                             reply_to))
            sent = True
        if sent:
            if not chat_muted(friend["id"], "dm", uid):
                notify(friend["id"], "dm", actor=current_user()["username"],
                       link=url_for("dm_thread", username=current_user()["username"]))
            db.commit()
        return redirect(url_for("dm_thread", username=username))
    db.execute("UPDATE dms SET is_read = 1 WHERE from_id = ? AND to_id = ?",
               (friend["id"], uid))
    db.commit()
    clear_notifs("dm", prefix=request.path)   # chat opened -> its notification is done
    cleared = chat_cleared_id(uid, "dm", friend["id"])
    thread = db.execute(
        "SELECT m.*, r.content AS r_content, r.kind AS r_kind, r.from_id AS r_from, "
        "r.orig_name AS r_orig FROM dms m LEFT JOIN dms r ON r.id = m.reply_to "
        "WHERE m.id > ? AND ((m.from_id = ? AND m.to_id = ?) "
        "OR (m.from_id = ? AND m.to_id = ?)) ORDER BY m.id DESC LIMIT 200",
        (cleared, uid, friend["id"], friend["id"], uid)).fetchall()
    thread = list(reversed(thread))
    reacts = {}
    if thread:
        ph = ",".join("?" * len(thread))
        for row in db.execute(
                f"SELECT msg_id, emoji, COUNT(*) AS n, MAX(user_id = ?) AS me "
                f"FROM dm_reactions WHERE msg_id IN ({ph}) GROUP BY msg_id, emoji",
                [uid] + [m["id"] for m in thread]):
            reacts.setdefault(row["msg_id"], []).append(
                {"e": row["emoji"], "n": row["n"], "me": bool(row["me"])})
    online = False
    if friend["last_seen"]:
        try:
            online = (datetime.utcnow() - datetime.fromisoformat(
                friend["last_seen"])).total_seconds() < 120
        except ValueError:
            pass
    return render_template("dm.html", user=current_user(), friend=friend,
                           thread=thread, reacts=reacts,
                           muted=chat_muted(uid, "dm", friend["id"]),
                           pres={"online": online, "at": friend["last_seen"] or ""},
                           REACTIONS=REACTION_EMOJIS)


@app.route("/messages/<username>/poll")
@login_required
def dm_poll(username):
    """Live chat: return messages newer than ?after=<id> as JSON (no refresh)."""
    db = get_db()
    uid = session["user_id"]
    friend = db.execute("SELECT id, last_seen, allow_dm_all FROM users "
                        "WHERE username = ?", (username,)).fetchone()
    if friend is None:
        abort(404)
    if not are_friends(uid, friend["id"]) and not friend["allow_dm_all"]:
        abort(403)
    after = max(request.args.get("after", 0, type=int),
                chat_cleared_id(uid, "dm", friend["id"]))
    # reader has the thread open -> mark incoming as read (powers live checkmarks)
    db.execute("UPDATE dms SET is_read = 1 WHERE from_id = ? AND to_id = ? "
               "AND is_read = 0", (friend["id"], uid))
    db.commit()
    clear_notifs("dm", prefix=url_for("dm_thread", username=username))
    rows = db.execute(
        "SELECT m.*, r.content AS r_content, r.kind AS r_kind, r.from_id AS r_from, "
        "r.orig_name AS r_orig FROM dms m LEFT JOIN dms r ON r.id = m.reply_to "
        "WHERE ((m.from_id = ? AND m.to_id = ?) "
        "OR (m.from_id = ? AND m.to_id = ?)) AND m.id > ? "
        "ORDER BY m.id ASC LIMIT 100",
        (uid, friend["id"], friend["id"], uid, after)).fetchall()
    read_max = db.execute(
        "SELECT COALESCE(MAX(id), 0) FROM dms WHERE from_id = ? AND to_id = ? "
        "AND is_read = 1", (uid, friend["id"])).fetchone()[0]
    # reactions for the whole visible window (they can change on old messages)
    reacts = {}
    for row in db.execute(
            "SELECT r.msg_id, r.emoji, COUNT(*) AS n, MAX(r.user_id = ?) AS me "
            "FROM dm_reactions r JOIN dms m ON m.id = r.msg_id "
            "WHERE (m.from_id = ? AND m.to_id = ?) OR (m.from_id = ? AND m.to_id = ?) "
            "GROUP BY r.msg_id, r.emoji",
            (uid, uid, friend["id"], friend["id"], uid)):
        reacts.setdefault(str(row["msg_id"]), []).append(
            {"e": row["emoji"], "n": row["n"], "me": bool(row["me"])})

    def snip(m):
        if m["reply_to"] is None:
            return None
        txt = (m["r_content"] or "").strip()
        if not txt:
            txt = {"image": "📷", "voice": "🎤 🎵",
                   "file": "📄 " + (m["r_orig"] or "")}.get(m["r_kind"] or "", "")
        return {"id": m["reply_to"], "me": m["r_from"] == uid, "text": txt[:90]}
    # ids of deleted messages anywhere in the thread (so both sides remove them live)
    dels = [r[0] for r in db.execute(
        "SELECT id FROM dms WHERE ((from_id = ? AND to_id = ?) "
        "OR (from_id = ? AND to_id = ?)) AND deleted = 1",
        (uid, friend["id"], friend["id"], uid))]
    # friend presence: online if active in the last 2 minutes
    online = False
    if friend["last_seen"]:
        try:
            online = (datetime.utcnow() - datetime.fromisoformat(
                friend["last_seen"])).total_seconds() < 120
        except ValueError:
            pass
    return {"msgs": [{"id": m["id"], "me": m["from_id"] == uid,
                      "kind": m["kind"] or "text", "content": m["content"] or "",
                      "orig": m["orig_name"] or "", "stored": bool(m["stored"]),
                      "at": (m["created_at"] or "")[5:16].replace("T", " "),
                      "ts": m["created_at"] or "",
                      "read": bool(m["is_read"]), "reply": snip(m),
                      "gone": bool(m["deleted"])} for m in rows],
            "read_max": read_max, "reacts": reacts, "del": dels,
            "pres": {"online": online, "at": friend["last_seen"] or ""}}


@app.route("/messages/<username>/delete", methods=["POST"])
@login_required
def dm_delete(username):
    """Delete my own message for everyone (WhatsApp style)."""
    db = get_db()
    uid = session["user_id"]
    friend = db.execute("SELECT id, allow_dm_all FROM users WHERE username = ?",
                        (username,)).fetchone()
    if friend is None:
        abort(404)
    if not are_friends(uid, friend["id"]) and not friend["allow_dm_all"]:
        abort(403)
    msg_id = request.form.get("msg_id", type=int)
    m = db.execute("SELECT * FROM dms WHERE id = ? AND from_id = ? AND to_id = ?",
                   (msg_id, uid, friend["id"])).fetchone()
    if m and not m["deleted"]:
        if m["stored"]:
            try:
                os.remove(os.path.join(BASE_DIR, "dmfiles", m["stored"]))
            except OSError:
                pass
        db.execute("UPDATE dms SET deleted = 1, content = '', stored = '', "
                   "orig_name = '' WHERE id = ?", (msg_id,))
        db.execute("DELETE FROM dm_reactions WHERE msg_id = ?", (msg_id,))
        db.commit()
    return {"ok": 1}


@app.route("/messages/<username>/react", methods=["POST"])
@login_required
def dm_react(username):
    db = get_db()
    uid = session["user_id"]
    friend = db.execute("SELECT id, allow_dm_all FROM users WHERE username = ?",
                        (username,)).fetchone()
    if friend is None:
        abort(404)
    if not are_friends(uid, friend["id"]) and not friend["allow_dm_all"]:
        abort(403)
    msg_id = request.form.get("msg_id", type=int)
    emoji = request.form.get("emoji", "")
    m = db.execute("SELECT 1 FROM dms WHERE id = ? AND ((from_id = ? AND to_id = ?) "
                   "OR (from_id = ? AND to_id = ?))",
                   (msg_id, uid, friend["id"], friend["id"], uid)).fetchone()
    if m and emoji in REACTION_EMOJIS:
        hit = db.execute("SELECT 1 FROM dm_reactions WHERE msg_id = ? AND user_id = ? "
                         "AND emoji = ?", (msg_id, uid, emoji)).fetchone()
        if hit:
            db.execute("DELETE FROM dm_reactions WHERE msg_id = ? AND user_id = ? "
                       "AND emoji = ?", (msg_id, uid, emoji))
        else:
            db.execute("INSERT INTO dm_reactions(msg_id, user_id, emoji) "
                       "VALUES(?,?,?)", (msg_id, uid, emoji))
        db.commit()
    return {"ok": 1}


# ------------------------------------------------------------ push + live pings
@app.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    sub = request.get_json(silent=True) or {}
    ep = sub.get("endpoint", "")
    keys = sub.get("keys", {})
    if not ep or not keys.get("p256dh") or not keys.get("auth"):
        return {"ok": 0}
    db = get_db()
    db.execute("INSERT INTO push_subs(endpoint, user_id, p256dh, auth, created_at) "
               "VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
               "user_id = excluded.user_id, p256dh = excluded.p256dh, "
               "auth = excluded.auth",
               (ep[:500], session["user_id"], keys["p256dh"][:200],
                keys["auth"][:100], datetime.utcnow().isoformat(timespec="seconds")))
    db.commit()
    return {"ok": 1}


@app.route("/push/test", methods=["POST"])
@login_required
def push_test():
    """Fire a real push at yourself so you SEE it working on the lock screen."""
    db = get_db()
    subs = [dict(r) for r in db.execute(
        "SELECT * FROM push_subs WHERE user_id = ?", (session["user_id"],))]
    s = get_settings()
    if not subs or not s.get("vapid_private"):
        return {"ok": 0, "subs": len(subs)}
    tt = T.get(session.get("lang", "en")) or T["en"]
    payload = {"title": "🎉 " + s.get("site_name", "KurdRoom"),
               "body": tt.get("push_test_ok", "Notifications are working!"),
               "url": "/", "tag": "test"}
    threading.Thread(target=_do_push,
                     args=(subs, payload, s["vapid_private"]), daemon=True).start()
    return {"ok": 1, "subs": len(subs)}


@app.route("/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    sub = request.get_json(silent=True) or {}
    if sub.get("endpoint"):
        db = get_db()
        db.execute("DELETE FROM push_subs WHERE endpoint = ?", (sub["endpoint"],))
        db.commit()
    return {"ok": 1}


@app.route("/api/pings")
@login_required
def api_pings():
    """Tiny endpoint the whole app polls to feel instant: unread counts + toasts."""
    db = get_db()
    uid = session["user_id"]
    lang = session.get("lang", "en")
    unread_d = db.execute("SELECT COUNT(*) FROM dms WHERE to_id = ? AND is_read = 0",
                          (uid,)).fetchone()[0]
    unread_n = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? "
                          "AND is_read = 0", (uid,)).fetchone()[0]
    out = {"dm": unread_d, "notif": unread_n,
           "dm_id": 0, "dm_text": "", "dm_link": "",
           "notif_id": 0, "notif_text": "", "notif_link": "",
           "kinds": [r[0] for r in db.execute(
               "SELECT DISTINCT kind FROM notifications WHERE user_id = ? "
               "AND is_read = 0", (uid,))]}
    if unread_d:
        m = db.execute(
            "SELECT d.id, u.username, u.full_name FROM dms d "
            "JOIN users u ON u.id = d.from_id WHERE d.to_id = ? AND d.is_read = 0 "
            "ORDER BY d.id DESC LIMIT 1", (uid,)).fetchone()
        if m:
            out["dm_id"] = m["id"]
            out["dm_text"] = "✉️ " + (m["full_name"] or m["username"]) + " · " + \
                (T.get(lang) or T["en"]).get("new_msg_toast", "New message")
            out["dm_link"] = url_for("dm_thread", username=m["username"])
    if unread_n:
        n = db.execute("SELECT * FROM notifications WHERE user_id = ? AND is_read = 0 "
                       "AND kind != 'dm' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        if n:
            out["notif_id"] = n["id"]
            out["notif_text"] = NOTIF_ICONS.get(n["kind"], "🔔") + " " + \
                push_text(lang, n["kind"], n["actor"])
            out["notif_link"] = n["link"] or url_for("notifications")
    return out


_transcode_lock = threading.Lock()


def transcode_voice(stored, folder="dmfiles"):
    """Convert a webm voice file to mp3 so iPhones can play it.
    Returns the new stored filename, or None if conversion failed."""
    if not stored.lower().endswith(".webm"):
        return None
    dst_name = stored.rsplit(".", 1)[0] + ".mp3"
    src = os.path.join(BASE_DIR, folder, stored)
    dst = os.path.join(BASE_DIR, folder, dst_name)
    with _transcode_lock:
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst_name          # another request already converted it
        if not os.path.exists(src):
            return None
        try:
            import subprocess
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            r = subprocess.run([ff, "-y", "-i", src, "-vn",
                                "-acodec", "libmp3lame", "-b:a", "64k", dst],
                               capture_output=True, timeout=60,
                               stdin=subprocess.DEVNULL)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                try:
                    os.remove(src)
                except OSError:
                    pass
                return dst_name
        except Exception:
            pass
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass
        return None


@app.route("/dmfile/<int:msg_id>")
@login_required
def dm_file(msg_id):
    from flask import send_from_directory
    db = get_db()
    m = db.execute("SELECT * FROM dms WHERE id = ?", (msg_id,)).fetchone()
    if m is None or not m["stored"]:
        abort(404)
    if session["user_id"] not in (m["from_id"], m["to_id"]):
        abort(403)
    if m["kind"] == "voice" and m["stored"].lower().endswith(".webm"):
        new_name = transcode_voice(m["stored"])     # heal old voices on the fly
        if new_name:
            db.execute("UPDATE dms SET stored = ? WHERE id = ?", (new_name, msg_id))
            db.commit()
            m = db.execute("SELECT * FROM dms WHERE id = ?", (msg_id,)).fetchone()
    inline = m["kind"] in ("image", "voice")
    import mimetypes as _mt
    ext = m["stored"].rsplit(".", 1)[-1].lower() if "." in m["stored"] else ""
    mime = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "webm": "audio/webm",
            "ogg": "audio/ogg", "wav": "audio/wav"}.get(ext) if m["kind"] == "voice"         else _mt.guess_type(m["stored"])[0]
    return send_from_directory(os.path.join(BASE_DIR, "dmfiles"), m["stored"],
                               as_attachment=not inline, mimetype=mime,
                               download_name=(m["stored"] if m["kind"] == "voice"
                                              else (m["orig_name"] or m["stored"])))


# ---------------------------------------------------------------- posts
POST_CATS = ["research", "science", "tech", "ai", "other"]


@app.route("/posts")
@login_required
def posts():
    db = get_db()
    uid = session["user_id"]
    cat = request.args.get("cat", "")
    where, args = "", []
    if cat in POST_CATS:
        where, args = "WHERE p.category = ?", [cat]
    rows = db.execute(
        f"SELECT p.*, u.username, u.full_name, "
        f"(SELECT COUNT(*) FROM post_likes l WHERE l.post_id = p.id) AS likes, "
        f"(SELECT COUNT(*) FROM post_likes l WHERE l.post_id = p.id AND l.user_id = ?) AS mine, "
        f"(SELECT COUNT(*) FROM post_comments c WHERE c.post_id = p.id) AS n_comments "
        f"FROM posts p JOIN users u ON u.id = p.user_id {where} "
        f"ORDER BY p.id DESC LIMIT 60", [uid] + args).fetchall()
    items = []
    for p in rows:
        comments = db.execute(
            "SELECT c.*, u.username FROM post_comments c JOIN users u ON u.id = c.user_id "
            "WHERE c.post_id = ? ORDER BY c.id", (p["id"],)).fetchall()
        items.append(dict(p, comments=comments,
                          level=user_xp(p["user_id"])["level"]))
    return render_template("posts.html", user=current_user(), posts=items, cat=cat,
                           cats=POST_CATS)


@app.route("/post/create", methods=["POST"])
@login_required
def post_create():
    from werkzeug.utils import secure_filename
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    cat = request.form.get("category", "other")
    if cat not in POST_CATS:
        cat = "other"
    if title and content:
        db = get_db()
        cur = db.execute("INSERT INTO posts(user_id, title, content, category, "
                         "created_at) VALUES(?,?,?,?,?)",
                         (session["user_id"], title[:120], content[:4000], cat,
                          datetime.utcnow().isoformat(timespec="seconds")))
        f = request.files.get("image")
        if f and f.filename and "." in f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower()
            if ext in IMG_EXTS:
                stored = f"{cur.lastrowid}_{secure_filename(f.filename)}"
                f.save(os.path.join(BASE_DIR, "postfiles", stored))
                db.execute("UPDATE posts SET image = ? WHERE id = ?",
                           (stored, cur.lastrowid))
        db.commit()
    return redirect(url_for("posts"))


@app.route("/postimg/<int:post_id>")
@login_required
def post_image(post_id):
    from flask import send_from_directory
    p = get_db().execute("SELECT image FROM posts WHERE id = ?", (post_id,)).fetchone()
    if p is None or not p["image"]:
        abort(404)
    return send_from_directory(os.path.join(BASE_DIR, "postfiles"), p["image"])


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def post_like(post_id):
    db = get_db()
    if db.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone():
        hit = db.execute("SELECT 1 FROM post_likes WHERE post_id = ? AND user_id = ?",
                         (post_id, session["user_id"])).fetchone()
        if hit:
            db.execute("DELETE FROM post_likes WHERE post_id = ? AND user_id = ?",
                       (post_id, session["user_id"]))
        else:
            db.execute("INSERT INTO post_likes(post_id, user_id) VALUES(?,?)",
                       (post_id, session["user_id"]))
        db.commit()
    return redirect(request.referrer or url_for("posts"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def post_comment(post_id):
    content = request.form.get("content", "").strip()
    db = get_db()
    if content and db.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone():
        db.execute("INSERT INTO post_comments(post_id, user_id, content, created_at) "
                   "VALUES(?,?,?,?)", (post_id, session["user_id"], content[:500],
                                       datetime.utcnow().isoformat(timespec="seconds")))
        db.commit()
    return redirect(request.referrer or url_for("posts"))


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def post_delete(post_id):
    db = get_db()
    p = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if p is None:
        abort(404)
    if p["user_id"] != session["user_id"] and not current_user()["is_admin"]:
        abort(403)
    if p["image"]:
        fp = os.path.join(BASE_DIR, "postfiles", p["image"])
        if os.path.exists(fp):
            os.remove(fp)
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()
    return redirect(url_for("posts"))


# ---------------------------------------------------------------- support
@app.route("/support", methods=["GET", "POST"])
@login_required
def support():
    db = get_db()
    uid = session["user_id"]
    if request.method == "POST":
        msg = request.form.get("message", "").strip()
        rating = request.form.get("rating") or None
        if rating:
            try:
                rating = max(1, min(5, int(rating)))
            except ValueError:
                rating = None
        if msg or rating:
            db.execute("INSERT INTO feedback(user_id, message, rating, created_at) "
                       "VALUES(?,?,?,?)", (uid, msg[:2000], rating,
                                           datetime.utcnow().isoformat(timespec="seconds")))
            db.commit()
            flash(tr("thanks_fb"), "ok")
        return redirect(url_for("support"))
    mine = db.execute("SELECT * FROM feedback WHERE user_id = ? ORDER BY id DESC "
                      "LIMIT 10", (uid,)).fetchall()
    return render_template("support.html", user=current_user(), mine=mine)


@app.route("/admin/feedback/<int:fb_id>/resolve", methods=["POST"])
@admin_required
def admin_feedback_resolve(fb_id):
    db = get_db()
    db.execute("UPDATE feedback SET resolved = 1 - resolved WHERE id = ?", (fb_id,))
    db.commit()
    return redirect(url_for("admin"))


# ---------------------------------------------------------------- leaderboard
@app.route("/leaderboard")
@login_required
def leaderboard():
    from datetime import timedelta
    db = get_db()
    uid = session["user_id"]
    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    rows = []
    for pid in sorted(get_friend_ids(uid) | {uid}):
        person = db.execute("SELECT id, username, full_name FROM users WHERE id = ?",
                            (pid,)).fetchone()
        if person is None:
            continue
        plans_done = db.execute(
            "SELECT COUNT(*) FROM plans WHERE user_id = ? AND done = 1 "
            "AND substr(done_at,1,10) >= ?", (pid, week_start)).fetchone()[0]
        habit_hits = db.execute(
            "SELECT COUNT(*) FROM habit_checks hc JOIN habits h ON h.id = hc.habit_id "
            "WHERE h.user_id = ? AND hc.day >= ?", (pid, week_start)).fetchone()[0]
        streak = user_streak(pid)
        points = plans_done * 10 + habit_hits * 5 + streak * 3
        rows.append(dict(person=person, plans=plans_done, habits=habit_hits,
                         streak=streak, points=points))
    rows.sort(key=lambda r: r["points"], reverse=True)
    return render_template("leaderboard.html", user=current_user(), rows=rows,
                           week_start=week_start)


# ---------------------------------------------------------------- group files
FILE_EXTS = ("pdf", "png", "jpg", "jpeg", "webp", "gif", "txt", "md",
             "docx", "xlsx", "pptx", "zip")


@app.route("/group/<int:group_id>/file/upload", methods=["POST"])
@login_required
def gfile_upload(group_id):
    member_group_or_403(group_id)
    f = request.files.get("file")
    if f and f.filename:
        from werkzeug.utils import secure_filename
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext in FILE_EXTS:
            db = get_db()
            safe = secure_filename(f.filename) or f"file.{ext}"
            cur = db.execute(
                "INSERT INTO group_files(group_id, user_id, stored, orig_name, "
                "created_at) VALUES(?,?,?,?,?)",
                (group_id, session["user_id"], "", f.filename[:120],
                 datetime.utcnow().isoformat(timespec="seconds")))
            stored = f"{group_id}_{cur.lastrowid}_{safe}"
            path = os.path.join(BASE_DIR, "groupfiles", stored)
            f.save(path)
            db.execute("UPDATE group_files SET stored = ?, size = ? WHERE id = ?",
                       (stored, os.path.getsize(path), cur.lastrowid))
            db.commit()
            flash(tr("ok_saved"), "ok")
        else:
            flash(tr("err_file_type"), "error")
    return redirect(url_for("group_page", group_id=group_id) + "#files")


@app.route("/gfile/<int:file_id>")
@login_required
def gfile_download(file_id):
    from flask import send_from_directory
    db = get_db()
    f = db.execute("SELECT * FROM group_files WHERE id = ?", (file_id,)).fetchone()
    if f is None:
        abort(404)
    if not is_group_member(f["group_id"], session["user_id"]):
        abort(403)
    return send_from_directory(os.path.join(BASE_DIR, "groupfiles"), f["stored"],
                               as_attachment=True, download_name=f["orig_name"])


@app.route("/gfile/<int:file_id>/delete", methods=["POST"])
@login_required
def gfile_delete(file_id):
    db = get_db()
    f = db.execute("SELECT * FROM group_files WHERE id = ?", (file_id,)).fetchone()
    if f is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (f["group_id"],)).fetchone()
    if f["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    path = os.path.join(BASE_DIR, "groupfiles", f["stored"])
    if os.path.exists(path):
        os.remove(path)
    db.execute("DELETE FROM group_files WHERE id = ?", (file_id,))
    db.commit()
    return redirect(url_for("group_page", group_id=f["group_id"]) + "#files")


# ---------------------------------------------------------------- shared decks
@app.route("/group/<int:group_id>/deck/share", methods=["POST"])
@login_required
def gdeck_share(group_id):
    member_group_or_403(group_id)
    subject = request.form.get("subject", "").strip()
    if subject:
        db = get_db()
        owns = db.execute("SELECT 1 FROM flashcards WHERE user_id = ? AND subject = ?",
                          (session["user_id"], subject)).fetchone()
        if owns:
            try:
                db.execute("INSERT INTO group_decks(group_id, user_id, subject, "
                           "created_at) VALUES(?,?,?,?)",
                           (group_id, session["user_id"], subject,
                            datetime.utcnow().isoformat(timespec="seconds")))
                db.commit()
                flash(tr("ok_saved"), "ok")
            except sqlite3.IntegrityError:
                pass  # already shared
    return redirect(url_for("group_page", group_id=group_id) + "#decks")


@app.route("/gdeck/<int:deck_id>/unshare", methods=["POST"])
@login_required
def gdeck_unshare(deck_id):
    db = get_db()
    d = db.execute("SELECT * FROM group_decks WHERE id = ?", (deck_id,)).fetchone()
    if d is None:
        abort(404)
    g_row = db.execute("SELECT owner_id FROM groups WHERE id = ?",
                       (d["group_id"],)).fetchone()
    if d["user_id"] != session["user_id"] and g_row["owner_id"] != session["user_id"]:
        abort(403)
    db.execute("DELETE FROM group_decks WHERE id = ?", (deck_id,))
    db.commit()
    return redirect(url_for("group_page", group_id=d["group_id"]) + "#decks")


# ---------------------------------------------------------------- fonts
BUILTIN_FONTS = ["Carlito", "Aria", "Bebas Neue", "Poppins", "Montserrat", "Oswald",
                 "Playfair Display", "Lobster", "Pacifico", "Roboto Slab",
                 "Cairo", "Amiri", "Tajawal"]
GOOGLE_FONTS_URL = ("https://fonts.googleapis.com/css2?family=Bebas+Neue"
                    "&family=Poppins:wght@400;700&family=Montserrat:wght@400;800"
                    "&family=Oswald:wght@500&family=Playfair+Display:wght@700"
                    "&family=Lobster&family=Pacifico&family=Roboto+Slab:wght@700"
                    "&family=Cairo:wght@400;700&family=Amiri:wght@700"
                    "&family=Tajawal:wght@700&display=swap")
FONT_EXTS = ("ttf", "otf", "woff", "woff2")


def my_fonts():
    return get_db().execute("SELECT * FROM fonts WHERE user_id = ? ORDER BY id",
                            (session["user_id"],)).fetchall()


@app.route("/font/upload", methods=["POST"])
@login_required
def font_upload():
    f = request.files.get("font")
    if f and f.filename and "." in f.filename:
        from werkzeug.utils import secure_filename
        ext = f.filename.rsplit(".", 1)[-1].lower()
        if ext in FONT_EXTS:
            db = get_db()
            name = os.path.splitext(f.filename)[0][:40] or "MyFont"
            cur = db.execute("INSERT INTO fonts(user_id, name, stored, created_at) "
                             "VALUES(?,?,?,?)",
                             (session["user_id"], name, "",
                              datetime.utcnow().isoformat(timespec="seconds")))
            stored = f"{session['user_id']}_{cur.lastrowid}_{secure_filename(f.filename)}"
            f.save(os.path.join(BASE_DIR, "static", "fonts", stored))
            db.execute("UPDATE fonts SET stored = ? WHERE id = ?", (stored, cur.lastrowid))
            db.commit()
            flash(tr("ok_saved"), "ok")
        else:
            flash(tr("err_font_type"), "error")
    return redirect(request.referrer or url_for("tool_poster"))


@app.route("/font/<int:font_id>/delete", methods=["POST"])
@login_required
def font_delete(font_id):
    db = get_db()
    row = db.execute("SELECT * FROM fonts WHERE id = ? AND user_id = ?",
                     (font_id, session["user_id"])).fetchone()
    if row:
        p = os.path.join(BASE_DIR, "static", "fonts", row["stored"])
        if os.path.exists(p):
            os.remove(p)
        db.execute("DELETE FROM fonts WHERE id = ?", (font_id,))
        db.commit()
    return redirect(request.referrer or url_for("tool_poster"))


# ---------------------------------------------------------------- student tools
@app.route("/tools/poster")
@login_required
def tool_poster():
    return render_template("tools_poster.html", user=current_user(),
                           fonts=BUILTIN_FONTS, user_fonts=my_fonts(),
                           gfonts=GOOGLE_FONTS_URL)


@app.route("/tools/cv")
@login_required
def tool_cv():
    return render_template("tools_cv.html", user=current_user(),
                           fonts=BUILTIN_FONTS, user_fonts=my_fonts(),
                           gfonts=GOOGLE_FONTS_URL)


@app.route("/tools/essay")
@login_required
def tool_essay():
    return render_template("tools_essay.html", user=current_user())


@app.route("/tools/citations")
@login_required
def tool_citations():
    return render_template("tools_cite.html", user=current_user())


@app.route("/tools/quiz")
@login_required
def tool_quiz():
    cards = get_db().execute(
        "SELECT subject, question, answer FROM flashcards WHERE user_id = ? ORDER BY id",
        (session["user_id"],)).fetchall()
    data = [dict(s=c["subject"], q=c["question"], a=c["answer"]) for c in cards]
    return render_template("tools_quiz.html", user=current_user(), cards=data,
                           subjects=sorted({c["subject"] for c in cards}))


# ---------------------------------------------------------------- AI assistant
def call_ai(task, text, lang, system_override=None):
    import json as _json
    import urllib.request
    import urllib.error
    s = get_settings()
    key = (s.get("ai_api_key") or "").strip()
    if not key:
        return None, "not_configured"
    model = (s.get("ai_model") or "").strip() or "claude-haiku-4-5"
    lang_name = {"en": "English", "ar": "Arabic", "ku": "Kurdish (Sorani)"}.get(lang, "English")
    tasks = {
        "rate": "You are a fair, encouraging university writing tutor. Rate the essay "
                "out of 100, then give strengths, weaknesses, and 3 concrete improvements.",
        "sum": "Summarize the text clearly in short bullet-like lines a student can revise from.",
        "explain": "Explain the text or topic simply, like teaching a first-year student, "
                   "with a small example.",
        "improve": "Rewrite the text with better clarity, grammar, and flow. Keep the "
                   "author's voice and meaning. Then list the main changes you made.",
    }
    system = ((system_override or tasks.get(task, tasks["explain"]))
              + f" Respond in {lang_name}. Be concise and practical.")
    body = _json.dumps({"model": model, "max_tokens": 1500, "system": system,
                        "messages": [{"role": "user", "content": text[:12000]}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = _json.load(r)
        return "".join(b.get("text", "") for b in data.get("content", [])), None
    except urllib.error.HTTPError as e:
        try:
            msg = _json.load(e).get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return None, msg
    except Exception as e:
        return None, str(e)


@app.route("/tools/ai", methods=["GET", "POST"])
@login_required
def tool_ai():
    configured = bool((get_settings().get("ai_api_key") or "").strip())
    result = error = None
    text = request.form.get("text", "")
    task = request.form.get("task", "rate")
    if request.method == "POST" and configured and text.strip():
        result, error = call_ai(task, text.strip(), session.get("lang", "en"))
        if error == "not_configured":
            configured, error = False, None
    return render_template("tools_ai.html", user=current_user(), configured=configured,
                           result=result, error=error, text=text, task=task)


# ---------------------------------------------------------------- Plus tools
def plus_gate_or_none(icon, name_key):
    """Free users see a beautiful upgrade screen instead of the tool."""
    if not current_user()["plus"]:
        return render_template("plus_gate.html", user=current_user(),
                               tool_icon=icon, tool_key=name_key)
    return None


@app.route("/tools/studyplan", methods=["GET", "POST"])
@login_required
def tool_studyplan():
    gate = plus_gate_or_none("🗓", "tool_studyplan")
    if gate:
        return gate
    db = get_db()
    uid = session["user_id"]
    today_d = date.today()
    exams = [e for e in db.execute("SELECT * FROM exams WHERE user_id = ? "
                                   "ORDER BY exam_date", (uid,))
             if (e["exam_date"] or "") > today_d.isoformat()]
    plan, created = None, 0
    if request.method == "POST" and exams:
        per_day = min(4, max(1, request.form.get("per_day", 2, type=int)))
        sched = {}
        horizon = max(date.fromisoformat(e["exam_date"]) for e in exams)
        d = today_d
        while d < horizon:
            todays = []
            for e in sorted(exams, key=lambda x: x["exam_date"]):
                if len(todays) >= per_day:
                    break
                ed = date.fromisoformat(e["exam_date"])
                if d >= ed or e["subject"] in todays:
                    continue
                days_left = (ed - today_d).days
                gap = 1 if days_left <= 7 else (2 if days_left <= 14 else 3)
                if (d - today_d).days % gap == 0:
                    todays.append(e["subject"])
            if todays:
                sched[d.isoformat()] = todays
            d += _td(days=1)
        if request.form.get("save") and sched:
            now = datetime.utcnow().isoformat(timespec="seconds")
            for day_iso, subs in sched.items():
                for subj in subs:
                    db.execute("INSERT INTO plans(user_id, title, plan_type, "
                               "priority, due_date, created_at) VALUES(?,?,?,?,?,?)",
                               (uid, f"📖 {tr('sp_revise')}: {subj}", "short",
                                "medium", day_iso, now))
                    created += 1
            db.commit()
            flash(f"{created} {tr('sp_created')}", "ok")
            return redirect(url_for("dashboard"))
        plan = sorted(sched.items())
    return render_template("tools_studyplan.html", user=current_user(),
                           exams=exams, plan=plan,
                           per_day=request.form.get("per_day", 2, type=int) or 2)


PLUS_AI_TOOLS = {
    "summarizer": dict(
        icon="📄", key="tool_sum2", ph="sum2_ph",
        system="You are an expert study assistant. Summarize the material into: "
               "1) a five-line summary, 2) the key points to memorize as short "
               "bullets, 3) three likely exam focus areas."),
    "present": dict(
        icon="🎤", key="tool_present", ph="present_ph",
        system="You are a presentation coach. Create a slide-by-slide outline "
               "(6–10 slides) for the topic: a title per slide, three short bullet "
               "points each, brief speaker notes, plus a strong opening and "
               "closing line."),
    "translate": dict(icon="🔤", key="tool_translate", ph="translate_ph",
                      system=None),   # built per direction below
    "predict": dict(
        icon="❓", key="tool_predict", ph="predict_ph",
        system="You are an experienced university examiner. From the material, "
               "predict the most likely exam questions: six short-answer and two "
               "essay questions, each with a model answer outline. Order by "
               "likelihood."),
}


@app.route("/tools/plus/<tool>", methods=["GET", "POST"])
@login_required
def tool_plus(tool):
    cfg = PLUS_AI_TOOLS.get(tool)
    if cfg is None:
        abort(404)
    gate = plus_gate_or_none(cfg["icon"], cfg["key"])
    if gate:
        return gate
    configured = bool((get_settings().get("ai_api_key") or "").strip())
    result = error = None
    text = request.form.get("text", "")
    target = request.form.get("target", "en")
    if request.method == "POST" and configured and text.strip():
        system = cfg["system"]
        if tool == "translate":
            tn = {"en": "English", "ar": "Arabic",
                  "ku": "Kurdish (Sorani)"}.get(target, "English")
            system = (f"You are an academic translator. Translate the text into "
                      f"{tn} with a clear academic tone and polished style. After "
                      f"the translation, add 2–3 short notes about important word "
                      f"choices. Always answer in {tn}, regardless of any other "
                      f"language instruction.")
        result, error = call_ai("custom", text.strip(),
                                session.get("lang", "en"), system_override=system)
        if error == "not_configured":
            configured, error = False, None
    return render_template("tools_plusai.html", user=current_user(), tool=tool,
                           cfg=cfg, configured=configured, result=result,
                           error=error, text=text, target=target)


# ---------------------------------------------------------------- SEO
@app.route("/favicon.ico")
def favicon():
    # browsers and Google that ask for the classic path get the app icon
    return redirect(url_for("static", filename="icon-192.png"))


@app.route("/robots.txt")
def robots_txt():
    from flask import Response
    return Response("User-agent: *\nAllow: /\nSitemap: " +
                    request.url_root.rstrip("/") + "/sitemap.xml\n",
                    mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    from flask import Response
    base = request.url_root.rstrip("/")
    pages = ["/", "/login", "/register"]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in pages:
        xml.append(f"<url><loc>{base}{p}</loc><changefreq>weekly</changefreq></url>")
    xml.append("</urlset>")
    return Response("\n".join(xml), mimetype="application/xml")


# ---------------------------------------------------------------- PWA
@app.route("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------- admin
@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    users = db.execute("""
        SELECT u.*, COUNT(p.id) AS plan_count
        FROM users u LEFT JOIN plans p ON p.user_id = u.id
        GROUP BY u.id ORDER BY u.created_at ASC""").fetchall()
    quotes = db.execute("SELECT * FROM quotes ORDER BY id").fetchall()
    fb = db.execute("SELECT f.*, u.username, u.plus AS uplus FROM feedback f "
                    "JOIN users u ON u.id = f.user_id "
                    "ORDER BY f.resolved ASC, u.plus DESC, f.id DESC LIMIT 40").fetchall()
    avg_rating = db.execute("SELECT AVG(rating), COUNT(rating) FROM feedback "
                            "WHERE rating IS NOT NULL").fetchone()
    return render_template("admin.html", user=current_user(), users=users,
                           quotes=quotes, fb=fb,
                           avg_rating=round(avg_rating[0], 2) if avg_rating[0] else None,
                           n_ratings=avg_rating[1])


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    db = get_db()
    fields = ["site_name", "tagline_en", "tagline_ar", "tagline_ku", "accent_color",
              "ai_api_key", "ai_model", "sponsor_name", "sponsor_url", "plus_price",
              "fib_link", "fastpay_link", "about_text", "about_en", "about_ar",
              "about_ku", "social_instagram",
              "social_facebook", "social_website", "social_email", "plus_phone",
              "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from",
              "reg_universities", "reg_colleges", "reg_departments", "reg_jobs"]
    for f in fields:
        if f in request.form:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (f, request.form[f].strip()))
    for flag in ("allow_registration", "sponsor_enabled"):
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (flag, "1" if request.form.get(flag) else "0"))
    logo = request.files.get("sponsor_img")
    if logo and logo.filename:
        ext = logo.filename.rsplit(".", 1)[-1].lower() if "." in logo.filename else ""
        if ext in ("png", "jpg", "jpeg", "webp", "gif"):
            logo.save(os.path.join(BASE_DIR, "static", "avatars", "sponsor.png"))
    qr = request.files.get("fib_qr")
    if qr and qr.filename:
        ext = qr.filename.rsplit(".", 1)[-1].lower() if "." in qr.filename else ""
        if ext in ("png", "jpg", "jpeg", "webp"):
            qr.save(os.path.join(BASE_DIR, "static", "avatars", "fibqr.png"))
    # delete uploaded images when the admin ticks the boxes
    if request.form.get("del_fib_qr"):
        try:
            os.remove(os.path.join(BASE_DIR, "static", "avatars", "fibqr.png"))
        except OSError:
            pass
    if request.form.get("del_sponsor_img"):
        try:
            os.remove(os.path.join(BASE_DIR, "static", "avatars", "sponsor.png"))
        except OSError:
            pass
    db.commit()
    flash(tr("ok_saved"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/plus", methods=["POST"])
@admin_required
def admin_toggle_plus(user_id):
    db = get_db()
    row = db.execute("SELECT plus FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        db.execute("UPDATE users SET plus = ? WHERE id = ?",
                   (0 if row["plus"] else 1, user_id))
        if not row["plus"]:   # just activated -> congratulate them instantly
            notify(user_id, "plus_on", link=url_for("plus_page"))
        db.commit()
        flash(tr("ok_saved"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/toggle_admin", methods=["POST"])
@admin_required
def admin_toggle_admin(user_id):
    if user_id == session["user_id"]:
        return redirect(url_for("admin"))  # can't demote yourself
    db = get_db()
    row = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        db.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                   (0 if row["is_admin"] else 1, user_id))
        db.commit()
        flash(tr("ok_saved"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session["user_id"]:
        return redirect(url_for("admin"))  # can't delete yourself
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash(tr("ok_deleted"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/reset_password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    new_pw = request.form.get("new_password", "")
    if len(new_pw) < 6:
        flash(tr("err_pw_short"), "error")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (generate_password_hash(new_pw), user_id))
    db.commit()
    flash(tr("ok_saved"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/quote/add", methods=["POST"])
@admin_required
def admin_quote_add():
    en = request.form.get("text_en", "").strip()
    if not en:
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("INSERT INTO quotes(text_en, text_ar, text_ku) VALUES(?,?,?)",
               (en, request.form.get("text_ar", "").strip(),
                request.form.get("text_ku", "").strip()))
    db.commit()
    flash(tr("ok_saved"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/quote/<int:quote_id>/delete", methods=["POST"])
@admin_required
def admin_quote_delete(quote_id):
    db = get_db()
    db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    db.commit()
    flash(tr("ok_deleted"), "ok")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------- error reporting
@app.errorhandler(Exception)
def handle_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e                      # keep normal 403/404 behaviour
    import traceback
    tb = traceback.format_exc()
    try:
        with open(os.path.join(BASE_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write("\n==== " + datetime.utcnow().isoformat(timespec="seconds")
                    + " " + request.path + " ====\n" + tb)
    except Exception:
        pass
    # show the technical details only on localhost (i.e. to the developer)
    show = request.remote_addr in ("127.0.0.1", "::1")
    try:
        return render_template("error.html", tb=tb if show else None), 500
    except Exception:
        return "<h2>Error</h2><pre>" + (tb if show else "See error.log") + "</pre>", 500


# ---------------------------------------------------------------- main
init_db()

@app.route("/admin/stats")
@admin_required
def admin_stats():
    db = get_db()

    def rows(q, *a):
        return db.execute(q, a).fetchall()

    def one(q, *a):
        return db.execute(q, a).fetchone()[0]

    total = one("SELECT COUNT(*) FROM users")
    week_ago = (datetime.utcnow() - _td(days=7)).isoformat(timespec="seconds")
    cards = dict(
        total=total,
        plus=one("SELECT COUNT(*) FROM users WHERE plus = 1"),
        verified=one("SELECT COUNT(*) FROM users WHERE email_verified = 1"),
        active7=one("SELECT COUNT(*) FROM users WHERE last_seen > ?", week_ago),
        completed=one("SELECT COUNT(*) FROM users WHERE profile_v >= 2"),
    )
    lvls = rows("SELECT COALESCE(NULLIF(edu_level, ''), 'unset') AS k, COUNT(*) AS n "
                "FROM users GROUP BY k ORDER BY n DESC")
    unis = rows("SELECT institution AS k, COUNT(*) AS n FROM users WHERE edu_level "
                "IN ('bachelor','master','phd','professor') AND institution != '' "
                "GROUP BY k ORDER BY n DESC")
    colleges = rows("SELECT college AS k, COUNT(*) AS n FROM users WHERE "
                    "college != '' GROUP BY k ORDER BY n DESC LIMIT 25")
    deps = rows("SELECT department AS k, COUNT(*) AS n FROM users WHERE "
                "department != '' GROUP BY k ORDER BY n DESC LIMIT 25")
    slvls = rows("SELECT COALESCE(NULLIF(school_level, ''), '?') AS k, COUNT(*) AS n "
                 "FROM users WHERE edu_level = 'school' GROUP BY k ORDER BY n DESC")
    grades = rows("SELECT grade AS k, COUNT(*) AS n FROM users WHERE "
                  "edu_level = 'school' AND grade != '' GROUP BY k "
                  "ORDER BY CAST(k AS INTEGER)")
    jobs = rows("SELECT job_title AS k, COUNT(*) AS n FROM users WHERE "
                "job_title != '' GROUP BY k ORDER BY n DESC LIMIT 25")
    months = rows("SELECT substr(created_at, 1, 7) AS k, COUNT(*) AS n FROM users "
                  "GROUP BY k ORDER BY k DESC LIMIT 12")
    return render_template("admin_stats.html", user=current_user(), cards=cards,
                           lvls=lvls, unis=unis, colleges=colleges, deps=deps,
                           slvls=slvls, grades=grades, jobs=jobs,
                           months=list(reversed(months)))


# ---------------------------------------------------------------- reminders
def _emit_reminder(con, uid, lang, kind, actor, link, priv, site):
    con.execute("INSERT INTO notifications(user_id, kind, actor, link, created_at) "
                "VALUES(?,?,?,?,?)",
                (uid, kind, actor, link,
                 datetime.utcnow().isoformat(timespec="seconds")))
    subs = [dict(r) for r in con.execute(
        "SELECT * FROM push_subs WHERE user_id = ?", (uid,))]
    if subs and priv:
        _do_push(subs, {"title": f"{NOTIF_ICONS.get(kind, '🔔')} {site}",
                        "body": push_text(lang or "en", kind, actor),
                        "url": link, "tag": kind}, priv)


def _scan_reminders(con):
    today = date.today().isoformat()
    tomorrow = (date.today() + _td(days=1)).isoformat()
    s = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    priv = s.get("vapid_private", "")
    site = s.get("site_name", "KurdRoom")
    # plans due today — one warning per plan per day
    for p in con.execute(
            "SELECT p.id, p.user_id, p.title, u.lang FROM plans p "
            "JOIN users u ON u.id = p.user_id "
            "WHERE p.done = 0 AND p.due_date = ?", (today,)):
        link = f"/dashboard#p{p['id']}"
        if con.execute("SELECT 1 FROM notifications WHERE user_id = ? AND "
                       "kind = 'deadline' AND link = ? AND created_at >= ?",
                       (p["user_id"], link, today)).fetchone():
            continue
        _emit_reminder(con, p["user_id"], p["lang"], "deadline", p["title"],
                       link, priv, site)
    # plans overdue — one warning per plan, ever
    for p in con.execute(
            "SELECT p.id, p.user_id, p.title, u.lang FROM plans p "
            "JOIN users u ON u.id = p.user_id WHERE p.done = 0 "
            "AND p.due_date IS NOT NULL AND p.due_date != '' AND p.due_date < ?",
            (today,)):
        link = f"/dashboard#p{p['id']}"
        if con.execute("SELECT 1 FROM notifications WHERE user_id = ? AND "
                       "kind = 'overdue' AND link = ?",
                       (p["user_id"], link)).fetchone():
            continue
        _emit_reminder(con, p["user_id"], p["lang"], "overdue", p["title"],
                       link, priv, site)
    # pending homework — a nudge every 2 hours until it's done
    two_h_ago = (datetime.utcnow() - _td(hours=2)).isoformat(timespec="seconds")
    for u in con.execute(
            "SELECT h.user_id, u.lang, COUNT(*) AS n, "
            "GROUP_CONCAT(h.title, ', ') AS titles FROM homework h "
            "JOIN users u ON u.id = h.user_id WHERE h.done = 0 "
            "GROUP BY h.user_id"):
        if con.execute("SELECT 1 FROM notifications WHERE user_id = ? AND "
                       "kind = 'homework' AND created_at > ?",
                       (u["user_id"], two_h_ago)).fetchone():
            continue
        titles = (u["titles"] or "")[:75]
        actor = f"{u['n']} · {titles}" if u["n"] > 1 else titles
        # collapse older unread homework nudges so they never pile up
        con.execute("DELETE FROM notifications WHERE user_id = ? AND "
                    "kind = 'homework' AND is_read = 0", (u["user_id"],))
        _emit_reminder(con, u["user_id"], u["lang"], "homework", actor,
                       "/university#homework", priv, site)
    # Friday weekly report — once per week per user
    if date.today().weekday() == 4:
        wk_start = (date.today() - _td(days=date.today().weekday())).isoformat()
        six_days_ago = (datetime.utcnow() - _td(days=6)).isoformat(timespec="seconds")
        for u in con.execute("SELECT id, lang FROM users"):
            if con.execute("SELECT 1 FROM notifications WHERE user_id = ? AND "
                           "kind = 'weekly' AND created_at > ?",
                           (u["id"], six_days_ago)).fetchone():
                continue
            p_n = con.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND "
                              "done = 1 AND done_at >= ?",
                              (u["id"], wk_start)).fetchone()[0]
            h_n = con.execute("SELECT COUNT(*) FROM habit_checks hc JOIN habits ha "
                              "ON ha.id = hc.habit_id WHERE ha.user_id = ? AND "
                              "hc.day >= ?", (u["id"], wk_start)).fetchone()[0]
            hw_n = con.execute("SELECT COUNT(*) FROM homework WHERE user_id = ? AND "
                               "done = 1 AND created_at >= ?",
                               (u["id"], wk_start)).fetchone()[0]
            if p_n + h_n + hw_n == 0:
                continue           # nothing to brag about — skip quiet accounts
            actor = f"✓ {p_n} · 📊 {h_n} · 📝 {hw_n}"
            _emit_reminder(con, u["id"], u["lang"], "weekly", actor,
                           "/dashboard", priv, site)
    # exams today or tomorrow — one reminder per exam per day
    for e in con.execute(
            "SELECT e.id, e.user_id, e.subject, u.lang FROM exams e "
            "JOIN users u ON u.id = e.user_id WHERE e.exam_date IN (?, ?)",
            (today, tomorrow)):
        link = f"/university#e{e['id']}"
        if con.execute("SELECT 1 FROM notifications WHERE user_id = ? AND "
                       "kind = 'exam_soon' AND link = ? AND created_at >= ?",
                       (e["user_id"], link, today)).fetchone():
            continue
        _emit_reminder(con, e["user_id"], e["lang"], "exam_soon", e["subject"],
                       link, priv, site)
    con.commit()


def _reminder_loop():
    time.sleep(25)          # let the app finish booting first
    while True:
        try:
            con = sqlite3.connect(DB_PATH)
            con.row_factory = sqlite3.Row
            # 10-minute slot guard so multiple gunicorn workers scan only once
            slot = datetime.utcnow().strftime("%Y-%m-%dT%H:") + \
                str(datetime.utcnow().minute // 10)
            con.execute("INSERT OR IGNORE INTO settings(key, value) "
                        "VALUES('reminder_slot', '')")
            cur = con.execute("UPDATE settings SET value = ? WHERE "
                              "key = 'reminder_slot' AND value != ?", (slot, slot))
            con.commit()
            if cur.rowcount:
                _scan_reminders(con)
            con.close()
        except Exception:
            pass
        time.sleep(600)


threading.Thread(target=_reminder_loop, daemon=True).start()


if __name__ == "__main__":
    # Port 5001 by default (macOS AirPlay already occupies 5000).
    # Change it any time with:  PORT=8000 python app.py
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
