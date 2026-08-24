---
title: Add or update a project
summary: The minimal contribution workflow for the GitHub-backed MWE.
show_date: false
show_breadcrumb: true
---

This first version deliberately uses GitHub for access control and review. The public website is static and does not accept files directly.

## Add a new project

1. Copy `content/projects/euclid-primes/` to a new lowercase directory name.
2. Replace the example problem, status, run metadata, prompt, output, events, and artifacts.
3. Keep `project_id` equal to the project directory name, `run_id` equal to its run directory name, and `build.publishResources: false` in `index.md`.
4. Leave `draft: true` until the bundle is complete and redaction-reviewed; drafts receive no public continuation files.
5. Run `python3 scripts/prepare_projects.py --check` locally if possible.
6. Commit the new directory and open a pull request, or push it directly if you are an approved maintainer.

The build checks the schema, lineage, timestamps, event sequence, evidence links, referenced files, and redaction acknowledgement before publication.

## Update an existing project

Runs are immutable. Add a new `runs/<run-id>/` directory, point its `parents` field at the run it continues, and update `status.yaml` after reviewing the result. Do not overwrite the earlier prompt or output.

## Publication safety

Everything merged into a public GitHub Pages repository can become public, including source files that the website itself does not link. Remove API keys, credentials, private material, personal data, and machine-specific secrets before committing. Verification commands are displayed for reproducibility but are never executed by the website build.
