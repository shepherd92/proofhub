# Project files

The AI writes this format; contributors do not need to edit it by hand.

A new project needs `index.md`, `problem.txt`, and its first run. The page is:

```yaml
---
title: A concise problem title
project_id: my-problem
build:
  publishResources: false
---

{{< proof-project >}}
```

A run is a numbered folder such as `runs/run-001/`. It needs only `prompt.txt`, `output.txt`, and `result.yaml`. Useful proof files go in an optional `artifacts/` folder. The highest run number supplies the current status; each run keeps its own summary.

```yaml
kind: live
summary: Reduced the problem to one explicit lemma.
current_status:
  outcome: partial
  summary: The finite case is established. The general case still needs the extension lemma.
  established:
    - The statement holds in the finite case.
  candidate_results: []
  special_cases: []
  failed_attempts:
    - Direct induction stalled because the invariant is not preserved.
  next_steps:
    - Prove the extension lemma stated in the output.
```

Use `kind: imported` for completed work being packaged now. Outcomes are `no_progress`, `partial`, `candidate_proof`, `proved`, `candidate_refutation`, or `refuted`. These describe the submitted mathematical assessment, not an independent check by the website.

Optional metadata records facts available from the runtime or source logs:

- `model`: name, provider, exact version or snapshot.
- `agent`: name, version, architecture.
- `timing`: original start/end timestamps and duration in seconds.
- `capacity`: actual input/output/total tokens, or an amount with its unit and source.
- `configuration`: relevant settings such as reasoning effort, token budget, and tools.

Omit unknown values. Do not treat an allocated budget or account-wide quota as usage by this project. Imported work uses its original timing and usage, never the packaging session's.

The build reads the files, generates the catalogue and prompts, and derives contributor names from Git history. It does not execute proof files or call an AI model.
