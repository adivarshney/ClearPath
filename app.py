import calendar
import csv
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from io import StringIO
from pathlib import Path

from openpyxl import load_workbook
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_DIR / "clearpath.db"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
APPROVAL_TYPES = ["EC", "CTE", "CTO"]
FREQUENCY_TYPES = ["General", "One-time", "Monthly", "Quarterly"]
STATUS_FILTERS = ["All", "Pending", "Due in 7 days", "Overdue", "Completed"]
CALENDAR_VIEW_OPTIONS = [1, 2, 3]
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_IMPORT_EXTENSIONS = {".csv", ".xlsx"}
IMPORT_HEADER_ALIASES = {
    "s. no": "serial_number",
    "s no": "serial_number",
    "serial no": "serial_number",
    "serial number": "serial_number",
    "condition description": "condition_description",
    "condition": "condition_description",
    "description": "condition_description",
    "action to be taken": "action_to_be_taken",
    "action": "action_to_be_taken",
    "due date": "due_date",
    "status": "status",
    "frequency": "frequency",
    "type": "frequency",
}


app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "clearpath-dev-secret")
app.config["DATABASE"] = str(DATABASE_PATH)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    INSTANCE_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(app.config["DATABASE"])
    db.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            client_name TEXT NOT NULL,
            location TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS project_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            approval_type TEXT NOT NULL,
            issue_date TEXT NOT NULL DEFAULT '',
            expiry_date TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS compliance_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            condition_description TEXT NOT NULL,
            action_to_be_taken TEXT NOT NULL DEFAULT '',
            due_date TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL DEFAULT 'General',
            status TEXT NOT NULL CHECK (status IN ('Pending', 'Completed')) DEFAULT 'Pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            compliance_item_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (compliance_item_id) REFERENCES compliance_items(id) ON DELETE CASCADE
        );
        """
    )
    ensure_column(db, "project_approvals", "issue_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "project_approvals", "expiry_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "compliance_items", "frequency", "TEXT NOT NULL DEFAULT 'General'")
    db.close()


def ensure_column(db, table_name, column_name, definition):
    existing_columns = {row[1] for row in db.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in existing_columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None
    g.notification_items = []
    g.notification_count = 0
    if user_id is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if g.user is not None:
            g.notification_items = fetch_notification_items_for_user(g.user["id"])
            g.notification_count = count_upcoming_items_for_user(g.user["id"])


def get_owned_project(project_id):
    project = get_db().execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, g.user["id"])
    ).fetchone()
    if project is None:
        abort(404)
    return project


def get_owned_compliance_item(item_id):
    item = get_db().execute(
        """
        SELECT ci.*, p.user_id
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE ci.id = ? AND p.user_id = ?
        """,
        (item_id, g.user["id"]),
    ).fetchone()
    if item is None:
        abort(404)
    return item


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def allowed_import_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_IMPORT_EXTENSIONS


def normalize_header(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def clean_text(value):
    return str(value or "").replace("\xa0", " ").strip()


def normalize_status(value):
    cleaned = (value or "").strip().lower()
    if cleaned == "completed":
        return "Completed"
    return "Pending"


def normalize_due_date(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return ""

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def normalize_frequency(value, due_date=""):
    cleaned = normalize_header(value)
    frequency_map = {
        "general": "General",
        "ongoing": "General",
        "one-time": "One-time",
        "one time": "One-time",
        "onetime": "One-time",
        "monthly": "Monthly",
        "quarterly": "Quarterly",
    }
    if cleaned in frequency_map:
        return frequency_map[cleaned]
    return "One-time" if due_date else "General"


def looks_like_section_header(text):
    cleaned = clean_text(text)
    if not cleaned:
        return False
    uppercase_ratio = sum(1 for char in cleaned if char.isupper()) / max(
        1, sum(1 for char in cleaned if char.isalpha())
    )
    return cleaned.startswith(("PART ", "SECTION ", "CHAPTER ")) or cleaned.endswith(":") or uppercase_ratio > 0.75


def parse_import_matrix(rows):
    if not rows:
        return []

    first_row = [clean_text(cell) for cell in rows[0]]
    mapped_headers = [IMPORT_HEADER_ALIASES.get(normalize_header(cell)) for cell in first_row]
    is_structured = any(mapped_headers)
    data_rows = rows[1:] if is_structured else rows
    normalized_rows = []

    for row in data_rows:
        row = tuple(row)
        if is_structured:
            mapped = {}
            for index, mapped_key in enumerate(mapped_headers):
                if mapped_key:
                    mapped[mapped_key] = row[index] if index < len(row) else None

            condition_description = clean_text(mapped.get("condition_description"))
            action_to_be_taken = clean_text(mapped.get("action_to_be_taken"))
            due_date = normalize_due_date(mapped.get("due_date"))
            status_raw = clean_text(mapped.get("status"))
            frequency_raw = clean_text(mapped.get("frequency"))
            serial_number = clean_text(mapped.get("serial_number"))

            if not condition_description:
                continue
            if (
                not serial_number
                and not action_to_be_taken
                and not due_date
                and not status_raw
                and not frequency_raw
                and looks_like_section_header(condition_description)
            ):
                continue
        else:
            non_empty_cells = [clean_text(cell) for cell in row if clean_text(cell)]
            if not non_empty_cells:
                continue
            if IMPORT_HEADER_ALIASES.get(normalize_header(non_empty_cells[0])):
                continue

            condition_description = non_empty_cells[0]
            action_to_be_taken = non_empty_cells[1] if len(non_empty_cells) > 1 else ""
            due_date = normalize_due_date(non_empty_cells[2]) if len(non_empty_cells) > 2 else ""
            status_raw = non_empty_cells[3] if len(non_empty_cells) > 3 else ""
            frequency_raw = non_empty_cells[4] if len(non_empty_cells) > 4 else ""

        normalized_rows.append(
            {
                "condition_description": condition_description,
                "action_to_be_taken": action_to_be_taken,
                "due_date": due_date,
                "status": normalize_status(status_raw),
                "frequency": normalize_frequency(frequency_raw, due_date),
            }
        )

    return normalized_rows


def parse_import_rows(file_storage):
    extension = Path(file_storage.filename).suffix.lower()

    if extension == ".csv":
        content = file_storage.stream.read().decode("utf-8-sig", errors="ignore")
        reader = csv.reader(StringIO(content))
        rows = list(reader)
    else:
        workbook = load_workbook(file_storage, read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            return []
        rows = values

    return parse_import_matrix(rows)


def build_sample_import_csv():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Condition Description", "Action To Be Taken", "Due Date", "Status", "Frequency"])
    writer.writerow(
        [
            "Submit half-yearly compliance report to the regional office.",
            "Prepare the report, review internally, and submit before the deadline.",
            "2026-07-15",
            "Pending",
            "One-time",
        ]
    )
    writer.writerow(
        [
            "Display the latest EC copy at the site office and entry gate.",
            "",
            "",
            "Pending",
            "General",
        ]
    )
    writer.writerow(
        [
            "Submit monthly wastewater monitoring results.",
            "Collect samples, review lab values, and share the report with the consultant.",
            "2026-05-05",
            "Pending",
            "Monthly",
        ]
    )
    return output.getvalue()


def build_approval_form_entries(selected_rows=None):
    selected_map = {}
    if selected_rows:
        for row in selected_rows:
            approval_type = row["approval_type"] if isinstance(row, sqlite3.Row) else row["approval_type"]
            selected_map[approval_type] = {
                "issue_date": row["issue_date"],
                "expiry_date": row["expiry_date"],
            }

    return [
        {
            "approval_type": approval_type,
            "selected": approval_type in selected_map,
            "issue_date": selected_map.get(approval_type, {}).get("issue_date", ""),
            "expiry_date": selected_map.get(approval_type, {}).get("expiry_date", ""),
        }
        for approval_type in APPROVAL_TYPES
    ]


def parse_approval_form(form):
    selected_entries = []
    form_entries = []
    for approval_type in APPROVAL_TYPES:
        selected = bool(form.get(f"approval_enabled_{approval_type}"))
        issue_date = form.get(f"issue_date_{approval_type}", "").strip()
        expiry_date = form.get(f"expiry_date_{approval_type}", "").strip()
        form_entries.append(
            {
                "approval_type": approval_type,
                "selected": selected,
                "issue_date": issue_date,
                "expiry_date": expiry_date,
            }
        )
        if selected:
            selected_entries.append(
                {
                    "approval_type": approval_type,
                    "issue_date": issue_date,
                    "expiry_date": expiry_date,
                }
            )
    return selected_entries, form_entries


def derive_item_status(status, due_date_value):
    if status == "Completed":
        return "Completed"
    if not due_date_value:
        return "Pending"

    today = date.today().isoformat()
    seven_days = (date.today() + timedelta(days=7)).isoformat()
    if due_date_value < today:
        return "Overdue"
    if due_date_value <= seven_days:
        return "Due in 7 days"
    return "Pending"


def annotate_compliance_items(rows):
    annotated = []
    for row in rows:
        item = dict(row)
        item["derived_status"] = derive_item_status(item["status"], item["due_date"])
        item["has_due_date"] = bool(item["due_date"])
        item["is_overdue"] = item["derived_status"] == "Overdue"
        item["is_due_soon"] = item["derived_status"] == "Due in 7 days"
        annotated.append(item)
    return annotated


def build_status_counts(items):
    counts = {filter_name: 0 for filter_name in STATUS_FILTERS}
    counts["All"] = len(items)
    for item in items:
        counts[item["derived_status"]] += 1
    return counts


def build_frequency_counts(items):
    counts = {"All": len(items)}
    for frequency in FREQUENCY_TYPES:
        counts[frequency] = sum(1 for item in items if item["frequency"] == frequency)
    return counts


def filter_compliance_items(items, status_filter, frequency_filter):
    filtered = items
    if status_filter != "All":
        filtered = [item for item in filtered if item["derived_status"] == status_filter]
    if frequency_filter != "All":
        filtered = [item for item in filtered if item["frequency"] == frequency_filter]
    return filtered


def annotate_approval_rows(rows):
    today = date.today().isoformat()
    warning_cutoff = (date.today() + timedelta(days=30)).isoformat()
    annotated = []
    for row in rows:
        approval = dict(row)
        approval["expiry_state"] = "No expiry set"
        if approval["expiry_date"]:
            if approval["expiry_date"] < today:
                approval["expiry_state"] = "Expired"
            elif approval["expiry_date"] <= warning_cutoff:
                approval["expiry_state"] = "Expiring soon"
            else:
                approval["expiry_state"] = "Active"
        annotated.append(approval)
    return annotated


def build_project_calendar(project_id, months=1):
    project = get_owned_project(project_id)
    db = get_db()
    due_items = db.execute(
        """
        SELECT id, condition_description, due_date, frequency
        FROM compliance_items
        WHERE project_id = ? AND due_date != ''
        ORDER BY due_date ASC
        """,
        (project_id,),
    ).fetchall()
    approvals = fetch_project_approvals(project_id)

    event_map = {}

    def add_event(event_date, label, kind, href):
        if not event_date:
            return
        event_map.setdefault(event_date, []).append(
            {"label": label, "kind": kind, "href": href}
        )

    for item in due_items:
        add_event(
            item["due_date"],
            f"{item['frequency']}: {item['condition_description']}",
            "due",
            url_for("project_detail", project_id=project["id"]),
        )

    for approval in approvals:
        add_event(
            approval["issue_date"],
            f"{approval['approval_type']} issue date",
            "issue",
            url_for("edit_project", project_id=project["id"]),
        )
        add_event(
            approval["expiry_date"],
            f"{approval['approval_type']} expiry date",
            "expiry",
            url_for("edit_project", project_id=project["id"]),
        )

    start = date.today().replace(day=1)
    current_year = start.year
    current_month = start.month
    months_data = []

    for _ in range(months):
        month_matrix = calendar.monthcalendar(current_year, current_month)
        days = []
        for week in month_matrix:
            for day_number in week:
                if day_number == 0:
                    days.append({"day": "", "date": "", "events": [], "is_today": False})
                    continue

                current_date = date(current_year, current_month, day_number)
                iso_date = current_date.isoformat()
                days.append(
                    {
                        "day": day_number,
                        "date": iso_date,
                        "events": event_map.get(iso_date, []),
                        "is_today": current_date == date.today(),
                    }
                )

        months_data.append(
            {
                "label": f"{calendar.month_name[current_month]} {current_year}",
                "days": days,
            }
        )

        if current_month == 12:
            current_month = 1
            current_year += 1
        else:
            current_month += 1

    return months_data


def fetch_project_approvals(project_id):
    return get_db().execute(
        """
        SELECT approval_type, issue_date, expiry_date
        FROM project_approvals
        WHERE project_id = ?
        ORDER BY approval_type
        """,
        (project_id,),
    ).fetchall()


def fetch_project_compliance_items(project_id):
    return get_db().execute(
        """
        SELECT ci.*,
               COUNT(d.id) AS document_count
        FROM compliance_items ci
        LEFT JOIN documents d ON d.compliance_item_id = ci.id
        WHERE ci.project_id = ?
        GROUP BY ci.id
        ORDER BY CASE WHEN ci.due_date = '' THEN 1 ELSE 0 END, ci.due_date ASC, ci.created_at DESC
        """,
        (project_id,),
    ).fetchall()


def fetch_project_documents(project_id):
    return get_db().execute(
        """
        SELECT d.*, ci.project_id
        FROM documents d
        JOIN compliance_items ci ON ci.id = d.compliance_item_id
        WHERE ci.project_id = ?
        ORDER BY d.uploaded_at DESC
        """,
        (project_id,),
    ).fetchall()


def build_documents_by_item(documents):
    docs_by_item = {}
    for document in documents:
        docs_by_item.setdefault(document["compliance_item_id"], []).append(document)
    return docs_by_item


def fetch_upcoming_items_for_user(user_id, days=7):
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return get_db().execute(
        """
        SELECT ci.id, ci.condition_description, ci.due_date, p.id AS project_id, p.name AS project_name
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
          AND ci.status = 'Pending'
          AND ci.due_date != ''
          AND ci.due_date BETWEEN ? AND ?
        ORDER BY ci.due_date ASC, p.name ASC
        LIMIT 8
        """,
        (user_id, today, cutoff),
    ).fetchall()


def fetch_notification_items_for_user(user_id, days=7, limit=6):
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return get_db().execute(
        """
        SELECT ci.id, ci.condition_description, ci.due_date, p.id AS project_id, p.name AS project_name
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
          AND ci.status = 'Pending'
          AND ci.due_date != ''
          AND ci.due_date BETWEEN ? AND ?
        ORDER BY ci.due_date ASC, p.name ASC
        LIMIT ?
        """,
        (user_id, today, cutoff, limit),
    ).fetchall()


def fetch_overdue_items_for_user(user_id):
    today = date.today().isoformat()
    return get_db().execute(
        """
        SELECT ci.id, ci.condition_description, ci.due_date, p.id AS project_id, p.name AS project_name
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
          AND ci.status = 'Pending'
          AND ci.due_date != ''
          AND ci.due_date < ?
        ORDER BY ci.due_date ASC, p.name ASC
        LIMIT 8
        """,
        (user_id, today),
    ).fetchall()


def count_upcoming_items_for_user(user_id, days=7):
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return get_db().execute(
        """
        SELECT COUNT(*) AS count
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
          AND ci.status = 'Pending'
          AND ci.due_date != ''
          AND ci.due_date BETWEEN ? AND ?
        """,
        (user_id, today, cutoff),
    ).fetchone()["count"]


def count_overdue_items_for_user(user_id):
    today = date.today().isoformat()
    return get_db().execute(
        """
        SELECT COUNT(*) AS count
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
          AND ci.status = 'Pending'
          AND ci.due_date != ''
          AND ci.due_date < ?
        """,
        (user_id, today),
    ).fetchone()["count"]


def fetch_project_reminders(project_id, days=7):
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    db = get_db()
    overdue_items = db.execute(
        """
        SELECT id, condition_description, due_date
        FROM compliance_items
        WHERE project_id = ?
          AND status = 'Pending'
          AND due_date != ''
          AND due_date < ?
        ORDER BY due_date ASC
        """,
        (project_id, today),
    ).fetchall()
    upcoming_items = db.execute(
        """
        SELECT id, condition_description, due_date
        FROM compliance_items
        WHERE project_id = ?
          AND status = 'Pending'
          AND due_date != ''
          AND due_date BETWEEN ? AND ?
        ORDER BY due_date ASC
        """,
        (project_id, today, cutoff),
    ).fetchall()
    return overdue_items, upcoming_items


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        error = None
        if not email:
            error = "Email is required."
        elif not password:
            error = "Password is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm_password:
            error = "Passwords do not match."

        db = get_db()
        if error is None and db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            error = "An account with this email already exists."

        if error is None:
            db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password, method="pbkdf2:sha256")),
            )
            db.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))

        flash(error, "error")

    return render_template("auth.html", mode="signup")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    project_count = db.execute(
        "SELECT COUNT(*) AS count FROM projects WHERE user_id = ?", (g.user["id"],)
    ).fetchone()["count"]
    status_counts = db.execute(
        """
        SELECT ci.status, COUNT(*) AS count
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
        GROUP BY ci.status
        """,
        (g.user["id"],),
    ).fetchall()
    counts = {row["status"]: row["count"] for row in status_counts}
    recent_projects = db.execute(
        """
        SELECT p.id, p.name, p.client_name, p.location,
               COUNT(ci.id) AS compliance_count
        FROM projects p
        LEFT JOIN compliance_items ci ON ci.project_id = p.id
        WHERE p.user_id = ?
        GROUP BY p.id
        ORDER BY p.created_at DESC
        LIMIT 5
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template(
        "dashboard.html",
        project_count=project_count,
        pending_count=counts.get("Pending", 0),
        completed_count=counts.get("Completed", 0),
        recent_projects=recent_projects,
        overdue_items=fetch_overdue_items_for_user(g.user["id"]),
        upcoming_items=fetch_upcoming_items_for_user(g.user["id"]),
        overdue_count=count_overdue_items_for_user(g.user["id"]),
        upcoming_count=count_upcoming_items_for_user(g.user["id"]),
    )


