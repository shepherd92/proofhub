# Contributing proof runs

For a new project, start with:

```bash
python3 scripts/proofhub.py new-project my-problem --title "Problem title"
```

For a continuation, start with:

```bash
python3 scripts/proofhub.py new-run my-problem
```

Keep projects and runs as drafts while importing material. Published run directories are immutable: corrections, retries, forks, continuations, and merges always use a new run ID.

Before opening a pull request:

1. Check every file for credentials and private material.
2. Confirm that outcome and verification labels match the evidence.
3. Confirm that supervisor-reviewed status records `reviewed_at`; add `reviewed_by` only if the reviewer differs from the status commit author.
4. Run `python3 scripts/prepare_projects.py --check` and `python3 -m unittest discover -s tests -v`.
5. If frontend tooling is installed, run `pnpm run build` to verify the production site and all internal links.

The public contribution guide at `content/guide/index.md` explains the complete file contract and GitHub upload workflow.

The website automatically credits the Git commit that first adds each run. Use the optional `--submitted-by "Name"` flag only when importing run material supplied by somebody other than the committer. Add standard `Co-authored-by:` trailers to the commit message when several people should share repository credit.
