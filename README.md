# ClearPath MVP

A simple web-based environmental compliance tracker for consultants handling approvals such as EC, CTE, and CTO.

## Features

- Email and password signup/login
- Project creation with client, location, and approval types
- Per-project compliance tracker with due dates and manual status updates
- Document uploads for each compliance item
- Dashboard with total projects, pending items, and completed items

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
