import calendar
import csv
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO, StringIO
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from openpyxl import Workbook, load_workbook
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_DIR / "clearpath.db"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
DEFAULT_APPROVAL_TYPES = [
    "EC",
    "CTE",
    "CTO",
    "HWM",
    "BMW",
    "Fire NOC",
    "Forest",
    "AERB",
    "CGWA",
]
FREQUENCY_TYPES = ["General", "One-time", "Monthly", "Quarterly", "Half-yearly", "Yearly"]
LIFECYCLE_STATUSES = ["Pending", "Completed", "On hold", "Not applicable"]
STATUS_FILTERS = ["All", "Pending", "Due in 7 days", "Overdue", "Completed", "On hold", "Not applicable"]
CALENDAR_VIEW_OPTIONS = [1, 2, 3]
SUBMISSION_MODE_OPTIONS = [
    "",
    "Portal",
    "Email",
    "Courier",
    "Hand delivery",
    "Post",
    "Meeting / visit",
    "Other",
]
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
    "schedule source": "schedule_source",
    "submitted to": "submitted_to",
    "submission mode": "submission_mode",
    "responsible person": "responsible_person",
    "acknowledgment number": "acknowledgment_number",
    "acknowledgement number": "acknowledgment_number",
    "reference no": "acknowledgment_number",
    "reference number": "acknowledgment_number",
    "remarks": "remarks",
    "notes": "remarks",
}
SCHEMA_WHITELIST = {
    "project_approvals": {"issue_date", "expiry_date", "approval_notes"},
    "documents": {"document_title", "version_notes"},
}


secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required before starting ClearPath.")

app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
app.config["SECRET_KEY"] = secret_key
app.config["DATABASE"] = str(DATABASE_PATH)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["WTF_CSRF_TIME_LIMIT"] = None
app.config["RATELIMIT_STORAGE_URI"] = "memory://"

csrf = CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.context_processor
def inject_helpers():
    return {
        "csrf_token": generate_csrf,
        "lifecycle_statuses": LIFECYCLE_STATUSES,
        "submission_mode_options": SUBMISSION_MODE_OPTIONS,
    }


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def table_columns(db, table_name):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table_name})")}


def ensure_column(db, table_name, column_name, definition):
    if table_name not in SCHEMA_WHITELIST or column_name not in SCHEMA_WHITELIST[table_name]:
        raise ValueError(f"Unsafe schema update attempted for {table_name}.{column_name}")
    existing_columns = table_columns(db, table_name)
    if column_name not in existing_columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def migrate_project_approvals_table(db):
    ensure_column(db, "project_approvals", "issue_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "project_approvals", "expiry_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "project_approvals", "approval_notes", "TEXT NOT NULL DEFAULT ''")


def migrate_documents_table(db):
    ensure_column(db, "documents", "document_title", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "documents", "version_notes", "TEXT NOT NULL DEFAULT ''")


def compliance_table_needs_rebuild(db):
    existing_columns = table_columns(db, "compliance_items")
    required_columns = {
        "frequency",
        "schedule_source",
        "submitted_to",
        "submission_mode",
        "responsible_person",
        "acknowledgment_number",
        "remarks",
    }
    if not required_columns.issubset(existing_columns):
        return True
    create_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'compliance_items'"
    ).fetchone()
    sql_text = (create_sql["sql"] if create_sql else "") or ""
    return "On hold" not in sql_text or "Not applicable" not in sql_text


