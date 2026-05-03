# ClearPath

ClearPath is a web-based environmental compliance workspace for consultants managing approvals like `EC`, `CTE`, `CTO`, and related NOCs.

Instead of tracking conditions across scattered Excel files, folders, and reminders, ClearPath keeps the full compliance picture in one place:

- projects and approvals
- approval-specific conditions
- deadlines and recurring schedules
- proof documents and annexures
- portfolio-level risk views
- compliance report outputs

## Current Release

Current branch release state: `v2.2.0`  
Latest commit documented here includes the EC extraction preview workflow, extraction polish, and bulk-edit usability improvements.

## Core Product Flow

1. Create a project and select the approvals being tracked.
2. Add issue / expiry dates and notes for each approval.
3. Open a project and click into a specific approval like `EC`, `CTE`, or `CTO`.
4. Manage that approval through dedicated tabs:
   - `Conditions`
   - `Compliance report`
   - `Documents`
   - `Activity`
5. Track recurring deadlines, upload proof, and export consultant-ready outputs.

## Current Features

### Authentication & Security

- Email + password signup and login
- Session-based access
- CSRF protection on forms
- Rate limiting on signup and login
- Required `SECRET_KEY` environment variable

### Project Management

- Create, edit, and delete projects
- Store:
  - project name
  - client name
  - location
- Track multiple approvals per project
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
- Store approval issue date, expiry date, and notes
- Hide unticked approval date fields on the project form

### Project Overview

- Dedicated project overview page
- Clickable NOC rows for each approval
- Approval cards show:
  - issue date
  - expiry date
  - expiry state
  - condition count
  - overdue / due soon summary
- Compact project calendar with `1m`, `2m`, and `3m` views

### Approval Workspaces

- Separate approval-level pages for `EC`, `CTE`, and `CTO`
- Each approval page includes tabs for:
  - `Conditions`
  - `Compliance report`
  - `Documents`
  - `Activity`
- Conditions are no longer mixed into one flat project-level list
- Every condition is explicitly linked to an approval type

### Compliance Conditions

- Add, edit, delete, and bulk edit conditions
- Supported fields:
  - approval type
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
- Lifecycle status options:
  - `Pending`
  - `Completed`
  - `On hold`
  - `Not applicable`

### Recurring Scheduling & Occurrences

- Frequency options:
  - `General`
  - `One-time`
  - `Monthly`
  - `Quarterly`
  - `Half-yearly`
  - `Yearly`
- Schedule from:
  - custom date
  - or approval grant date like `EC`, `CTE`, `CTO`
- Recurring items generate independent occurrences over time
- Each occurrence can be marked complete separately
- Completing one month / quarter does not affect the next one
- Condition cards show recurrence timelines and a summary like:
  - overdue count
  - completed count
  - upcoming count

### Deadlines, Colour States, & Alerts

- Derived urgency states:
  - `Pending`
  - `Due in 7 days`
  - `Overdue`
  - `Completed`
  - `On hold`
  - `Not applicable`
- Condition due dates are colour-coded:
  - red for overdue
  - amber for due soon
  - green for healthy future dates
- Header notifications for upcoming due dates
- Overdue view grouped by project
- Overdue rows show NOC badges
- Project blocks on overdue view are collapsible

### Calendar

- Project-level deadline calendar
- Cross-project calendar screen
- Calendar event colours reflect urgency:
  - red
  - amber
  - green
- Approval issue and expiry milestones appear alongside compliance deadlines

### Import & Bulk Cleanup

- Import trackers from `.csv` and `.xlsx`
- Supports:
  - structured multi-column sheets
  - single-column condition-only uploads
  - uploads missing action / due-date columns
- Supports import column aliases for approval type, condition, action, due date, status, frequency, schedule source, and metadata
- Download sample import CSV
- Bulk edit imported conditions in one screen
- Bulk edit layout now keeps long condition text readable and action fields usable on desktop widths

### EC Letter Extraction

