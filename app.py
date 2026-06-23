import os
import json
import re
import secrets
import smtplib
import sqlite3
from email.message import EmailMessage

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lms-ai-dev")
DATABASE = os.environ.get("LMS_DATABASE", "lms_ai.db")
LMS_COURSE_SLUG = "master1-ai-course"

LMS_TRANSLATIONS = {
    "fr": {
        "login": "Connexion", "register": "Inscription", "email": "Email",
        "password": "Mot de passe", "confirm_password": "Confirmer le mot de passe",
        "full_name": "Nom complet", "sign_in": "Se connecter", "sign_up": "S'inscrire",
        "new_student": "Nouvel etudiant ?", "create_account": "Creer un compte",
        "already_registered": "Deja inscrit ?", "dashboard": "Tableau de bord",
        "planning": "Cours & Planning", "assignments": "Devoirs", "quiz": "Quiz",
        "notes": "Notes de cours", "texts": "Textes", "qa": "Questions & Reponses",
        "announcements": "Annonces", "certificates": "Certificats", "settings": "Parametres",
        "teacher_dashboard": "Pilotage", "students": "Etudiants", "content": "Contenus",
        "grades": "Notes", "student_view": "Vue etudiant", "logout": "Deconnexion",
        "classes": "Classes", "select_class": "Choisir une classe",
        "invite_student": "Inscrire un etudiant", "send_invite": "Envoyer l'invitation",
        "active": "Actif", "inactive": "Desactive", "invited": "Invite",
        "reset_password": "Reinitialiser le mot de passe", "activate": "Activer",
        "deactivate": "Desactiver", "language": "Langue", "save": "Enregistrer",
    },
    "en": {
        "login": "Login", "register": "Registration", "email": "Email",
        "password": "Password", "confirm_password": "Confirm password",
        "full_name": "Full name", "sign_in": "Sign in", "sign_up": "Sign up",
        "new_student": "New student?", "create_account": "Create account",
        "already_registered": "Already registered?", "dashboard": "Dashboard",
        "planning": "Courses & Schedule", "assignments": "Assignments", "quiz": "Quiz",
        "notes": "Course notes", "texts": "Texts", "qa": "Questions & Answers",
        "announcements": "Announcements", "certificates": "Certificates", "settings": "Settings",
        "teacher_dashboard": "Management", "students": "Students", "content": "Content",
        "grades": "Grades", "student_view": "Student view", "logout": "Log out",
        "classes": "Classes", "select_class": "Select a class",
        "invite_student": "Enroll a student", "send_invite": "Send invitation",
        "active": "Active", "inactive": "Disabled", "invited": "Invited",
        "reset_password": "Reset password", "activate": "Activate",
        "deactivate": "Deactivate", "language": "Language", "save": "Save",
    },
}


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column_if_missing(conn, table, column, definition):
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def user_by_email(conn, email):
    return conn.execute("SELECT * FROM lms_users WHERE lower(email)=lower(?)", (email,)).fetchone()


def create_user(conn, full_name, email, password, role="student", status="active"):
    conn.execute("""
    INSERT OR IGNORE INTO lms_users (full_name, email, password_hash, role, status)
    VALUES (?, ?, ?, ?, ?)
    """, (full_name, email, generate_password_hash(password), role, status))
    return user_by_email(conn, email)


