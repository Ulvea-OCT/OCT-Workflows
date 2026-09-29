# OCT Workflows

This repository contains the shared GitHub Actions workflows used across OCT
projects.

The purpose of this repository is to centralize workflow implementation,
versioning, and technical documentation so that individual project
repositories can consume stable, reusable workflow versions.

## Repository

The shared workflow repository is:

```text
Ulvea-OCT/workflows
```

Project repositories should consume workflows from this repository using an
explicit Git tag.

## Available Workflows

| Workflow | Purpose | Current version |
|---|---|---:|
| Roadmap to GitHub Project | Parses, validates, schedules and optionally applies Markdown roadmaps to GitHub Issues and Projects | v1.0 |

Additional workflows can be added to this repository as the OCT automation
library evolves.

## Workflow Architecture

A reusable workflow may be composed of:

- a reusable workflow under `.github/workflows/`;
- composite actions under `.github/actions/`;
- Python or other supporting scripts;
- configuration and dependency files.

A project repository normally contains only the caller workflow. The
implementation remains centralized in this repository.

For example:

```text
Project repository
└── .github/
    └── workflows/
        └── roadmap-to-project.yml
                    │
                    ▼
Ulvea-OCT/workflows
└── .github/workflows/
    └── roadmap-to-project.yml
                    │
                    ▼
            shared implementation
```

## Versioning

Workflows are consumed through Git tags.

Example:

```yaml
uses: Ulvea-OCT/workflows/.github/workflows/roadmap-to-project.yml@v1.0
```

The tag is the version contract between the workflow library and consuming
repositories.

Existing projects should not depend on `@main` for production automation.
Changes should be released through a new version tag.

When a new compatible release is available, consuming repositories can
explicitly update their `uses:` reference.

## Roadmap to GitHub Project

The Roadmap to GitHub Project workflow processes Markdown roadmap files and
can:

1. parse roadmap task metadata;
2. validate the roadmap;
3. schedule tasks according to dependencies;
4. optionally create or update GitHub Issues and Projects.

The workflow supports a dry-run mode so that parsing and scheduling can be
validated before GitHub resources are modified.

### Documentation

The implementation-specific documentation for each workflow should live
alongside the workflow implementation.

For the Roadmap to GitHub Project workflow, document:

- supported inputs;
- expected roadmap structure;
- required secrets;
- dry-run behaviour;
- apply behaviour;
- generated files;
- permissions;
- dependency and scheduling rules;
- troubleshooting;
- version-specific changes.

## Secrets and Authentication

Secrets required by a workflow should be documented by that workflow.

For the Roadmap to GitHub Project workflow, the project integration uses:

```text
ROADMAP_PROJECT_TOKEN
```

The recommended configuration is an Organization Secret in `Ulvea-OCT`.

Consuming repositories should use:

```yaml
secrets: inherit
```

when the reusable workflow is designed to receive inherited secrets.

Repository-level secrets with the same name should be avoided unless an
explicit repository-specific override is required.

## Adding a New Workflow

When adding a new shared workflow:

1. Add the reusable workflow under `.github/workflows/`.
2. Add any supporting composite action under `.github/actions/`.
3. Add supporting scripts and dependencies.
4. Document the workflow.
5. Test the workflow independently.
6. Create a version tag.
7. Update the workflow inventory in this README.
8. Update the consuming templates when the new workflow is intended to be
   part of the standard template.

A new workflow should have a clear versioning strategy before it is consumed
by project repositories.

## Relationship with the Project Template

The standard project template is maintained separately:

```text
template-repo-v1.0
```

The template documents **which versions of shared workflows it consumes**.

This repository documents **how those workflows work and are maintained**.

This separation avoids duplicating technical workflow documentation across
project repositories.

## Development Principles

Shared workflows should:

- be explicitly versioned;
- avoid hidden dependencies on individual project repositories;
- expose clear inputs and outputs;
- document required permissions and secrets;
- support safe validation where practical;
- keep implementation centralized;
- preserve backwards compatibility within a version where possible.

## Release Checklist

Before publishing a workflow version:

```text
1. Validate workflow syntax
        ↓
2. Test reusable workflow
        ↓
3. Test composite action(s)
        ↓
4. Test with a consuming repository
        ↓
5. Verify secrets and permissions
        ↓
6. Create/update version tag
        ↓
7. Update workflow inventory
```
