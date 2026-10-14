"""The read-time classifier: declarative rules over recorded shell blocks.

Classification is derived from the raw recording events on every read (ADR
0015); this package never writes a recording and never executes anything. The
public API is the rule and policy loaders, the bounded parser, the engine entry
points and the model types; the CLI lives in :mod:`lion.command.classify` and
the doctor check in :mod:`lion.classify.checks`.
"""

from lion.classify.engine import classify_events, classify_recording, classify_text, is_silent_command
from lion.classify.model import (
    Block,
    Classification,
    Command,
    Match,
    Policy,
    PolicyEvidence,
    PolicyPattern,
    PolicyResult,
    RuleSetInfo,
)
from lion.classify.parser import PARSER_VERSION, ParsedCommand, find_closing_quote, parse_command, strip_prefixes
from lion.classify.policies import (
    ClassifierPolicyError,
    evaluate_command,
    load_policies,
    pattern_matches,
    user_policies_path,
)
from lion.classify.rules import (
    GROUP_FIELDS,
    ClassifierRuleError,
    Rule,
    RuleSet,
    compile_pattern,
    default_rules_path,
    get_config_dir,
    load_ruleset,
    user_rules_path,
)
from lion.classify.text import sanitize_controls

__all__ = [
    "GROUP_FIELDS",
    "PARSER_VERSION",
    "Block",
    "Classification",
    "ClassifierPolicyError",
    "ClassifierRuleError",
    "Command",
    "Match",
    "ParsedCommand",
    "Policy",
    "PolicyEvidence",
    "PolicyPattern",
    "PolicyResult",
    "Rule",
    "RuleSet",
    "RuleSetInfo",
    "classify_events",
    "classify_recording",
    "classify_text",
    "compile_pattern",
    "default_rules_path",
    "evaluate_command",
    "find_closing_quote",
    "get_config_dir",
    "is_silent_command",
    "load_policies",
    "load_ruleset",
    "parse_command",
    "pattern_matches",
    "sanitize_controls",
    "strip_prefixes",
    "user_policies_path",
    "user_rules_path",
]