def send_email_now(recipient, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return False, "SMTP non configure"

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", username or "noreply@lms-ai.local")
    use_tls = os.environ.get("SMTP_TLS", "1") != "0"

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def queue_email(conn, recipient, subject, body):
    sent, detail = send_email_now(recipient, subject, body)
    status = "sent" if sent else "queued"
    stored_body = body if sent else f"{body}\n\n[SMTP] {detail}"
    conn.execute("""
    INSERT INTO lms_email_outbox (recipient, subject, body, status, sent_at)
    VALUES (?, ?, ?, ?, CASE WHEN ?='sent' THEN CURRENT_TIMESTAMP ELSE NULL END)
    """, (recipient, subject, stored_body, status, status))


def create_reset_token(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("""
    UPDATE lms_users
    SET reset_token=?, reset_sent_at=CURRENT_TIMESTAMP, must_reset_password=1
    WHERE id=?
    """, (token, user_id))
    return token


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS lms_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher', 'admin')),
            status TEXT NOT NULL DEFAULT 'active',
            language TEXT NOT NULL DEFAULT 'fr',
            reset_token TEXT,
            reset_sent_at TEXT,
            must_reset_password INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            level TEXT,
            academic_year TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_class_teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            role TEXT DEFAULT 'teacher',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(class_id, teacher_id)
        );
        CREATE TABLE IF NOT EXISTS lms_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            class_id INTEGER,
            semester TEXT,
            year TEXT,
            description TEXT,
            meet_url TEXT,
            presence_weight INTEGER DEFAULT 10,
            assignments_weight INTEGER DEFAULT 60,
            deadline_weight INTEGER DEFAULT 10,
            report_weight INTEGER DEFAULT 20,
            teacher_id INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, course_id)
        );
        CREATE TABLE IF NOT EXISTS lms_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration TEXT DEFAULT '2h',
            status TEXT DEFAULT 'upcoming',
            meet_url TEXT,
            replay_url TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            due_at TEXT,
            max_score INTEGER DEFAULT 20,
            status TEXT DEFAULT 'published',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            content TEXT,
            file_url TEXT,
            score REAL,
            feedback TEXT,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            graded_at TEXT,
            UNIQUE(assignment_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS lms_quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT 20,
            status TEXT DEFAULT 'published',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            option_a TEXT,
            option_b TEXT,
            option_c TEXT,
            option_d TEXT,
            correct_option TEXT,
            points INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS lms_quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            score REAL,
            max_score REAL,
            answers TEXT,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(quiz_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS lms_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            resource_type TEXT DEFAULT 'note',
            url TEXT,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            category TEXT DEFAULT 'Info',
            priority TEXT DEFAULT 'normal',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lms_qa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            answer TEXT,
            answered_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            answered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS lms_certificates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            issued_at TEXT,
            status TEXT DEFAULT 'draft',
            certificate_code TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS lms_email_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT DEFAULT 'queued',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT
        );
        """)
        seed_data(conn)


def seed_data(conn):
    teacher = create_user(conn, "Professeur IUA", "prof@iua.ci", "prof123", "teacher")
    student = create_user(conn, "Archille KOUAME", "archille.kouame@iua.ci", "demo123")

    classes = [
        ("Master 1 - Intelligence Artificielle", "M1-AI-2026", "Master 1", "2026"),
        ("Master 2 - Data & Innovation", "M2-DATA-2026", "Master 2", "2026"),
    ]
    for name, code, level, year in classes:
        conn.execute("""
        INSERT OR IGNORE INTO lms_classes (name, code, level, academic_year)
        VALUES (?, ?, ?, ?)
        """, (name, code, level, year))

    primary = conn.execute("SELECT * FROM lms_classes WHERE code='M1-AI-2026'").fetchone()
    secondary = conn.execute("SELECT * FROM lms_classes WHERE code='M2-DATA-2026'").fetchone()
    for class_row in [primary, secondary]:
        conn.execute(
            "INSERT OR IGNORE INTO lms_class_teachers (class_id, teacher_id) VALUES (?, ?)",
            (class_row["id"], teacher["id"]),
        )

    course_defs = [
        ("Master1-AI Course", LMS_COURSE_SLUG, primary["id"]),
        ("Data Strategy Course", "data-strategy-course", secondary["id"]),
    ]
    for title, slug, class_id in course_defs:
        conn.execute("""
        INSERT OR IGNORE INTO lms_courses (
            title, slug, class_id, semester, year, description, meet_url, teacher_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title, slug, class_id, "Semestre 2", "2026",
            "Cours en ligne avec planning, devoirs, quiz, ressources et certificats.",
            "https://meet.google.com/auv-cpbo-ceb", teacher["id"],
        ))

    course = conn.execute("SELECT * FROM lms_courses WHERE slug=?", (LMS_COURSE_SLUG,)).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO lms_enrollments (user_id, course_id) VALUES (?, ?)",
        (student["id"], course["id"]),
    )

    if conn.execute("SELECT COUNT(*) total FROM lms_sessions").fetchone()["total"] == 0:
        dates = ["2026-06-06", "2026-06-13", "2026-06-27", "2026-07-04", "2026-07-11"]
        for index, date in enumerate(dates, 1):
            conn.execute("""
            INSERT INTO lms_sessions (course_id, title, session_date, start_time, end_time, status, meet_url)
            VALUES (?, ?, ?, '08:00', '09:30', ?, ?)
            """, (course["id"], f"Seance {index} - {course['title']}", date, "completed" if index <= 2 else "upcoming", course["meet_url"]))

    if conn.execute("SELECT COUNT(*) total FROM lms_assignments").fetchone()["total"] == 0:
        for title, due in [("2026/06/06 Resume du cours", "2026-06-26 12:55"), ("2026/06/13 Resume du cours", "2026-07-03 12:58")]:
            conn.execute(
                "INSERT INTO lms_assignments (course_id, title, description, due_at) VALUES (?, ?, ?, ?)",
                (course["id"], title, "Veuillez rediger et soumettre votre travail.", due),
            )
        for assignment in conn.execute("SELECT id FROM lms_assignments").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO lms_submissions (assignment_id, student_id, content) VALUES (?, ?, ?)",
                (assignment["id"], student["id"], "Soumission de demonstration."),
            )

    if conn.execute("SELECT COUNT(*) total FROM lms_quizzes").fetchone()["total"] == 0:
        for title in ["2026/06/13 Histoire de la recherche en IA", "2026/06/06 Definition de l'IA"]:
            cur = conn.execute("INSERT INTO lms_quizzes (course_id, title) VALUES (?, ?)", (course["id"], title))
            quiz_id = cur.lastrowid
            conn.execute("""
            INSERT INTO lms_quiz_questions (quiz_id, question, option_a, option_b, option_c, option_d, correct_option, points)
            VALUES (?, ?, ?, ?, ?, ?, 'A', 10)
            """, (quiz_id, "Quel est l'objectif du module ?", "Comprendre l'IA", "Ignorer les donnees", "Remplacer les cours", "Eviter les exercices"))
            conn.execute(
                "INSERT OR IGNORE INTO lms_quiz_attempts (quiz_id, student_id, score, max_score, answers) VALUES (?, ?, 10, 10, 'A')",
                (quiz_id, student["id"]),
            )

    conn.execute("""
    INSERT OR IGNORE INTO lms_resources (id, course_id, title, resource_type, description)
    VALUES (1, ?, 'G_kentei_1_Francais.pdf', 'texte', 'Support de lecture.')
    """, (course["id"],))
    conn.execute("""
    INSERT OR IGNORE INTO lms_announcements (id, course_id, title, body, category)
    VALUES (1, ?, 'Bienvenue sur le LMS', 'Les cours et devoirs seront publies ici.', 'Info')
    """, (course["id"],))


