# ClearPath

ClearPath is a lightweight environmental compliance tracking tool for consultants managing approvals such as `EC`, `CTE`, and `CTO`.

It is built to replace scattered Excel trackers and folder-based follow-up with a single web workflow for:

- project setup
- approval tracking
- compliance condition management
- recurring deadline visibility
- proof document storage

## Current Features

### Authentication

- Email and password signup/login
- Session-based access to each consultant's projects

### Project Management

- Create and edit projects with:
  - project name
  - client name
  - location
- Select one or more approvals per project:
  - `EC`
  - `CTE`
  - `CTO`
- Record issue date and expiry date for each selected approval
- View all projects in a single list
- Delete projects when no longer needed

### Compliance Tracker

- Add compliance items per project
- Edit and delete compliance items
- Manual status updates
- Optional due date support for non-deadline-based conditions
- Frequency options:
  - `General`
  - `One-time`
  - `Monthly`
  - `Quarterly`
  - `Half-yearly`
  - `Yearly`
- Schedule source support:
  - custom date
  - approval grant date (`EC`, `CTE`, `CTO`)
- Recurring schedule calculation from approval issue date through expiry
- Clear next due visibility for recurring items

### Import & Review

- Import compliance trackers from `.csv` and `.xlsx`
- Support structured multi-column sheets
- Support single-column condition-only sheets
- Tolerate partial uploads where `Action To Be Taken` or `Due Date` is missing
- Download a sample import template directly from the project page
- After import, each condition can be reviewed and updated through `Edit action / due date`

### Status, Deadlines & Review

- Derived urgency states:
  - `Pending`
  - `Due in 7 days`
  - `Overdue`
  - `Completed`
- Filter conditions by urgency
- Filter conditions by frequency
- Overdue reminder section
- Upcoming due in 7 days reminder section
- Header notification control for upcoming deadlines

### Calendar & Approval Visibility

- Project calendar with:
  - compliance due dates
  - approval issue milestones
  - approval expiry milestones
- Calendar view toggle:
  - `1 Month`
  - `2 Months`
  - `3 Months`
- Recurring conditions appear on the calendar based on the selected schedule source
- NOC cards showing issue date, expiry date, and expiry state

### Documents

- Upload proof documents for each compliance item
- Supported formats:
  - PDF
  - images
- Uploaded files are linked back to the relevant compliance item

### Dashboard

- Total projects
- Pending items
- Completed items
- Overdue count
- Due in 7 days count
- Recent projects table

### UI / UX

- Responsive layout for desktop and smaller widths
- Improved behavior under browser zoom
- Lighter minimalist interface
- Cleaner calendar rendering with explicit month-range selection

## Run Locally

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

2. Start the app:

```bash
python3 app.py
```

3. Open:

[http://127.0.0.1:5000](http://127.0.0.1:5000)

## Data Storage

- SQLite database is stored in `instance/clearpath.db`
- Uploaded documents are stored in `instance/uploads/`

## Import Format

The compliance import accepts `.csv` and `.xlsx` files.

Supported columns:

- `Condition Description`
- `Action To Be Taken` (optional)
- `Due Date` (optional)
- `Status` (optional, defaults to `Pending`)
- `Frequency` (optional)

Notes:

- Single-column condition sheets are supported
- Imported rows can later be completed by editing action, due date, schedule source, and frequency

## Release History

### Release 1.0.0

Initial MVP for environmental compliance tracking.

- user signup/login
- project creation and listing
- per-project compliance tracker
- document upload support
- basic dashboard

### Release 1.1.0

Operational workflow improvements for early consultant use.

- CSV/XLSX import
- project edit/delete
- compliance item edit/delete
- overdue and upcoming reminders

### Release 1.2.0

Usability, deadline clarity, and lighter interface improvements.

- approval issue and expiry dates
- optional due dates
- frequency-based tracking
- single-column import support
- urgency and frequency filters
- sample template download
- minimalist UI refresh

### Release 1.3.0

Deadline visibility and planning improvements.

- header notification control
- project calendar
- 1/2/3 month calendar views
- improved responsive layout and zoom behavior

### Release 1.4.0

Recurring compliance scheduling enhancements.

- monthly/quarterly/half-yearly/yearly schedules
- schedule from approval grant date
- recurring next-due calculation
- recurring deadlines reflected in tracker, reminders, notifications, and calendar
- clearer post-import editing flow for action and due date completion

## Current Scope Notes

The current app is focused on structured tracking and manual review. It does not yet include:

- automated EC/CTE/CTO letter extraction from PDF
- OCR for scanned approvals
- bulk upload of letters
- owner-facing reporting workflows
- role-based access

## Tech Stack

- Python
- Flask
- SQLite
- OpenPyXL
