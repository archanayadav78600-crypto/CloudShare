# CloudShare — Secure Cloud File Sharing System

CloudShare is a small web application for uploading, listing, searching, downloading and deleting files. This is **Phase 1**: everything runs locally on your laptop with files stored in the `uploads/` folder. Phase 2 will move storage to AWS S3 (Boto3) and add temporary pre-signed sharing links.

> **Important:** this version has **no login and no user accounts**. Anyone who can open the site can see, download and delete every file. It is a single-user local demo, not a secure multi-user system. Sharing is clearly marked as "not available" until the AWS phase.

## Features

- Responsive blue-and-white dashboard (sidebar, cards, works on desktop and mobile)
- Dashboard: total files, storage used, file categories, recent files
- Upload by file picker or drag-and-drop, with a progress bar per file
- Server-side validation: extension allowlist, 10 MB limit (configurable), real-content check
- File list with name, type, size, upload date; search by name; filter by category
- Download (always as an attachment) and delete (with confirmation dialog)
- Duplicate names are never overwritten: `report.pdf` becomes `report (1).pdf`
- Sharing button that honestly says sharing is unavailable locally

## Technology stack

| Layer | Technology |
|-------|-----------|
| Frontend | HTML5, CSS3, JavaScript (fetch / XMLHttpRequest) |
| Backend | Python 3, Flask (JSON REST API) |
| Storage (now) | Local folder `uploads/` + `data/files.json` for metadata |
| Storage (later) | AWS S3 with Boto3, IAM least-privilege user |
| Version control | Git and GitHub |

## Folder structure

```
CloudShare/
├── app.py            Flask app: routes, validation flow, error handlers
├── storage.py        LocalStorage (file bytes) + MetadataStore (JSON records)
├── validators.py     Safe filenames, extension allowlist, content checks
├── test_app.py       Automated tests (unittest)
├── requirements.txt  Python dependencies
├── README.md         This file
├── .gitignore        Keeps venv, .env, uploads and data out of GitHub
├── .env.example      Sample settings (copy to .env)
├── templates/
│   └── index.html    The dashboard page
├── static/
│   ├── style.css     Styling and responsive layout
│   └── script.js     Frontend logic
├── uploads/          Uploaded files (created automatically)
└── data/             files.json metadata (created automatically)
```

Extra files and why they exist: `storage.py` and `validators.py` keep `app.py` short and make the S3 switch easy; `test_app.py` lets you prove the security rules work; `data/files.json` stores original names, sizes and dates.

## Architecture

```
Browser (index.html + style.css + script.js)
        |  JSON over HTTP
        v
Flask (app.py) -- validators.py (safe names, type checks)
        |
        v
storage.py: LocalStorage (uploads/)   MetadataStore (data/files.json)
```

Each file gets a random id (`uuid4`). On disk it is saved as `<id>.<ext>`, so the user's filename never decides where a file is written. The original (sanitised) name is kept only in the metadata and used for display and as the download name.

In Phase 2, an `S3Storage` class with the same methods (`save`, `exists`, `delete`, `download_response`) replaces `LocalStorage`, and the frontend does not change.

## API

| Method and URL | Purpose |
|----------------|---------|
| `GET /` | Dashboard page |
| `GET /api/stats` | Totals, category counts, recent files |
| `GET /api/files?search=&type=` | List files (`type`: all, images, documents, spreadsheets, presentations, archives) |
| `POST /api/upload` | Upload one file (form field `file`) |
| `GET /api/download/<id>` | Download a file |
| `DELETE /api/files/<id>` | Delete a file |
| `POST /api/share/<id>` | Returns 501: sharing not available locally |

## Prerequisites

- Windows with Python 3.9 or newer (check with `python --version`)
- VS Code (optional but recommended)
- Git (optional, for GitHub)

## Installation (Windows PowerShell)

```powershell
# 1. Go to the folder where you keep projects (example: Desktop)
cd $HOME\Desktop

# 2. Create the project folder (skip if you already copied the files there)
mkdir CloudShare
cd CloudShare

# 3. Create a virtual environment
python -m venv venv

# 4. Activate it (your prompt will start with "(venv)")
.\venv\Scripts\Activate.ps1

# 5. Install dependencies
pip install -r requirements.txt
```

