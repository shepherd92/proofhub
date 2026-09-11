# Proof Relay

A small HugoBlox website for sharing AI-assisted proof work. Each project shows its current mathematical status, earlier prompts and results, and who submitted each run.

## Start a project

1. On the website, open **Start a project**, enter the problem, and copy the AI prompt.
2. Paste it into an AI session that can create files. The AI prepares the project directory, including its summary and run record.
3. Copy the contents of the generated `proofhub-upload/` folder into this repository, then commit and push. If the AI works in this checkout, it can write the files here directly.

You do not run an import script or fill in metadata.

## Continue a project

Pull the repository's latest changes, then open the project's page and copy its continuation prompt. Give the AI access to that project folder. The prompt includes the current status and tells the AI exactly where to save the next run.

Copy the new run directory into `content/projects/<project>/runs/`, then commit and push. The website updates automatically from the highest-numbered run.

## Add results you already have

On an existing project, use **Copy packaging prompt** and give the AI the original conversation, results, and any useful files. It packages that work as a new run without starting another proof attempt. For a new project, use the starter prompt and tell the AI to package the supplied work.

The AI preserves the original prompt and output, creates a cumulative summary, and records the original model/version, time, and capacity usage when the source material contains them. Missing metadata stays unknown. A token budget is configuration, not measured usage.

Anyone with write access to the repository can push results. The site displays the GitHub username associated with the commit; no contributor name needs to be typed.

## What gets saved?

The AI creates these files:

```text
content/projects/my-problem/
├── index.md
├── problem.txt
└── runs/
    └── run-001/
        ├── prompt.txt
        ├── output.txt
        ├── result.yaml
        └── artifacts/        optional proof files
```

Each later contribution adds one run directory. Its result record contains both that run's summary and the cumulative project status. GitHub keeps the change history.

[File format](docs/FORMAT.md) · [Local development](docs/DEVELOPMENT.md)
