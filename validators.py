"""
validators.py - Input validation helpers for CloudShare.

Everything a user sends us (file name, extension, file content) is untrusted.
This module turns that untrusted input into safe values, or rejects it.
"""

import re

from werkzeug.utils import secure_filename

# File categories shown on the dashboard. The extensions listed here are the
# ONLY extensions the server accepts (an "allowlist" is safer than a blocklist).
CATEGORY_EXTENSIONS = {
    "images": ("png", "jpg", "jpeg", "gif"),
    "documents": ("pdf", "txt", "docx"),
    "spreadsheets": ("xlsx", "csv"),
    "presentations": ("pptx",),
    "archives": ("zip",),
}

ALLOWED_EXTENSIONS = frozenset(
    ext for extensions in CATEGORY_EXTENSIONS.values() for ext in extensions
)

EXTENSION_TO_CATEGORY = {
    ext: category
    for category, extensions in CATEGORY_EXTENSIONS.items()
    for ext in extensions
}

# A file id is a UUID4 written as 32 lowercase hex characters.
FILE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
EXTENSION_PATTERN = re.compile(r"[a-z0-9]{1,10}")
MAX_STEM_LENGTH = 100

# How many bytes from the start of the file we inspect to check its real type.
HEADER_BYTES = 8192

ZIP_SIGNATURE = b"PK\x03\x04"  # .docx, .xlsx, .pptx are ZIP files internally
BINARY_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "zip": (ZIP_SIGNATURE,),
    "docx": (ZIP_SIGNATURE,),
    "xlsx": (ZIP_SIGNATURE,),
    "pptx": (ZIP_SIGNATURE,),
}
TEXT_EXTENSIONS = frozenset({"txt", "csv"})


def is_valid_file_id(file_id):
    """Return True only for ids that look exactly like the ids we generate."""
    return bool(file_id) and FILE_ID_PATTERN.fullmatch(file_id) is not None


def get_extension(raw_filename):
    """
    Return the lowercase extension of a client-supplied filename (without dot).
    Returns "" when there is no usable extension.
    """
    if not raw_filename or "." not in raw_filename:
        return ""
    extension = raw_filename.rsplit(".", 1)[1].strip().lower()
    return extension if EXTENSION_PATTERN.fullmatch(extension) else ""


def make_safe_display_name(raw_filename):
    """
    Turn a client-supplied filename into a safe display name.

    Returns (display_name, extension). The extension may be "" if the
    filename had none - the caller must reject that case.

    Example: "../../etc/passwd.txt" -> ("etc_passwd.txt", "txt")

    Note: the display name is only for showing to the user. On disk the file
    is stored under a random id, so even a weird name can never choose where
    the file is written.
    """
    extension = get_extension(raw_filename)
    stem = raw_filename.rsplit(".", 1)[0] if extension else raw_filename
    # secure_filename removes slashes, "..", spaces and non-ASCII characters.
    safe_stem = secure_filename(stem)[:MAX_STEM_LENGTH].strip("._-") or "file"
    display_name = f"{safe_stem}.{extension}" if extension else safe_stem
    return display_name, extension


def get_category(extension):
    """Map an extension to a dashboard category."""
    return EXTENSION_TO_CATEGORY.get(extension, "other")


def matches_signature(extension, header):
    """
    Check that the first bytes of the file really look like the claimed type.

    We never trust the extension or the MIME type sent by the browser, so we
    look at the file content itself ("magic bytes"). This is a basic check,
    not a virus scan.
    """
    if not header:
        return False
    if extension == "pdf":
        return b"%PDF-" in header[:1024]
    if extension in TEXT_EXTENSIONS:
        # Plain text files must not contain NUL bytes.
        return b"\x00" not in header
    signatures = BINARY_SIGNATURES.get(extension)
    if signatures is None:
        return False
    return header.startswith(signatures)
