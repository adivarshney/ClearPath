# ClearPath MVP

A simple web-based environmental compliance tracker for consultants handling approvals such as EC, CTE, and CTO.

## Features

- Email and password signup/login
- Project creation with client, location, and approval types
- Project approvals with issue date and expiry date slots
- Per-project compliance tracker with optional due dates, frequency, and manual status updates
- Document uploads for each compliance item
- Dashboard with total projects, pending items, and completed items
- CSV/XLSX import for compliance trackers
- Edit/delete flows for projects and compliance items
- Upcoming and overdue reminder sections
- Status and frequency filters for compliance review

## Run locally

1. Install dependencies:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Start the app:

   ```bash
   python3 app.py
   ```

3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

Uploaded files and the SQLite database are stored under `instance/`.

## Import format

The compliance import accepts `.csv` and `.xlsx` files with these columns:

- `Condition Description`
- `Action To Be Taken` (optional)
- `Due Date` (optional)
- `Status` (optional, defaults to `Pending`)
- `Frequency` (optional)

Single-column condition sheets are also supported.
