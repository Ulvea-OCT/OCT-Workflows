#!/usr/bin/env python3
"""
Apply a parsed roadmap JSON to GitHub.

The roadmap JSON is produced by parse-roadmap.py.
This script is intentionally generic: it does not know about SL-* IDs or a
specific repository. A task may optionally specify a "repo" field; when omitted, the issue lives in the repository running the workflow.

Authentication:
  GH_TOKEN (or GITHUB_TOKEN) must be able to:
    - create/update issues in all target repositories
    - read/write the organization/user GitHub Project
    - manage Project fields/items
    - add issue dependencies

For organization-owned Projects, a fine-grained PAT or GitHub App token is
recommended. The workflow passes it as ROADMAP_PROJECT_TOKEN -> GH_TOKEN.

The script is idempotent:
  - project is found by owner + title before creation
  - issues are found by a stable roadmap marker before creation
  - existing issues are updated
  - project fields are found/created by name
  - existing project items are reused
  - dependencies are only added when missing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
API_VERSION = "2026-03-10"
MARKER_RE = re.compile(r"<!--\s*roadmap-id:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*-->")


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str):
        self.token = token

    def _request(self, url: str, method: str = "GET", body: Any = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "roadmap-project-compiler",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            raise GitHubError(f"{method} {url} -> HTTP {exc.code}: {payload}") from exc

    def rest(self, path: str, method: str = "GET", body: Any = None) -> Any:
        return self._request(f"{API}{path}", method, body)

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._request(
            GRAPHQL,
            "POST",
            {"query": query, "variables": variables or {}},
        )
        if payload.get("errors"):
            raise GitHubError(f"GraphQL errors: {json.dumps(payload['errors'], indent=2)}")
        return payload["data"]


def owner_kind(value: str) -> str:
    value = value.lower()
    if value not in {"organization", "user"}:
        raise ValueError("PROJECT_OWNER_TYPE must be 'organization' or 'user'")
    return value


def get_owner_node_id(gh: GitHubClient, owner: str, kind: str) -> str:
    if kind == "organization":
        data = gh.graphql(
            """
            query($login:String!) {
              organization(login:$login) { id login }
            }
            """,
            {"login": owner},
        )
        node = data.get("organization")
    else:
        data = gh.graphql(
            """
            query($login:String!) {
              user(login:$login) { id login }
            }
            """,
            {"login": owner},
        )
        node = data.get("user")

    if not node:
        raise GitHubError(f"Cannot resolve Project owner '{owner}' as {kind}")
    return node["id"]


def find_project(gh: GitHubClient, owner: str, kind: str, title: str) -> dict[str, Any] | None:
    query_field = "organization" if kind == "organization" else "user"
    query = f"""
    query($login:String!, $first:Int!) {{
      {query_field}(login:$login) {{
        projectsV2(first:$first) {{
          nodes {{ id number title url closed }}
        }}
      }}
    }}
    """
    data = gh.graphql(query, {"login": owner, "first": 100})
    node = data.get(query_field)
    if not node:
        raise GitHubError(f"Cannot read Projects for '{owner}'")
    for project in node["projectsV2"]["nodes"]:
        if project and project["title"] == title and not project["closed"]:
            return project
    return None


def create_project(gh: GitHubClient, owner_id: str, title: str) -> dict[str, Any]:
    data = gh.graphql(
        """
        mutation($ownerId:ID!, $title:String!) {
          createProjectV2(input:{ownerId:$ownerId, title:$title}) {
            projectV2 { id number title url }
          }
        }
        """,
        {"ownerId": owner_id, "title": title},
    )
    return data["createProjectV2"]["projectV2"]


def update_project_description(gh: GitHubClient, project_id: str, title: str, roadmap: str) -> None:
    gh.graphql(
        """
        mutation($projectId:ID!, $short:String!, $readme:String!) {
          updateProjectV2(input:{
            projectId:$projectId,
            shortDescription:$short,
            readme:$readme
          }) {
            projectV2 { id }
          }
        }
        """,
        {
            "projectId": project_id,
            "short": f"Generated from {roadmap}",
            "readme": (
                f"# {title}\n\n"
                f"Generated from `{roadmap}`.\n\n"
                "Markdown is the declarative source of truth. "
                "GitHub Issues/Project are the execution layer."
            ),
        },
    )


def list_project_views(gh: GitHubClient, project_id: str) -> list[dict[str, Any]]:
    data = gh.graphql(
        """
        query($id:ID!) {
          node(id:$id) {
            ... on ProjectV2 {
              views(first:100) {
                nodes { id name number layout }
              }
            }
          }
        }
        """,
        {"id": project_id},
    )
    return data["node"]["views"]["nodes"]


def ensure_roadmap_view(
    gh: GitHubClient,
    project_id: str,
    fields: dict[str, dict[str, Any]],
    dry_run: bool,
) -> None:
    """Ensure a native GitHub Roadmap view exists.

    GitHub's roadmap layout uses project date/iteration fields to position items.
    We create the Start/Target date fields before the view, so GitHub can use
    those existing date fields when configuring the new roadmap view.
    """
    if dry_run:
        print("✓ Roadmap view would be created/configured")
        return

    views = list_project_views(gh, project_id)
    roadmap_view = next(
        (view for view in views if view and view.get("layout") == "ROADMAP_LAYOUT"),
        None,
    )
    if roadmap_view is None:
        named = next((view for view in views if view and view.get("name") == "Roadmap"), None)
        if named:
            roadmap_view = named

    if roadmap_view:
        data = gh.graphql(
            """
            mutation($viewId:ID!, $name:String!, $layout:ProjectV2ViewLayout!) {
              updateProjectV2View(input:{
                viewId:$viewId,
                name:$name,
                layout:$layout
              }) {
                projectV2View { id name number layout }
              }
            }
            """,
            {
                "viewId": roadmap_view["id"],
                "name": "Roadmap",
                "layout": "ROADMAP_LAYOUT",
            },
        )
        view = data["updateProjectV2View"]["projectV2View"]
        print(f"✓ Roadmap view ready: {view['name']} #{view['number']}")
        return

    data = gh.graphql(
        """
        mutation($projectId:ID!, $name:String!, $layout:ProjectV2ViewLayout!) {
          createProjectV2View(input:{
            projectId:$projectId,
            name:$name,
            layout:$layout
          }) {
            projectV2View { id name number layout }
          }
        }
        """,
        {
            "projectId": project_id,
            "name": "Roadmap",
            "layout": "ROADMAP_LAYOUT",
        },
    )
    view = data["createProjectV2View"]["projectV2View"]
    print(f"✓ Roadmap view created: {view['name']} #{view['number']}")


def list_project_fields(gh: GitHubClient, project_id: str) -> list[dict[str, Any]]:
    data = gh.graphql(
        """
        query($id:ID!) {
          node(id:$id) {
            ... on ProjectV2 {
              fields(first:100) {
                nodes {
                  __typename
                  ... on ProjectV2FieldCommon { id name dataType }
                  ... on ProjectV2SingleSelectField { id name dataType options { id name } }
                }
              }
            }
          }
        }
        """,
        {"id": project_id},
    )
    return data["node"]["fields"]["nodes"]


def ensure_field(
    gh: GitHubClient,
    project_id: str,
    name: str,
    data_type: str,
    options: list[str] | None = None,
) -> dict[str, Any]:
    fields = list_project_fields(gh, project_id)
    for field in fields:
        if field and field.get("name") == name:
            return field

    if data_type == "SINGLE_SELECT":
        # GitHub requires every single-select option to include a non-null
        # color and description. The roadmap only supplies the option names,
        # so assign stable generic colors/descriptions here.
        colors = ["GRAY", "BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PURPLE", "PINK"]
        option_payload = [
            {
                "name": str(x),
                "color": colors[i % len(colors)],
                "description": f"Roadmap {name} option: {x}",
            }
            for i, x in enumerate(options or [])
        ]
        mutation = """
        mutation($projectId:ID!, $name:String!, $options:[ProjectV2SingleSelectFieldOptionInput!]) {
          createProjectV2Field(input:{
            projectId:$projectId,
            name:$name,
            dataType:SINGLE_SELECT,
            singleSelectOptions:$options
          }) {
            projectV2Field {
              __typename
              ... on ProjectV2SingleSelectField {
                id name dataType options { id name }
              }
              ... on ProjectV2FieldCommon { id name dataType }
            }
          }
        }
        """
        data = gh.graphql(
            mutation,
            {"projectId": project_id, "name": name, "options": option_payload},
        )
    else:
        mutation = """
        mutation($projectId:ID!, $name:String!, $dataType:ProjectV2CustomFieldType!) {
          createProjectV2Field(input:{
            projectId:$projectId,
            name:$name,
            dataType:$dataType
          }) {
            projectV2Field {
              __typename
              ... on ProjectV2FieldCommon { id name dataType }
              ... on ProjectV2SingleSelectField { id name dataType options { id name } }
            }
          }
        }
        """
        data = gh.graphql(
            mutation,
            {"projectId": project_id, "name": name, "dataType": data_type},
        )
    return data["createProjectV2Field"]["projectV2Field"]


def find_issue_by_marker(
    gh: GitHubClient, repo: str, roadmap_id: str
) -> dict[str, Any] | None:
    owner, name = repo.split("/", 1)
    page = 1
    while True:
        issues = gh.rest(
            f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/issues"
            f"?state=all&per_page=100&page={page}"
        )
        if not issues:
            return None
        for issue in issues:
            # Pull requests also appear in the issues endpoint; ignore them.
            if issue.get("pull_request"):
                continue
            body = issue.get("body") or ""
            match = MARKER_RE.search(body)
            if match and match.group(1) == roadmap_id:
                return issue
        if len(issues) < 100:
            return None
        page += 1


def ensure_label(gh: GitHubClient, repo: str, label: str) -> None:
    owner, name = repo.split("/", 1)
    encoded = urllib.parse.quote(label, safe="")
    try:
        gh.rest(f"/repos/{owner}/{name}/labels/{encoded}")
        return
    except GitHubError as exc:
        if "HTTP 404" not in str(exc):
            raise

    gh.rest(
        f"/repos/{owner}/{name}/labels",
        "POST",
        {"name": label, "color": "ededed", "description": "Roadmap compiler label"},
    )


def desired_labels(task: dict[str, Any]) -> list[str]:
    labels = [
        f"priority:{task['priority']}",
        f"phase:{task['phase']}",
        f"workstream:{task['workstream']}",
        f"risk:{task['risk']}",
        f"gate:{task['gate']}",
    ]
    return list(dict.fromkeys(labels))


def build_issue_body(task: dict[str, Any], roadmap: str) -> str:
    marker = f"<!-- roadmap-id: {task['id']} -->"
    metadata = [
        f"- **Roadmap:** `{roadmap}`",
        f"- **Roadmap ID:** `{task['id']}`",
        f"- **Phase:** `{task['phase']}`",
        f"- **Gate:** `{task['gate']}`",
        f"- **Priority:** `{task['priority']}`",
        f"- **Workstream:** `{task['workstream']}`",
        f"- **Risk:** `{task['risk']}`",
        f"- **Effort:** `{task['effort']}`",
        f"- **Declared Start:** `{task['start']}`",
        f"- **Declared Target:** `{task['target']}`",
        f"- **Scheduled Start:** `{task.get('scheduled_start', task['start'])}`",
        f"- **Scheduled Target:** `{task.get('scheduled_target', task['target'])}`",
    ]
    blocked = task.get("blocked_by") or []
    if blocked:
        metadata.append("- **Blocked by:** " + ", ".join(f"`{x}`" for x in blocked))
    metadata.append(f"- **Owners:** `{task.get('owners', 0)}`")

    return (
        f"{marker}\n\n"
        + "\n".join(metadata)
        + "\n\n---\n\n"
        + (task.get("body") or "").strip()
        + "\n"
    )


def ensure_issue(
    gh: GitHubClient,
    task: dict[str, Any],
    roadmap: str,
    default_repo: str,
    dry_run: bool,
) -> dict[str, Any]:
    repo = task.get("repo") or default_repo
    owner, name = repo.split("/", 1)
    title = f"{task['id']} — {task['title']}"
    body = build_issue_body(task, roadmap)
    labels = desired_labels(task)

    existing = find_issue_by_marker(gh, repo, task["id"])
    if dry_run:
        action = "update" if existing else "create"
        print(f"  [{action}] {repo} :: {title}")
        return existing or {"number": 0, "id": 0, "node_id": ""}

    for label in labels:
        ensure_label(gh, repo, label)

    if existing:
        issue_number = existing["number"]
        result = gh.rest(
            f"/repos/{owner}/{name}/issues/{issue_number}",
            "PATCH",
            {"title": title, "body": body, "labels": labels},
        )
        print(f"  ✓ issue #{issue_number} updated: {repo}")
        return result

    result = gh.rest(
        f"/repos/{owner}/{name}/issues",
        "POST",
        {"title": title, "body": body, "labels": labels},
    )
    print(f"  ✓ issue #{result['number']} created: {repo}")
    return result


def project_items(gh: GitHubClient, project_id: str) -> list[dict[str, Any]]:
    data = gh.graphql(
        """
        query($id:ID!, $first:Int!) {
          node(id:$id) {
            ... on ProjectV2 {
              items(first:$first) {
                nodes {
                  id
                  content {
                    __typename
                    ... on Issue { id number repository { nameWithOwner } }
                  }
                }
              }
            }
          }
        }
        """,
        {"id": project_id, "first": 100},
    )
    return data["node"]["items"]["nodes"]


def ensure_project_item(
    gh: GitHubClient,
    project_id: str,
    issue_node_id: str,
    existing_items: list[dict[str, Any]],
    dry_run: bool,
) -> str:
    for item in existing_items:
        content = item.get("content")
        if content and content.get("id") == issue_node_id:
            return item["id"]

    if dry_run:
        return ""

    data = gh.graphql(
        """
        mutation($projectId:ID!, $contentId:ID!) {
          addProjectV2ItemById(input:{
            projectId:$projectId,
            contentId:$contentId
          }) {
            item { id }
          }
        }
        """,
        {"projectId": project_id, "contentId": issue_node_id},
    )
    return data["addProjectV2ItemById"]["item"]["id"]


def set_field(
    gh: GitHubClient,
    project_id: str,
    item_id: str,
    field: dict[str, Any],
    value: Any,
    dry_run: bool,
) -> None:
    if dry_run or not item_id:
        return

    data_type = field.get("dataType")
    field_id = field["id"]

    if data_type == "SINGLE_SELECT":
        option_id = None
        for option in field.get("options", []):
            if option["name"] == str(value):
                option_id = option["id"]
                break
        if not option_id:
            raise GitHubError(
                f"Option '{value}' not found for Project field '{field['name']}'"
            )
        input_value = {"singleSelectOptionId": option_id}
    elif data_type == "DATE":
        input_value = {"date": str(value)}
    elif data_type == "NUMBER":
        input_value = {"number": float(value)}
    elif data_type == "TEXT":
        input_value = {"text": str(value)}
    else:
        raise GitHubError(
            f"Unsupported field data type '{data_type}' for '{field['name']}'"
        )

    gh.graphql(
        """
        mutation(
          $projectId:ID!,
          $itemId:ID!,
          $fieldId:ID!,
          $value:ProjectV2FieldValue!
        ) {
          updateProjectV2ItemFieldValue(input:{
            projectId:$projectId,
            itemId:$itemId,
            fieldId:$fieldId,
            value:$value
          }) {
            projectV2Item { id }
          }
        }
        """,
        {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "value": input_value,
        },
    )


def add_dependency(
    gh: GitHubClient,
    blocked_repo: str,
    blocked_number: int,
    blocking_issue_id: int,
    dry_run: bool,
) -> None:
    if dry_run or not blocked_number:
        return
    owner, name = blocked_repo.split("/", 1)
    # The endpoint is idempotent enough for our purposes: GitHub returns 422
    # if the dependency already exists, so treat that case as success.
    try:
        gh.rest(
            f"/repos/{owner}/{name}/issues/{blocked_number}/dependencies/blocked_by",
            "POST",
            {"issue_id": blocking_issue_id},
        )
        print(f"  ✓ dependency: {blocked_repo}#{blocked_number} blocked by issue id {blocking_issue_id}")
    except GitHubError as exc:
        if "HTTP 422" in str(exc):
            print(f"  ✓ dependency already present: {blocked_repo}#{blocked_number}")
        else:
            raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="Parsed roadmap JSON")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_owner = os.environ.get("PROJECT_OWNER") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    project_owner_type = owner_kind(os.environ.get("PROJECT_OWNER_TYPE", "organization"))
    default_repo = os.environ.get("DEFAULT_REPO") or os.environ.get("GITHUB_REPOSITORY")

    if not default_repo or "/" not in default_repo:
        print("ERROR: DEFAULT_REPO or GITHUB_REPOSITORY must be owner/repository", file=sys.stderr)
        return 2
    if not project_owner:
        print("ERROR: PROJECT_OWNER is required", file=sys.stderr)
        return 2

    with open(args.json, encoding="utf-8") as fh:
        payload = json.load(fh)

    title = payload["project"]
    roadmap = payload["roadmap"]
    tasks = payload["issues"]

    print("=" * 60)
    print(f"Roadmap: {roadmap}")
    print(f"Project: {title}")
    print(f"Tasks:   {len(tasks)}")
    scheduler = payload.get("scheduler") or {}
    print(f"Max parallel tasks: {scheduler.get('max_parallel_tasks', 4)}")
    print(f"Owner:   {project_owner} ({project_owner_type})")
    print(f"Default repo: {default_repo}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 60)

    # DRY RUN is deliberately API-free: no token, REST, or GraphQL calls.
    if args.dry_run:
        field_specs = {
            "Priority": ("SINGLE_SELECT", sorted({str(t["priority"]) for t in tasks})),
            "Phase": ("SINGLE_SELECT", sorted({str(t["phase"]) for t in tasks})),
            "Gate": ("SINGLE_SELECT", sorted({str(t["gate"]) for t in tasks})),
            "Workstream": ("SINGLE_SELECT", sorted({str(t["workstream"]) for t in tasks})),
            "Risk": ("SINGLE_SELECT", sorted({str(t["risk"]) for t in tasks})),
            "Effort": ("SINGLE_SELECT", sorted({str(t["effort"]) for t in tasks})),
            "Start": ("DATE", None),
            "Target": ("DATE", None),
            "Owners": ("NUMBER", None),
        }

        print(f"Project would be created or updated: {title}")
        print("\nPROJECT")
        print("  [would create/update] Project")
        print("  [would update] Project description")
        for name in field_specs:
            print(f"  [would create/update] field: {name}")
        print("  [would create/configure] Roadmap view")

        print("\nISSUES")
        for task in tasks:
            repo = task.get("repo") or default_repo
            issue_title = f"{task['id']} — {task['title']}"
            print(f"  [would create/update] {repo} :: {issue_title}")

        print("\nPROJECT ITEMS")
        for task in tasks:
            repo = task.get("repo") or default_repo
            issue_title = f"{task['id']} — {task['title']}"
            print(f"  [would add/update] {repo} :: {issue_title}")

        print("\nDEPENDENCIES")
        for task in tasks:
            for blocker_id in task.get("blocked_by") or []:
                print(f"  [would add] {task['id']} blocked by {blocker_id}")

        print("\nDONE")
        print("Dry run complete. No GitHub API calls or mutations were performed.")
        return 0

    # APPLY
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GH_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    gh = GitHubClient(token)

    owner_id = get_owner_node_id(gh, project_owner, project_owner_type)
    project = find_project(gh, project_owner, project_owner_type, title)
    if project:
        print(f"✓ Project exists: #{project['number']} {project['url']}")
    else:
        project = create_project(gh, owner_id, title)
        print(f"✓ Project created: #{project['number']} {project['url']}")
    update_project_description(gh, project["id"], title, roadmap)

    field_specs = {
        "Priority": ("SINGLE_SELECT", sorted({str(t["priority"]) for t in tasks})),
        "Phase": ("SINGLE_SELECT", sorted({str(t["phase"]) for t in tasks})),
        "Gate": ("SINGLE_SELECT", sorted({str(t["gate"]) for t in tasks})),
        "Workstream": ("SINGLE_SELECT", sorted({str(t["workstream"]) for t in tasks})),
        "Risk": ("SINGLE_SELECT", sorted({str(t["risk"]) for t in tasks})),
        "Effort": ("SINGLE_SELECT", sorted({str(t["effort"]) for t in tasks})),
        "Start": ("DATE", None),
        "Target": ("DATE", None),
        "Owners": ("NUMBER", None),
    }

    fields = {}
    for name, (dtype, options) in field_specs.items():
        fields[name] = ensure_field(gh, project["id"], name, dtype, options)
        print(f"✓ field ready: {name}")

    ensure_roadmap_view(gh, project["id"], fields, False)

    issue_map = {}
    project_item_ids = {}

    print("\nISSUES")
    for task in tasks:
        issue = ensure_issue(gh, task, roadmap, default_repo, False)
        issue_map[task["id"]] = issue

    existing_items = project_items(gh, project["id"])
    print("\nPROJECT ITEMS")
    for task in tasks:
        issue = issue_map[task["id"]]
        item_id = ensure_project_item(gh, project["id"], issue["node_id"], existing_items, False)
        project_item_ids[task["id"]] = item_id
        existing_items.append({"id": item_id, "content": {"id": issue["node_id"]}})
        print(f"  ✓ {task['id']} -> project item")

        for field_name, value in {
            "Priority": task["priority"], "Phase": task["phase"], "Gate": task["gate"],
            "Workstream": task["workstream"], "Risk": task["risk"], "Effort": task["effort"],
            "Start": task.get("scheduled_start", task["start"]),
            "Target": task.get("scheduled_target", task["target"]),
            "Owners": task.get("owners", 0),
        }.items():
            set_field(gh, project["id"], item_id, fields[field_name], value, False)

    print("\nDEPENDENCIES")
    for task in tasks:
        for blocker_id in task.get("blocked_by") or []:
            blocked_issue = issue_map[task["id"]]
            blocker_issue = issue_map.get(blocker_id)
            if not blocker_issue:
                raise GitHubError(f"{task['id']} depends on unknown roadmap ID '{blocker_id}'")
            add_dependency(gh, task.get("repo") or default_repo, blocked_issue["number"], blocker_issue["id"], False)

    print("\nDONE")
    print(f"Project: {project['url']}")
    print(f"Applied {len(tasks)} roadmap task(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GitHubError, KeyError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