def current_language():
    if session.get("lms_lang") in LMS_TRANSLATIONS:
        return session["lms_lang"]
    return "fr"


def t(key):
    return LMS_TRANSLATIONS[current_language()].get(key, key)


@app.context_processor
def inject_context():
    return {"tr": LMS_TRANSLATIONS[current_language()], "lms_lang": current_language()}


def current_user():
    user_id = session.get("lms_user_id")
    if not user_id:
        return None
    with get_db() as conn:
        user = conn.execute("SELECT * FROM lms_users WHERE id=?", (user_id,)).fetchone()
    if user and user["status"] != "active":
        session.clear()
        return None
    return user


def require_login(role=None):
    user = current_user()
    if user is None:
        return None, redirect(url_for("lms_login"))
    roles = {role} if isinstance(role, str) else set(role or [])
    if roles and user["role"] not in roles:
        flash("Acces reserve.")
        return None, redirect(url_for("lms_dashboard"))
    return user, None


def teacher_classes(conn, user):
    if user["role"] == "admin":
        return conn.execute("SELECT * FROM lms_classes WHERE status='active' ORDER BY name").fetchall()
    if user["role"] == "teacher":
        return conn.execute("""
        SELECT c.* FROM lms_classes c
        JOIN lms_class_teachers ct ON ct.class_id=c.id
        WHERE ct.teacher_id=? AND c.status='active'
        ORDER BY c.name
        """, (user["id"],)).fetchall()
    return []


def selected_class_id(conn, user):
    classes = teacher_classes(conn, user)
    if not classes:
        return None
    selected = session.get("lms_class_id")
    allowed = {row["id"] for row in classes}
    if selected in allowed:
        return selected
    session["lms_class_id"] = classes[0]["id"]
    return classes[0]["id"]


def load_course(conn, user):
    if user["role"] in {"teacher", "admin"}:
        class_id = selected_class_id(conn, user)
        course = conn.execute("SELECT * FROM lms_courses WHERE class_id=? ORDER BY id LIMIT 1", (class_id,)).fetchone()
    else:
        course = conn.execute("""
        SELECT c.* FROM lms_courses c
        JOIN lms_enrollments e ON e.course_id=c.id
        WHERE e.user_id=? AND e.status='active'
        ORDER BY c.id LIMIT 1
        """, (user["id"],)).fetchone()
    return course or conn.execute("SELECT * FROM lms_courses ORDER BY id LIMIT 1").fetchone()


def nav_for(user):
    if user["role"] in {"teacher", "admin"}:
        return [
            ("lms_teacher_dashboard", t("teacher_dashboard"), "teacher"),
            ("lms_teacher_students", t("students"), "students"),
            ("lms_teacher_content", t("content"), "content"),
            ("lms_teacher_grades", t("grades"), "grades"),
            ("lms_dashboard", t("student_view"), "dashboard"),
        ]
    return [
        ("lms_dashboard", t("dashboard"), "dashboard"),
        ("lms_planning", t("planning"), "planning"),
        ("lms_devoirs", t("assignments"), "devoirs"),
        ("lms_quiz", t("quiz"), "quiz"),
        ("lms_notes", t("notes"), "notes"),
        ("lms_textes", t("texts"), "textes"),
        ("lms_qa", t("qa"), "qa"),
        ("lms_annonces", t("announcements"), "annonces"),
        ("lms_certificats", t("certificates"), "certificats"),
        ("lms_parametres", t("settings"), "parametres"),
    ]


def stats(conn, user, course):
    total = conn.execute("SELECT COUNT(*) total FROM lms_assignments WHERE course_id=?", (course["id"],)).fetchone()["total"]
    submitted = conn.execute("""
    SELECT COUNT(*) total FROM lms_assignments a
    JOIN lms_submissions s ON s.assignment_id=a.id AND s.student_id=?
    WHERE a.course_id=?
    """, (user["id"], course["id"])).fetchone()["total"]
    avg = conn.execute("""
    SELECT AVG(score / NULLIF(max_score, 0) * 100.0) average
    FROM lms_quiz_attempts WHERE student_id=?
    """, (user["id"],)).fetchone()["average"]
    return {
        "pending_assignments": max(total - submitted, 0),
        "attendance_rate": 0,
        "quiz_average": int(round(avg or 0)),
        "score_total": f"{submitted * 10}/100",
    }


