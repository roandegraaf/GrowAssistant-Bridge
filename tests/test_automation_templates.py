"""Tests for the safe {{ … }} template engine.

Confirms whole-string templates preserve type, embedded templates stringify,
the context is limited to variables/trigger/states, and the sandbox blocks
dangerous expressions (imports, dunder/attribute escapes).
"""

import pytest

from app.automations import templates


def _render(value, variables=None, trigger=None, states=None):
    return templates.render(
        value,
        variables=variables or {},
        trigger=trigger or {},
        states=states or {},
    )


class TestRendering:
    def test_non_string_passes_through(self):
        assert _render(42) == 42
        assert _render(True) is True
        assert _render(None) is None

    def test_template_free_string_passes_through(self):
        assert _render("just text") == "just text"

    def test_whole_template_preserves_type(self):
        assert _render("{{ states['sensor.temp'] }}", states={"sensor.temp": 21.5}) == 21.5
        assert _render("{{ 1 + 2 }}") == 3

    def test_embedded_template_stringifies(self):
        out = _render("temp is {{ states['sensor.temp'] }}C", states={"sensor.temp": 21})
        assert out == "temp is 21C"

    def test_reads_variables_and_trigger(self):
        assert _render("{{ variables['n'] + 1 }}", variables={"n": 4}) == 5
        assert _render("{{ trigger['value'] }}", trigger={"value": 30}) == 30

    def test_safe_functions_available(self):
        assert _render("{{ round(states['x'], 1) }}", states={"x": 3.14159}) == 3.1
        assert _render("{{ int('5') + 1 }}") == 6


class TestSandbox:
    def test_unknown_name_raises(self):
        with pytest.raises(templates.TemplateError):
            _render("{{ secrets }}")

    def test_blocks_dunder_access(self):
        with pytest.raises(templates.TemplateError):
            _render("{{ ''.__class__ }}")

    def test_blocks_import(self):
        with pytest.raises(templates.TemplateError):
            _render("{{ __import__('os') }}")


class TestRenderData:
    def test_renders_each_value(self):
        out = templates.render_data(
            {"value": "{{ variables['t'] }}", "label": "fixed"},
            variables={"t": 22},
            trigger={},
            states={},
        )
        assert out == {"value": 22, "label": "fixed"}
