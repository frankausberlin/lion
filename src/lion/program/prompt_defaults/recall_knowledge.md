---
name = "recall_knowledge"
description = "Answer a question from the stored ADRs and recordings."
[[arguments]]
name = "query"
description = "The question to answer from the stored ledger."
required = true
---

Answer this from the stored knowledge: {query}

Use lion_search first, then lion_adr_show or lion_recording for the hits that matter. Cite the references (ADR ids and recording refs) and say plainly when the ledger has no answer.
