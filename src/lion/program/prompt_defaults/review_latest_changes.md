---
name = "review_latest_changes"
description = "Review what changed on this machine between the previous and the latest stored state."
[[arguments]]
name = "reference"
description = "State reference to compare against; defaults to the previous state, so the review covers the newest changes."
required = false
[[arguments]]
name = "focus"
description = "Optional area to focus the review on, for example 'packages' or 'network'."
required = false
---

Review what changed on this machine between the stored state {reference} and the latest state.

Use the lion_history and lion_diff tools to compare the stored states, and lion_snapshot for the full newest state. Lead with structural changes, then the field-level ones, and name anything that needs attention. Remember that a collector can be unavailable or incomplete; say so instead of guessing.

If fewer than two states are stored, say explicitly that there is no comparison baseline yet instead of reviewing nothing.

{focus}