def render_lms(page, **extra):
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        teacher_classes_rows = teacher_classes(conn, user)
        context = {
            "mode": "app", "page": page, "user": user, "course": course,
            "nav_items": nav_for(user), "stats": stats(conn, user, course),
            "teacher_classes": teacher_classes_rows,
            "selected_class_id": session.get("lms_class_id"),
        }
    context.update(extra)
    return render_template("lms.html", **context)


def course_id_from_request():
    value = request.form.get("course_id") or request.args.get("course_id")
    if value:
        return int(value)
    user = current_user()
    with get_db() as conn:
        return load_course(conn, user)["id"]


@app.route("/")
def index():
    return redirect(url_for("lms_login"))


@app.route("/lms/login", methods=["GET", "POST"])
def lms_login():
    if request.method == "POST":
        with get_db() as conn:
            user = user_by_email(conn, request.form.get("email", ""))
        if user and user["status"] == "active" and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session["lms_user_id"] = user["id"]
            session["lms_role"] = user["role"]
            session["lms_lang"] = user["language"]
            if user["role"] in {"teacher", "admin"}:
                return redirect(url_for("lms_teacher_dashboard"))
            return redirect(url_for("lms_dashboard"))
        flash("Email ou mot de passe incorrect.")
    return render_template("lms.html", mode="login")


@app.route("/lms/inscription", methods=["GET", "POST"])
def lms_register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not full_name or not email or password != confirm:
            flash("Informations invalides.")
        else:
            with get_db() as conn:
                if user_by_email(conn, email):
                    flash("Un compte existe deja.")
                else:
                    user = create_user(conn, full_name, email, password)
                    course = conn.execute("SELECT id FROM lms_courses ORDER BY id LIMIT 1").fetchone()
                    conn.execute("INSERT OR IGNORE INTO lms_enrollments (user_id, course_id) VALUES (?, ?)", (user["id"], course["id"]))
                    session["lms_user_id"] = user["id"]
                    session["lms_role"] = user["role"]
                    return redirect(url_for("lms_dashboard"))
    return render_template("lms.html", mode="register")


@app.route("/lms/activation/<token>", methods=["GET", "POST"])
def lms_activate_account(token):
    with get_db() as conn:
        invited = conn.execute("SELECT * FROM lms_users WHERE reset_token=?", (token,)).fetchone()
        if invited is None:
            abort(404)
        if request.method == "POST":
            password = request.form.get("password", "")
            if password and password == request.form.get("confirm", ""):
                conn.execute("""
                UPDATE lms_users
                SET password_hash=?, status='active', reset_token=NULL, must_reset_password=0
                WHERE id=?
                """, (generate_password_hash(password), invited["id"]))
                session["lms_user_id"] = invited["id"]
                session["lms_role"] = invited["role"]
                return redirect(url_for("lms_dashboard"))
            flash("Les mots de passe ne correspondent pas.")
    return render_template("lms.html", mode="activate", invited=invited)


@app.route("/lms/logout", methods=["POST"])
def lms_logout():
    session.clear()
    return redirect(url_for("lms_login"))


@app.route("/lms/langue/<lang>")
def lms_switch_language(lang):
    if lang not in LMS_TRANSLATIONS:
        abort(404)
    session["lms_lang"] = lang
    user = current_user()
    if user:
        with get_db() as conn:
            conn.execute("UPDATE lms_users SET language=? WHERE id=?", (lang, user["id"]))
    return redirect(request.referrer or url_for("lms_dashboard"))


@app.route("/lms")
@app.route("/lms/dashboard")
def lms_dashboard():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        next_session = conn.execute("""
        SELECT * FROM lms_sessions WHERE course_id=? AND status='upcoming'
        ORDER BY session_date, start_time LIMIT 1
        """, (course["id"],)).fetchone()
        assignments = conn.execute("""
        SELECT a.*, s.submitted_at, s.score FROM lms_assignments a
        LEFT JOIN lms_submissions s ON s.assignment_id=a.id AND s.student_id=?
        WHERE a.course_id=? ORDER BY a.due_at DESC LIMIT 3
        """, (user["id"], course["id"])).fetchall()
        teacher_classes_rows = teacher_classes(conn, user)
        return render_template("lms.html", mode="app", page="dashboard", user=user, course=course,
                               nav_items=nav_for(user), stats=stats(conn, user, course), next_session=next_session,
                               assignments=assignments, teacher_classes=teacher_classes_rows,
                               selected_class_id=session.get("lms_class_id"))


@app.route("/lms/planning")
def lms_planning():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        sessions = conn.execute("SELECT * FROM lms_sessions WHERE course_id=? ORDER BY session_date", (course["id"],)).fetchall()
    return render_lms("planning", sessions=sessions)


