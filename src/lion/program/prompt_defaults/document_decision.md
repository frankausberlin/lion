---
name = "document_decision"
description = "Document a decision as a standalone ADR with the LION catalog."
[[arguments]]
name = "subject"
description = "What the decision is about, for example 'the default browser'."
required = true
[[arguments]]
name = "recording"
description = "Optional recording reference the decision belongs to."
required = false
---

Document a decision about {subject} as a standalone ADR.

Gather the context first: use lion_search, and lion_recording when a recording is involved. Then create the ADR with lion_adr_add and the catalog fields (title, decision, component, scope, context, consequences, alternatives, revisit_when, status). A complete ADR carries a title and a decision; keep the text factual and cite the evidence you used.

{recording}
