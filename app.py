"""
CloudShare - Secure Cloud File Sharing System (Phase 1: local version)

Flask backend. It exposes a small JSON API used by static/script.js:

    GET    /                      -> the dashboard page
    GET    /api/stats             -> totals, category breakdown, recent files
    GET    /api/files             -> list files (?search=...&type=...)
    POST   /api/upload            -> upload ONE file (form field "file")
    GET    /api/download/<id>     -> download a file
    DELETE /api/files/<id>        -> delete a file
    POST   /api/share/<id>        -> sharing (not available locally, returns 501)

Run:  python app.py   ->  http://127.0.0.1:5000

IMPORTANT: this version has NO login. Anyone who can open the site can see,
download and delete every file. It is a single-user local demo.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from storage import FileTooLargeError, LocalStorage, MetadataStore, StorageError
from validators import (
    ALLOWED_EXTENSIONS,
    CATEGORY_EXTENSIONS,
    HEADER_BYTES,
    get_category,
    is_valid_file_id,
    make_safe_display_name,
    matches_signature,
)

# Load settings from a local .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
# Multipart form data adds a little overhead on top of the file itself.
REQUEST_OVERHEAD_BYTES = 1024 * 1024
RECENT_FILES_LIMIT = 5


def read_max_upload_mb():
    """Read MAX_UPLOAD_MB from the environment (default 10)."""
    try:
        value = float(os.environ.get("MAX_UPLOAD_MB", "10"))
    except ValueError:
        return 10.0
    return value if value > 0 else 10.0


def format_size(num_bytes):
    """Turn a byte count into a readable string such as '2.4 MB'."""
    size = float(num_bytes)
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"


def create_app(test_config=None):
    """Application factory: builds and configures the Flask app."""
    app = Flask(__name__)
    app.config.update(
        MAX_UPLOAD_MB=read_max_upload_mb(),
        UPLOAD_FOLDER=str(BASE_DIR / "uploads"),
        METADATA_FILE=str(BASE_DIR / "data" / "files.json"),
    )
    if test_config:
        app.config.update(test_config)

    max_bytes = int(app.config["MAX_UPLOAD_MB"] * 1024 * 1024)
    max_label = f"{app.config['MAX_UPLOAD_MB']:g} MB"
    # Flask rejects any request bigger than this with HTTP 413 before we
    # even look at the file. We also re-check the exact size while saving.
    app.config["MAX_CONTENT_LENGTH"] = max_bytes + REQUEST_OVERHEAD_BYTES

    storage = LocalStorage(app.config["UPLOAD_FOLDER"])
    store = MetadataStore(app.config["METADATA_FILE"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def error_response(message, status):
        return jsonify({"error": message}), status

    def serialize(record):
        """Public view of a record. Never includes server paths or stored names."""
        return {
            "id": record["id"],
            "name": record["name"],
            "extension": record["extension"],
            "category": record["category"],
            "size": record["size"],
            "size_readable": format_size(record["size"]),
            "uploaded_at": record["uploaded_at"],
        }

    def find_record(file_id):
        """Find a managed file by id. Returns None for unknown or malformed ids."""
        if not is_valid_file_id(file_id):
            return None
        return store.get(file_id)

    def visible_records():
        """
        Records whose file still exists on disk, newest first.
        (If someone deletes a file manually from uploads/, it disappears from
        the list and the statistics instead of showing a broken entry.)
        """
        records = [r for r in store.list_records() if storage.exists(r["stored_name"])]
        records.sort(key=lambda r: r["uploaded_at"], reverse=True)
        return records

    # ------------------------------------------------------------------
    # Security headers for every response
    # ------------------------------------------------------------------
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        return response

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            max_upload_bytes=max_bytes,
            max_upload_label=max_label,
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        )

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @app.get("/api/stats")
    def get_stats():
        records = visible_records()
        categories = {name: {"count": 0, "size": 0} for name in CATEGORY_EXTENSIONS}
        for record in records:
            bucket = categories.setdefault(record["category"], {"count": 0, "size": 0})
            bucket["count"] += 1
            bucket["size"] += record["size"]
        total_size = sum(record["size"] for record in records)
        return jsonify(
            {
                "total_files": len(records),
                "total_size": total_size,
                "total_size_readable": format_size(total_size),
                "categories": categories,
                "recent": [serialize(r) for r in records[:RECENT_FILES_LIMIT]],
            }
        )

    @app.get("/api/files")
    def list_files():
        search = request.args.get("search", "").strip().lower()
        file_type = request.args.get("type", "all").strip().lower()
        if file_type != "all" and file_type not in CATEGORY_EXTENSIONS:
            return error_response("Unknown file type filter.", 400)

        records = visible_records()
        matching = [
            record
            for record in records
            if (not search or search in record["name"].lower())
            and (file_type == "all" or record["category"] == file_type)
        ]
        return jsonify(
            {
                "files": [serialize(r) for r in matching],
                "count": len(matching),
                "total_count": len(records),
            }
        )

    @app.post("/api/upload")
    def upload_file():
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return error_response("No file was selected.", 400)

        # 1. Never trust the client's file name: build a safe one.
        display_name, extension = make_safe_display_name(uploaded.filename)
        if not extension:
            return error_response("The file must have an extension, for example .pdf.", 415)
        if extension not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            return error_response(
                f"Files of type .{extension} are not allowed. Allowed types: {allowed}.", 415
            )

        # 2. Never trust the extension alone: look at the real content.
        header = uploaded.stream.read(HEADER_BYTES)
        uploaded.stream.seek(0)
        if not header:
            return error_response("The file is empty.", 400)
        if not matches_signature(extension, header):
            return error_response(
                f"The content of this file does not look like a real .{extension} file.", 415
            )

        # 3. Save under a random id. The user-supplied name never touches disk.
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}.{extension}"
        try:
            size = storage.save(uploaded.stream, stored_name, max_bytes)
        except FileTooLargeError:
            return error_response(f"The file is larger than the {max_label} limit.", 413)
        except StorageError:
            app.logger.exception("Storage error while saving upload")
            return error_response("The file could not be stored.", 500)

        record = {
            "id": file_id,
            "name": display_name,
            "stored_name": stored_name,
            "extension": extension,
            "category": get_category(extension),
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            record = store.add(record)
        except Exception:
            storage.delete(stored_name)  # do not leave an untracked file behind
            raise

        return jsonify({"message": f"'{record['name']}' uploaded.", "file": serialize(record)}), 201

    @app.get("/api/download/<file_id>")
    def download_file(file_id):
        record = find_record(file_id)
        if record is None:
            return error_response("File not found.", 404)
        if not storage.exists(record["stored_name"]):
            return error_response("This file is no longer available in storage.", 404)
        return storage.download_response(record["stored_name"], record["name"])

    @app.delete("/api/files/<file_id>")
    def delete_file(file_id):
        record = find_record(file_id)
        if record is None:
            return error_response("File not found.", 404)
        storage.delete(record["stored_name"])
        store.remove(file_id)
        return jsonify({"message": f"'{record['name']}' was deleted."})

    @app.post("/api/share/<file_id>")
    def share_file(file_id):
        if find_record(file_id) is None:
            return error_response("File not found.", 404)
        # Honest answer: we do not create fake links that cannot work.
        return (
            jsonify(
                {
                    "available": False,
                    "error": (
                        "Sharing is not available in the local version. "
                        "It will be added with AWS S3 pre-signed URLs."
                    ),
                }
            ),
            501,
        )

    # ------------------------------------------------------------------
    # Error handlers: always answer with JSON, never leak internal details.
    # ------------------------------------------------------------------
    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(_error):
        return error_response(f"The file is larger than the {max_label} limit.", 413)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return error_response(error.description or error.name, error.code)

    @app.errorhandler(Exception)
    def handle_unexpected_error(_error):
        app.logger.exception("Unexpected server error")
        return error_response("Something went wrong on the server. Please try again.", 500)

    return app


app = create_app()

if __name__ == "__main__":
    # debug=False by default: Flask's debugger lets visitors run code if
    # exposed, so only enable it (FLASK_DEBUG=1) on your own laptop.
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
