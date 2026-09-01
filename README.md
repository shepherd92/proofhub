# Proof Relay MWE

Proof Relay is a minimal HugoBlox website for publishing AI-assisted mathematical proof runs. It answers three questions for every project:

1. What is the current mathematical status, and how strongly has it been verified?
2. Which prompts, models, agent architecture, configuration, outputs, and artifacts produced that status?
3. What should the next agent download to continue from the best known state?

The website is static. GitHub provides contribution access, review, history, attribution, and deployment. No database, API key, account system, or upload server is required for this MWE.

## Who can publish?

Public visitors can read projects and download handoff packages, but they cannot upload to the website or push to the repository. Only the repository owner and approved GitHub collaborators can push changes. An outsider may propose a pull request from a fork, but it has no effect unless the owner accepts it. A draft becomes public only after it is reviewed, merged to `main`, and passes the automated build.

For a two-person deployment, invite the supervisor as the only collaborator and protect `main`. GitHub commit history supplies the uploader's username automatically.

## New project in three steps

1. Prepare `problem.txt` plus a result directory containing only `prompt.txt`, `output.txt`, and `summary.txt`. An `artifacts/` directory is optional.
2. Import it as a safe, unpublished draft:

   ```bash
   python3 scripts/proofhub.py import-project my-problem \
     --title "A concise mathematical title" \
     --problem-file problem.txt \
     --run-dir result
   ```

3. Review the draft, then run `python3 scripts/proofhub.py finalize-run my-problem --redaction-reviewed`. Commit the validated result to a branch and open a pull request.

Imports default to the conservative `no_progress` classification. Add `--outcome partial`, `--outcome candidate_proof`, or `--outcome candidate_refutation` only when the factual summary supports it; verification remains a separate review decision.

## Continue a project in three steps

1. Click **Download handoff ZIP** on the project page and give that one package to the next agent.
2. Keep only the new run's `prompt.txt`, `output.txt`, `summary.txt`, and genuinely useful new or changed artifacts.
3. Run `python3 scripts/proofhub.py import-run my-problem --run-dir result`, review the draft, then run `python3 scripts/proofhub.py finalize-run my-problem --redaction-reviewed` and open a pull request.

Do not upload the handoff ZIP or duplicate earlier runs. See the [short contribution guide](content/guide/index.md) for the exact review fields, administrator setup, and browser-upload fallback.

## What already works

- A status-aware project catalogue at `/projects/`.
- One self-contained directory per proof project.
- CI-enforced immutable published runs with parent links for continuation, retry, fork, and merge histories, plus editable unpublished draft runs.
- Versioned JSON Schemas for project status and run metadata.
- Validation of IDs, timestamps, lineage, evidence, file references, event logs, and redaction acknowledgement.
- Separate mathematical outcome and verification labels.
- A readable project dashboard with status, open goals, failed attempts, run metadata, prompts, outputs, events, and artifacts.
- Explicit status-review attribution and timestamps.
- Deterministically generated continuation prompts and handoff ZIPs.
- Reproducible ZIP timestamps, permissions, checksums, and file manifest.
- Safe project/run scaffolding that refuses overwrites.
- Automated tests, a subpath-aware broken-link check, and GitHub Pages deployment.

The included `euclid-primes` calibration project intentionally contains an unfinished Lean scaffold. It demonstrates why a plausible natural-language proof and a machine-checked artifact must have different labels.

## Repository structure

```text
content/projects/
├── _index.md
└── euclid-primes/
    ├── index.md
    ├── problem.txt
    ├── status.yaml
    └── runs/run-001/
        ├── run.yaml
        ├── prompt.txt
        ├── output.txt
        ├── events.jsonl
        ├── verification.log
        └── artifacts/Main.lean

schemas/                       Versioned upload schemas
scripts/prepare_projects.py    Validator and deterministic generator
scripts/proofhub.py            Safe project/run import and scaffolding commands
scripts/check_site.py          Post-build internal-link checker
layouts/_shortcodes/           Project dashboard renderer
layouts/projects/list.html     Status-aware project catalogue
assets/css/custom.css          Dashboard styling
tests/                         Validator/generator tests
docs/FORMAT.md                 Bundle-format reference
```

## Local setup

Prerequisites:

- Hugo Extended `0.162.0`
- Go
- Node.js 22 or a current supported Node version
- pnpm
- Python 3.11 or newer