def migrate_compliance_items_table(db):
    if not compliance_table_needs_rebuild(db):
        return

    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("ALTER TABLE compliance_items RENAME TO compliance_items_legacy")
    db.execute(
        """
        CREATE TABLE compliance_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            condition_description TEXT NOT NULL,
            action_to_be_taken TEXT NOT NULL DEFAULT '',
            due_date TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL DEFAULT 'General',
            schedule_source TEXT NOT NULL DEFAULT '',
            submitted_to TEXT NOT NULL DEFAULT '',
            submission_mode TEXT NOT NULL DEFAULT '',
            responsible_person TEXT NOT NULL DEFAULT '',
            acknowledgment_number TEXT NOT NULL DEFAULT '',
            remarks TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('Pending', 'Completed', 'On hold', 'Not applicable')) DEFAULT 'Pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )

    legacy_columns = table_columns(db, "compliance_items_legacy")

    def select_expr(column_name, fallback="''"):
        return column_name if column_name in legacy_columns else fallback

    db.execute(
        f"""
        INSERT INTO compliance_items (
            id,
            project_id,
            condition_description,
            action_to_be_taken,
            due_date,
            frequency,
            schedule_source,
            submitted_to,
            submission_mode,
            responsible_person,
            acknowledgment_number,
            remarks,
            status,
            created_at
        )
        SELECT
            id,
            project_id,
            condition_description,
            {select_expr('action_to_be_taken')},
            {select_expr('due_date')},
            {select_expr('frequency', "'General'")},
            {select_expr('schedule_source')},
            {select_expr('submitted_to')},
            {select_expr('submission_mode')},
            {select_expr('responsible_person')},
            {select_expr('acknowledgment_number')},
            {select_expr('remarks')},
            CASE
                WHEN status IN ('Pending', 'Completed', 'On hold', 'Not applicable') THEN status
                ELSE 'Pending'
            END,
            COALESCE({select_expr('created_at', 'CURRENT_TIMESTAMP')}, CURRENT_TIMESTAMP)
        FROM compliance_items_legacy
        """
    )
    db.execute("DROP TABLE compliance_items_legacy")
    db.execute("PRAGMA foreign_keys = ON")


def create_history_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_item_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            compliance_item_id INTEGER,
            project_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            field_label TEXT NOT NULL DEFAULT '',
            previous_value TEXT NOT NULL DEFAULT '',
            new_value TEXT NOT NULL DEFAULT '',
            item_snapshot TEXT NOT NULL DEFAULT '',
            actor_email TEXT NOT NULL DEFAULT '',
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def init_db():
    INSTANCE_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
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
            approval_notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS compliance_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            condition_description TEXT NOT NULL,
            action_to_be_taken TEXT NOT NULL DEFAULT '',
            due_date TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL DEFAULT 'General',
            schedule_source TEXT NOT NULL DEFAULT '',
            submitted_to TEXT NOT NULL DEFAULT '',
            submission_mode TEXT NOT NULL DEFAULT '',
            responsible_person TEXT NOT NULL DEFAULT '',
            acknowledgment_number TEXT NOT NULL DEFAULT '',
            remarks TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('Pending', 'Completed', 'On hold', 'Not applicable')) DEFAULT 'Pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            compliance_item_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            document_title TEXT NOT NULL DEFAULT '',
            version_notes TEXT NOT NULL DEFAULT '',
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (compliance_item_id) REFERENCES compliance_items(id) ON DELETE CASCADE
        );
        """
    )
    migrate_project_approvals_table(db)
    migrate_compliance_items_table(db)
    migrate_documents_table(db)
    create_history_table(db)
    db.commit()
    db.close()


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
            _, upcoming_items = build_user_reminders(g.user["id"])
            g.notification_items = upcoming_items[:6]
            g.notification_count = len(upcoming_items)


@app.errorhandler(CSRFError)
def handle_csrf_error(_error):
    flash("Your session form token expired or was invalid. Please try that action again.", "error")
    return redirect(request.referrer or url_for("dashboard"))


def current_user_email():
    return g.user["email"] if getattr(g, "user", None) else ""


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
        SELECT ci.*, p.user_id, p.name AS project_name
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
    cleaned = normalize_header(value)
    mapping = {
        "pending": "Pending",
        "completed": "Completed",
        "on hold": "On hold",
        "hold": "On hold",
        "not applicable": "Not applicable",
        "na": "Not applicable",
        "n/a": "Not applicable",
    }
    return mapping.get(cleaned, "Pending")


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
        "half yearly": "Half-yearly",
        "half-yearly": "Half-yearly",
        "half yearly basis": "Half-yearly",
        "yearly": "Yearly",
        "annual": "Yearly",
    }
    if cleaned in frequency_map:
        return frequency_map[cleaned]
    return "One-time" if due_date else "General"


def normalize_submission_mode(value):
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    for option in SUBMISSION_MODE_OPTIONS:
        if cleaned.lower() == option.lower():
            return option
    return cleaned


