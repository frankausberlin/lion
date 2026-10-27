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
chronicle, not a normative source: durable decisions belong in ADRs/APs and the
current state in the project documentation.
