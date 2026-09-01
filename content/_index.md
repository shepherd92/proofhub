---
title: 'Home'
type: landing
sections:
  - block: hero
    id: intro
    content:
      eyebrow: A minimal, extensible proof-run archive
      title: Continue the proof, not the duplication
      text: Proof Relay records exactly what an AI agent tried, what it achieved, how well the result was checked, and the files needed for the next run.
      primary_action:
        text: Browse projects
        url: projects/
        icon: hero/folder-open
        style: gradient
      secondary_action:
        text: Upload results
        url: guide/
        icon: hero/arrow-up-tray
        style: text
    design:
      spacing:
        padding: ['6rem', 0, '5rem', 0]
      background:
        gradient:
          start: 'rgba(79, 70, 229, 0.14)'
          end: 'rgba(15, 118, 110, 0.04)'
          direction: 135
  - block: features
    content:
      title: Four essential files, one complete handoff
      text: Contributors supply the problem, prompt, output, and a short summary. GitHub handles approval and attribution; Proof Relay generates the technical record and continuation package.
      items:
        - name: Structured status
          icon: hero/check-badge
          description: Separate established results, candidate claims, failed attempts, and open goals.
        - name: Lightweight run records
          icon: hero/code-bracket-square
          description: Preserve the exact prompt and output. Add useful proof files and model or timing details only when available.
        - name: One-click continuation
          icon: hero/arrow-down-tray
          description: Generate a continuation prompt and a deterministic handoff ZIP on every build.
  - block: collection
    content:
      title: Projects
      text: Each card below is generated from one directory in `content/projects/`.
      count: 6
      page_type: projects
      sort_by: Date
      sort_ascending: false
      filters:
        folders:
          - projects
      archive:
        enable: true
        text: View all projects
        link: projects/
    design:
      view: card
      columns: 2
      fill_image: false
      show_date: false
      show_read_time: false
      show_read_more: true
---
