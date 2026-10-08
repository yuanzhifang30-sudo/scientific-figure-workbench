"""SQLite transactions plus content-addressed files and version-bound reviews."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path, PureWindowsPath
import sqlite3
from threading import RLock
import uuid
import zipfile

from .checks import MAX_FILE_BYTES, digest, inspect_files


class WorkflowError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise WorkflowError(f"{label}需要1～{limit}个字符")
    return value.strip()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "workbench.sqlite3"
        self.lock = RLock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS board (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, revision INTEGER, kind TEXT, task_id TEXT, at TEXT, payload TEXT)")
            initial = {"schema_version": 1, "revision": 0, "max_active_per_owner": 2, "tasks": {}, "releases": []}
            conn.execute("INSERT OR IGNORE INTO board VALUES (1, ?)", (json.dumps(initial),))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=15)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def state(self):
        with self.lock, self.connect() as conn:
            board = json.loads(conn.execute("SELECT data FROM board WHERE id=1").fetchone()[0])
            events = conn.execute("SELECT seq,revision,kind,task_id,at,payload FROM events ORDER BY seq DESC LIMIT 80").fetchall()
            board["events"] = [{"seq": r[0], "revision": r[1], "kind": r[2], "task_id": r[3], "at": r[4], "payload": json.loads(r[5])} for r in events]
            return board

    def mutate(self, expected, kind, task_id, operation):
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            board = json.loads(conn.execute("SELECT data FROM board WHERE id=1").fetchone()[0])
            if type(expected) is not int or expected != board["revision"]:
                raise WorkflowError("工作台版本已变化，请刷新后再操作", 409)
            result = operation(board)
            board["revision"] += 1
            conn.execute("UPDATE board SET data=? WHERE id=1", (json.dumps(board, ensure_ascii=False),))
            conn.execute("INSERT INTO events(revision,kind,task_id,at,payload) VALUES (?,?,?,?,?)",
                         (board["revision"], kind, task_id, now(), json.dumps(result, ensure_ascii=False)))
            return {"revision": board["revision"], "result": result}

    @staticmethod
    def task(board, task_id):
        try:
            return board["tasks"][task_id]
        except KeyError:
            raise WorkflowError("任务不存在", 404) from None

    @staticmethod
    def active_count(board, owner, exclude=None):
        return sum(t["owner"] == owner and t["id"] != exclude and t["status"] in {"working", "review", "rework"}
                   for t in board["tasks"].values())

    def ensure_capacity(self, board, task):
        if self.active_count(board, task["owner"], task["id"]) >= board["max_active_per_owner"]:
            raise WorkflowError("该制图者已有两张在途图件，请先完成验收", 409)

    def create_task(self, payload):
        task_id = "fig-" + uuid.uuid4().hex[:10]

        def operation(board):
            title = text(payload.get("title"), "标题", 120)
            owner = text(payload.get("owner"), "制图者代号", 80)
            brief = text(payload.get("brief"), "验收要求")
            board["tasks"][task_id] = {"id": task_id, "title": title, "owner": owner, "brief": brief,
                                      "status": "planned", "current_submission": None, "submissions": [], "reviews": []}
            return {"task_id": task_id, "title": title}
        return self.mutate(payload.get("expected_revision"), "create", task_id, operation)

    def start(self, task_id, payload):
        def operation(board):
            task = self.task(board, task_id)
            if task["status"] not in {"planned", "rework"}:
                raise WorkflowError("只有待开始或返工任务可以开始", 409)
            self.ensure_capacity(board, task)
            task["status"] = "working"
            return {"task_id": task_id}
        return self.mutate(payload.get("expected_revision"), "start", task_id, operation)

    def blob(self, file):
        path = (self.directory / file["blob_path"]).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError:
            raise WorkflowError("文件路径越界") from None
        return path

    def read_bytes(self, file):
        return self.blob(file).read_bytes()

    def store_files(self, files):
        if not isinstance(files, list) or not 1 <= len(files) <= 3:
            raise WorkflowError("提交1～3个文件：PNG、CSV和可选源码")
        result, roles = [], set()
        suffixes = {"figure": {".png"}, "data": {".csv"}, "code": {".py", ".m", ".tex", ".txt"}}
        for file in files:
            if not isinstance(file, dict):
                raise WorkflowError("文件记录格式错误")
            role, name = file.get("role"), text(file.get("name"), "文件名", 160)
            if role not in suffixes or role in roles:
                raise WorkflowError("文件角色无效或重复")
            if Path(name).name != name or PureWindowsPath(name).name != name or ":" in name or name in {".", ".."}:
                raise WorkflowError("文件名不能包含目录或盘符")
            suffix = Path(name).suffix.lower()
            if suffix not in suffixes[role]:
                raise WorkflowError("文件扩展名与角色不匹配")
            try:
                data = base64.b64decode(file.get("data_base64", ""), validate=True)
            except (ValueError, TypeError):
                raise WorkflowError("文件编码无效") from None
            if not data or len(data) > MAX_FILE_BYTES:
                raise WorkflowError("单个文件需为1字节～8MiB")
            sha = digest(data)
            target = self.directory / "blobs" / (sha + suffix)
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                if digest(target.read_bytes()) != sha:
                    raise WorkflowError("内容寻址文件损坏，请使用新的数据目录", 409)
            else:
                with target.open("xb") as stream:
                    stream.write(data)
            result.append({"id": uuid.uuid4().hex, "role": role, "name": name, "size": len(data),
                           "sha256": sha, "blob_path": target.relative_to(self.directory).as_posix()})
            roles.add(role)
        return result

    def submit(self, task_id, payload):
        def operation(board):
            task = self.task(board, task_id)
            if task["status"] == "planned":
                raise WorkflowError("请先开始任务", 409)
            self.ensure_capacity(board, task)
            version = text(payload.get("version"), "版本标签", 120)
            if any(s["version"] == version for s in task["submissions"]):
                raise WorkflowError("版本标签已使用，请提交新的版本标签", 409)
            caption = text(payload.get("caption"), "图注")
            files = self.store_files(payload.get("files"))
            submission = {"id": uuid.uuid4().hex, "number": len(task["submissions"]) + 1,
                          "version": version, "caption": caption, "files": files, "created_at": now(),
                          "checks": inspect_files(files, self.read_bytes)}
            task["submissions"].append(submission)
            task["current_submission"] = submission["id"]
            task["status"] = "review"
            return {"task_id": task_id, "submission_id": submission["id"], "version": version}
        return self.mutate(payload.get("expected_revision"), "submit", task_id, operation)

    @staticmethod
    def current(task):
        return next((s for s in task["submissions"] if s["id"] == task["current_submission"]), None)

    def inspect(self, task_id):
        task = self.task(self.state(), task_id)
        submission = self.current(task)
        return {"task_id": task_id, "submission_id": submission["id"] if submission else None,
                "checks": inspect_files(submission["files"], self.read_bytes) if submission else []}

    def review(self, task_id, payload):
        def operation(board):
            task = self.task(board, task_id)
            submission = self.current(task)
            if (not submission or payload.get("submission_id") != submission["id"]
                    or task["status"] != "review"):
                raise WorkflowError("验收必须绑定当前待验收版本", 409)
            decision = payload.get("decision")
            if decision not in {"accept", "rework"}:
                raise WorkflowError("无效验收决定")
            note = text(payload.get("note"), "验收备注")
            checklist = payload.get("checklist", {})
            if decision == "accept":
                if not isinstance(checklist, dict) or any(checklist.get(k) is not True for k in ("visual", "data", "caption")):
                    raise WorkflowError("通过前需人工确认视觉、数据口径和图注三个项目")
                live_checks = inspect_files(submission["files"], self.read_bytes)
                if any(c["level"] == "error" for c in live_checks):
                    raise WorkflowError("当前文件未通过自动检查，不能验收通过", 409)
            review = {"id": uuid.uuid4().hex, "submission_id": submission["id"], "decision": decision,
                      "checklist": checklist, "note": note, "created_at": now()}
            task["reviews"].append(review)
            task["status"] = "accepted" if decision == "accept" else "rework"
            return {"task_id": task_id, "submission_id": submission["id"], "decision": decision}
        return self.mutate(payload.get("expected_revision"), "review", task_id, operation)

    def freeze(self, payload):
        def operation(board):
            tasks = list(board["tasks"].values())
            if not tasks or any(t["status"] != "accepted" for t in tasks):
                raise WorkflowError("需要全部任务的当前版本均已人工验收通过", 409)
            release_id = uuid.uuid4().hex
            manifest = {"schema_version": 1, "release_id": release_id, "created_at": now(),
                        "source_board_revision": board["revision"], "figures": [],
                        "scope": "当前全部任务的已验收版本；冻结图包不随后续修改改变"}
            content = {}
            for task in tasks:
                sub = self.current(task)
                review = next((r for r in reversed(task["reviews"]) if r["submission_id"] == sub["id"]), None)
                if not review or review["decision"] != "accept":
                    raise WorkflowError("验收记录未绑定当前版本", 409)
                # Inspect the same immutable byte snapshot that will enter the ZIP.
                data = {f["id"]: self.read_bytes(f) for f in sub["files"]}
                if any(c["level"] == "error" for c in inspect_files(sub["files"], lambda f: data[f["id"]])):
                    raise WorkflowError("已验收文件发生变化，拒绝冻结", 409)
                figure = {"task_id": task["id"], "title": task["title"], "submission_id": sub["id"],
                          "version": sub["version"], "caption": sub["caption"], "review": deepcopy(review), "files": []}
                for file in sub["files"]:
                    name = f"{task['id']}/{file['role']}{Path(file['name']).suffix.lower()}"
                    content[name] = data[file["id"]]
                    figure["files"].append({"path": name, "original_name": file["name"], "sha256": file["sha256"], "size": len(data[file["id"]])})
                manifest["figures"].append(figure)
            target = self.directory / "releases" / (release_id + ".zip")
            target.parent.mkdir(exist_ok=True)
            with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in content.items():
                    archive.writestr(name, data)
                archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            release = {"id": release_id, "created_at": manifest["created_at"], "figure_count": len(tasks),
                       "source_board_revision": board["revision"], "sha256": digest(target.read_bytes())}
            board["releases"].append(release)
            return release
        return self.mutate(payload.get("expected_revision"), "freeze", None, operation)

    def release_bytes(self, release_id):
        release = next((r for r in self.state()["releases"] if r["id"] == release_id), None)
        if not release:
            raise WorkflowError("图包不存在", 404)
        data = (self.directory / "releases" / (release["id"] + ".zip")).read_bytes()
        if digest(data) != release["sha256"]:
            raise WorkflowError("冻结图包哈希不一致", 409)
        return data

    def file_bytes(self, file_id):
        for task in self.state()["tasks"].values():
            for sub in task["submissions"]:
                for file in sub["files"]:
                    if file["id"] == file_id:
                        data = self.read_bytes(file)
                        if digest(data) != file["sha256"]:
                            raise WorkflowError("文件哈希不一致", 409)
                        return file, data
        raise WorkflowError("文件不存在", 404)
