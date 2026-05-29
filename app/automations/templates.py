"""Safe ``{{ … }}`` expression rendering for automation rules.

Rules may reference run-time values with Jinja-ish ``{{ expr }}`` templates in
``set_variable.value_template``, ``call`` data payloads, ``fire_event`` data and
``state`` condition/trigger string fields. Expressions are evaluated with
``simpleeval`` (no imports, no attribute access to arbitrary objects, no
builtins beyond a tiny safe set) over a context exposing *exactly*:

* ``variables`` — the run-scoped variables built by ``set_variable``;
* ``trigger``   — a dict describing what fired the rule;
* ``states``    — a read-only snapshot of entity → latest value.

Nothing else is reachable — no filesystem, no ``__import__``, no Python object
graph — so a rule author cannot escape the sandbox.

A value that is *entirely* one template (``"{{ states['sensor.x'] }}"``) returns
the evaluated result with its native type (int/float/bool/…); a template
embedded in surrounding text returns a string with each ``{{ … }}`` substituted.
A non-string value is returned unchanged, so literal ``set_variable`` values and
numeric payload fields pass straight through.
"""

import logging
import re
from typing import Any

from simpleeval import EvalWithCompoundTypes, NameNotDefined, SimpleEval

logger = logging.getLogger(__name__)

# Matches a `{{ … }}` template fragment (non-greedy so multiple fragments work).
_TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# True when the whole string is a single `{{ … }}` and nothing else.
_WHOLE_RE = re.compile(r"^\s*\{\{(.*)\}\}\s*$", re.DOTALL)


class TemplateError(Exception):
    """Raised when a ``{{ … }}`` expression cannot be evaluated."""


def is_template(value: Any) -> bool:
    """Whether ``value`` is a string containing a ``{{ … }}`` template."""
    return isinstance(value, str) and "{{" in value


def _evaluator(variables: dict[str, Any], trigger: dict[str, Any], states: dict[str, Any]):
    """Build a sandboxed evaluator exposing only variables/trigger/states.

    ``EvalWithCompoundTypes`` allows dict/list literals and subscripting (so
    ``states['sensor.x']`` and ``variables['n'] + 1`` work) while still blocking
    imports, attribute access to arbitrary objects, and dangerous builtins.
    """
    ev: SimpleEval = EvalWithCompoundTypes(
        names={"variables": variables, "trigger": trigger, "states": states},
        functions={
            "float": float,
            "int": int,
            "str": str,
            "round": round,
            "abs": abs,
            "min": min,
            "max": max,
            "len": len,
            "bool": bool,
        },
    )
    return ev


def render(
    value: Any,
    *,
    variables: dict[str, Any],
    trigger: dict[str, Any],
    states: dict[str, Any],
) -> Any:
    """Render ``{{ … }}`` templates in ``value`` against the given context.

    Non-strings and template-free strings are returned unchanged. A whole-string
    template preserves the evaluated type; an embedded template stringifies.
    Raises ``TemplateError`` if an expression references an unknown name or
    fails to evaluate.
    """
    if not is_template(value):
        return value

    ev = _evaluator(variables, trigger, states)

    whole = _WHOLE_RE.match(value)
    if whole:
        return _eval_expr(ev, whole.group(1))

    def _sub(match: "re.Match[str]") -> str:
        return str(_eval_expr(ev, match.group(1)))

    return _TEMPLATE_RE.sub(_sub, value)


def _eval_expr(ev: SimpleEval, expr: str) -> Any:
    expr = expr.strip()
    try:
        return ev.eval(expr)
    except NameNotDefined as e:
        raise TemplateError(f"unknown name in template '{expr}': {e}") from e
    except Exception as e:  # simpleeval raises a variety of its own errors
        raise TemplateError(f"failed to evaluate template '{expr}': {e}") from e


def render_data(
    data: dict[str, Any],
    *,
    variables: dict[str, Any],
    trigger: dict[str, Any],
    states: dict[str, Any],
) -> dict[str, Any]:
    """Render every value of a payload dict (one level deep is enough for the
    HA-subset payloads we support)."""
    return {
        k: render(v, variables=variables, trigger=trigger, states=states)
        for k, v in (data or {}).items()
    }
