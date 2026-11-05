# Journal

One entry per session, immutable. Filename:
`YYYY-MM-DD-HHMM-<slug>.md` (UTC; lowercase slug, hyphens).

Create the entry at the end of a session and commit it with the change.
Latest entries:

```shell
ls -1 journal | grep -v '^README\.md$' | sort | tail -n 3
```

Categories, tags and frontmatter are intentionally not part of the convention
yet; they can be added later as metadata without renaming files. Entries are a
chronicle, not a normative source:

- Architecture decisions belong in `docs/adr/` and are maintained by the
  project owner; the agent only reads them.
- Recurring operational notes belong in the Attention Points in `AGENTS.md`.
- The current project state belongs in the project documentation.

Refer to ADRs by number and Attention Points by title.