@app.route("/lms/devoirs")
def lms_devoirs():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        assignments = conn.execute("""
        SELECT a.*, s.content, s.file_url, s.score, s.feedback, s.submitted_at
        FROM lms_assignments a
        LEFT JOIN lms_submissions s ON s.assignment_id=a.id AND s.student_id=?
        WHERE a.course_id=? ORDER BY a.due_at
        """, (user["id"], course["id"])).fetchall()
    return render_lms("devoirs", assignments=assignments)


@app.route("/lms/devoirs/<int:assignment_id>/soumettre", methods=["POST"])
def lms_submit_assignment(assignment_id):
    user, redirect_response = require_login("student")
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("""
        INSERT INTO lms_submissions (assignment_id, student_id, content, file_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(assignment_id, student_id) DO UPDATE SET
            content=excluded.content, file_url=excluded.file_url, submitted_at=CURRENT_TIMESTAMP
        """, (assignment_id, user["id"], request.form.get("content", ""), request.form.get("file_url", "")))
    flash("Devoir soumis.")
    return redirect(url_for("lms_devoirs"))


@app.route("/lms/quiz")
def lms_quiz():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        quizzes = conn.execute("""
        SELECT q.*, COUNT(qq.id) question_count, a.score, a.max_score, a.submitted_at
        FROM lms_quizzes q
        LEFT JOIN lms_quiz_questions qq ON qq.quiz_id=q.id
        LEFT JOIN lms_quiz_attempts a ON a.quiz_id=q.id AND a.student_id=?
        WHERE q.course_id=? GROUP BY q.id ORDER BY q.created_at DESC
        """, (user["id"], course["id"])).fetchall()
    return render_lms("quiz", quizzes=quizzes)


@app.route("/lms/quiz/<int:quiz_id>", methods=["GET", "POST"])
def lms_take_quiz(quiz_id):
    user, redirect_response = require_login("student")
    if redirect_response:
        return redirect_response

    with get_db() as conn:
        quiz = conn.execute("SELECT * FROM lms_quizzes WHERE id=?", (quiz_id,)).fetchone()
        if quiz is None:
            abort(404)

        questions = conn.execute("""
        SELECT *
        FROM lms_quiz_questions
        WHERE quiz_id=?
        ORDER BY id
        """, (quiz_id,)).fetchall()

        if request.method == "POST":
            score = 0
            max_score = 0
            answers = {}

            for question in questions:
                key = f"question_{question['id']}"
                answer = request.form.get(key, "").strip().upper()
                answers[str(question["id"])] = answer
                points = int(question["points"] or 1)
                max_score += points
                if answer and answer == str(question["correct_option"] or "").upper():
                    score += points

            conn.execute("""
            INSERT INTO lms_quiz_attempts (quiz_id, student_id, score, max_score, answers, submitted_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(quiz_id, student_id) DO UPDATE SET
                score=excluded.score,
                max_score=excluded.max_score,
                answers=excluded.answers,
                submitted_at=CURRENT_TIMESTAMP
            """, (quiz_id, user["id"], score, max_score, json.dumps(answers)))
            flash("Quiz soumis.")
            return redirect(url_for("lms_quiz_result", quiz_id=quiz_id))

    return render_lms("quiz_take", quiz=quiz, questions=questions)


@app.route("/lms/quiz/<int:quiz_id>/resultat")
def lms_quiz_result(quiz_id):
    user, redirect_response = require_login("student")
    if redirect_response:
        return redirect_response

    with get_db() as conn:
        quiz = conn.execute("SELECT * FROM lms_quizzes WHERE id=?", (quiz_id,)).fetchone()
        if quiz is None:
            abort(404)
        questions = conn.execute("""
        SELECT *
        FROM lms_quiz_questions
        WHERE quiz_id=?
        ORDER BY id
        """, (quiz_id,)).fetchall()
        attempt = conn.execute("""
        SELECT *
        FROM lms_quiz_attempts
        WHERE quiz_id=? AND student_id=?
        """, (quiz_id, user["id"])).fetchone()

    answers = json.loads(attempt["answers"] or "{}") if attempt else {}
    return render_lms("quiz_result", quiz=quiz, questions=questions, attempt=attempt, answers=answers)


def resources_page(page, resource_type):
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        resources = conn.execute("""
        SELECT * FROM lms_resources WHERE course_id=? AND resource_type=? ORDER BY created_at DESC
        """, (course["id"], resource_type)).fetchall()
    return render_lms(page, resources=resources)


@app.route("/lms/notes")
def lms_notes():
    return resources_page("notes", "note")


@app.route("/lms/textes")
def lms_textes():
    return resources_page("textes", "texte")


@app.route("/lms/qa", methods=["GET", "POST"])
def lms_qa():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        if request.method == "POST" and request.form.get("title"):
            conn.execute("INSERT INTO lms_qa (course_id, author_id, title, body) VALUES (?, ?, ?, ?)",
                         (course["id"], user["id"], request.form["title"], request.form.get("body", "")))
            return redirect(url_for("lms_qa"))
        questions = conn.execute("""
        SELECT q.*, u.full_name author_name FROM lms_qa q
        JOIN lms_users u ON u.id=q.author_id
        WHERE q.course_id=? ORDER BY q.created_at DESC
        """, (course["id"],)).fetchall()
    return render_lms("qa", questions=questions)