If PowerShell says "running scripts is disabled", run this once and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

If `python` is not found, try `py -m venv venv` instead.

Optional: copy the sample settings (only needed if you want to change them):

```powershell
Copy-Item .env.example .env
```

## Run

```powershell
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Stop the server with `Ctrl + C`.

## Run the automated tests

```powershell
python -m unittest -v
```

## Manual testing checklist

Create some test files first (PowerShell):

```powershell
"hello cloudshare" | Set-Content hello.txt
"just text pretending to be an image" | Set-Content fake.png
"program" | Set-Content virus.exe
('a' * 11534336) | Set-Content big.txt        # about 11 MB
```

| # | Feature | Steps | Expected result |
|---|---------|-------|-----------------|
| 1 | Upload allowed file | Drag `hello.txt` into the upload area | Green toast, file appears, cards update |
| 2 | Reject unsupported type | Upload `virus.exe` | Red error: type not allowed |
| 3 | Reject fake image | Upload `fake.png` | Red error: content does not look like a real .png |
| 4 | Reject large file | Upload `big.txt` | Red error: larger than the 10 MB limit |
| 5 | Listing | Upload 2-3 files | Name, type, size, date shown; empty state shown when none |
| 6 | Search | Type part of a name | List filters as you type |
| 7 | Filter | Click Documents / Images | Only that category is shown |
| 8 | Download | Click Download on `hello.txt` | Browser saves the same content |
| 9 | Duplicate names | Upload `hello.txt` again | Second file is named `hello (1).txt` |
| 10 | Delete | Click Delete, then Cancel, then Delete and confirm | Cancel keeps it; confirm removes it and updates cards |
| 11 | Dashboard stats | Compare cards with the list | Total files and storage match |
| 12 | Missing file | Delete a file from the `uploads` folder by hand, refresh | It disappears from list and stats; no crash |
| 13 | Path traversal | `curl.exe -F "file=@hello.txt;filename=../../evil.txt" http://127.0.0.1:5000/api/upload` | Saved as `evil.txt` inside `uploads` only |
| 14 | Arbitrary file access | Open `http://127.0.0.1:5000/api/download/..%2Fapp.py` | 404 JSON error, `app.py` is not returned |
| 15 | Sharing | Click Share | Dialog says sharing is not available yet |

## Security notes

- Filenames are sanitised with `secure_filename`, and files are stored under random ids.
- Extensions are checked against an allowlist; the first bytes of the file are checked too. Browser-sent MIME types are ignored.
- Size is limited by Flask (`MAX_CONTENT_LENGTH`) and checked again while saving.
- Downloads use `as_attachment=True` plus `X-Content-Type-Options: nosniff`.
- File ids must match a strict pattern, and storage paths are checked to stay inside `uploads/`.
- Responses never contain server paths; unexpected errors return a generic message and are logged on the server.
- The frontend inserts file names with `textContent` (no HTML injection), and a Content-Security-Policy header is set.

## Common errors and fixes

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'flask'` | Activate the venv and run `pip install -r requirements.txt` |
| `Address already in use` / port 5000 busy | Set `PORT=5001` in `.env` and open http://127.0.0.1:5001 |
| Page shows no styling | Hard refresh with `Ctrl + F5`; make sure `static/style.css` exists |
| "Cannot reach the server" toast | The Flask terminal was closed; run `python app.py` again |
| Upload of a valid file rejected as wrong type | The file content must really match its extension (a renamed file will be rejected) |

## Known limitations

- No authentication or authorisation: all files are visible to anyone who can open the site.
- File type checking looks only at the first bytes; it is not a virus scanner.
- Metadata lives in a JSON file, fine for one user but not for many users or several server processes.
- Flask's built-in server is for development only, not production.
- Sharing is not implemented until the AWS S3 phase. Anyone holding a valid pre-signed URL can open the file until it expires, so such links must be shared carefully.

## GitHub

```powershell
git init
git add .
git commit -m "CloudShare phase 1: local file sharing app"
```

`.gitignore` already excludes `venv/`, `.env`, `uploads/` contents and `data/`.