def parse_iso_date(value):
    cleaned = clean_text(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(source_date, months):
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def recurrence_interval_months(frequency):
    return {
        "Monthly": 1,
        "Quarterly": 3,
        "Half-yearly": 6,
        "Yearly": 12,
    }.get(frequency, 0)


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

            schedule_source = clean_text(mapped.get("schedule_source"))
            submitted_to = clean_text(mapped.get("submitted_to"))
            submission_mode = normalize_submission_mode(mapped.get("submission_mode"))
            responsible_person = clean_text(mapped.get("responsible_person"))
            acknowledgment_number = clean_text(mapped.get("acknowledgment_number"))
            remarks = clean_text(mapped.get("remarks"))
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
            schedule_source = ""
            submitted_to = ""
            submission_mode = ""
            responsible_person = ""
            acknowledgment_number = ""
            remarks = ""

        normalized_rows.append(
            {
                "condition_description": condition_description,
                "action_to_be_taken": action_to_be_taken,
                "due_date": due_date,
                "status": normalize_status(status_raw),
                "frequency": normalize_frequency(frequency_raw, due_date),
                "schedule_source": schedule_source,
                "submitted_to": submitted_to,
                "submission_mode": submission_mode,
                "responsible_person": responsible_person,
                "acknowledgment_number": acknowledgment_number,
                "remarks": remarks,
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
    writer.writerow(
        [
            "Condition Description",
            "Action To Be Taken",
            "Due Date",
            "Status",
            "Frequency",
            "Schedule Source",
            "Submitted To",
            "Submission Mode",
            "Responsible Person",
            "Acknowledgment Number",
            "Remarks",
        ]
    )
    writer.writerow(
        [
            "Submit half-yearly compliance report to the regional office.",
            "Prepare the report, review internally, and submit before the deadline.",
            "2026-07-15",
            "Pending",
            "One-time",
            "",
            "Regional Office",
            "Portal",
            "Consultant lead",
            "EC-HY-2026-01",
            "Attach the signed copy after submission.",
        ]
    )
    writer.writerow(
        [
            "Submit quarterly monitoring summary from EC grant date.",
            "Compile monitoring results and submit to the authority.",
            "",
            "Pending",
            "Quarterly",
            "EC",
            "State authority",
            "Email",
            "Site environment manager",
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "Display the latest EC copy at the site office and entry gate.",
            "",
            "",
            "Pending",
            "General",
            "",
            "",
            "",
            "",
            "",
            "",
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
                "approval_notes": row.get("approval_notes", "") if isinstance(row, dict) else row["approval_notes"],
            }

    return [
        {
            "approval_type": approval_type,
            "selected": approval_type in selected_map,
            "issue_date": selected_map.get(approval_type, {}).get("issue_date", ""),
            "expiry_date": selected_map.get(approval_type, {}).get("expiry_date", ""),
            "approval_notes": selected_map.get(approval_type, {}).get("approval_notes", ""),
        }
        for approval_type in DEFAULT_APPROVAL_TYPES
    ]


def parse_approval_form(form):
    selected_entries = []
    form_entries = []
    for approval_type in DEFAULT_APPROVAL_TYPES:
        selected = bool(form.get(f"approval_enabled_{approval_type}"))
        issue_date = form.get(f"issue_date_{approval_type}", "").strip()
        expiry_date = form.get(f"expiry_date_{approval_type}", "").strip()
        approval_notes = form.get(f"approval_notes_{approval_type}", "").strip()
        form_entries.append(
            {
                "approval_type": approval_type,
                "selected": selected,
                "issue_date": issue_date,
                "expiry_date": expiry_date,
                "approval_notes": approval_notes,
            }
        )
        if selected:
            selected_entries.append(
                {
                    "approval_type": approval_type,
                    "issue_date": issue_date,
                    "expiry_date": expiry_date,
                    "approval_notes": approval_notes,
                }
            )
    return selected_entries, form_entries


def derive_item_status(status, due_date_value):
    if status in {"Completed", "On hold", "Not applicable"}:
        return status
    if not due_date_value:
        return "Pending"

    today = date.today().isoformat()
    seven_days = (date.today() + timedelta(days=7)).isoformat()
    if due_date_value < today:
        return "Overdue"
    if due_date_value <= seven_days:
        return "Due in 7 days"
    return "Pending"


def annotate_compliance_items(rows, approval_lookup):
    annotated = []
    for row in rows:
        item = dict(row)
        item["next_due_date"] = compute_next_due_date(item, approval_lookup)
        item["display_due_date"] = item["next_due_date"] or item["due_date"]
        item["derived_status"] = derive_item_status(item["status"], item["display_due_date"])
        item["has_due_date"] = bool(item["display_due_date"])
        item["is_overdue"] = item["derived_status"] == "Overdue"
        item["is_due_soon"] = item["derived_status"] == "Due in 7 days"
        item["schedule_label"] = item["frequency"]
        if item.get("schedule_source"):
            item["schedule_label"] = f"{item['frequency']} from {item['schedule_source']}"
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


def build_approval_lookup(approval_rows):
    return {row["approval_type"]: dict(row) for row in approval_rows}


def resolve_schedule_anchor(item, approval_lookup):
    schedule_source = clean_text(item.get("schedule_source"))
    if schedule_source and schedule_source in approval_lookup:
        approval = approval_lookup[schedule_source]
        return parse_iso_date(approval.get("issue_date")), parse_iso_date(approval.get("expiry_date"))
    return parse_iso_date(item.get("due_date")), None


def compute_next_due_date(item, approval_lookup, from_date=None):
    if item["frequency"] in {"General", ""}:
        return item.get("due_date", "")

    from_date = from_date or date.today()
    interval_months = recurrence_interval_months(item["frequency"])
    if not interval_months:
        return item.get("due_date", "")

    anchor_date, end_date = resolve_schedule_anchor(item, approval_lookup)
    if not anchor_date:
        return item.get("due_date", "")

    occurrence = anchor_date
    last_occurrence = None
    while occurrence <= from_date:
        last_occurrence = occurrence
        occurrence = add_months(occurrence, interval_months)

    if item.get("status") == "Pending" and last_occurrence is not None:
        candidate = last_occurrence
    else:
        candidate = occurrence

    if end_date and candidate > end_date:
        return ""
    return candidate.isoformat()


def generate_schedule_dates(item, approval_lookup, window_start, window_end):
    if item["frequency"] in {"General", ""}:
        due_date = parse_iso_date(item.get("due_date"))
        if due_date and window_start <= due_date <= window_end:
            return [due_date]
        return []

    interval_months = recurrence_interval_months(item["frequency"])
    if not interval_months:
        due_date = parse_iso_date(item.get("due_date"))
        if due_date and window_start <= due_date <= window_end:
            return [due_date]
        return []

    anchor_date, end_date = resolve_schedule_anchor(item, approval_lookup)
    if not anchor_date:
        return []

    occurrence = anchor_date
    dates = []
    while occurrence <= window_end:
        if end_date and occurrence > end_date:
            break
        if occurrence >= window_start:
            dates.append(occurrence)
        occurrence = add_months(occurrence, interval_months)
    return dates


def build_schedule_preview(frequency, due_date, schedule_source, approval_lookup, occurrences=4):
    if frequency == "General":
        return "No recurring schedule. Use this for general site conditions without a deadline."

    if frequency == "One-time":
        if due_date:
            return f"One-time deadline on {due_date}."
        return "One-time item with no date selected yet."

    interval_months = recurrence_interval_months(frequency)
    if not interval_months:
        return "Schedule preview becomes available after selecting a valid frequency."

    item = {
        "frequency": frequency,
        "due_date": due_date,
        "schedule_source": schedule_source,
    }
    anchor_date, end_date = resolve_schedule_anchor(item, approval_lookup)
    if not anchor_date:
        anchor_label = schedule_source or "a starting date"
        return f"Select {anchor_label} issue date or a custom date to preview the recurring schedule."

    dates = []
    occurrence = anchor_date
    while len(dates) < occurrences:
        if end_date and occurrence > end_date:
            break
        dates.append(occurrence.isoformat())
        occurrence = add_months(occurrence, interval_months)

    if not dates:
        return "No recurring dates fall inside the selected approval validity window."

    suffix = f" until {end_date.isoformat()}" if end_date else ""
    return f"Upcoming schedule: {', '.join(dates)}{suffix}."


def build_project_calendar(project_id, months=1):
    project = get_owned_project(project_id)
    approvals = fetch_project_approvals(project_id)
    approval_lookup = build_approval_lookup(approvals)
    due_items = fetch_project_compliance_items(project_id)

    event_map = {}

    def add_event(event_date, label, kind, href):
        if not event_date:
            return
        event_map.setdefault(event_date, []).append({"label": label, "kind": kind, "href": href})

    start = date.today().replace(day=1)
    current_year = start.year
    current_month = start.month
    months_data = []
    window_end = add_months(start, months) - timedelta(days=1)

    for item in due_items:
        compact_label = item["condition_description"][:56].rstrip()
        if len(item["condition_description"]) > 56:
            compact_label += "..."
        for occurrence in generate_schedule_dates(dict(item), approval_lookup, start, window_end):
            add_event(
                occurrence.isoformat(),
                f"{item['frequency']}: {compact_label}",
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

        months_data.append({"label": f"{calendar.month_name[current_month]} {current_year}", "days": days})

        if current_month == 12:
            current_month = 1
            current_year += 1
        else:
            current_month += 1

    return months_data


def fetch_project_approvals(project_id):
    return get_db().execute(
        """
        SELECT approval_type, issue_date, expiry_date, approval_notes
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


def fetch_project_history(project_id, limit=12):
    return get_db().execute(
        """
        SELECT *
        FROM compliance_item_history
        WHERE project_id = ?
        ORDER BY changed_at DESC, id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()


def build_documents_by_item(documents):
    docs_by_item = {}
    for document in documents:
        docs_by_item.setdefault(document["compliance_item_id"], []).append(document)
    return docs_by_item


def fetch_user_approval_rows(user_id):
    return get_db().execute(
        """
        SELECT pa.project_id, pa.approval_type, pa.issue_date, pa.expiry_date, pa.approval_notes
        FROM project_approvals pa
        JOIN projects p ON p.id = pa.project_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ).fetchall()


def fetch_user_compliance_rows(user_id):
    return get_db().execute(
        """
        SELECT ci.*, p.name AS project_name
        FROM compliance_items ci
        JOIN projects p ON p.id = ci.project_id
        WHERE p.user_id = ?
        ORDER BY p.name ASC, ci.created_at DESC
        """,
        (user_id,),
    ).fetchall()


def build_project_approval_lookup(approval_rows):
    project_lookup = {}
    for row in approval_rows:
        project_lookup.setdefault(row["project_id"], {})[row["approval_type"]] = dict(row)
    return project_lookup


def annotate_user_compliance_items(user_id):
    approval_lookup = build_project_approval_lookup(fetch_user_approval_rows(user_id))
    annotated = []
    for row in fetch_user_compliance_rows(user_id):
        item = dict(row)
        project_approvals = approval_lookup.get(item["project_id"], {})
        item["next_due_date"] = compute_next_due_date(item, project_approvals)
        item["display_due_date"] = item["next_due_date"] or item["due_date"]
        item["derived_status"] = derive_item_status(item["status"], item["display_due_date"])
        annotated.append(item)
    return annotated


def build_user_reminders(user_id, days=7):
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    items = annotate_user_compliance_items(user_id)
    overdue_items = [
        item for item in items if item["status"] == "Pending" and item["derived_status"] == "Overdue"
    ]
    upcoming_items = [
        item for item in items if item["status"] == "Pending" and item["derived_status"] == "Due in 7 days"
    ]
    upcoming_items = [item for item in upcoming_items if item["display_due_date"] <= cutoff]
    overdue_items.sort(key=lambda item: item["display_due_date"])
    upcoming_items.sort(key=lambda item: item["display_due_date"])
    return overdue_items, upcoming_items


def build_project_reminders(items):
    overdue_items = [
        {"id": item["id"], "condition_description": item["condition_description"], "due_date": item["display_due_date"]}
        for item in items
        if item["status"] == "Pending" and item["derived_status"] == "Overdue"
    ]
    upcoming_items = [
        {"id": item["id"], "condition_description": item["condition_description"], "due_date": item["display_due_date"]}
        for item in items
        if item["status"] == "Pending" and item["derived_status"] == "Due in 7 days"
    ]
    return overdue_items, upcoming_items


def record_history(
    project_id,
    item_id,
    change_type,
    field_label="",
    previous_value="",
    new_value="",
    item_snapshot="",
    actor_email="",
):
    get_db().execute(
        """
        INSERT INTO compliance_item_history (
            compliance_item_id,
            project_id,
            change_type,
            field_label,
            previous_value,
            new_value,
            item_snapshot,
            actor_email
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            project_id,
            change_type,
            field_label,
            previous_value,
            new_value,
            item_snapshot,
            actor_email or current_user_email(),
        ),
    )


def sanitize_schedule_source(schedule_source, approval_options):
    if not schedule_source:
        return ""
    return schedule_source if schedule_source in approval_options else ""


def validate_compliance_payload(payload, approval_options):
    if not payload["condition_description"]:
        return "Condition description is required."
    if payload["frequency"] not in FREQUENCY_TYPES:
        return "Invalid frequency."
    if payload["schedule_source"] and payload["schedule_source"] not in approval_options:
        return "Invalid schedule source."
    if payload["status"] not in LIFECYCLE_STATUSES:
        return "Invalid status."
    if payload["submission_mode"] and payload["submission_mode"] not in SUBMISSION_MODE_OPTIONS:
        return "Invalid submission mode."
    return None


def get_compliance_form_payload(form, approval_options):
    payload = {
        "condition_description": form.get("condition_description", "").strip(),
        "action_to_be_taken": form.get("action_to_be_taken", "").strip(),
        "due_date": form.get("due_date", "").strip(),
        "frequency": form.get("frequency", "General").strip(),
        "schedule_source": sanitize_schedule_source(form.get("schedule_source", "").strip(), approval_options),
        "submitted_to": form.get("submitted_to", "").strip(),
        "submission_mode": form.get("submission_mode", "").strip(),
        "responsible_person": form.get("responsible_person", "").strip(),
        "acknowledgment_number": form.get("acknowledgment_number", "").strip(),
        "remarks": form.get("remarks", "").strip(),
        "status": form.get("status", "Pending").strip(),
    }
    return payload, validate_compliance_payload(payload, approval_options)


def compare_item_changes(existing_item, payload):
    field_labels = {
        "condition_description": "Condition description",
        "action_to_be_taken": "Action to be taken",
        "due_date": "Due date",
        "frequency": "Frequency",
        "schedule_source": "Schedule source",
        "submitted_to": "Submitted to",
        "submission_mode": "Submission mode",
        "responsible_person": "Responsible person",
        "acknowledgment_number": "Acknowledgment number",
        "remarks": "Remarks",
        "status": "Status",
    }
    changes = []
    for field_name, label in field_labels.items():
        previous_value = existing_item[field_name] or ""
        new_value = payload[field_name] or ""
        if previous_value != new_value:
            changes.append((label, previous_value, new_value))
    return changes


def selected_project_approval_types(project_id):
    return [row["approval_type"] for row in fetch_project_approvals(project_id)]


def approval_context_for_template(approval_rows):
    return [
        {
            "approval_type": row["approval_type"],
            "issue_date": row["issue_date"],
            "expiry_date": row["expiry_date"],
        }
        for row in approval_rows
    ]


def validate_project_form(name, client_name, location, approval_entries):
    if not name:
        return "Project name is required."
    if not client_name:
        return "Client name is required."
    if not location:
        return "Location is required."
    if not approval_entries:
        return "Select at least one approval type."
    return None


def build_export_workbook(project, approvals, compliance_items):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Compliance Register"

    sheet.append(["Project", project["name"]])
    sheet.append(["Client", project["client_name"]])
    sheet.append(["Location", project["location"]])
    sheet.append([])
    sheet.append(["Selected approvals"])
    sheet.append(["Approval", "Issue date", "Expiry date", "Notes"])
    for approval in approvals:
        sheet.append(
            [
                approval["approval_type"],
                approval["issue_date"],
                approval["expiry_date"],
                approval["approval_notes"],
            ]
        )

    sheet.append([])
    sheet.append(
        [
            "Condition description",
            "Action to be taken",
            "Status",
            "Urgency",
            "Next due",
            "Base due date",
            "Frequency",
            "Schedule source",
            "Submitted to",
            "Submission mode",
            "Responsible person",
            "Acknowledgment number",
            "Remarks",
            "Document count",
        ]
    )
    for item in compliance_items:
        sheet.append(
            [
                item["condition_description"],
                item["action_to_be_taken"],
                item["status"],
                item["derived_status"],
                item["next_due_date"],
                item["due_date"],
                item["frequency"],
                item["schedule_source"],
                item["submitted_to"],
                item["submission_mode"],
                item["responsible_person"],
                item["acknowledgment_number"],
                item["remarks"],
                item["document_count"],
            ]
        )

    for column in sheet.columns:
        length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(length + 2, 14), 36)

    return workbook


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute")
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
@limiter.limit("8 per minute")
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
    overdue_items, upcoming_items = build_user_reminders(g.user["id"])
    annotated_items = annotate_user_compliance_items(g.user["id"])
    status_counts = build_status_counts(annotated_items)
    project_count = get_db().execute(
        "SELECT COUNT(*) AS count FROM projects WHERE user_id = ?", (g.user["id"],)
    ).fetchone()["count"]
    recent_projects = get_db().execute(
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
        pending_count=status_counts.get("Pending", 0),
        completed_count=status_counts.get("Completed", 0),
        on_hold_count=status_counts.get("On hold", 0),
        not_applicable_count=status_counts.get("Not applicable", 0),
        recent_projects=recent_projects,
        overdue_items=overdue_items[:8],
        upcoming_items=upcoming_items[:8],
        overdue_count=len(overdue_items),
        upcoming_count=len(upcoming_items),
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
                INSERT INTO project_approvals (project_id, approval_type, issue_date, expiry_date, approval_notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_id,
                        approval["approval_type"],
                        approval["issue_date"],
                        approval["expiry_date"],
                        approval["approval_notes"],
                    )
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
        form_copy="Capture the core details, approvals, dates, and regulatory notes for this compliance workspace.",
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
                INSERT INTO project_approvals (project_id, approval_type, issue_date, expiry_date, approval_notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_id,
                        approval["approval_type"],
                        approval["issue_date"],
                        approval["expiry_date"],
                        approval["approval_notes"],
                    )
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
        form_copy="Update project details, approval dates, and state-specific notes as the compliance scope evolves.",
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

    approval_rows = fetch_project_approvals(project_id)
    approvals = annotate_approval_rows(approval_rows)
    approval_lookup = build_approval_lookup(approval_rows)
    compliance_items = annotate_compliance_items(fetch_project_compliance_items(project_id), approval_lookup)
    documents = fetch_project_documents(project_id)
    overdue_items, upcoming_items = build_project_reminders(compliance_items)
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
        recent_history=fetch_project_history(project_id),
        status_filter=status_filter,
        frequency_filter=frequency_filter,
        status_counts=build_status_counts(compliance_items),
        frequency_counts=build_frequency_counts(compliance_items),
        status_filters=STATUS_FILTERS,
        frequency_filters=["All", *FREQUENCY_TYPES],
        calendar_months=calendar_months,
        calendar_view=calendar_view,
        calendar_view_options=CALENDAR_VIEW_OPTIONS,
        approval_options=[approval["approval_type"] for approval in approvals],
        approval_context=approval_context_for_template(approvals),
        schedule_preview_text=build_schedule_preview("General", "", "", approval_lookup),
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


@app.get("/projects/<int:project_id>/bulk-edit")
@login_required
def bulk_edit_project(project_id):
    project = get_owned_project(project_id)
    approval_rows = fetch_project_approvals(project_id)
    approval_lookup = build_approval_lookup(approval_rows)
    items = annotate_compliance_items(fetch_project_compliance_items(project_id), approval_lookup)
    return render_template(
        "bulk_edit.html",
        project=project,
        items=items,
        approval_options=[approval["approval_type"] for approval in approval_rows],
        approval_context=approval_context_for_template(approval_rows),
        frequency_types=FREQUENCY_TYPES,
    )


@app.post("/projects/<int:project_id>/bulk-edit")
@login_required
def save_bulk_edit_project(project_id):
    get_owned_project(project_id)
    db = get_db()
    approval_options = selected_project_approval_types(project_id)
    item_rows = fetch_project_compliance_items(project_id)
    updated_count = 0

    for item in item_rows:
        item_id = item["id"]
        payload = {
            "condition_description": item["condition_description"],
            "action_to_be_taken": request.form.get(f"action_to_be_taken_{item_id}", "").strip(),
            "due_date": request.form.get(f"due_date_{item_id}", "").strip(),
            "frequency": request.form.get(f"frequency_{item_id}", item["frequency"]).strip(),
            "schedule_source": sanitize_schedule_source(
                request.form.get(f"schedule_source_{item_id}", "").strip(), approval_options
            ),
            "submitted_to": item["submitted_to"],
            "submission_mode": item["submission_mode"],
            "responsible_person": item["responsible_person"],
            "acknowledgment_number": item["acknowledgment_number"],
            "remarks": item["remarks"],
            "status": request.form.get(f"status_{item_id}", item["status"]).strip(),
        }
        error = validate_compliance_payload(payload, approval_options)
        if error is not None:
            flash(f"Bulk edit stopped on '{item['condition_description'][:50]}': {error}", "error")
            return redirect(url_for("bulk_edit_project", project_id=project_id))

        changes = compare_item_changes(item, payload)
        if not changes:
            continue

        db.execute(
            """
            UPDATE compliance_items
            SET action_to_be_taken = ?, due_date = ?, frequency = ?, schedule_source = ?, status = ?
            WHERE id = ?
            """,
            (
                payload["action_to_be_taken"],
                payload["due_date"],
                payload["frequency"],
                payload["schedule_source"],
                payload["status"],
                item_id,
            ),
        )
        for label, previous_value, new_value in changes:
            record_history(
                project_id,
                item_id,
                "bulk_edit",
                label,
                previous_value,
                new_value,
                item["condition_description"],
            )
        updated_count += 1

    db.commit()
    flash(f"Updated {updated_count} compliance items in bulk.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.get("/projects/<int:project_id>/export")
@login_required
def export_project_register(project_id):
    project = get_owned_project(project_id)
    approval_rows = fetch_project_approvals(project_id)
    approval_lookup = build_approval_lookup(approval_rows)
    compliance_items = annotate_compliance_items(fetch_project_compliance_items(project_id), approval_lookup)

    workbook = build_export_workbook(project, approval_rows, compliance_items)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = secure_filename(f"{project['name']}-compliance-register.xlsx") or "compliance-register.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/projects/<int:project_id>/compliance")
@login_required
def add_compliance_item(project_id):
    project = get_owned_project(project_id)
    approval_options = selected_project_approval_types(project_id)
    payload, error = get_compliance_form_payload(request.form, approval_options)

    if error is None:
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO compliance_items (
                project_id,
                condition_description,
                action_to_be_taken,
                due_date,
                frequency,
                schedule_source,
                submitted_to,
                submission_mode,
                responsible_person,
                acknowledgment_number,
                remarks,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"],
                payload["condition_description"],
                payload["action_to_be_taken"],
                payload["due_date"],
                payload["frequency"],
                payload["schedule_source"],
                payload["submitted_to"],
                payload["submission_mode"],
                payload["responsible_person"],
                payload["acknowledgment_number"],
                payload["remarks"],
                payload["status"],
            ),
        )
        record_history(
            project["id"],
            cursor.lastrowid,
            "created",
            "Compliance item",
            "",
            payload["status"],
            payload["condition_description"],
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

    approval_options = selected_project_approval_types(project_id)
    valid_rows = []
    for row in rows:
        if not row["condition_description"]:
            continue
        row["schedule_source"] = sanitize_schedule_source(row.get("schedule_source", ""), approval_options)
        error = validate_compliance_payload(row, approval_options)
        if error is None:
            valid_rows.append(row)

    if not valid_rows:
        flash("No valid condition rows found in the uploaded file.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    db = get_db()
    for row in valid_rows:
        cursor = db.execute(
            """
            INSERT INTO compliance_items (
                project_id,
                condition_description,
                action_to_be_taken,
                due_date,
                frequency,
                schedule_source,
                submitted_to,
                submission_mode,
                responsible_person,
                acknowledgment_number,
                remarks,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"],
                row["condition_description"],
                row["action_to_be_taken"],
                row["due_date"],
                row["frequency"],
                row["schedule_source"],
                row["submitted_to"],
                row["submission_mode"],
                row["responsible_person"],
                row["acknowledgment_number"],
                row["remarks"],
                row["status"],
            ),
        )
        record_history(
            project["id"],
            cursor.lastrowid,
            "imported",
            "Import",
            "",
            row["status"],
            row["condition_description"],
        )
    db.commit()
    flash(
        f"Imported {len(valid_rows)} compliance items into {project['name']}. Use bulk edit or 'Edit action / due date' to finish scheduling details.",
        "success",
    )
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/compliance/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_compliance_item(item_id):
    item = get_owned_compliance_item(item_id)
    approval_rows = fetch_project_approvals(item["project_id"])
    approval_options = [approval["approval_type"] for approval in approval_rows]

    if request.method == "POST":
        payload, error = get_compliance_form_payload(request.form, approval_options)

        if error is None:
            changes = compare_item_changes(item, payload)
            db = get_db()
            db.execute(
                """
                UPDATE compliance_items
                SET condition_description = ?,
                    action_to_be_taken = ?,
                    due_date = ?,
                    frequency = ?,
                    schedule_source = ?,
                    submitted_to = ?,
                    submission_mode = ?,
                    responsible_person = ?,
                    acknowledgment_number = ?,
                    remarks = ?,
                    status = ?
                WHERE id = ?
                """,
                (
                    payload["condition_description"],
                    payload["action_to_be_taken"],
                    payload["due_date"],
                    payload["frequency"],
                    payload["schedule_source"],
                    payload["submitted_to"],
                    payload["submission_mode"],
                    payload["responsible_person"],
                    payload["acknowledgment_number"],
                    payload["remarks"],
                    payload["status"],
                    item_id,
                ),
            )
            for label, previous_value, new_value in changes:
                record_history(
                    item["project_id"],
                    item_id,
                    "edited",
                    label,
                    previous_value,
                    new_value,
                    payload["condition_description"],
                )
            db.commit()
            flash("Compliance item updated.", "success")
            return redirect(url_for("project_detail", project_id=item["project_id"]))

        flash(error, "error")

    current_item = get_owned_compliance_item(item_id)
    project_approvals = annotate_approval_rows(approval_rows)
    return render_template(
        "compliance_form.html",
        item=current_item,
        frequency_types=FREQUENCY_TYPES,
        approval_options=approval_options,
        approval_context=approval_context_for_template(project_approvals),
        schedule_preview_text=build_schedule_preview(
            current_item["frequency"],
            current_item["due_date"],
            current_item["schedule_source"],
            build_approval_lookup(project_approvals),
        ),
    )


@app.post("/compliance/<int:item_id>/delete")
@login_required
def delete_compliance_item(item_id):
    item = get_owned_compliance_item(item_id)
    db = get_db()
    record_history(
        item["project_id"],
        item_id,
        "deleted",
        "Compliance item",
        item["status"],
        "",
        item["condition_description"],
    )
    db.execute("DELETE FROM compliance_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Compliance item deleted.", "success")
    return redirect(url_for("project_detail", project_id=item["project_id"]))


@app.post("/compliance/<int:item_id>/status")
@login_required
def update_compliance_status(item_id):
    item = get_owned_compliance_item(item_id)
    new_status = request.form.get("status", "").strip()
    if new_status not in LIFECYCLE_STATUSES:
        flash("Invalid status update.", "error")
        return redirect(url_for("project_detail", project_id=item["project_id"]))

    db = get_db()
    db.execute("UPDATE compliance_items SET status = ? WHERE id = ?", (new_status, item_id))
    record_history(
        item["project_id"],
        item_id,
        "status_changed",
        "Status",
        item["status"],
        new_status,
        item["condition_description"],
    )
    db.commit()
    flash("Compliance status updated.", "success")
    return redirect(url_for("project_detail", project_id=item["project_id"]))


@app.post("/compliance/<int:item_id>/upload")
@login_required
def upload_document(item_id):
    item = get_owned_compliance_item(item_id)
    file = request.files.get("document")
    document_title = request.form.get("document_title", "").strip()
    version_notes = request.form.get("version_notes", "").strip()

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

    if not document_title:
        document_title = Path(original_filename).stem

    db = get_db()
    db.execute(
        """
        INSERT INTO documents (compliance_item_id, original_filename, stored_filename, document_title, version_notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (item_id, original_filename, unique_name, document_title, version_notes),
    )
    record_history(
        item["project_id"],
        item_id,
        "document_uploaded",
        "Document",
        "",
        document_title,
        item["condition_description"],
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
