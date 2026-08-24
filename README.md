# Proof Relay MWE

Proof Relay is a minimal HugoBlox website for publishing AI-assisted mathematical proof runs. It answers three questions for every project:

1. What is the current mathematical status, and how strongly has it been verified?
2. Which prompts, models, agent architecture, configuration, outputs, and artifacts produced that status?
3. What should the next agent download to continue from the best known state?

The website is static. GitHub provides contribution access, review, history, attribution, and deployment. No database, API key, account system, or upload server is required for this MWE.

## What already works

- Native HugoBlox project cards at `/projects/`.
- One self-contained directory per proof project.
- Immutable runs with parent links for continuation, retry, fork, and merge histories.
- Versioned JSON Schemas for project status and run metadata.
- Validation of IDs, timestamps, lineage, evidence, file references, event logs, and redaction acknowledgement.
- Separate mathematical outcome and verification labels.
- A readable project dashboard with status, open goals, failed attempts, run metadata, prompts, outputs, events, and artifacts.
- Deterministically generated continuation prompts and handoff ZIPs.
- Reproducible ZIP timestamps, permissions, checksums, and file manifest.
- Automated tests and GitHub Pages deployment.

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
layouts/_shortcodes/           Project dashboard renderer
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

## Add a project

1. Copy `content/projects/euclid-primes/` to `content/projects/<new-project-id>/`.
2. Replace the example contents.
3. Set `project_id` in `index.md` equal to the directory name.
4. Keep `build.publishResources: false`; this prevents unlinked uploads from becoming public web files.
5. Set every `run_id` equal to its run directory name.
6. Keep exact prompts and outputs as `.txt` files.
7. Put visible message/tool history in `events.jsonl`.
8. Put proof code, counterexamples, logs, and reproducibility files in `artifacts/`.
9. Edit `status.yaml` only after reviewing what the run actually supports.
10. Run the validator and commit the new directory.

A directory by itself is not enough. Each project needs `index.md`, `problem.txt`, `status.yaml`, and at least one complete run directory.

Set `draft: true` while a bundle should remain unpublished. Draft projects are validated only at the page-envelope level and are omitted from generated site data and downloads. Change it to `false` only when the bundle is complete and redaction-reviewed.

See [docs/FORMAT.md](docs/FORMAT.md) for field definitions and invariants.

## Continue an existing project

Do not overwrite a published run. Add a new run directory and connect it to its predecessor:

```yaml
parents:
  - run_id: run-001
    relation: continuation
```

After reviewing the new result, update `status.yaml`. `current_run` selects the default continuation base; it does not have to be the most recent run. `as_of_runs` can cite several branches when the current status combines their results.

## Generated output

Running `scripts/prepare_projects.py --generate` creates ignored derived files:

```text
data/generated/proof_projects/<project-id>.json
static/generated/<project-id>/continuation.txt
static/generated/<project-id>/handoff.zip
```

The handoff ZIP contains the current run, its ancestors, status evidence, the exact problem, a continuation prompt, and a SHA-256 manifest. Optional `raw/` vendor exports are excluded from both the rendered site and handoff ZIPs, but they are still visible to anyone who can read the Git repository. Never commit secrets or unredacted private material there.

To keep static builds predictable, a project file is limited to 25 MiB and a complete project bundle to 100 MiB. Store larger source exports outside this repository and publish a reviewed, compact derivative.

The generator never executes uploaded source code or verification commands.

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

4. Open the **Actions** tab and watch the deployment.

The workflow validates and tests every pull request. Pushes to `main` additionally publish the site. GitHub supplies the correct base URL, so both user sites and repository subpaths work.

## Contribution and safety model

For this two-person MWE:

- Only approved GitHub collaborators can push.
- A protected `main` branch and pull-request review can provide supervisor approval.
- Git commits identify who changed which project and when.
- Run manifests additionally record `submitted_by`.
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