@app.route("/projects")
@login_required
def projects():
    rows = get_db().execute(
        """
        SELECT p.id, p.name, p.client_name, p.location,
               COUNT(ci.id) AS compliance_count,
               SUM(CASE WHEN ci.status = 'Pending' THEN 1 ELSE 0 END) AS pending_count
        FROM projects p
        LEFT JOIN compliance_items ci ON ci.project_id = p.id
        WHERE p.user_id = ?
        GROUP BY p.id
        ORDER BY p.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("projects.html", projects=rows)


def validate_project_form(name, client_name, location, approval_entries):
    if not name:
        return "Project name is required."
    if not client_name:
        return "Client name is required."
    if not location:
        return "Location is required."
    if not approval_entries:
        return "Select at least one approval type."
    invalid = [approval["approval_type"] for approval in approval_entries if approval["approval_type"] not in APPROVAL_TYPES]
    if invalid:
        return "Invalid approval type selected."
    return None


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def new_project():
    approval_form_entries = build_approval_form_entries()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        client_name = request.form.get("client_name", "").strip()
        location = request.form.get("location", "").strip()
        approval_entries, approval_form_entries = parse_approval_form(request.form)

        error = validate_project_form(name, client_name, location, approval_entries)

        if error is None:
            db = get_db()
            cursor = db.execute(
                """
                INSERT INTO projects (user_id, name, client_name, location)
                VALUES (?, ?, ?, ?)
                """,
                (g.user["id"], name, client_name, location),
            )
            project_id = cursor.lastrowid
            db.executemany(
                """
                INSERT INTO project_approvals (project_id, approval_type, issue_date, expiry_date)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (project_id, approval["approval_type"], approval["issue_date"], approval["expiry_date"])
                    for approval in approval_entries
                ],
            )
            db.commit()
            flash("Project created successfully.", "success")
            return redirect(url_for("project_detail", project_id=project_id))

        flash(error, "error")

    return render_template(
        "project_form.html",
        approval_form_entries=approval_form_entries,
        project=None,
        form_action=url_for("new_project"),
        form_title="Create a project",
        form_copy="Capture the core details and approval categories for this compliance workspace.",
        submit_label="Save Project",
    )


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    project = get_owned_project(project_id)
    approval_form_entries = build_approval_form_entries(fetch_project_approvals(project_id))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        client_name = request.form.get("client_name", "").strip()
        location = request.form.get("location", "").strip()
        approval_entries, approval_form_entries = parse_approval_form(request.form)

        error = validate_project_form(name, client_name, location, approval_entries)
        if error is None:
            db = get_db()
            db.execute(
                """
                UPDATE projects
                SET name = ?, client_name = ?, location = ?
                WHERE id = ? AND user_id = ?
                """,
                (name, client_name, location, project_id, g.user["id"]),
            )
            db.execute("DELETE FROM project_approvals WHERE project_id = ?", (project_id,))
            db.executemany(
                """
                INSERT INTO project_approvals (project_id, approval_type, issue_date, expiry_date)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (project_id, approval["approval_type"], approval["issue_date"], approval["expiry_date"])
                    for approval in approval_entries
                ],
            )
            db.commit()
            flash("Project updated successfully.", "success")
            return redirect(url_for("project_detail", project_id=project_id))

        flash(error, "error")

    return render_template(
        "project_form.html",
        approval_form_entries=approval_form_entries,
        project=project,
        form_action=url_for("edit_project", project_id=project_id),
        form_title="Edit project",
        form_copy="Update project details and approval categories as the compliance scope evolves.",
        submit_label="Update Project",
    )


@app.post("/projects/<int:project_id>/delete")
@login_required
def delete_project(project_id):
    project = get_owned_project(project_id)
    db = get_db()
    db.execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, g.user["id"]))
    db.commit()
    flash(f"Project '{project['name']}' deleted.", "success")
    return redirect(url_for("projects"))


@app.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = get_owned_project(project_id)
    status_filter = request.args.get("status_filter", "All")
    frequency_filter = request.args.get("frequency_filter", "All")
    calendar_view = request.args.get("calendar_view", "1")
    if status_filter not in STATUS_FILTERS:
        status_filter = "All"
    if frequency_filter not in {"All", *FREQUENCY_TYPES}:
        frequency_filter = "All"
    try:
        calendar_view = int(calendar_view)
    except ValueError:
        calendar_view = 1
    if calendar_view not in CALENDAR_VIEW_OPTIONS:
        calendar_view = 1

    approvals = annotate_approval_rows(fetch_project_approvals(project_id))
    compliance_items = annotate_compliance_items(fetch_project_compliance_items(project_id))
    documents = fetch_project_documents(project_id)
    overdue_items, upcoming_items = fetch_project_reminders(project_id)
    filtered_items = filter_compliance_items(compliance_items, status_filter, frequency_filter)
    calendar_months = build_project_calendar(project_id, months=calendar_view)

    return render_template(
        "project_detail.html",
        project=project,
        approvals=approvals,
        compliance_items=filtered_items,
        all_compliance_items=compliance_items,
        documents_by_item=build_documents_by_item(documents),
        overdue_items=overdue_items,
        upcoming_items=upcoming_items,
        status_filter=status_filter,
        frequency_filter=frequency_filter,
        status_counts=build_status_counts(compliance_items),
        frequency_counts=build_frequency_counts(compliance_items),
        status_filters=STATUS_FILTERS,
        frequency_filters=["All", *FREQUENCY_TYPES],
        calendar_months=calendar_months,
        calendar_view=calendar_view,
        calendar_view_options=CALENDAR_VIEW_OPTIONS,
    )


@app.get("/projects/<int:project_id>/import-sample")
@login_required
def download_project_import_sample(project_id):
    project = get_owned_project(project_id)
    filename = secure_filename(f"{project['name']}-compliance-sample.csv") or "compliance-sample.csv"
    return Response(
        build_sample_import_csv(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/projects/<int:project_id>/compliance")
@login_required
def add_compliance_item(project_id):
    project = get_owned_project(project_id)
    condition_description = request.form.get("condition_description", "").strip()
    action_to_be_taken = request.form.get("action_to_be_taken", "").strip()
    due_date = request.form.get("due_date", "").strip()
    frequency = request.form.get("frequency", "General").strip()
    status = request.form.get("status", "Pending").strip()

    error = None
    if not condition_description:
        error = "Condition description is required."
    elif frequency not in FREQUENCY_TYPES:
        error = "Invalid frequency."
    elif status not in {"Pending", "Completed"}:
        error = "Invalid status."

    if error is None:
        db = get_db()
        db.execute(
            """
            INSERT INTO compliance_items
            (project_id, condition_description, action_to_be_taken, due_date, frequency, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project["id"], condition_description, action_to_be_taken, due_date, frequency, status),
        )
        db.commit()
        flash("Compliance item added.", "success")
    else:
        flash(error, "error")

    return redirect(url_for("project_detail", project_id=project["id"]))


