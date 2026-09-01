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
- A run with `draft: true` is validated but omitted from public data, run history, status evidence, and handoffs.
- Parent links form an acyclic graph within one project.
- `status.yaml` is the supervisor-approved current view and can cite several runs.
- `current_run` must appear in `as_of_runs`; neither may refer to a draft run.
- Mathematical outcome and verification strength are separate fields.
- Every claim in project status links to existing evidence.
- `supervisor_reviewed` project status records the review time. The author of the latest `status.yaml` commit is the default reviewer; `reviewed_by` names a different reviewer when necessary.
- Public repository attribution is generated from Git history and never stores or publishes a Git email address.
- A terminal project status is supported by a matching terminal, strongly verified run in `as_of_runs`.
- All paths are relative, forward-slash paths contained in the project directory.
- Symlinks, ambiguous case-equivalent paths, unsafe archive/URL characters, YAML aliases, and non-JSON YAML values are rejected.
- `redaction_reviewed` must be true before publication.
- Verification commands are recorded but never executed by the website build.
- Files are limited to 25 MiB each, event logs to 10 MiB, structured metadata to 1 MiB, complete projects to 100 MiB, directory depth to 16, entries to 2,000, and runs to 500.
- Web-executable artifacts remain handoff-only instead of being served from the website's origin.

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

Use `draft: true` while staging a bundle. Drafts are still fully validated, but do not receive generated JSON, continuation prompts, or handoff ZIPs.

Individual runs may also set `draft: true`. This lets a complete run directory be committed and reviewed without exposing it or requiring `publication.redaction_reviewed: true`. A published status may not cite a draft run. Remove the draft flag (or set it to `false`) only after the run is complete and redaction-reviewed.

## Outcome values

- `no_progress`
- `partial`
- `candidate_proof`
- `proved`
- `candidate_refutation`
- `refuted`

`proved` and `refuted` require either `machine_checked` or `supervisor_reviewed` verification plus at least one established claim with evidence. A terminal run also needs a verification log; `machine_checked` additionally needs the exact verification command.

When project status uses `supervisor_reviewed`, it also contains:

```yaml
review:
  reviewed_at: 2026-08-24T10:00:00Z
  note: Optional concise scope of the review.
```

Add `reviewed_by: Supervisor name` only when the reviewer is not the person committing the status. In a run manifest, optional `submitted_by` has a different meaning: it credits the person or group that supplied imported run material, while the generated repository attribution records who added it to Proof Relay.

## Verification values

- `unverified`
- `failed`
- `agent_reported`
- `reproduced`
- `machine_checked`
- `supervisor_reviewed`

## Events

`events.jsonl` contains one strict JSON object per nonblank line. Duplicate keys and non-finite numbers are rejected. `seq` begins at 1 and is contiguous; timestamps are nondecreasing and fall inside completed execution times. Every tool call in a completed run has exactly one later result. Supported event types are `message`, `tool_call`, `tool_result`, `checkpoint`, `error`, and `note`.

Record visible prompts, outputs, tool activity, errors, and checkpoints. Do not attempt to publish hidden chain-of-thought. Large event content should live in a referenced file.

## Generated files

`scripts/prepare_projects.py --generate` creates:

```text
data/generated/proof_projects/<project-id>.json
static/generated/<project-id>/continuation.txt
static/generated/<project-id>/handoff.zip
```

Generated files are ignored by Git. GitHub Actions regenerates them before every Hugo build.

Generated project JSON also includes repository attribution for each run and the current status: a public display label, GitHub profile when resolvable, full and short commit SHA, commit time, and commit URL. The source email is used only for local matching and is never written to generated data. Git-derived enrichment stays out of the handoff ZIP so rebuilding the same mathematical bundle remains reproducible after a rebase or GitHub username change.

An optional `raw/` directory is not linked or packaged. It is still present in Git, so it must not contain credentials, private data, or anything unsuitable for repository readers.

Schemas are versioned under `schemas/`. New fields should first use the relevant `extensions` object. A future incompatible format should add v2 schemas and retain a v1 importer.
