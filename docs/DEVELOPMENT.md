# Local development

Contributors only need the workflow in the [README](../README.md). These commands are for working on the website itself.

Install Hugo Extended (the version in `hugoblox.yaml`), Go, Node.js, pnpm, and Python 3.11 or newer. Install the dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pnpm install --frozen-lockfile
```

Preview: `pnpm run dev`. Build: `pnpm run build`. Tests: `pnpm test`.

The build automatically reads project folders, generates the current status and AI prompts, and checks the site's internal links. Generated files under `data/generated/` and `static/generated/` are not committed.
