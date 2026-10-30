"""
test_app.py - Automated tests for the CloudShare API.

Run from the project folder:   python -m unittest -v

Every test uses a temporary folder, so your real uploads/ folder and
data/files.json are never touched.
"""

import io
import os
import shutil
import tempfile
import unittest

from app import create_app

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class CloudShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.upload_dir = os.path.join(self.tmp, "uploads")
        self.app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": self.upload_dir,
                "METADATA_FILE": os.path.join(self.tmp, "data", "files.json"),
                "MAX_UPLOAD_MB": 0.001,  # about 1 KB, so size tests stay tiny
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, content, filename):
        return self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    # ---------- upload ----------
    def test_upload_allowed_file(self):
        response = self.upload(b"hello cloud", "notes.txt")
        self.assertEqual(response.status_code, 201)
        data = response.get_json()["file"]
        self.assertEqual(data["name"], "notes.txt")
        self.assertEqual(data["size"], 11)
        self.assertNotIn("stored_name", data)
        self.assertNotIn(self.tmp, response.get_data(as_text=True))

    def test_reject_unsupported_extension(self):
        response = self.upload(b"MZ fake program", "virus.exe")
        self.assertEqual(response.status_code, 415)

    def test_reject_file_without_extension(self):
        self.assertEqual(self.upload(b"data", "README").status_code, 415)

    def test_reject_fake_image(self):
        response = self.upload(b"this is just text", "photo.png")
        self.assertEqual(response.status_code, 415)

    def test_accept_real_png(self):
        self.assertEqual(self.upload(PNG_BYTES, "pic.png").status_code, 201)

    def test_reject_empty_file(self):
        self.assertEqual(self.upload(b"", "empty.txt").status_code, 400)

    def test_reject_missing_file_field(self):
        response = self.client.post("/api/upload", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)

    def test_reject_file_over_limit(self):
        response = self.upload(b"a" * 2000, "big.txt")  # limit is about 1 KB
        self.assertEqual(response.status_code, 413)
        self.assertEqual(os.listdir(self.upload_dir), [])  # nothing left behind

    def test_reject_request_over_hard_limit(self):
        response = self.upload(b"a" * (3 * 1024 * 1024), "huge.txt")
        self.assertEqual(response.status_code, 413)

    def test_path_traversal_filename_is_neutralised(self):
        response = self.upload(b"x", "../../evil.txt")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["file"]["name"], "evil.txt")
        # The file must exist ONLY inside the uploads folder.
        self.assertEqual(len(os.listdir(self.upload_dir)), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_non_ascii_name_keeps_extension(self):
        response = self.upload(b"x", "नोट्स.txt")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["file"]["name"].endswith(".txt"))

    def test_duplicate_name_is_not_overwritten(self):
        first = self.upload(b"one", "report.txt").get_json()["file"]
        second = self.upload(b"two", "report.txt").get_json()["file"]
        self.assertEqual(second["name"], "report (1).txt")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(os.listdir(self.upload_dir)), 2)

    # ---------- list, search, filter, stats ----------
    def test_list_search_filter_and_stats(self):
        self.upload(b"abc", "alpha.txt")
        self.upload(PNG_BYTES, "beta.png")

        everything = self.client.get("/api/files").get_json()
        self.assertEqual(everything["count"], 2)

        search = self.client.get("/api/files?search=ALP").get_json()
        self.assertEqual([f["name"] for f in search["files"]], ["alpha.txt"])

        images = self.client.get("/api/files?type=images").get_json()
        self.assertEqual([f["name"] for f in images["files"]], ["beta.png"])
        self.assertEqual(images["total_count"], 2)

        self.assertEqual(self.client.get("/api/files?type=bogus").status_code, 400)

        stats = self.client.get("/api/stats").get_json()
        self.assertEqual(stats["total_files"], 2)
        self.assertEqual(stats["total_size"], 3 + len(PNG_BYTES))
        self.assertEqual(stats["categories"]["images"]["count"], 1)
        self.assertEqual(stats["categories"]["documents"]["count"], 1)
        self.assertEqual(len(stats["recent"]), 2)

    def test_empty_state(self):
        data = self.client.get("/api/files").get_json()
        self.assertEqual(data["files"], [])
        self.assertEqual(self.client.get("/api/stats").get_json()["total_files"], 0)

    # ---------- download ----------
    def test_download_returns_same_bytes_as_attachment(self):
        file_id = self.upload(b"secret notes", "notes.txt").get_json()["file"]["id"]
        response = self.client.get(f"/api/download/{file_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"secret notes")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("notes.txt", response.headers["Content-Disposition"])
        response.close()

    def test_download_invalid_and_unknown_ids(self):
        for bad_id in ("abc", "../app.py", "0" * 32):
            self.assertEqual(self.client.get(f"/api/download/{bad_id}").status_code, 404)

    def test_download_when_file_missing_on_disk(self):
        file_id = self.upload(b"data", "gone.txt").get_json()["file"]["id"]
        for name in os.listdir(self.upload_dir):
            os.remove(os.path.join(self.upload_dir, name))
        self.assertEqual(self.client.get(f"/api/download/{file_id}").status_code, 404)
        # The broken entry also disappears from the list and the statistics.
        self.assertEqual(self.client.get("/api/files").get_json()["count"], 0)
        self.assertEqual(self.client.get("/api/stats").get_json()["total_files"], 0)

    # ---------- delete ----------
    def test_delete_file(self):
        file_id = self.upload(b"bye", "bye.txt").get_json()["file"]["id"]
        self.assertEqual(self.client.delete(f"/api/files/{file_id}").status_code, 200)
        self.assertEqual(os.listdir(self.upload_dir), [])
        self.assertEqual(self.client.get("/api/files").get_json()["count"], 0)
        self.assertEqual(self.client.delete(f"/api/files/{file_id}").status_code, 404)

    def test_delete_rejects_arbitrary_paths(self):
        self.assertEqual(self.client.delete("/api/files/..%2Fapp.py").status_code, 404)
        self.assertEqual(self.client.delete("/api/files/not-an-id").status_code, 404)

    # ---------- share, errors, headers ----------
    def test_share_is_honestly_unavailable(self):
        file_id = self.upload(b"x", "share.txt").get_json()["file"]["id"]
        response = self.client.post(f"/api/share/{file_id}")
        self.assertEqual(response.status_code, 501)
        self.assertFalse(response.get_json()["available"])
        self.assertEqual(self.client.post("/api/share/" + "0" * 32).status_code, 404)

    def test_unknown_route_returns_json_404(self):
        response = self.client.get("/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())

    def test_wrong_method_returns_405(self):
        self.assertEqual(self.client.get("/api/upload").status_code, 405)

    def test_index_page_and_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CloudShare", response.data)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
