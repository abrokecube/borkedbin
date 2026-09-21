import asyncio
import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import main_public
from database import ApiKey, ContentItem


class FakeSession:
    def __init__(self, *, fail_commit=False):
        self.items = {}
        self.pending = None
        self.fail_commit = fail_commit
        self.rolled_back = False

    def get(self, model, item_id):
        return self.items.get(item_id)

    def add(self, item):
        self.pending = item
        self.items[item.id] = item

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")

    def rollback(self):
        self.rolled_back = True


class TrackedUpload:
    def __init__(self, content, filename, content_type):
        self._file = io.BytesIO(content)
        self.filename = filename
        self.content_type = content_type
        self.read_sizes = []
        self.closed = False

    async def read(self, size=-1):
        self.read_sizes.append(size)
        return self._file.read(size)

    async def close(self):
        self.closed = True
        self._file.close()


def request_for(path):
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1234),
            "server": ("testserver", 80),
        }
    )


class UploadSecurityTests(unittest.TestCase):
    def setUp(self):
        self.api_key = ApiKey(key="test", allowed_types="*")

    def test_storage_name_is_server_controlled_and_raw_download_is_hardened(self):
        payload = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mP8/x8AAusB9Wl5nW0AAAAASUVORK5CYII="
        )
        upload = TrackedUpload(payload, r"..\..\outside.html", "text/html")
        session = FakeSession()

        with tempfile.TemporaryDirectory() as directory, patch.object(
            main_public.settings, "uploads_dir", directory
        ), patch.object(main_public, "MAX_BLOB_SIZE", 1), patch.object(
            main_public, "MAX_FILE_SIZE", 1024
        ), patch.object(main_public, "new_content_id", return_value="serverid"):
            result = asyncio.run(
                main_public.add_file(
                    file=upload,
                    title=None,
                    expires_in=None,
                    session=session,
                    api_key=self.api_key,
                )
            )
            item = session.items[result["id"]]
            stored_path = Path(item.file_path).resolve()

            self.assertEqual(stored_path.parent, Path(directory).resolve())
            self.assertNotIn("outside", stored_path.name)
            self.assertEqual(item.metadata_dict["filename"], r"..\..\outside.html")
            self.assertTrue(stored_path.is_file())
            self.assertTrue(upload.closed)
            self.assertTrue(upload.read_sizes)
            self.assertTrue(all(size == main_public.UPLOAD_CHUNK_SIZE for size in upload.read_sizes))

            raw_response = asyncio.run(main_public.get_raw_item(item.id, session))
            self.assertEqual(raw_response.headers["content-type"], "image/png")
            self.assertEqual(raw_response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(
                raw_response.headers["content-disposition"],
                "attachment; filename*=UTF-8''outside.html",
            )

            stored_path.unlink()

    def test_oversized_upload_stops_chunked_read_and_removes_partial_file(self):
        upload = TrackedUpload(b"12345", "large.bin", "application/octet-stream")
        session = FakeSession()

        with tempfile.TemporaryDirectory() as directory, patch.object(
            main_public.settings, "uploads_dir", directory
        ), patch.object(main_public, "MAX_FILE_SIZE", 4), patch.object(
            main_public, "UPLOAD_CHUNK_SIZE", 2
        ), patch.object(main_public, "new_content_id", return_value="serverid"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    main_public.add_file(
                        file=upload,
                        title=None,
                        expires_in=None,
                        session=session,
                        api_key=self.api_key,
                    )
                )

            self.assertEqual(raised.exception.status_code, 413)
            self.assertEqual(upload.read_sizes, [2, 2, 2])
            self.assertTrue(upload.closed)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_database_failure_removes_filesystem_upload(self):
        payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 128)
        upload = TrackedUpload(payload, "image.png", "image/png")
        session = FakeSession(fail_commit=True)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            main_public.settings, "uploads_dir", directory
        ), patch.object(main_public, "MAX_BLOB_SIZE", 1), patch.object(
            main_public, "MAX_FILE_SIZE", 1024
        ), patch.object(main_public, "new_content_id", return_value="serverid"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    main_public.add_file(
                        file=upload,
                        title=None,
                        expires_in=None,
                        session=session,
                        api_key=self.api_key,
                    )
                )

            self.assertEqual(raised.exception.status_code, 500)
            self.assertTrue(session.rolled_back)
            self.assertEqual(list(Path(directory).iterdir()), [])


class RenderingSecurityTests(unittest.TestCase):
    def test_markdown_keeps_normal_formatting_but_disables_active_content(self):
        item = ContentItem(
            id="markdown",
            type="markdown",
            content_text=(
                "# Heading\n\n**bold**\n\n"
                "<script>alert(1)</script>\n\n"
                "[unsafe](javascript:alert(2))\n\n"
                "```html\n<div>example</div>\n```"
            ),
        )
        session = FakeSession()
        session.items[item.id] = item

        response = asyncio.run(
            main_public.get_item(request_for("/markdown"), item.id, session)
        )
        body = response.body.decode("utf-8")

        self.assertIn("<strong>bold</strong>", body)
        self.assertIn('<code class="language-html">&lt;div&gt;example&lt;/div&gt;', body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("javascript:", body.lower())
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])

    @staticmethod
    def _request_messages(path, chunks, headers):
        async def exercise():
            sent = []
            pending_chunks = list(chunks)

            async def receive():
                if pending_chunks:
                    body = pending_chunks.pop(0)
                    return {
                        "type": "http.request",
                        "body": body,
                        "more_body": bool(pending_chunks),
                    }
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "root_path": "",
                "headers": headers,
                "client": ("test", 1234),
                "server": ("testserver", 80),
            }
            await main_public.app(scope, receive, send)
            return sent

        return asyncio.run(exercise())

    def test_request_body_limiter_rejects_content_length_before_parsing(self):
        with patch.object(main_public, "MAX_FILE_SIZE", 4), patch.object(
            main_public, "MULTIPART_OVERHEAD", 2
        ):
            messages = self._request_messages(
                "/api/add/file",
                [b""],
                [(b"content-length", b"7")],
            )

        response_starts = [
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        ]
        self.assertEqual(response_starts, [413])

    def test_streamed_body_limit_replaces_parser_400_with_413(self):
        with self.subTest(content_type="multipart"):
            with patch.object(main_public, "MAX_FILE_SIZE", 1), patch.object(
                main_public, "MULTIPART_OVERHEAD", 0
            ):
                messages = self._request_messages(
                    "/api/add/file",
                    [b"-", b"-"],
                    [(b"content-type", b"multipart/form-data; boundary=boundary")],
                )

            response_starts = [
                message["status"]
                for message in messages
                if message["type"] == "http.response.start"
            ]
            self.assertEqual(response_starts, [413])

        with self.subTest(content_type="json"):
            with patch.object(main_public, "MAX_TEXT_SIZE", 1), patch.object(
                main_public, "JSON_BODY_OVERHEAD", 0
            ):
                messages = self._request_messages(
                    "/api/add/text",
                    [b'{"con', b'tent":'],
                    [(b"content-type", b"application/json")],
                )

            response_starts = [
                message["status"]
                for message in messages
                if message["type"] == "http.response.start"
            ]
            self.assertEqual(response_starts, [413])


if __name__ == "__main__":
    unittest.main()
