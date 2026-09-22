# OCT-Workflows

Centralized GitHub Actions framework for the **Ulvea-OCT** organization.

`OCT-Workflows` contains the reusable automation logic shared by project repositories. Project-specific repositories consume this repository through versioned reusable workflows and composite actions.

## Purpose

The architecture separates responsibilities:

- **OCT-Workflows** — centralized reusable workflows, composite actions, and automation scripts.
- **OCT-Template** — bootstrap template for new repositories.
- **Project repositories** — project-specific source code, configuration, and caller workflows.

## Repository structure

```text
OCT-Workflows/
├── .github/
│   ├── workflows/
│   │   ├── roadmap.yml
│   │   └── ...
│   └── actions/
│       ├── roadmap/
│       │   ├── action.yml
│       │   └── scripts/
│       │       ├── parse-roadmap.py
│       │       ├── schedule-roadmap.py
│       │       └── apply-roadmap.py
│       └── ...
└── README.md
```

## Reusable workflows

Reusable workflows are the entry points used by project repositories.

```yaml
jobs:
  roadmap:
    uses: Ulvea-OCT/OCT-Workflows/.github/workflows/roadmap.yml@v1
    secrets: inherit
```

## Composite actions

Composite actions contain shared implementation and run against the caller repository's checked-out workspace.

This avoids duplicating implementation in every project repository and avoids requiring a second checkout of the private `OCT-Workflows` repository.

## Scripts

Python scripts belong to the relevant composite action and are not copied into project repositories.

## Versioning

Project repositories should consume stable major versions:

```yaml
uses: Ulvea-OCT/OCT-Workflows/.github/workflows/roadmap.yml@v1
```

Avoid using `@main` in production workflows.

Breaking changes should be introduced under a new major version:

```text
v1
v2
```

## Design principles

1. Centralize shared logic.
2. Keep project repositories lightweight.
3. Do not hard-code a specific project repository into reusable workflows.
4. Use `github.repository` for the calling repository when a target repository is required.
5. Keep organization-level configuration in organization variables and secrets where appropriate.
6. Prefer reusable workflows for orchestration and composite actions for implementation.
7. Version reusable interfaces.
8. Keep project-specific behavior in the project repository.

## Roadmap automation

The roadmap workflow is responsible for:

1. Reading a roadmap from the caller repository.
2. Parsing task metadata.
3. Scheduling tasks while respecting dependencies and parallelism.
4. Creating or updating GitHub Issues.
5. Creating or updating an organization-owned GitHub Project.
6. Creating and maintaining project fields and roadmap metadata.
7. Applying relationships such as dependencies.

The implementation is centralized so improvements can be released through the reusable workflow version selected by each project.

## Configuration

Typical organization-level configuration:

```text
ROADMAP_PROJECT_OWNER=Ulvea-OCT
ROADMAP_PROJECT_OWNER_TYPE=organization
```

The target repository should normally come from:

```text
GITHUB_REPOSITORY
```

rather than being hard-coded.

If the automation requires a GitHub token, use an organization secret such as:

```text
ROADMAP_PROJECT_TOKEN
```

Centralizing a secret does not increase the permissions of the underlying token. The token must still have access to every repository and organization resource required by the automation.

For a larger organization, consider using a GitHub App instead of a repository-scoped PAT.

## Access and security

`OCT-Workflows` is infrastructure and should be protected accordingly.

Recommended controls include:

- Pull requests required for changes to `main`.
- At least one approval.
- Required CI checks once stable checks exist.
- Conversation resolution.
- No force pushes.
- No branch deletion.
- Limited administrative bypass.
- Additional security checks as the organization matures.

Organization-level rulesets should provide the common baseline. Repository-specific rulesets may add stricter requirements.

## Relationship with OCT-Template

`OCT-Template` provides the initial repository structure and caller workflows.

It should not contain copies of the Python implementation from this repository.

Example caller workflow:

```yaml
name: Roadmap

on:
  push:
    paths:
      - "roadmaps/**"
  workflow_dispatch:

jobs:
  roadmap:
    uses: Ulvea-OCT/OCT-Workflows/.github/workflows/roadmap.yml@v1
    secrets: inherit
```

Changes to `OCT-Template` do not retroactively modify repositories previously created from it. Ongoing shared behavior must therefore live in `OCT-Workflows`.

## Development workflow

A typical change follows this process:

1. Create a branch in `OCT-Workflows`.
2. Implement or update the reusable workflow/action.
3. Test the workflow against a suitable project repository.
4. Open a pull request.
5. Merge into `main`.
6. Update the relevant version tag when releasing a compatible change.
7. Create a new major version for breaking changes.

## Related repositories

- **OCT-Template** — repository bootstrap/template.
- **Project repositories** — repositories consuming the framework.

## Status

This repository is the central automation layer for the Ulvea-OCT GitHub repository architecture.
