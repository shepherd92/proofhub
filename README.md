# Proof Relay

A small HugoBlox website for sharing AI-assisted proof work. Each project shows its current mathematical status, earlier prompts and results, and who submitted each run.

Use the **ChatGPT desktop app** for the research and file preparation. Choose ChatGPT, with file creation available (for example, Work). No Codex, API key, repository access from ChatGPT, or local scripts are needed.

## Start a project

1. On the website, open **Start a project**, enter the problem, and copy the AI prompt.
2. Paste it into ChatGPT. Discuss the problem there; the prompt asks ChatGPT to generate the research files and return **proofhub-upload.zip** after each requested research task.
3. Download and unzip it. Copy the contents of `proofhub-upload/` into this repository, then commit and push.

You do not run an import script or fill in metadata.

## Continue a project

1. On the project's page, **Download project context** and attach that ZIP in ChatGPT. It contains the problem, previous prompts and results, and all saved proof files.
2. Copy the continuation prompt from the same page and paste it into that chat. ChatGPT uses the supplied results and returns **proofhub-upload.zip**, containing only the new run.
3. Download and unzip the result. Pull the repository's latest changes, copy the contents of `proofhub-upload/` into it, then commit and push. Do not replace an existing run; if its number is already taken, give ChatGPT the latest context download and ask it to repackage the result.

You do not need to connect ChatGPT to GitHub or open the repository in an AI tool. The website updates automatically from the highest-numbered run.

## Add results you already have

For an existing project, attach its context download and your original research files in ChatGPT, then use **Copy packaging prompt**. You can also paste this prompt into the chat where the research already happened. ChatGPT packages the supplied work as a new run without starting another proof attempt. For a new project, use the starter prompt and say: **“Package this existing work. Do not start a new proof attempt.”** Publish the returned ZIP in the same way.

ChatGPT preserves the available original prompts and outputs, creates a cumulative summary, and records the original model/version, time, and capacity usage when available. These details are not always exposed in a chat; missing metadata stays unknown, without a questionnaire. A token budget is configuration, not measured usage.

If ChatGPT cannot open a ZIP attachment, unzip the context and attach the needed files; the continuation prompt already includes the problem and latest status. If the chat cannot create downloadable files, use a ChatGPT mode with file creation available. The prompts also allow individually named file contents as a manual fallback; no Codex installation is required.

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
