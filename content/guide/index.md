---
title: Add or continue a project
summary: The shortest safe workflow for adding AI proof results.
show_date: false
show_breadcrumb: true
---

Proof Relay is a static website: the website itself never accepts uploads. GitHub supplies the accounts, permissions, review history, and publication step.

## Who can upload?

- A public visitor can read projects and download a continuation package. They cannot push to the repository or change the website. A pull request from an outside fork has no effect unless the owner accepts it.
- The repository owner and approved GitHub collaborators can upload a branch or open a pull request.
- A result appears on the public website only after an authorized change reaches `main` and the automated checks pass.

For this two-person version, add only your supervisor as a collaborator. The site derives the contributor's name from the GitHub account attached to the commit; no name field is needed.

## New project: three steps

1. Prepare only these files:

   ```text
   problem.txt
   result/
   ├── prompt.txt
   ├── output.txt
   └── summary.txt
   ```

   `problem.txt` is the exact mathematical problem. `prompt.txt` and `output.txt` are the exact visible input and final output. `summary.txt` is a short factual description of what the run achieved. Add `result/artifacts/` only when it contains files genuinely useful for checking or continuing the proof.

2. From a checkout of the repository, import the files:

   ```bash
   python3 scripts/proofhub.py import-project my-problem \
     --title "A concise mathematical title" \
     --problem-file problem.txt \
     --run-dir result
   ```

3. Read the generated draft and check every copied file for private data. When it is safe and the summary is accurate, finalize it without editing YAML:

   ```bash
   python3 scripts/proofhub.py finalize-run my-problem --redaction-reviewed
   ```

   The command updates the project status, changes the draft to publishable, and validates the result before writing. Commit to a branch and open a pull request.

The importer refuses to overwrite an existing project and keeps the new project private from the generated site while it is a draft.

## Continue a project: three steps

1. On the project page, click **Download handoff ZIP**. This is the only download needed. Give that ZIP to the next agent.
2. When the run finishes, make a small result directory containing only its new `prompt.txt`, `output.txt`, and `summary.txt`. If the agent used `continuation.txt` unchanged, use that file as `prompt.txt`. Add only new or changed proof files under `artifacts/`; do not upload the handoff ZIP or copies of the previous runs.
3. Import and review the result:

   ```bash
   python3 scripts/proofhub.py import-run my-problem --run-dir result
   ```

   The importer creates the next draft run and links it to the current continuation base. Read the draft and check its files and mathematical claim, then run:

   ```bash
   python3 scripts/proofhub.py finalize-run my-problem --redaction-reviewed
   ```

   Finalization makes this run the continuation base and updates the public summary without weakening a stronger existing classification. It validates everything before writing. Commit to a branch and open a pull request. If more than one draft exists, select the intended one with `--run-id run-NNN`.

Published runs are immutable. A correction or another attempt is always a new run, never a replacement of an old prompt or output.

## What is optional?

The minimum import is deliberately small: problem, prompt, output, and a factual summary. Model name, agent architecture, run time, token limits, configuration, event history, and artifacts are useful when known, but they should not block an import. Unknown metadata remains marked `not-recorded`.

Imports conservatively default to `no_progress`. When the summary clearly records progress, add `--outcome partial`, `--outcome candidate_proof`, or `--outcome candidate_refutation` to the import command. These are result classifications, not claims that somebody has verified the result.

Add `events.jsonl` only when a visible event log was actually preserved. Add proof code, notebooks, counterexamples, or verification logs only when they help another person check or continue the work. A `proved`, `refuted`, `reproduced`, or `machine_checked` label needs matching evidence and, where applicable, a verification log.

Never include hidden chain-of-thought. Preserve the visible prompt and final visible output without rewriting either after the fact.

## One-time administrator setup

1. In **Repository settings → Collaborators**, invite the supervisor with write access. Do not give public visitors write access.
2. In **Settings → Pages**, choose **GitHub Actions** as the source.
3. Protect `main` in **Settings → Rules → Rulesets** (shown as **Branches** in some GitHub layouts). Require the automated build to pass; require one pull-request approval if the owner and supervisor will review each other's uploads.
4. Keep deployment restricted to pushes to `main`. A branch or pull request is validated, but does not publish the website.

The repository owner can remove a collaborator at any time. Merging to `main` is the publication decision.

## Browser upload fallback

The import command is the safest way to create the correct directory structure. If a collaborator prefers GitHub's browser interface, first run the importer on any checkout, then use **Add file → Upload files** to upload the generated project or run directory to a new branch and open a pull request. Preserve the directory hierarchy.

The browser editor is suitable for reviewing the generated summaries. Run both the importer and finalizer before uploading, then upload every file they changed. Building a complete run file by file in the browser is possible but easy to get wrong; for the simplest two-person workflow, the supervisor can provide the four minimum text files and the repository owner can run the importer with `--submitted-by "Supervisor Name"` when the supplier and committer are different.

## Final safety check

Before passing `--redaction-reviewed`, inspect every new file for API keys, tokens, cookies, passwords, private URLs, personal information, local home-directory paths, and unpublished material. Everything committed to a public repository is public even when the website does not link to it.

Run this check before every push:

```bash
python3 scripts/prepare_projects.py --check
```

For a full local check, also run:

```bash
python3 -m unittest discover -s tests -v
pnpm run build
```

Validation errors name the affected file and field. The website build reads uploaded files but never executes submitted proof code or recorded verification commands.
