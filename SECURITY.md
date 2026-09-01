# Security and disclosure policy

Proof Relay treats every uploaded prompt, event, output, log, and artifact as untrusted data. The build reads and packages files but never executes proof code or recorded verification commands.

## Before publication

Review the complete Git directory—not only links visible on the website—for credentials, authentication material, personal data, private URLs, machine-specific environment values, copyrighted material, and unpublished mathematics. A `raw/` directory is excluded from the website and handoff ZIP, but remains visible to readers of a public repository.

Runs stay unpublished with `draft: true`. Setting `publication.redaction_reviewed: true` is an explicit human acknowledgement, not an automated secret scan.

Web-executable or Hugo-renderable formats such as HTML, SVG, JavaScript, CSS, XML, and Markdown are handoff-only. They are not deliberately linked from the GitHub Pages origin.

## If sensitive data is committed

Immediately revoke or rotate the affected credential. Removing it in a later commit does not erase Git history or caches. Rewrite the repository history only with the repository owner's approval, then invalidate any published Pages artifact and review forks or mirrors.

Report a vulnerability privately to the repository owner rather than opening a public issue containing exploit details or sensitive data.
