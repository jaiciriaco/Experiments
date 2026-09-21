from __future__ import annotations

import logging
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from uned_backup.downloader import Downloader, ManifestStore, _repair_mojibake
from uned_backup.inspector import _course_score, _merge_course_candidates, moodle_session_ready
from uned_backup.scraper import (
    _course_id,
    _with_preferred_language,
    is_crawl_candidate,
    is_download_candidate,
    is_indexed_module_candidate,
    is_snapshot_candidate,
    snapshot_filename,
)
from uned_backup.utils import normalize_url, sanitize_component


class CoreTests(unittest.TestCase):
    def test_windows_filename_sanitizing(self) -> None:
        self.assertEqual(sanitize_component("Tema: 1 / prueba?.pdf"), "Tema_ 1 _ prueba_.pdf")
        self.assertEqual(sanitize_component("CON"), "_CON")

    def test_mojibake_filename_repair(self) -> None:
        self.assertEqual(_repair_mojibake("decisiÃ³n.pdf"), "decisión.pdf")

    def test_fragment_removed_but_query_kept(self) -> None:
        self.assertEqual(
            normalize_url("HTTPS://CAMPUS.UNED.ES/a?id=2#tema"), "https://campus.uned.es/a?id=2"
        )

    def test_moodle_course_detection(self) -> None:
        score = _course_score(
            {
                "href": "https://agora.uned.es/course/view.php?id=123",
                "text": "Aprendizaje Automático 2026",
                "visible": True,
            }
        )
        self.assertGreaterEqual(score, 5)

    def test_non_course_agora_navigation_is_not_a_course(self) -> None:
        score = _course_score(
            {
                "href": "https://agora.uned.es/my/courses.php",
                "text": "Mis cursos",
                "visible": True,
            }
        )
        self.assertLess(score, 5)

    def test_ajax_courses_take_priority_and_are_deduplicated(self) -> None:
        url = "https://agora.uned.es/course/view.php?id=10610"
        merged = _merge_course_candidates(
            [{"href": url, "text": "Aprendizaje Profundo", "source": "moodle_ajax"}],
            [{"href": url + "#section-1", "text": "Otro texto", "source": "dom"}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["text"], "Aprendizaje Profundo")

    def test_loading_page_is_not_a_ready_moodle_session(self) -> None:
        class LoadingPage:
            @staticmethod
            def evaluate(_script: str) -> bool:
                return False

        self.assertFalse(moodle_session_ready(LoadingPage()))

    def test_download_detection(self) -> None:
        config = {"include_extensions": [".pdf"], "include_images": True}
        self.assertTrue(
            is_download_candidate(
                {
                    "href": "https://agora.uned.es/pluginfile.php/12/tema.pdf",
                    "text": "Tema 1",
                    "download": False,
                },
                config,
            )
        )

    def test_same_course_sections_are_crawled(self) -> None:
        config = {
            "allowed_domains": ["uned.es"],
            "exclude_url_patterns": [],
            "include_extensions": [".pdf"],
        }
        course = "https://agora.uned.es/course/view.php?id=10639"
        self.assertTrue(
            is_crawl_candidate(
                "https://agora.uned.es/course/view.php?id=10639&section=4",
                config,
                course,
            )
        )
        self.assertFalse(
            is_crawl_candidate(
                "https://agora.uned.es/course/view.php?id=10610&section=4",
                config,
                course,
            )
        )
        self.assertFalse(
            is_crawl_candidate(
                "https://agora.uned.es/course/view.php?id=10639&lang=en",
                config,
                course,
            )
        )

    def test_course_id_is_read_from_moodle_url(self) -> None:
        self.assertEqual(_course_id("https://agora.uned.es/course/view.php?id=10639"), 10639)
        self.assertIsNone(_course_id("https://agora.uned.es/my/courses.php"))

    def test_indexed_activity_root_is_crawled(self) -> None:
        config = {
            "allowed_domains": ["uned.es"],
            "exclude_url_patterns": [],
        }
        self.assertTrue(
            is_indexed_module_candidate(
                {
                    "href": "https://agora.uned.es/mod/quiz/view.php?id=765432",
                    "from_course_index": True,
                },
                config,
            )
        )
        self.assertFalse(
            is_indexed_module_candidate(
                {
                    "href": "https://agora.uned.es/mod/quiz/attempt.php?id=765432",
                    "from_course_index": True,
                },
                config,
            )
        )

    def test_language_variants_are_never_crawled(self) -> None:
        config = {
            "allowed_domains": ["uned.es"],
            "exclude_url_patterns": [],
            "include_extensions": [".pdf"],
        }
        course = "https://agora.uned.es/course/view.php?id=10639"
        self.assertFalse(
            is_crawl_candidate(
                "https://agora.uned.es/mod/assign/view.php?id=995398&lang=fi",
                config,
                course,
            )
        )
        self.assertFalse(
            is_crawl_candidate(
                "https://agora.uned.es/mod/page/view.php?id=744226&language=en",
                config,
                course,
            )
        )

    def test_first_course_page_forces_spanish_once(self) -> None:
        self.assertEqual(
            _with_preferred_language(
                "https://agora.uned.es/course/view.php?id=10639&lang=fi", "es"
            ),
            "https://agora.uned.es/course/view.php?id=10639&lang=es",
        )

    def test_activity_pages_are_snapshotted_only_at_their_root(self) -> None:
        self.assertTrue(
            is_snapshot_candidate("https://agora.uned.es/mod/assign/view.php?id=995398")
        )
        self.assertTrue(is_snapshot_candidate("https://agora.uned.es/mod/quiz/view.php?id=554148"))
        self.assertFalse(
            is_snapshot_candidate(
                "https://agora.uned.es/mod/assign/view.php?id=995398&action=editsubmission"
            )
        )
        self.assertFalse(is_snapshot_candidate("https://agora.uned.es/course/view.php?id=10639"))

    def test_activity_overview_and_open_grader_are_crawled(self) -> None:
        config = {
            "allowed_domains": ["uned.es"],
            "exclude_url_patterns": [],
            "include_extensions": [".pdf"],
        }
        course = "https://agora.uned.es/course/view.php?id=10639"
        self.assertTrue(
            is_crawl_candidate("https://agora.uned.es/course/overview.php?id=10639", config, course)
        )
        self.assertTrue(
            is_crawl_candidate(
                "https://agora.uned.es/local/joulegrader/view.php?courseid=10639",
                config,
                course,
            )
        )
        self.assertFalse(
            is_crawl_candidate(
                "https://agora.uned.es/local/joulegrader/view.php?courseid=10610",
                config,
                course,
            )
        )

    def test_quiz_reviews_and_grader_details_are_snapshotted(self) -> None:
        self.assertTrue(
            is_snapshot_candidate(
                "https://agora.uned.es/mod/quiz/review.php?attempt=1111111&cmid=222222"
            )
        )
        self.assertTrue(
            is_snapshot_candidate(
                "https://agora.uned.es/mod/quiz/review.php?attempt=1111111&cmid=222222&page=2"
            )
        )
        self.assertFalse(
            is_snapshot_candidate(
                "https://agora.uned.es/mod/quiz/review.php?attempt=1111111&cmid=222222&showall=0"
            )
        )
        self.assertTrue(
            is_snapshot_candidate(
                "https://agora.uned.es/local/joulegrader/view.php"
                "?courseid=12345&guser=333333&garea=444444"
            )
        )
        self.assertFalse(
            is_snapshot_candidate(
                "https://agora.uned.es/local/joulegrader/view.php"
                "?action=downloadall&s=123&courseid=10639"
            )
        )

    def test_snapshot_filename_identifies_attempt_page_and_grader_area(self) -> None:
        quiz_name = snapshot_filename(
            "Cuestionario del capítulo 1",
            "https://agora.uned.es/mod/quiz/review.php?attempt=1111111&cmid=222222&page=2",
        )
        self.assertEqual(
            quiz_name,
            "Cuestionario del capítulo 1 - revisión 1111111 - página 3.pdf",
        )
        grader_name = snapshot_filename(
            "Métodos de Aprendizaje Automático",
            "https://agora.uned.es/local/joulegrader/view.php"
            "?courseid=12345&guser=333333&garea=444444",
        )
        self.assertEqual(
            grader_name,
            "Métodos de Aprendizaje Automático - detalle Open Grader 444444.pdf",
        )

    def test_streamed_download_and_hash_duplicate(self) -> None:
        payload = b"%PDF-1.4\ncontenido de prueba\n"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", 'attachment; filename="Tema:1.pdf"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args) -> None:
                pass

        class FakePage:
            @staticmethod
            def evaluate(_script: str) -> str:
                return "UNED-Backup-Test"

        class FakeContext:
            def __init__(self) -> None:
                self.pages = [FakePage()]

            @staticmethod
            def cookies() -> list:
                return []

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = {
                    "output_dir": root / "out",
                    "download_timeout_seconds": 5,
                    "max_file_size_mb": 1,
                }
                store = ManifestStore(root / "manifest.json")
                downloader = Downloader(FakeContext(), config, store, logging.getLogger("test"))
                base = f"http://127.0.0.1:{server.server_port}"
                first = downloader.download(
                    "Asignatura", "Tema 1", {"href": f"{base}/primero", "text": "Documento"}
                )
                second = downloader.download(
                    "Asignatura",
                    "Tema 1",
                    {"href": f"{base}/segundo", "text": "Documento duplicado"},
                )
                self.assertEqual(first.status, "downloaded")
                self.assertEqual(second.status, "duplicate_hash")
                self.assertTrue(first.path and first.path.exists())
                self.assertEqual(len(store.data["files"]), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