@app.route("/lms/annonces")
def lms_annonces():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        announcements = conn.execute("SELECT * FROM lms_announcements WHERE course_id=? ORDER BY created_at DESC", (course["id"],)).fetchall()
    return render_lms("annonces", announcements=announcements)


@app.route("/lms/certificats")
def lms_certificats():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        certificates = conn.execute("SELECT * FROM lms_certificates WHERE course_id=? AND student_id=?", (course["id"], user["id"])).fetchall()
    return render_lms("certificats", certificates=certificates)


@app.route("/lms/parametres", methods=["GET", "POST"])
def lms_parametres():
    user, redirect_response = require_login()
    if redirect_response:
        return redirect_response
    if request.method == "POST":
        language = request.form.get("language", "fr")
        with get_db() as conn:
            conn.execute("UPDATE lms_users SET full_name=?, language=? WHERE id=?", (request.form.get("full_name", user["full_name"]), language, user["id"]))
            if request.form.get("password") and request.form.get("password") == request.form.get("confirm"):
                conn.execute("UPDATE lms_users SET password_hash=? WHERE id=?", (generate_password_hash(request.form["password"]), user["id"]))
        session["lms_lang"] = language
        return redirect(url_for("lms_parametres"))
    return render_lms("parametres")


@app.route("/lms/prof")
def lms_teacher_dashboard():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        counts = {
            "students": conn.execute("SELECT COUNT(*) total FROM lms_enrollments WHERE course_id=?", (course["id"],)).fetchone()["total"],
            "sessions": conn.execute("SELECT COUNT(*) total FROM lms_sessions WHERE course_id=?", (course["id"],)).fetchone()["total"],
            "assignments": conn.execute("SELECT COUNT(*) total FROM lms_assignments WHERE course_id=?", (course["id"],)).fetchone()["total"],
            "submissions": conn.execute("SELECT COUNT(*) total FROM lms_submissions").fetchone()["total"],
        }
        recent_questions = conn.execute("""
        SELECT q.*, u.full_name author_name FROM lms_qa q
        JOIN lms_users u ON u.id=q.author_id
        WHERE q.course_id=? ORDER BY q.created_at DESC LIMIT 5
        """, (course["id"],)).fetchall()
    return render_lms("teacher", counts=counts, recent_questions=recent_questions)


@app.route("/lms/prof/classes/selectionner", methods=["POST"])
def lms_teacher_select_class():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        allowed = {row["id"] for row in teacher_classes(conn, user)}
    class_id = int(request.form.get("class_id"))
    if class_id in allowed:
        session["lms_class_id"] = class_id
    return redirect(request.referrer or url_for("lms_teacher_dashboard"))


