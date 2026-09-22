#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml


TASK_HEADING_RE = re.compile(
    r"^###\s+(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[—–-]\s*)?(?P<title>.+?)\s*$"
)

ALLOWED_PHASES = {"F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "FINAL"}
ALLOWED_PRIORITIES = {"p0", "p1", "p2"}
ALLOWED_EFFORTS = {"s", "m", "l"}

REQUIRED_FIELDS = {
    "id",
    "title",
    "phase",
    "gate",
    "priority",
    "workstream",
    "risk",
    "effort",
    "start",
    "target",
    "blocked_by",
    "owners",
}


def normalize_yaml_value(value):
    """
    Convert YAML-native values that are not JSON serializable into
    stable JSON-compatible values.

    In particular, PyYAML parses ISO dates such as 2026-10-05 as
    datetime.date objects. For the roadmap compiler, dates should
    remain ISO YYYY-MM-DD strings.
    """
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): normalize_yaml_value(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [normalize_yaml_value(item) for item in value]

    if isinstance(value, tuple):
        return [normalize_yaml_value(item) for item in value]

    return value


def derive_project_name(path: Path) -> str:
    name = path.name

    if name.lower().endswith(".md"):
        name = name[:-3]

    if name.endswith("-roadmap"):
        name = name[:-8]

    if not name:
        raise ValueError(f"Cannot derive project name from {path}")

    return name


def parse_yaml_block(lines, start):
    if start >= len(lines) or lines[start].strip() != "```yaml":
        return None, start

    end = start + 1

    while end < len(lines) and lines[end].strip() != "```":
        end += 1

    if end >= len(lines):
        raise ValueError("Unclosed YAML code block")

    content = "".join(lines[start + 1:end])

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("YAML task metadata must be a mapping")

    return normalize_yaml_value(data), end + 1


def parse_task(lines, heading_index):
    heading = lines[heading_index].strip()
    match = TASK_HEADING_RE.match(heading)

    if not match:
        raise ValueError(
            f"Invalid task heading at line {heading_index + 1}: {heading}"
        )

    task_id = match.group("id")
    heading_title = match.group("title").strip()

    metadata, cursor = parse_yaml_block(lines, heading_index + 1)

    if metadata is None:
        raise ValueError(
            f"Task {task_id}: expected ```yaml metadata block "
            f"immediately after heading (line {heading_index + 2})"
        )

    missing = sorted(REQUIRED_FIELDS - set(metadata.keys()))

    if missing:
        raise ValueError(
            f"Task {task_id}: missing required metadata fields: "
            + ", ".join(missing)
        )

    if str(metadata.get("id")) != task_id:
        raise ValueError(
            f"Task {task_id}: metadata id is {metadata.get('id')!r}"
        )

    metadata_title = str(metadata.get("title", "")).strip()

    if not metadata_title:
        raise ValueError(f"Task {task_id}: title cannot be empty")

    issue = dict(metadata)
    issue["id"] = task_id
    issue["title"] = metadata_title

    body_start = cursor
    body_end = body_start

    while body_end < len(lines):
        if TASK_HEADING_RE.match(lines[body_end].strip()):
            break
        body_end += 1

    issue["body"] = "".join(lines[body_start:body_end]).strip()

    blocked_by = issue["blocked_by"]

    if blocked_by is None:
        issue["blocked_by"] = []
    elif not isinstance(blocked_by, list):
        raise ValueError(
            f"Task {task_id}: blocked_by must be a list"
        )

    owners = issue["owners"]

    if not isinstance(owners, (int, str)):
        raise ValueError(
            f"Task {task_id}: owners must be an integer or string"
        )

    if str(issue["phase"]) not in ALLOWED_PHASES:
        raise ValueError(
            f"Task {task_id}: phase must be one of "
            "F0, F1, F2, F3, F4, F5, F6, F7, FINAL"
        )

    if str(issue["gate"]) not in ALLOWED_PHASES:
        raise ValueError(
            f"Task {task_id}: gate must be one of "
            "F0, F1, F2, F3, F4, F5, F6, F7, FINAL"
        )

    if str(issue["priority"]) not in ALLOWED_PRIORITIES:
        raise ValueError(
            f"Task {task_id}: priority must be one of p0, p1, p2"
        )

    if str(issue["effort"]) not in ALLOWED_EFFORTS:
        raise ValueError(
            f"Task {task_id}: effort must be one of s, m, l"
        )

    for date_field in ("start", "target"):
        value = issue.get(date_field)

        if value is None:
            raise ValueError(
                f"Task {task_id}: {date_field} cannot be null"
            )

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            raise ValueError(
                f"Task {task_id}: {date_field} must use YYYY-MM-DD"
            )

        # Always store dates as strings in the JSON model.
        issue[date_field] = str(value)

    return issue, body_end


def parse_roadmap(path: Path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    project_name = derive_project_name(path)

    tasks = []
    task_ids = set()

    for index, line in enumerate(lines):
        if not line.startswith("### "):
            continue

        match = TASK_HEADING_RE.match(line.strip())

        if not match:
            continue

        task_id = match.group("id")

        if task_id in task_ids:
            raise ValueError(
                f"Duplicate task id {task_id} in {path}"
            )

        issue, _ = parse_task(lines, index)

        task_ids.add(task_id)
        tasks.append(issue)

    if not tasks:
        raise ValueError(
            f"No task sections found in {path}. "
            "Expected headings such as '### SL-01 — task-title'."
        )

    for issue in tasks:
        for dependency in issue["blocked_by"]:
            if dependency not in task_ids:
                raise ValueError(
                    f"Task {issue['id']}: blocked_by references unknown "
                    f"task {dependency}"
                )

    return {
        "project": project_name,
        "roadmap": str(path),
        "issues": tasks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--files",
        required=True,
        help="Newline-separated roadmap files",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = [
        Path(line.strip())
        for line in args.files.splitlines()
        if line.strip()
    ]

    if not files:
        print("ERROR: no roadmap files supplied", file=sys.stderr)
        return 1

    failed = False

    for path in files:
        print()
        print("=" * 60)
        print(f"Parsing: {path}")
        print("=" * 60)

        try:
            data = parse_roadmap(path)

            output_name = path.stem

            if output_name.endswith("-roadmap"):
                output_name = output_name[:-8]

            output = output_dir / f"{output_name}.json"

            output.write_text(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=False,
                ) + "\n",
                encoding="utf-8",
            )

            print(f"Project: {data['project']}")
            print(f"Issues:  {len(data['issues'])}")
            print(f"Output:  {output}")

        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
