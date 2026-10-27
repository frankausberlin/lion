"""Collection gate for the opt-in E2E and agent suites."""

import os

collect_ignore: list[str] = []
if os.environ.get("LUPYSTA_E2E") != "1":
    collect_ignore.append("e2e")
if os.environ.get("LUPYSTA_AGENT") != "1":
    collect_ignore.append("agent")
