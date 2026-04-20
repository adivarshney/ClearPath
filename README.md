# ClearPath

ClearPath is a web-based environmental compliance tracker for consultants managing approvals like `EC`, `CTE`, `CTO`, and related NOCs.

The product goal is clarity over chaos:

- keep project approvals in one place
- track every compliance condition in a structured way
- surface deadlines, recurrence, and renewal risk clearly
- keep proof documents linked to the exact condition they support

It is designed to replace scattered Excel files, folder-based proof storage, and manual follow-up.

## Demo-Ready Release

Current release: `2.0.0`

This release makes ClearPath significantly more demo-ready with:

- security hardening for forms and authentication
- richer regulatory metadata on compliance items
- bulk cleanup after import
- project-level compliance register export
- audit visibility for compliance changes
- broader approval coverage and approval notes
- recurring schedule preview before save

## Current Features

### Authentication

- Email and password signup/login
- Session-based project access
- CSRF protection on form actions
- Rate limiting on signup and login

### Project Management

- Create, edit, and delete projects
- Capture:
  - project name
  - client name
  - location
- Select one or more approvals per project
- Supported default approval types:
  - `EC`
  - `CTE`
  - `CTO`
  - `HWM`
  - `BMW`
  - `Fire NOC`
  - `Forest`
  - `AERB`
  - `CGWA`
- Record approval issue date and expiry date
- Add approval notes for state-specific rules, renewal notes, or reporting context

### Compliance Tracker

- Add, edit, delete, and bulk edit compliance items
- Fields supported per compliance item:
  - condition description
  - action to be taken
  - due date
  - frequency
  - schedule source
  - status
  - submitted to
  - submission mode
  - responsible person
  - acknowledgment number
  - remarks
- Status options:
  - `Pending`
  - `Completed`
  - `On hold`
  - `Not applicable`

### Recurring Scheduling

- Frequency options:
  - `General`
  - `One-time`
  - `Monthly`
  - `Quarterly`
  - `Half-yearly`
  - `Yearly`
- Use either:
  - a custom due date
  - or an approval grant date like `EC`, `CTE`, `CTO` as the schedule anchor
- Recurring dates are generated from issue date through approval expiry
- Quarterly, monthly, half-yearly, and yearly conditions reflect automatically in:
  - tracker
  - reminders
  - notifications
  - project calendar
- Schedule preview is shown before save so consultants can validate recurring timelines

### Import & Bulk Review

- Import compliance trackers from `.csv` and `.xlsx`
- Supports:
  - structured multi-column sheets
  - single-column condition-only sheets
  - partial uploads without action or due-date columns
- Supported import columns:
  - `Condition Description`
  - `Action To Be Taken`
  - `Due Date`
  - `Status`
  - `Frequency`
  - `Schedule Source`
  - `Submitted To`
  - `Submission Mode`
  - `Responsible Person`
  - `Acknowledgment Number`
  - `Remarks`
- Download a sample import template from the project page
- Bulk edit imported items in one screen for:
  - action
  - due date
  - frequency
  - schedule source
  - status

### Deadlines, Filters, and Alerts

- Derived urgency states:
  - `Pending`
  - `Due in 7 days`
  - `Overdue`
  - `Completed`
  - `On hold`
  - `Not applicable`
- Filter tracker by urgency
- Filter tracker by frequency
- Highlight overdue items clearly
- Show due-in-7-days reminders more visibly
- Header notification control for upcoming due dates within a week

### Calendar & Approval Visibility

- Project calendar showing:
  - compliance due dates
  - approval issue milestones
  - approval expiry milestones
- Calendar range switch:
  - `1 Month`
  - `2 Months`
  - `3 Months`
- NOC cards show issue date, expiry date, expiry state, and approval notes

### Documents

- Upload proof documents against each compliance item
- Supported file types:
  - PDF
  - images
- Add document title
- Add version notes / remarks
- Linked document list shown directly inside each compliance item

### Dashboard

- Total projects
- Pending items
- Completed items
- Overdue items
- Due in 7 days items
- On hold items
- Not applicable items
- Recent projects table

