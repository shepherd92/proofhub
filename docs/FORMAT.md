# Proof Relay bundle format v1

The source of truth is a directory under `content/projects/`. Hugo pages and downloads are derived from it.

```text
content/projects/<project-id>/
├── index.md
├── problem.txt
├── status.yaml
└── runs/<run-id>/
    ├── run.yaml
    ├── prompt.txt
    ├── output.txt
    ├── events.jsonl
    ├── verification.log        # optional
    ├── artifacts/
    └── raw/                    # optional; excluded from handoff ZIPs
```

## Invariants

- `project_id` equals the project directory name.
- Every project page sets `build.publishResources: false`; Hugo publishes only resources deliberately linked by the dashboard.
- `run_id` equals the run directory name.
- Runs are immutable; corrections and continuations use new IDs.
- Parent links form an acyclic graph within one project.
- `status.yaml` is the supervisor-approved current view and can cite several runs.
- Mathematical outcome and verification strength are separate fields.
- Every claim in project status links to existing evidence.
- All paths are relative, forward-slash paths contained in the project directory.
- Symlinks are rejected.
- `redaction_reviewed` must be true before publication.
- Verification commands are recorded but never executed by the website build.
- Files are limited to 25 MiB each and complete project directories to 100 MiB.

## Minimum project page

`index.md` supplies the HugoBlox card and invokes the generated dashboard:

```yaml
---
title: A concise problem title
date: 2026-08-24
summary: One sentence for the project list.
project_id: my-problem
draft: false
build:
  publishResources: false
---

Short human introduction.

{{< proof-project >}}
```

Use `draft: true` while staging a bundle. Drafts do not receive generated JSON, continuation prompts, or handoff ZIPs.

## Outcome values

- `no_progress`
- `partial`
- `candidate_proof`
- `proved`
- `candidate_refutation`
- `refuted`

`proved` and `refuted` require either `machine_checked` or `supervisor_reviewed` verification plus at least one established claim with evidence. A terminal run also needs a verification log; `machine_checked` additionally needs the exact verification command.

## Verification values

- `unverified`
- `failed`
- `agent_reported`
- `reproduced`
- `machine_checked`
- `supervisor_reviewed`

## Events

`events.jsonl` contains one JSON object per nonblank line. `seq` begins at 1 and is contiguous. Supported event types are `message`, `tool_call`, `tool_result`, `checkpoint`, `error`, and `note`.

Record visible prompts, outputs, tool activity, errors, and checkpoints. Do not attempt to publish hidden chain-of-thought. Large event content should live in a referenced file.

## Generated files

`scripts/prepare_projects.py --generate` creates:

```text
data/generated/proof_projects/<project-id>.json
static/generated/<project-id>/continuation.txt
static/generated/<project-id>/handoff.zip
```

Generated files are ignored by Git. GitHub Actions regenerates them before every Hugo build.

An optional `raw/` directory is not linked or packaged. It is still present in Git, so it must not contain credentials, private data, or anything unsuitable for repository readers.

Schemas are versioned under `schemas/`. New fields should first use the relevant `extensions` object. A future incompatible format should add v2 schemas and retain a v1 importer.