@app.post("/projects/<int:project_id>/import")
@login_required
def import_compliance_items(project_id):
    project = get_owned_project(project_id)
    file = request.files.get("import_file")

    if file is None or not file.filename:
        flash("Choose a CSV or Excel file to import.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    if not allowed_import_file(file.filename):
        flash("Unsupported import format. Use CSV or XLSX.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    try:
        rows = parse_import_rows(file)
    except Exception:
        flash("The file could not be read. Check the format and try again.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    valid_rows = [row for row in rows if row["condition_description"]]

    if not valid_rows:
        flash("No valid condition rows found in the uploaded file.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    db = get_db()
    db.executemany(
        """
        INSERT INTO compliance_items
        (project_id, condition_description, action_to_be_taken, due_date, frequency, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                project["id"],
                row["condition_description"],
                row["action_to_be_taken"],
                row["due_date"],
                row["frequency"],
                row["status"],
            )
            for row in valid_rows
        ],
    )
    db.commit()
    flash(f"Imported {len(valid_rows)} compliance items into {project['name']}.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/compliance/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_compliance_item(item_id):
    item = get_owned_compliance_item(item_id)

    if request.method == "POST":
        condition_description = request.form.get("condition_description", "").strip()
        action_to_be_taken = request.form.get("action_to_be_taken", "").strip()
        due_date = request.form.get("due_date", "").strip()
        frequency = request.form.get("frequency", "General").strip()
        status = request.form.get("status", "Pending").strip()

        error = None
        if not condition_description:
            error = "Condition description is required."
        elif frequency not in FREQUENCY_TYPES:
            error = "Invalid frequency."
        elif status not in {"Pending", "Completed"}:
            error = "Invalid status."

        if error is None:
            db = get_db()
            db.execute(
                """
                UPDATE compliance_items
                SET condition_description = ?, action_to_be_taken = ?, due_date = ?, frequency = ?, status = ?
                WHERE id = ?
                """,
                (condition_description, action_to_be_taken, due_date, frequency, status, item_id),
            )
            db.commit()
            flash("Compliance item updated.", "success")
            return redirect(url_for("project_detail", project_id=item["project_id"]))

        flash(error, "error")

    current_item = get_owned_compliance_item(item_id)
    return render_template("compliance_form.html", item=current_item, frequency_types=FREQUENCY_TYPES)


@app.post("/compliance/<int:item_id>/delete")
@login_required
def delete_compliance_item(item_id):
    item = get_owned_compliance_item(item_id)
    db = get_db()
    db.execute("DELETE FROM compliance_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Compliance item deleted.", "success")
    return redirect(url_for("project_detail", project_id=item["project_id"]))


@app.post("/compliance/<int:item_id>/status")
@login_required
def update_compliance_status(item_id):
    item = get_owned_compliance_item(item_id)
    new_status = request.form.get("status", "").strip()
    if new_status not in {"Pending", "Completed"}:
        flash("Invalid status update.", "error")
        return redirect(url_for("project_detail", project_id=item["project_id"]))

    db = get_db()
    db.execute("UPDATE compliance_items SET status = ? WHERE id = ?", (new_status, item_id))
    db.commit()
    flash("Compliance status updated.", "success")
    return redirect(url_for("project_detail", project_id=item["project_id"]))


@app.post("/compliance/<int:item_id>/upload")
@login_required
def upload_document(item_id):
    item = get_owned_compliance_item(item_id)
    file = request.files.get("document")

    if file is None or not file.filename:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("project_detail", project_id=item["project_id"]))

    if not allowed_file(file.filename):
        flash("Unsupported file type. Upload a PDF or image.", "error")
        return redirect(url_for("project_detail", project_id=item["project_id"]))

    original_filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}{Path(original_filename).suffix.lower()}"
    save_path = UPLOAD_DIR / unique_name
    file.save(save_path)

    db = get_db()
    db.execute(
        """
        INSERT INTO documents (compliance_item_id, original_filename, stored_filename)
        VALUES (?, ?, ?)
        """,
        (item_id, original_filename, unique_name),
    )
    db.commit()
    flash("Document uploaded.", "success")
    return redirect(url_for("project_detail", project_id=item["project_id"]))


@app.route("/documents/<int:document_id>")
@login_required
def view_document(document_id):
    document = get_db().execute(
        """
        SELECT d.*, ci.project_id, p.user_id
        FROM documents d
        JOIN compliance_items ci ON ci.id = d.compliance_item_id
        JOIN projects p ON p.id = ci.project_id
        WHERE d.id = ? AND p.user_id = ?
        """,
        (document_id, g.user["id"]),
    ).fetchone()
    if document is None:
        abort(404)

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        document["stored_filename"],
        as_attachment=False,
        download_name=document["original_filename"],
    )


@app.errorhandler(413)
def file_too_large(_error):
    flash("File is too large. Please keep uploads under 16 MB.", "error")
    return redirect(request.referrer or url_for("dashboard"))


init_db()


if __name__ == "__main__":
    app.run(debug=True)
