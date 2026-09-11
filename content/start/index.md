---
title: Start a project
summary: Use ChatGPT to prepare one upload ZIP.
show_date: false
show_breadcrumb: false
reading_time: false
share: false
---

Enter the problem and paste the prompt into the **ChatGPT desktop app**. Use a chat with file creation available, such as Work. No Codex, API key, or scripts are needed.

{{< new-project-prompt >}}

ChatGPT prepares **proofhub-upload.zip** with the problem, prompts, results, summary, and any available model/version, time, and usage records. Unknown metadata is left out.

When it finishes, **download and unzip the result, then copy the contents of `proofhub-upload/` into your repository, commit, and push**. ChatGPT does not need access to the repository. If the generated project name is already in use, ask ChatGPT to repackage under a different name rather than replacing it.

Already have results? Attach them in ChatGPT with the prompt, or use the chat where you did the work, and say: **“Package this existing work. Do not start a new proof attempt.”** It records the original research metadata when available.

To continue an existing project, attach the context download from its project page in ChatGPT and paste its continuation prompt.

If your chat cannot create downloadable files, use a ChatGPT mode with file creation available or ask for the individual file contents to save manually.

Anyone with repository write access can add results. The website automatically shows their GitHub username.