- EC-only PDF upload from the EC approval workspace
- Digital PDF text extraction using `pypdf`
- Review-before-import extraction flow
- Extracted metadata preview for:
  - EC reference number
  - issue date
  - validity text
  - proponent name
  - location text
- Editable extracted condition list before save
- Per-row select / deselect before import
- Imported conditions are tagged with extraction source and batch linkage for traceability
- Report-style PDF detection in preview
- Heuristic cleanup to keep leading directive condition text and trim common compliance-response wording

### Compliance Reports

- Dedicated compliance report tab per approval
- Period-based response grid
- Supports period selection for export
- Save response text by condition and reporting period
- Attach supporting files to each response
- Stored attachments become annexures in the report flow
- Export selected columns to:
  - Excel
  - PDF

### Documents

- Upload proof documents to action-to-be-taken conditions
- Support for:
  - PDF
  - image uploads
- Add document title and version notes
- Approval-level documents tab shows:
  - action-to-be-taken uploads
  - compliance report annexures
- Global documents page also includes report annexures

### Activity & Audit

- Approval-specific activity timeline
- Tracks:
  - imports
  - edits
  - status changes
  - occurrence completion
  - due date changes
  - document uploads
  - deletions

### Portfolio Views

- Approval portfolio pages for `EC`, `CTE`, and `CTO`
- Structured sections:
  - `Needs attention`
  - `Healthy`
  - `No record`
- Summary cards at the top for:
  - approval coverage
  - valid count
  - expiring / expired count
  - overdue condition count

### Dashboard & Reports

- Dashboard cards for:
  - total projects
  - pending
  - overdue
  - completed
- Recent projects view
- Cross-project report shell
- Project compliance register export to `.xlsx`

## Local Setup

### 1. Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2. Set the app secret

```bash
export SECRET_KEY="replace-this-with-a-secure-random-string"
```

### 3. Start the app

```bash
python3 app.py
```

### 4. Open locally

[http://127.0.0.1:5000](http://127.0.0.1:5000)

## Storage

- SQLite database: `instance/clearpath.db`
- Uploaded files: `instance/uploads/`

## Import Notes

- Single-column Excel uploads are supported
- `Approval Type` can be included in imports
- If `Approval Type` is not present, import can fall back to the selected approval context
- Action / due date / schedule fields can be completed later

## Release History

### v2.2.0

EC extraction onboarding and workflow polish.

- EC letter PDF upload from the EC workspace
- draft extraction batches with preview-before-import
- editable extracted metadata and condition rows
- per-row include / exclude before import
- imported conditions linked back to extraction batches
- report-style extraction cleanup to reduce response-text leakage
- extraction preview summary cards and warning state for report-like PDFs
- bulk edit screen layout cleanup for long conditions and action text
- local testing hardening for the EC extraction flow

### v2.1.0

Approval workspaces and reporting flow.

- project overview now links into dedicated approval pages
- separate `EC`, `CTE`, `CTO` workspaces
- conditions tied to approvals instead of one mixed list
- recurring conditions generate independent occurrences
- occurrence-level completion support
- compliance report tab with annexure attachments
- compliance report export to Excel and PDF
- approval-specific documents and activity views
- overdue screen with NOC badges and collapsible project groups
- approval portfolio pages with `Needs attention`, `Healthy`, and `No record`
- calendar urgency colour coding
- hidden approval date fields when approvals are unticked

### v2.0.0

Demo-readiness and workflow depth improvements.

- CSRF protection
- login/signup rate limiting
- required `SECRET_KEY`
- broader approval catalog
- approval notes
- bulk edit screen after import
- visible next-due support in tracker/export
- document title and version notes
- richer compliance metadata
- project export to Excel
- stronger audit visibility

### v1.0.0

Initial MVP.

- authentication
- project creation
- approval selection
- compliance tracker
- file uploads
- basic dashboard

## Upcoming / Next Release Candidates

- duplicate EC detection using reference number + issue date
- OCR support for scanned approval letters
- CTE / CTO extraction after EC matures further
- configurable approval master data instead of only hardcoded defaults
- stronger reports page with portfolio exports
- reminders beyond in-app notifications