### Export & Audit

- Export a project compliance register to `.xlsx`
- Export includes:
  - project details
  - selected approvals
  - approval notes
  - compliance items
  - next due date
  - urgency state
  - regulatory metadata
  - document count
- Recent activity feed on the project page shows:
  - imports
  - edits
  - status changes
  - document uploads
  - deletions

### UI / UX

- Responsive layout for desktop and smaller screens
- Better behavior under browser zoom
- Lightweight minimalist visual direction
- Clearer calendar rendering and event truncation

## Local Setup

### 1. Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2. Set the app secret

ClearPath now requires a real `SECRET_KEY`.

```bash
export SECRET_KEY="replace-this-with-a-secure-random-string"
```

### 3. Start the app

```bash
python3 app.py
```

### 4. Open locally

[http://127.0.0.1:5000](http://127.0.0.1:5000)

## Data Storage

- SQLite database: `instance/clearpath.db`
- Uploaded proof documents: `instance/uploads/`

## Import Notes

- Single-column Excel uploads are supported
- Action, due date, and other fields can be filled later
- For recurring schedules, attach the item to an approval issue date or provide a custom starting date

## Release History

### Release 2.0.0

Demo-readiness and workflow depth improvements.

- CSRF protection
- login/signup rate limiting
- required `SECRET_KEY`
- broader approval catalog
- approval notes
- bulk edit screen after import
- visible `Next due` support in tracker/export
- document title and version notes
- richer compliance metadata:
  - submitted to
  - submission mode
  - responsible person
  - acknowledgment number
  - remarks
- additional lifecycle statuses:
  - `On hold`
  - `Not applicable`
- project export to Excel
- project activity / audit feed
- recurring schedule preview before save

### Release 1.4.0

Recurring compliance scheduling enhancements.

- monthly/quarterly/half-yearly/yearly schedules
- schedule from approval grant date
- recurring next-due calculation
- recurring deadlines reflected in tracker, reminders, notifications, and calendar
- clearer post-import editing flow for action and due date completion

### Release 1.3.0

Deadline visibility and planning improvements.

- header notification control
- project calendar
- 1/2/3 month calendar views
- improved responsive layout and zoom behavior

### Release 1.2.0

Usability, deadline clarity, and interface improvements.

- approval issue and expiry dates
- optional due dates
- frequency-based tracking
- single-column import support
- urgency and frequency filters
- sample template download
- minimalist UI refresh

### Release 1.1.0

Operational workflow improvements for early consultant use.

- CSV/XLSX import
- project edit/delete
- compliance item edit/delete
- overdue and upcoming reminders

### Release 1.0.0

Initial MVP for environmental compliance tracking.

- user signup/login
- project creation and listing
- per-project compliance tracker
- document upload support
- basic dashboard

## Upcoming Releases

### Planned Release 2.1.0

Consultant communication and reporting polish.

- email reminders for due dates
- downloadable PDF summary / register output
- cleaner reminder settings by project
- better document browsing and proof review

### Planned Release 2.2.0

EC letter upload and extraction review flow.

- EC PDF upload
- metadata extraction preview
- extracted condition preview before save
- suggested action draft generation
- confirm-before-import workflow
- auto-extracted row labeling

### Planned Release 2.3.0

Advanced extraction and enterprise readiness.

- scanned PDF OCR
- duplicate EC detection
- extraction confidence markers
- approval extraction expansion beyond EC
- stronger audit trail depth

### Planned Release 3.0.0

Production-scale collaboration.

- role-based access
- team collaboration
- PostgreSQL migration
- stronger deployment profile
- organization-level reporting workflows

## Current Scope Notes

ClearPath is strong on structured compliance tracking today, but it still does not yet include:

- scanned approval OCR
- automatic condition extraction from uploaded approvals
- automated frequency inference from approval text
- owner-facing portal/reporting workflows
- role-based team access

## Tech Stack

- Python
- Flask
- Flask-WTF
- Flask-Limiter
- SQLite
- OpenPyXL