@app.route("/lms/prof/classes", methods=["POST"])
def lms_teacher_create_class():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip().upper()
    if not name or not code:
        flash("Nom et code requis.")
        return redirect(url_for("lms_teacher_students"))
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO lms_classes (name, code, level, academic_year, description) VALUES (?, ?, ?, ?, ?)",
                     (name, code, request.form.get("level", ""), request.form.get("academic_year", "2026"), request.form.get("description", "")))
        class_row = conn.execute("SELECT * FROM lms_classes WHERE code=?", (code,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO lms_class_teachers (class_id, teacher_id) VALUES (?, ?)", (class_row["id"], user["id"]))
        slug = re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")
        conn.execute("""
        INSERT OR IGNORE INTO lms_courses (title, slug, class_id, semester, year, description, meet_url, teacher_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("course_title") or name, slug, class_row["id"], "Semestre 2", request.form.get("academic_year", "2026"), request.form.get("description", ""), request.form.get("meet_url", ""), user["id"]))
        session["lms_class_id"] = class_row["id"]
    return redirect(url_for("lms_teacher_students"))


@app.route("/lms/prof/etudiants")
def lms_teacher_students():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        students = conn.execute("""
        SELECT u.*, e.status enrollment_status, COUNT(s.id) submissions_count,
               AVG(qa.score / NULLIF(qa.max_score, 0) * 100.0) quiz_average
        FROM lms_enrollments e
        JOIN lms_users u ON u.id=e.user_id
        LEFT JOIN lms_submissions s ON s.student_id=u.id
        LEFT JOIN lms_quiz_attempts qa ON qa.student_id=u.id
        WHERE e.course_id=? AND u.role='student'
        GROUP BY u.id ORDER BY u.full_name
        """, (course["id"],)).fetchall()
        outbox = conn.execute("SELECT * FROM lms_email_outbox ORDER BY created_at DESC LIMIT 5").fetchall()
    return render_lms("students", students=students, outbox=outbox)


@app.route("/lms/prof/etudiants/inviter", methods=["POST"])
def lms_teacher_invite_student():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        email = request.form.get("email", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        student = user_by_email(conn, email)
        if student is None:
            token = secrets.token_urlsafe(32)
            conn.execute("""
            INSERT INTO lms_users (full_name, email, password_hash, role, status, reset_token, must_reset_password)
            VALUES (?, ?, ?, 'student', 'invited', ?, 1)
            """, (full_name, email, generate_password_hash(secrets.token_urlsafe(16)), token))
            student = user_by_email(conn, email)
        else:
            token = create_reset_token(conn, student["id"])
        conn.execute("INSERT OR IGNORE INTO lms_enrollments (user_id, course_id) VALUES (?, ?)", (student["id"], course_id_from_request()))
        activation_url = url_for("lms_activate_account", token=token, _external=True)
        queue_email(conn, email, "Invitation IUA LMS", f"Bonjour {full_name}, activez votre compte ici: {activation_url}")
    return redirect(url_for("lms_teacher_students"))


@app.route("/lms/prof/etudiants/<int:student_id>/statut", methods=["POST"])
def lms_teacher_update_student_status(student_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    status = request.form.get("status", "active")
    if status not in {"active", "disabled", "invited"}:
        abort(400)
    with get_db() as conn:
        conn.execute("UPDATE lms_users SET status=? WHERE id=? AND role='student'", (status, student_id))
    return redirect(url_for("lms_teacher_students"))


@app.route("/lms/prof/etudiants/<int:student_id>/reset", methods=["POST"])
def lms_teacher_reset_student_password(student_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        student = conn.execute("SELECT * FROM lms_users WHERE id=? AND role='student'", (student_id,)).fetchone()
        if student is None:
            abort(404)
        token = create_reset_token(conn, student_id)
        queue_email(conn, student["email"], "Reinitialisation IUA LMS", url_for("lms_activate_account", token=token, _external=True))
    return redirect(url_for("lms_teacher_students"))


@app.route("/lms/prof/contenus")
def lms_teacher_content():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        data = {
            "sessions": conn.execute("SELECT * FROM lms_sessions WHERE course_id=? ORDER BY session_date", (course["id"],)).fetchall(),
            "assignments": conn.execute("SELECT * FROM lms_assignments WHERE course_id=? ORDER BY due_at", (course["id"],)).fetchall(),
            "quizzes": conn.execute("SELECT * FROM lms_quizzes WHERE course_id=? ORDER BY created_at DESC", (course["id"],)).fetchall(),
            "resources": conn.execute("SELECT * FROM lms_resources WHERE course_id=? ORDER BY created_at DESC", (course["id"],)).fetchall(),
            "announcements": conn.execute("SELECT * FROM lms_announcements WHERE course_id=? ORDER BY created_at DESC", (course["id"],)).fetchall(),
            "questions": conn.execute("SELECT q.*, u.full_name author_name FROM lms_qa q JOIN lms_users u ON u.id=q.author_id WHERE q.course_id=? ORDER BY q.created_at DESC", (course["id"],)).fetchall(),
        }
    return render_lms("content", data=data)


@app.route("/lms/prof/seances", methods=["POST"])
def lms_teacher_add_session():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("""
        INSERT INTO lms_sessions (course_id, title, session_date, start_time, end_time, duration, status, meet_url, replay_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (course_id_from_request(), request.form.get("title", "Nouvelle seance"), request.form.get("session_date", ""), request.form.get("start_time", "08:00"), request.form.get("end_time", "09:30"), request.form.get("duration", "2h"), request.form.get("status", "upcoming"), request.form.get("meet_url", ""), request.form.get("replay_url", "")))
    return redirect(url_for("lms_teacher_content"))


@app.route("/lms/prof/devoirs", methods=["POST"])
def lms_teacher_add_assignment():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("INSERT INTO lms_assignments (course_id, title, description, due_at, max_score) VALUES (?, ?, ?, ?, ?)",
                     (course_id_from_request(), request.form.get("title", ""), request.form.get("description", ""), request.form.get("due_at", ""), request.form.get("max_score", 20)))
    return redirect(url_for("lms_teacher_content"))


@app.route("/lms/prof/quiz", methods=["POST"])
def lms_teacher_add_quiz():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        cur = conn.execute("INSERT INTO lms_quizzes (course_id, title, duration_minutes) VALUES (?, ?, ?)",
                           (course_id_from_request(), request.form.get("title", ""), request.form.get("duration_minutes", 20)))
        if request.form.get("question"):
            conn.execute("""
            INSERT INTO lms_quiz_questions (quiz_id, question, option_a, option_b, option_c, option_d, correct_option, points)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (cur.lastrowid, request.form.get("question", ""), request.form.get("option_a", ""), request.form.get("option_b", ""), request.form.get("option_c", ""), request.form.get("option_d", ""), request.form.get("correct_option", "A"), request.form.get("points", 1)))
    return redirect(url_for("lms_teacher_edit_quiz", quiz_id=cur.lastrowid))


@app.route("/lms/prof/quiz/<int:quiz_id>")
def lms_teacher_edit_quiz(quiz_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response

    with get_db() as conn:
        quiz = conn.execute("SELECT * FROM lms_quizzes WHERE id=?", (quiz_id,)).fetchone()
        if quiz is None:
            abort(404)
        questions = conn.execute("""
        SELECT *
        FROM lms_quiz_questions
        WHERE quiz_id=?
        ORDER BY id
        """, (quiz_id,)).fetchall()
        attempts = conn.execute("""
        SELECT a.*, u.full_name AS student_name
        FROM lms_quiz_attempts a
        JOIN lms_users u ON u.id=a.student_id
        WHERE a.quiz_id=?
        ORDER BY a.submitted_at DESC
        """, (quiz_id,)).fetchall()

    return render_lms("quiz_edit", quiz=quiz, questions=questions, attempts=attempts)


@app.route("/lms/prof/quiz/<int:quiz_id>/questions", methods=["POST"])
def lms_teacher_add_quiz_question(quiz_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response

    with get_db() as conn:
        if conn.execute("SELECT id FROM lms_quizzes WHERE id=?", (quiz_id,)).fetchone() is None:
            abort(404)
        conn.execute("""
        INSERT INTO lms_quiz_questions (
            quiz_id, question, option_a, option_b, option_c, option_d, correct_option, points
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            quiz_id,
            request.form.get("question", "").strip(),
            request.form.get("option_a", "").strip(),
            request.form.get("option_b", "").strip(),
            request.form.get("option_c", "").strip(),
            request.form.get("option_d", "").strip(),
            request.form.get("correct_option", "A").strip().upper(),
            request.form.get("points", "1").strip() or 1,
        ))
    flash("Question ajoutee.")
    return redirect(url_for("lms_teacher_edit_quiz", quiz_id=quiz_id))


@app.route("/lms/prof/ressources", methods=["POST"])
def lms_teacher_add_resource():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("INSERT INTO lms_resources (course_id, title, resource_type, url, description) VALUES (?, ?, ?, ?, ?)",
                     (course_id_from_request(), request.form.get("title", ""), request.form.get("resource_type", "note"), request.form.get("url", ""), request.form.get("description", "")))
    return redirect(url_for("lms_teacher_content"))


@app.route("/lms/prof/annonces", methods=["POST"])
def lms_teacher_add_announcement():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("INSERT INTO lms_announcements (course_id, title, body, category, priority) VALUES (?, ?, ?, ?, ?)",
                     (course_id_from_request(), request.form.get("title", ""), request.form.get("body", ""), request.form.get("category", "Info"), request.form.get("priority", "normal")))
    return redirect(url_for("lms_teacher_content"))


@app.route("/lms/prof/questions/<int:question_id>/repondre", methods=["POST"])
def lms_teacher_answer_question(question_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("UPDATE lms_qa SET answer=?, answered_by=?, answered_at=CURRENT_TIMESTAMP WHERE id=?",
                     (request.form.get("answer", ""), user["id"], question_id))
    return redirect(url_for("lms_teacher_content"))


@app.route("/lms/prof/notes")
def lms_teacher_grades():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        course = load_course(conn, user)
        submissions = conn.execute("""
        SELECT s.*, a.title assignment_title, a.max_score, u.full_name student_name
        FROM lms_submissions s
        JOIN lms_assignments a ON a.id=s.assignment_id
        JOIN lms_users u ON u.id=s.student_id
        WHERE a.course_id=? ORDER BY s.submitted_at DESC
        """, (course["id"],)).fetchall()
        students = conn.execute("""
        SELECT u.* FROM lms_enrollments e JOIN lms_users u ON u.id=e.user_id
        WHERE e.course_id=? AND u.role='student' ORDER BY u.full_name
        """, (course["id"],)).fetchall()
        certificates = conn.execute("""
        SELECT c.*, u.full_name student_name FROM lms_certificates c
        JOIN lms_users u ON u.id=c.student_id WHERE c.course_id=?
        """, (course["id"],)).fetchall()
    return render_lms("grades", submissions=submissions, students=students, certificates=certificates)


@app.route("/lms/prof/soumissions/<int:submission_id>/noter", methods=["POST"])
def lms_teacher_grade_submission(submission_id):
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    with get_db() as conn:
        conn.execute("UPDATE lms_submissions SET score=?, feedback=?, graded_at=CURRENT_TIMESTAMP WHERE id=?",
                     (request.form.get("score", ""), request.form.get("feedback", ""), submission_id))
    return redirect(url_for("lms_teacher_grades"))


@app.route("/lms/prof/certificats", methods=["POST"])
def lms_teacher_issue_certificate():
    user, redirect_response = require_login({"teacher", "admin"})
    if redirect_response:
        return redirect_response
    course_id = course_id_from_request()
    student_id = request.form.get("student_id")
    code = f"IUA-{course_id}-{student_id}"
    with get_db() as conn:
        conn.execute("""
        INSERT INTO lms_certificates (course_id, student_id, title, issued_at, status, certificate_code)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'issued', ?)
        ON CONFLICT(certificate_code) DO UPDATE SET title=excluded.title, issued_at=CURRENT_TIMESTAMP, status='issued'
        """, (course_id, student_id, request.form.get("title", "Certificat de participation"), code))
    return redirect(url_for("lms_teacher_grades"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)
