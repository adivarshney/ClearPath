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
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_IMPORT_EXTENSIONS = {".csv", ".xlsx"}
IMPORT_HEADER_ALIASES = {
    "condition description": "condition_description",
    "condition": "condition_description",
    "description": "condition_description",
    "action to be taken": "action_to_be_taken",
    "action": "action_to_be_taken",
    "due date": "due_date",
    "status": "status",
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
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS compliance_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            condition_description TEXT NOT NULL,
            action_to_be_taken TEXT NOT NULL,
            due_date TEXT NOT NULL,
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
    if user_id is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


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


def parse_import_rows(file_storage):
    extension = Path(file_storage.filename).suffix.lower()

    if extension == ".csv":
        content = file_storage.stream.read().decode("utf-8-sig")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
    else:
        workbook = load_workbook(file_storage, read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            return []
        headers = [str(cell).strip() if cell is not None else "" for cell in values[0]]
        rows = []
        for row in values[1:]:
            rows.append(
                {headers[index]: row[index] if index < len(row) else None for index in range(len(headers))}
            )

    normalized_rows = []
    for row in rows:
        mapped = {}
        for key, value in row.items():
            normalized_key = IMPORT_HEADER_ALIASES.get((key or "").strip().lower())
            if normalized_key:
                mapped[normalized_key] = value

        normalized_rows.append(
            {
                "condition_description": str(mapped.get("condition_description", "") or "").strip(),
                "action_to_be_taken": str(mapped.get("action_to_be_taken", "") or "").strip(),
                "due_date": normalize_due_date(mapped.get("due_date")),
                "status": normalize_status(str(mapped.get("status", "") or "")),
            }
        )

    return normalized_rows


def fetch_project_approvals(project_id):
    return get_db().execute(
        "SELECT approval_type FROM project_approvals WHERE project_id = ? ORDER BY approval_type",
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
        ORDER BY ci.due_date ASC, ci.created_at DESC
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
          AND ci.due_date BETWEEN ? AND ?
        ORDER BY ci.due_date ASC, p.name ASC
        LIMIT 8
        """,
        (user_id, today, cutoff),
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
          AND ci.due_date < ?
        ORDER BY ci.due_date ASC, p.name ASC
        LIMIT 8
        """,
        (user_id, today),
    ).fetchall()


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


def validate_project_form(name, client_name, location, approval_types):
    if not name:
        return "Project name is required."
    if not client_name:
        return "Client name is required."
    if not location:
        return "Location is required."
    if not approval_types:
        return "Select at least one approval type."
    invalid = [approval for approval in approval_types if approval not in APPROVAL_TYPES]
    if invalid:
        return "Invalid approval type selected."
    return None


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def new_project():
    selected_approvals = []
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        client_name = request.form.get("client_name", "").strip()
        location = request.form.get("location", "").strip()
        approval_types = request.form.getlist("approval_types")
        selected_approvals = approval_types

        error = validate_project_form(name, client_name, location, approval_types)

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
                "INSERT INTO project_approvals (project_id, approval_type) VALUES (?, ?)",
                [(project_id, approval) for approval in approval_types],
            )
            db.commit()
            flash("Project created successfully.", "success")
            return redirect(url_for("project_detail", project_id=project_id))

        flash(error, "error")

    return render_template(
        "project_form.html",
        approval_types=APPROVAL_TYPES,
        project=None,
        selected_approvals=selected_approvals,
        form_action=url_for("new_project"),
        form_title="Create a project",
        form_copy="Capture the core details and approval categories for this compliance workspace.",
        submit_label="Save Project",
    )


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    project = get_owned_project(project_id)
    approvals = [row["approval_type"] for row in fetch_project_approvals(project_id)]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        client_name = request.form.get("client_name", "").strip()
        location = request.form.get("location", "").strip()
        approval_types = request.form.getlist("approval_types")

        error = validate_project_form(name, client_name, location, approval_types)
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
                "INSERT INTO project_approvals (project_id, approval_type) VALUES (?, ?)",
                [(project_id, approval) for approval in approval_types],
            )
            db.commit()
            flash("Project updated successfully.", "success")
            return redirect(url_for("project_detail", project_id=project_id))

        flash(error, "error")
        approvals = approval_types

    return render_template(
        "project_form.html",
        approval_types=APPROVAL_TYPES,
        project=project,
        selected_approvals=approvals,
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
    approvals = fetch_project_approvals(project_id)
    compliance_items = fetch_project_compliance_items(project_id)
    documents = fetch_project_documents(project_id)
    overdue_items, upcoming_items = fetch_project_reminders(project_id)

    return render_template(
        "project_detail.html",
        project=project,
        approvals=[row["approval_type"] for row in approvals],
        compliance_items=compliance_items,
        documents_by_item=build_documents_by_item(documents),
        overdue_items=overdue_items,
        upcoming_items=upcoming_items,
    )


@app.post("/projects/<int:project_id>/compliance")
@login_required
def add_compliance_item(project_id):
    project = get_owned_project(project_id)
    condition_description = request.form.get("condition_description", "").strip()
    action_to_be_taken = request.form.get("action_to_be_taken", "").strip()
    due_date = request.form.get("due_date", "").strip()
    status = request.form.get("status", "Pending").strip()

    error = None
    if not condition_description:
        error = "Condition description is required."
    elif not action_to_be_taken:
        error = "Action to be taken is required."
    elif not due_date:
        error = "Due date is required."
    elif status not in {"Pending", "Completed"}:
        error = "Invalid status."

    if error is None:
        db = get_db()
        db.execute(
            """
            INSERT INTO compliance_items
            (project_id, condition_description, action_to_be_taken, due_date, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project["id"], condition_description, action_to_be_taken, due_date, status),
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

    valid_rows = []
    for row in rows:
        if row["condition_description"] and row["action_to_be_taken"] and row["due_date"]:
            valid_rows.append(row)

    if not valid_rows:
        flash("No valid rows found. Required columns: Condition Description, Action To Be Taken, Due Date.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    db = get_db()
    db.executemany(
        """
        INSERT INTO compliance_items
        (project_id, condition_description, action_to_be_taken, due_date, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                project["id"],
                row["condition_description"],
                row["action_to_be_taken"],
                row["due_date"],
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
        status = request.form.get("status", "Pending").strip()

        error = None
        if not condition_description:
            error = "Condition description is required."
        elif not action_to_be_taken:
            error = "Action to be taken is required."
        elif not due_date:
            error = "Due date is required."
        elif status not in {"Pending", "Completed"}:
            error = "Invalid status."

        if error is None:
            db = get_db()
            db.execute(
                """
                UPDATE compliance_items
                SET condition_description = ?, action_to_be_taken = ?, due_date = ?, status = ?
                WHERE id = ?
                """,
                (condition_description, action_to_be_taken, due_date, status, item_id),
            )
            db.commit()
            flash("Compliance item updated.", "success")
            return redirect(url_for("project_detail", project_id=item["project_id"]))

        flash(error, "error")

    current_item = get_owned_compliance_item(item_id)
    return render_template("compliance_form.html", item=current_item)


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
