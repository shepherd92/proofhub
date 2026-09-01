## What changed

Briefly describe the project, run, or site change.

## Proof-run review checklist

- [ ] Published run directories were not overwritten; new work uses a new run ID.
- [ ] The exact prompt, visible event log, final output, model, architecture, timing, and configuration are recorded.
- [ ] Claims in `status.yaml` do not exceed their linked evidence or verification level.
- [ ] `current_run` is the intended continuation base and appears in `as_of_runs`.
- [ ] Prompts, outputs, logs, environment files, artifacts, and `raw/` were checked for secrets and private data.
- [ ] Every newly published run sets `draft: false` and `publication.redaction_reviewed: true`.
- [ ] `submitted_by` is present only when imported run material should credit someone other than the commit author; co-contributors are credited with `Co-authored-by:` trailers.
- [ ] `python3 scripts/prepare_projects.py --check` and the test suite pass locally, or GitHub Actions passes.
