"""The ``classify`` doctor topic: the classifier rule set loads cleanly.

The check is read-only: it loads the bundled default file and the optional user
file and reports their validity. It never classifies a recording and never
writes.
"""

from lion.classify.rules import ClassifierRuleError, load_ruleset
from lion.program.checks import CheckStatus, DoctorContext, Finding

__all__ = ["classifier_checks"]


def classifier_checks(_ctx: DoctorContext) -> list[Finding]:
    """Report whether the classifier rule set can be loaded (topic ``classify``)."""
    try:
        ruleset = load_ruleset()
    except ClassifierRuleError as exc:
        return [
            Finding(
                topic="classify",
                name="classify.ruleset",
                status=CheckStatus.ERROR,
                message=str(exc),
                hint="Fix or remove the user classifier file, or keep the built-in defaults only.",
            )
        ]
    origin = f"built-in plus {ruleset.user_path}" if ruleset.user_loaded else "built-in rules only"
    message = f"Rule set {ruleset.version} loaded ({origin}); hash {ruleset.hash[:12]}\u2026."
    return [Finding(topic="classify", name="classify.ruleset", status=CheckStatus.OK, message=message)]
