"""
storage.py - Where file bytes and file metadata live.

Two small classes:

* LocalStorage  - saves/deletes/serves file bytes in the local uploads folder.
* MetadataStore - remembers the original name, size and upload date of every
                  file in a small JSON file.

Why separate them? In Phase 2 we will write an S3Storage class with the SAME
methods (save, exists, delete, download_response). app.py and the frontend will
not need to change. This is called a "storage abstraction".
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from flask import send_from_directory

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024  # read/write uploads in 64 KB pieces


class StorageError(Exception):
    """Something went wrong while storing or reading a file."""


class FileTooLargeError(StorageError):
    """The uploaded file is bigger than the allowed maximum."""


class LocalStorage:
    """Stores files inside one folder on the local disk."""

    def __init__(self, upload_dir):
        self.upload_dir = Path(upload_dir).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, stored_name):
        """
        Build the full path for a stored file name and make sure it stays
        inside the uploads folder (protection against path traversal).
        """
        path = (self.upload_dir / stored_name).resolve()
        if path.parent != self.upload_dir:
            raise StorageError("Invalid storage key.")
        return path

    def save(self, stream, stored_name, max_bytes):
        """
        Copy the uploaded stream to disk. Returns the number of bytes written.
        Raises FileTooLargeError (and removes the partial file) if the stream
        is bigger than max_bytes. Never overwrites an existing file.
        """
        path = self._path_for(stored_name)
        try:
            output = open(path, "xb")  # "x" = fail if the file already exists
        except FileExistsError as error:
            raise StorageError("Storage key already exists.") from error

        written = 0
        try:
            with output:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise FileTooLargeError("File is too large.")
                    output.write(chunk)
        except BaseException:
            # The file is closed here, so it is safe to delete on Windows too.
            path.unlink(missing_ok=True)
            raise
        return written

    def exists(self, stored_name):
        try:
            return self._path_for(stored_name).is_file()
        except StorageError:
            return False

    def delete(self, stored_name):
        """Delete a stored file. Does nothing if it is already gone."""
        self._path_for(stored_name).unlink(missing_ok=True)

    def download_response(self, stored_name, download_name):
        """
        Build the Flask response that sends the file to the browser.
        as_attachment=True forces a download, so a file can never be
        displayed/executed inside our website.
        """
        self._path_for(stored_name)  # validates the name
        return send_from_directory(
            self.upload_dir,
            stored_name,
            as_attachment=True,
            download_name=download_name,
        )


class MetadataStore:
    """
    Keeps file records in a JSON file: { file_id: {record}, ... }.

    A lock protects against two requests writing at the same moment, and
    writes are "atomic" (write to a temp file, then replace) so the JSON file
    cannot be left half-written if the program stops suddenly.
    """

    def __init__(self, json_path):
        self.path = Path(json_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("metadata root must be an object")
            return data
        except (OSError, ValueError) as error:
            # Keep the broken file for inspection and start fresh.
            logger.error("Metadata file is unreadable (%s). Starting empty.", error)
            try:
                os.replace(self.path, self.path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            return {}

    def _write(self, data):
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
        ) as temp_file:
            json.dump(data, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_file.name, self.path)

    @staticmethod
    def _unique_name(name, taken_names_lower):
        """'report.pdf' -> 'report (1).pdf' if the name is already used."""
        if name.lower() not in taken_names_lower:
            return name
        stem, _, extension = name.rpartition(".")
        counter = 1
        while True:
            candidate = f"{stem} ({counter}).{extension}"
            if candidate.lower() not in taken_names_lower:
                return candidate
            counter += 1

    def list_records(self):
        with self._lock:
            return list(self._read().values())

    def get(self, file_id):
        with self._lock:
            return self._read().get(file_id)

    def add(self, record):
        """
        Save a new record. If another file already has the same display name,
        the name is changed to 'name (1).ext' so nothing is silently replaced.
        """
        with self._lock:
            data = self._read()
            taken = {item["name"].lower() for item in data.values()}
            record["name"] = self._unique_name(record["name"], taken)
            data[record["id"]] = record
            self._write(data)
            return record

    def remove(self, file_id):
        """Remove a record. Returns the removed record or None."""
        with self._lock:
            data = self._read()
            record = data.pop(file_id, None)
            if record is not None:
                self._write(data)
            return record
