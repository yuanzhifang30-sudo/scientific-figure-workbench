# Scientific Figure Workbench

A local prototype for scientific figure tasks, version-bound review and immutable handoff packages.

[中文](README.md) · [Design](docs/design.md) · [Agent Skill](skills/scientific-figure-review/SKILL.md)

![Workbench](docs/assets/board.png)

## Run

Requires Python 3.10+. Runtime uses the standard library and Pillow; no model API or cloud service.

```sh
git clone https://github.com/yuanzhifang30-sudo/scientific-figure-workbench.git
cd scientific-figure-workbench
uv sync --locked
uv run fwb demo --data-dir .local/demo
uv run fwb serve --data-dir .local/demo
```

Open http://127.0.0.1:8788. The demo creates six anonymous synthetic figures with explicitly simulated approvals. It refuses to overwrite existing tasks.

For a blank project, serve a different data directory and create tasks from the UI. Store the data outside tracked source; `.local/` is ignored.

## Workflow

- Assign figure tasks using author aliases; at most two active tasks per alias.
- Submit a unique version label, PNG, source CSV and caption; optionally archive source code.
- Decode PNG, check dimensions/uniform images, CSV structure/finite values/duplicate points and file hashes.
- Record manual visual/data/caption checks and acceptance or rework against the exact current submission.
- Inspect history or compare adjacent figure versions. A new submission requires a new review.
- Freeze only when all current tasks are accepted. The ZIP records exact file bytes, hashes, captions, versions and reviews; future submissions do not alter it.

PNG minimum: 800×400 px. CSV: `series,x,value`, unique `(series,x)`, finite values. Failed automatic checks prevent acceptance. Revision conflicts return409 without automatic retries. SQLite transactions preserve state and events across restarts.

## Scope

This is a loopback-only, local single-reviewer prototype, not a remote identity-authenticated collaboration service. The tool cannot prove scientific correctness, visual quality, source reproducibility or correspondence between plotted pixels and source data. Uploaded code is archived, never executed. Formats other than PNG and the declared CSV schema are not implemented.

The optional Agent Skill helps inspect evidence and draft review notes; it must not impersonate a human approval or send collaborator messages. No contest artifacts, team source, real communications or personal biographies are bundled.

```sh
uv run python -m unittest discover -s tests -v
```

Maintained by [yuanzhifang30-sudo](https://github.com/yuanzhifang30-sudo). MIT license.
