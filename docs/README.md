# docs

## architecture.html

Interactive system diagram — pan/zoom, search, relationship tracing, light/dark,
and PNG/SVG export. Self-contained; open it directly in a browser.

`architecture.json` is its source. Edit that, then regenerate:

```bash
node ~/.claude/skills/archify/bin/archify.mjs deliver architecture \
  docs/architecture.json docs/architecture.html --quality showcase
```

Without the source checked in, changing one box means rebuilding the whole
diagram by hand.

Regeneration writes `architecture.visual-check.*` screenshots and a JSON
receipt beside the output. They are verification evidence, not artefacts —
`.gitignore` keeps them out of the repository.
