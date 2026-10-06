"""Guards for the web UI's shared design language (tokens, fonts, logo)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
APP_REPO = ROOT.parent / "GrowAssistant"
VENDORED_TOKENS = WEB / "static" / "css" / "tokens.css"
LEGACY = re.compile(r"#22c55e|Outfit|JetBrains Mono", re.IGNORECASE)


def _ui_sources():
    for path in WEB.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".js"}:
            yield path
    yield ROOT / "tailwind.config.js"


def test_no_legacy_brand_green_or_fonts():
    offenders = [str(p.relative_to(ROOT)) for p in _ui_sources() if LEGACY.search(p.read_text())]
    assert offenders == []


def test_fonts_are_self_hosted():
    fonts = WEB / "static" / "fonts"
    assert (fonts / "Geist-Variable.woff2").is_file()
    assert (fonts / "GeistMono-Variable.woff2").is_file()
    for path in _ui_sources():
        assert "fonts.googleapis.com" not in path.read_text(), path


def test_tokens_are_vendored():
    text = VENDORED_TOKENS.read_text()
    assert "--ga-brand:" in text
    assert ".dark {" in text


@pytest.mark.skipif(
    not APP_REPO.is_dir(), reason="GrowAssistant app repo not checked out next to the bridge"
)
def test_vendored_tokens_match_the_app():
    generated = APP_REPO / "lib" / "tokens" / "generated" / "bridge-tokens.css"
    assert VENDORED_TOKENS.read_text() == generated.read_text(), "run `npm run sync:tokens`"


@pytest.mark.skipif(
    not APP_REPO.is_dir(), reason="GrowAssistant app repo not checked out next to the bridge"
)
def test_logo_matches_the_app():
    source = (APP_REPO / "lib" / "brand" / "logo.ts").read_text()
    path = re.search(r'LOGO_PATH =\s*"([^"]+)"', source).group(1)
    assert path in (WEB / "templates" / "partials" / "_logo.html").read_text()
    assert path in (WEB / "static" / "img" / "logo.svg").read_text()
