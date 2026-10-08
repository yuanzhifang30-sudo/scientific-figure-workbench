import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from figure_workbench.checks import digest
from figure_workbench.demo import build_demo, example_files
from figure_workbench.server import make_server
from figure_workbench.store import Store, WorkflowError


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def payload(self, **extra):
        return {"expected_revision": self.store.state()["revision"], **extra}

    def create(self, owner="author-a", title="Synthetic test figure"):
        return self.store.create_task(self.payload(title=title, owner=owner, brief="Test simulation only"))["result"]["task_id"]

    def submit(self, task, version="v1", broken=False):
        return self.store.submit(task, self.payload(version=version, caption="Synthetic data / test only", files=example_files(broken=broken)))["result"]["submission_id"]

    def approve(self, task, sub):
        return self.store.review(task, self.payload(submission_id=sub, decision="accept", note="Simulated test approval", checklist={"visual": True, "data": True, "caption": True}))

    def ready(self, owner="author-a"):
        task = self.create(owner)
        self.store.start(task, self.payload())
        sub = self.submit(task)
        return task, sub

    def test_new_submission_requires_new_review_and_keeps_history(self):
        task, sub = self.ready()
        self.approve(task, sub)
        new_sub = self.submit(task, "v2")
        saved = self.store.state()["tasks"][task]
        self.assertEqual(saved["status"], "review")
        self.assertEqual(saved["current_submission"], new_sub)
        self.assertEqual(len(saved["submissions"]), 2)
        self.assertEqual(saved["reviews"][0]["submission_id"], sub)
        with self.assertRaises(WorkflowError):
            self.approve(task, sub)

    def test_stale_board_revision_has_no_effect(self):
        task = self.create()
        old = self.payload()
        self.create("author-b")
        before = self.store.state()
        with self.assertRaises(WorkflowError) as ctx:
            self.store.start(task, old)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.store.state(), before)

    def test_boolean_revision_is_rejected(self):
        task = self.create()
        with self.assertRaises(WorkflowError):
            self.store.start(task, {"expected_revision": True})

    def test_capacity_two_including_rework(self):
        a, sub = self.ready()
        self.store.review(a, self.payload(submission_id=sub, decision="rework", note="Test rework"))
        b = self.create()
        self.store.start(b, self.payload())
        c = self.create()
        with self.assertRaises(WorkflowError):
            self.store.start(c, self.payload())
        self.assertEqual(self.store.state()["tasks"][c]["status"], "planned")

    def test_acceptance_releases_capacity(self):
        a, sub = self.ready()
        b = self.create()
        self.store.start(b, self.payload())
        self.approve(a, sub)
        c = self.create()
        self.store.start(c, self.payload())
        self.assertEqual(self.store.state()["tasks"][c]["status"], "working")

    def test_bad_png_and_csv_block_approval(self):
        task = self.create()
        self.store.start(task, self.payload())
        sub = self.submit(task, broken=True)
        codes = [c["code"] for c in self.store.inspect(task)["checks"]]
        self.assertIn("low_resolution", codes)
        self.assertIn("invalid_csv", codes)
        with self.assertRaises(WorkflowError):
            self.approve(task, sub)

    def test_manual_checklist_is_required(self):
        task, sub = self.ready()
        for checklist in ({}, {"visual": True, "data": True, "caption": 1}):
            with self.subTest(checklist=checklist), self.assertRaises(WorkflowError):
                self.store.review(task, self.payload(submission_id=sub, decision="accept", note="Test", checklist=checklist))

    def test_duplicate_version_is_not_overwritten(self):
        task, sub = self.ready()
        with self.assertRaises(WorkflowError):
            self.submit(task)
        self.assertEqual(len(self.store.state()["tasks"][task]["submissions"]), 1)

    def test_missing_data_is_visible_and_not_approvable(self):
        task = self.create()
        self.store.start(task, self.payload())
        sub = self.store.submit(task, self.payload(version="v1", caption="Test", files=example_files()[:1]))["result"]["submission_id"]
        self.assertIn("missing_data", [c["code"] for c in self.store.inspect(task)["checks"]])
        with self.assertRaises(WorkflowError):
            self.approve(task, sub)

    def test_filename_escape_and_bad_base64_rejected(self):
        task = self.create()
        self.store.start(task, self.payload())
        for name in ("../figure.png", "C:\\figure.png", "dir/figure.png", "dir\\figure.png"):
            files = example_files()
            files[0]["name"] = name
            with self.subTest(name=name), self.assertRaises(WorkflowError):
                self.store.submit(task, self.payload(version="v1", caption="Test", files=files))
        files = example_files()
        files[0]["data_base64"] = "%%%"
        with self.assertRaises(WorkflowError):
            self.store.submit(task, self.payload(version="v1", caption="Test", files=files))

    def test_forged_png_extension_fails_content_check(self):
        task = self.create()
        self.store.start(task, self.payload())
        files = example_files()
        files[0]["data_base64"] = base64.b64encode(b"not an image").decode()
        sub = self.store.submit(task, self.payload(version="v1", caption="Test", files=files))["result"]["submission_id"]
        self.assertIn("invalid_png", [c["code"] for c in self.store.inspect(task)["checks"]])
        with self.assertRaises(WorkflowError):
            self.approve(task, sub)

    def test_source_duplicate_and_empty_csv_rejected(self):
        for csv in ("series,x,value\n", "series,x,value\na,1,2\na,1,3\n", "series,x,value\na,1,Infinity\n"):
            task = self.create("author-" + str(self.store.state()["revision"]))
            self.store.start(task, self.payload())
            files = example_files()
            files[1]["data_base64"] = base64.b64encode(csv.encode()).decode()
            self.store.submit(task, self.payload(version="v1", caption="Test", files=files))
            self.assertIn("invalid_csv", [c["code"] for c in self.store.inspect(task)["checks"]])

    def test_changed_bytes_are_detected_before_review_and_download(self):
        task, sub = self.ready()
        file = self.store.state()["tasks"][task]["submissions"][0]["files"][0]
        self.store.blob(file).write_bytes(b"tampered")
        self.assertIn("hash_mismatch", [c["code"] for c in self.store.inspect(task)["checks"]])
        with self.assertRaises(WorkflowError):
            self.approve(task, sub)
        with self.assertRaises(WorkflowError):
            self.store.file_bytes(file["id"])

    def test_partial_acceptance_cannot_freeze(self):
        task, sub = self.ready()
        self.approve(task, sub)
        self.create("author-b")
        with self.assertRaises(WorkflowError):
            self.store.freeze(self.payload())

    def test_frozen_package_is_immutable_and_manifest_matches_bytes(self):
        task, sub = self.ready()
        self.approve(task, sub)
        release = self.store.freeze(self.payload())["result"]
        old = self.store.release_bytes(release["id"])
        with zipfile.ZipFile(io.BytesIO(old)) as z:
            manifest = json.loads(z.read("MANIFEST.json"))
            self.assertEqual(manifest["figures"][0]["submission_id"], sub)
            self.assertEqual(manifest["figures"][0]["review"]["decision"], "accept")
            for file in manifest["figures"][0]["files"]:
                self.assertEqual(digest(z.read(file["path"])), file["sha256"])
        self.submit(task, "v2")
        self.assertEqual(self.store.release_bytes(release["id"]), old)
        with self.assertRaises(WorkflowError):
            self.store.freeze(self.payload())

    def test_tampering_after_acceptance_blocks_freeze(self):
        task, sub = self.ready()
        self.approve(task, sub)
        file = self.store.state()["tasks"][task]["submissions"][0]["files"][0]
        self.store.blob(file).write_bytes(b"changed")
        with self.assertRaises(WorkflowError):
            self.store.freeze(self.payload())

    def test_frozen_zip_tampering_is_detected(self):
        task, sub = self.ready()
        self.approve(task, sub)
        release = self.store.freeze(self.payload())["result"]
        (self.store.directory / "releases" / (release["id"] + ".zip")).write_bytes(b"changed")
        with self.assertRaises(WorkflowError):
            self.store.release_bytes(release["id"])

    def test_persistence_and_events_survive_restart(self):
        self.ready()
        state = self.store.state()
        restarted = Store(self.tmp.name)
        self.assertEqual(state, restarted.state())
        self.assertEqual([e["kind"] for e in state["events"]], ["submit", "start", "create"])

    def test_demo_is_anonymous_and_does_not_overwrite_existing_data(self):
        directory = Path(self.tmp.name) / "demo"
        demo = build_demo(directory)
        self.assertEqual(len(demo.state()["tasks"]), 6)
        self.assertEqual(sum(t["status"] == "accepted" for t in demo.state()["tasks"].values()), 2)
        self.assertTrue(all("演示模拟" in r["note"] for t in demo.state()["tasks"].values() for r in t["reviews"]))
        before = demo.state()
        with self.assertRaises(WorkflowError):
            build_demo(directory)
        self.assertEqual(before, demo.state())


class HTTPTests(unittest.TestCase):
    def test_browser_assets_api_and_cross_origin_guards(self):
        with tempfile.TemporaryDirectory() as root:
            server = make_server(Store(root), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                for path, mime in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css"), ("/favicon.svg", "image/svg+xml"), ("/api/state", "application/json")):
                    with urlopen(base + path) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(mime, response.headers["Content-Type"])
                        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                for headers in ({"Host": "attacker.test"}, {"Origin": "http://attacker.test"}):
                    request = Request(base + "/api/tasks", data=b"{}", headers={"Content-Type": "application/json", **headers})
                    with self.subTest(headers=headers), self.assertRaises(HTTPError) as ctx:
                        urlopen(request)
                    self.assertEqual(ctx.exception.code, 403)
                request = Request(base + "/api/tasks", data=b"{}", headers={"Content-Type": "text/plain"})
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(request)
                self.assertEqual(ctx.exception.code, 415)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(base + "/../../workbench.sqlite3")
                self.assertEqual(ctx.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
