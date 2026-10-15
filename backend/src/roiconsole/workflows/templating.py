"""Render Jinja templates in step arguments and prompts, preserving native values."""

# ============= Standard Library =============
import re
from typing import Any

# ============= Third-Party =============
from jinja2 import StrictUndefined, TemplateError, Undefined
from jinja2.sandbox import SandboxedEnvironment

# ============= Constants =============
# a string that is exactly one expression evaluates to a native value (list, dict, None)
SINGLE_EXPRESSION_RE = re.compile(r"^\s*\{\{(?P<expr>.*)\}\}\s*$", re.DOTALL)

ENVIRONMENT = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


# ============= Rendering =============


class TemplateRenderError(ValueError):
    """Raised when a template references a missing value or fails to render."""


def render_string(template: str, context_dict: dict[str, Any]) -> Any:
    """
    Render one template string.

    Parameters
    ----------
    template : str
        Jinja template. A string that is exactly one `{{ expr }}` returns the
        native value of the expression; anything else renders to a string.
    context_dict : dict
        Variables: inputs, steps, refs.

    Returns
    -------
    value : Any
        Native value or rendered string.
    """
    match = SINGLE_EXPRESSION_RE.match(template)
    try:
        if match and "{{" not in match.group("expr") and "{%" not in template:
            expression = ENVIRONMENT.compile_expression(
                match.group("expr").strip(), undefined_to_none=False
            )
            value = expression(**context_dict)
            if isinstance(value, Undefined):
                raise TemplateRenderError(f"undefined value in template: {template}")
            return value
        return ENVIRONMENT.from_string(template).render(**context_dict)
    except TemplateError as exc:
        raise TemplateRenderError(f"template failed: {exc}") from exc
    except (TypeError, AttributeError, KeyError) as exc:
        raise TemplateRenderError(f"template failed on value shape: {exc}") from exc


def render_value(value: Any, context_dict: dict[str, Any]) -> Any:
    """
    Recursively render strings inside dicts and lists.

    Parameters
    ----------
    value : Any
        Template value: string, dict, list, or scalar.
    context_dict : dict
        Variables: inputs, steps, refs.

    Returns
    -------
    rendered : Any
        Same structure with strings rendered.
    """
    if isinstance(value, str):
        return render_string(value, context_dict)
    if isinstance(value, dict):
        return {k: render_value(v, context_dict) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, context_dict) for v in value]
    return value
