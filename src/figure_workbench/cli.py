import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .demo import build_demo
from .server import make_server
from .store import Store, WorkflowError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fwb", description="科研图件验收工作台 / local scientific figure review")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "serve", "status", "inspect"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=Path(".local/demo"))
        if name == "serve":
            command.add_argument("--port", type=int, default=8788)
        if name == "inspect":
            command.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            store = build_demo(args.data_dir)
            print(f"已创建6张匿名自造图件的演示。数据目录: {store.directory}")
            print(f"运行 fwb serve --data-dir {args.data_dir} ，然后打开 http://127.0.0.1:8788")
            return 0
        store = Store(args.data_dir)
        if args.command == "serve":
            server = make_server(store, args.port)
            print(f"工作台: http://127.0.0.1:{server.server_address[1]}  |  数据: {store.directory}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        result = store.inspect(args.task_id) if args.command == "inspect" else store.state()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(args.command == "inspect" and any(c["level"] == "error" for c in result["checks"]))
    except (WorkflowError, OSError, ValueError) as exc:
        print(f"fwb: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