Install the local dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
pnpm install --frozen-lockfile
```

Validate and test the project data:

```bash
python scripts/prepare_projects.py --check
python -m unittest discover -s tests -v
```

Start the local website:

```bash
pnpm run dev
```

The development command validates the uploaded bundles, generates continuation downloads, and then starts Hugo.

Build the production site:

```bash
pnpm run build
```

## Draft and review rules

Both import commands are atomic, reject unsafe identifiers and existing destinations, and create drafts. Draft projects and runs are validated but omitted from the public site and downloads.

Before publication:

1. Read every imported file and remove secrets or private material.
2. Keep the factual `summary.txt` conservative; stronger verification labels require matching evidence.
3. Run `python3 scripts/proofhub.py finalize-run PROJECT_ID --redaction-reviewed`. The command changes the draft to publishable, updates the project status and continuation base conservatively, and validates the complete project before writing. If several draft runs exist, add `--run-id run-NNN`.
4. Commit to a branch and open a pull request. The change reaches the website only after it is merged to `main` and the deployment passes.

Published run directories are immutable. Corrections, retries, and forks are new runs. Use the lower-level `new-project` and `new-run` scaffold commands only when constructing manifests manually; the import commands are the normal workflow.

See [docs/FORMAT.md](docs/FORMAT.md) for field definitions and invariants.

## Generated output

Running `scripts/prepare_projects.py --generate` creates ignored derived files:

```text
data/generated/proof_projects/<project-id>.json
static/generated/<project-id>/continuation.txt
static/generated/<project-id>/handoff.zip
```

The handoff ZIP contains the exact problem, current status, continuation instructions, supporting evidence, and reusable proof files needed by the next agent, plus a SHA-256 manifest. Optional `raw/` vendor exports are excluded from both the rendered site and handoff ZIPs, but they are still visible to anyone who can read the Git repository. Never commit secrets or unredacted private material there.

To keep static builds predictable, a project file is limited to 25 MiB and a complete project bundle to 100 MiB. Store larger source exports outside this repository and publish a reviewed, compact derivative.

The generator never executes uploaded source code or verification commands. Web-executable or Hugo-renderable artifacts such as HTML, SVG, JavaScript, CSS, XML, and Markdown remain available in the handoff ZIP but are never linked on the site's own origin.

## Publish with GitHub Pages

This directory was based on the official HugoBlox starter and retains its GitHub Actions workflows.

1. Create an empty GitHub repository.
2. In the new repository, open **Settings → Pages** and set **Source** to **GitHub Actions**. Doing this before the first push lets the deployment workflow configure the correct Pages URL immediately.
3. Point this checkout at it and push:

   ```bash
   git remote add origin git@github.com:YOUR-NAME/YOUR-REPOSITORY.git
   git add .
   git commit -m "Create Proof Relay MWE"
   git push -u origin main
   ```

4. Open **Settings → Collaborators** and invite the supervisor with write access.
5. Protect `main` under **Settings → Rules → Rulesets** (or **Branches**, depending on the GitHub layout). Require the build check and, if desired for the two-person review, one approval.
6. Open the **Actions** tab and watch the deployment.

The workflow validates and tests every pull request. It rejects changes to a run that was already published at the comparison revision, builds under a synthetic repository subpath, and checks every generated internal link, download, resource, component button, and fragment. Only a push to `main` can deploy the site. GitHub supplies the production base URL, so both user sites and repository subpaths work.

## Backups

The `Backup repository history` workflow creates a verified Git bundle every day and keeps the last 30 days as Actions artifacts. A bundle contains repository history and can be restored with:

```bash
git clone proof-relay.bundle restored-proof-relay
```

This protects against an accidental branch deletion but is still stored by the same provider. For genuine disaster recovery, periodically download one bundle to independently controlled storage or mirror the repository to a second host.

## Contribution and safety model

For this two-person MWE:

- Only approved GitHub collaborators can push.
- A protected `main` branch and pull-request review can provide supervisor approval.
- Git commits identify who changed which project and when.
- The generated site attributes each run to the commit that first added its `run.yaml`, showing the associated GitHub username when GitHub can resolve one, the commit date, and the exact commit link. Co-authors declared with standard `Co-authored-by:` trailers are also shown.
- An optional run-manifest `submitted_by` label records who supplied imported material when that person differs from the repository contributor.
- For supervisor-reviewed status, the latest `status.yaml` commit author is the default reviewer; `review.reviewed_by` is an explicit override when the reviewer and committer differ.
- Published run directories are treated as immutable.

Before merging, inspect prompts, transcripts, outputs, environment dumps, and raw exports for API keys, credentials, personal information, or unpublished material. GitHub Pages content should be treated as public.

## Extension path

The bundle format is intentionally independent of Hugo. Later additions can consume the same files:

- AI-drafted `status.yaml` pull requests with supervisor approval.
- Machine-verification workers and stronger evidence badges.
- Generated individual run pages and lineage graphs.
- A CLI or GitHub App that creates valid bundles.
- A browser upload backend that writes the same directory format.
- Multiple contributors, project stewards, and review attestations.
- Import into a relational database without changing historic bundles.

Use the `extensions` objects for experiment-specific metadata. Introduce a new schema version rather than silently changing the meaning of existing fields.

## Upstream

The site uses [HugoBlox](https://hugoblox.com/) and the official [HugoBlox starter](https://github.com/HugoBlox/hugo-starter). The starter's automated upgrade workflow remains enabled; review upgrade pull requests carefully because this MWE extends its build workflow.
