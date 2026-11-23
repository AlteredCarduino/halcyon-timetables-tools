"""Shared Halcyon Timetables & Tools section shell (drawer nav + navy background)."""

from __future__ import annotations

import html
import re

NAV_VERSION = "30"
CSS_VERSION = "3"

_TOOLS_CSS_MARKER = "halcyon-tools-section.css"
_SECTION_NAV_MARKER = "halcyon-tools-section-nav.js"
_LEGACY_SECTION_PAGES = (
    "today",
    "schoolyearcalendar",
    "timetabledrooms",
    "personaltimetables",
    "calendar",
    "studentemails",
    "roombookingsadmin",
)


def tools_section_stylesheet_link() -> str:
    return (
        f'<link rel="stylesheet" href="/static/signin/{_TOOLS_CSS_MARKER}?v={CSS_VERSION}" />'
    )


def wrap_tools_section_page(
    *,
    title: str,
    section_page: str,
    page_class: str,
    content_class: str,
    inner_styles: str,
    body_inner: str,
    tail_scripts: str = "",
) -> str:
    section_page = html.escape(section_page)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/static/signin/styles.css" />
  <link rel="stylesheet" href="/static/signin/halcyon-shell.css" />
  <link rel="stylesheet" href="/static/signin/halcyon-subpages.css?v=13" />
  {tools_section_stylesheet_link()}
  <style>{inner_styles}</style>
</head>
<body class="halcyon-page halcyon-tools-section">
  <div class="halcyon-app">
    <div id="halcyonNavMount"></div>
    <script>window.HALCYON_NAV_MODE = 'tools'; window.HALCYON_SECTION_PAGE = '{section_page}';</script>
    <script src="/static/signin/halcyon-nav.js?v={NAV_VERSION}"></script>
    <script src="/static/signin/halcyon-shell.js?v=9"></script>
    <main class="halcyon-main {page_class}">
      <div class="{content_class}">
{body_inner}
      </div>
    </main>
  </div>
  {tail_scripts}
</body>
</html>
"""


def upgrade_tools_section_html(
    text: str,
    *,
    section_page: str,
    page_class: str,
    content_class: str,
) -> str:
    """Ensure generated/storage HTML uses the Timetables & Tools section shell."""
    text = _ensure_body_class(text)
    text = _strip_sidebar_layout(text, page_class, content_class)
    if f'class="{content_class}"' not in text and f"class='{content_class}'" not in text:
        text = _wrap_legacy_main(text, page_class, content_class)
    text = _ensure_nav_config(text, section_page)
    text = _remove_section_nav_script(text)
    text = _ensure_tools_stylesheet(text)
    text = _ensure_nav_js_version(text)
    return text


def _ensure_body_class(text: str) -> str:
    if "halcyon-tools-section" in text:
        return text
    if '<body class="halcyon-page">' in text:
        return text.replace(
            '<body class="halcyon-page">',
            '<body class="halcyon-page halcyon-tools-section">',
            1,
        )
    return re.sub(
        r'(<body class="halcyon-page")(?=>|\s)',
        r"\1 halcyon-tools-section",
        text,
        count=1,
    )


def _strip_sidebar_layout(text: str, page_class: str, content_class: str) -> str:
    text = re.sub(
        r'\s*<aside id="halcyonToolsNavMount"[^>]*>\s*</aside>\s*',
        "\n",
        text,
    )
    text = text.replace(
        f'<main class="halcyon-main halcyon-tools-layout {page_class}">',
        f'<main class="halcyon-main {page_class}">',
    )
    unwrap_patterns = [
        '<div class="halcyon-tools-content">\n          <div class="' + content_class + '">',
        '<div class="halcyon-tools-content">\n        <div class="' + content_class + '">',
        '<div class="halcyon-tools-content">\n          <div class="' + content_class + '">\n',
        '<div class="halcyon-tools-content">\n        <div class="' + content_class + '">\n',
    ]
    for pattern in unwrap_patterns:
        if pattern in text:
            text = text.replace(pattern, '<div class="' + content_class + '">', 1)
            break
    close_pairs = [
        ("          </div>\n          </div>\n        </main>", "          </div>\n        </main>"),
        ("        </div>\n        </div>\n      </main>", "        </div>\n      </main>"),
    ]
    for old, new in close_pairs:
        if old in text and "halcyon-tools-content" not in text:
            text = text.replace(old, new, 1)
            break
    return text


def _wrap_legacy_main(text: str, page_class: str, content_class: str) -> str:
    patterns = [
        f'<main class="halcyon-main {page_class}">\n          <div class="{content_class}">',
        f'<main class="halcyon-main {page_class}">\n        <div class="{content_class}">',
        f'<main class="halcyon-main {page_class}">\n          <div class="{content_class}">\n',
        f'<main class="halcyon-main {page_class}">\n        <div class="{content_class}">\n',
    ]
    for pattern in patterns:
        if pattern in text:
            return text
    replacement = f'<main class="halcyon-main {page_class}">\n          <div class="{content_class}">'
    old = f'<main class="halcyon-main {page_class}">'
    if old in text and content_class not in text:
        return text.replace(old, replacement, 1)
    return text


def _ensure_nav_config(text: str, section_page: str) -> str:
    config = (
        f"window.HALCYON_NAV_MODE = 'tools'; "
        f"window.HALCYON_SECTION_PAGE = '{section_page}';"
    )
    config_script = f"<script>{config}</script>"

    text = re.sub(
        r"<script>\s*window\.HALCYON_SECTION_PAGE\s*=\s*['\"][^'\"]+['\"];?\s*</script>\s*",
        "",
        text,
    )
    text = re.sub(
        r"window\.HALCYON_NAV_PAGE\s*=\s*['\"]tools['\"];?\s*",
        "",
        text,
    )
    for legacy in _LEGACY_SECTION_PAGES:
        text = text.replace(
            f"window.HALCYON_NAV_PAGE = '{legacy}';",
            config,
        )
        text = text.replace(
            f'window.HALCYON_NAV_PAGE = "{legacy}";',
            config,
        )

    if "HALCYON_NAV_MODE" in text:
        text = re.sub(
            r"<script>\s*window\.HALCYON_NAV_MODE[^<]*</script>",
            config_script,
            text,
            count=1,
        )
        text = re.sub(
            r"window\.HALCYON_SECTION_PAGE\s*=\s*['\"][^'\"]+['\"]",
            f"window.HALCYON_SECTION_PAGE = '{section_page}'",
            text,
            count=1,
        )
    elif '<script src="/static/signin/halcyon-nav.js' in text:
        text = text.replace(
            '<script src="/static/signin/halcyon-nav.js',
            config_script + '\n      <script src="/static/signin/halcyon-nav.js',
            1,
        )
    return text


def _remove_section_nav_script(text: str) -> str:
    return re.sub(
        rf'\s*<script src="/static/signin/{re.escape(_SECTION_NAV_MARKER)}[^>]*></script>\s*',
        "\n",
        text,
    )


def _ensure_tools_stylesheet(text: str) -> str:
    link = tools_section_stylesheet_link()
    text = re.sub(
        r'<link rel="stylesheet" href="/static/signin/halcyon-tools-section\.css\?v=\d+" />',
        link,
        text,
    )
    if _TOOLS_CSS_MARKER in text:
        return text
    anchor = '<link rel="stylesheet" href="/static/signin/halcyon-subpages.css'
    idx = text.find(anchor)
    if idx == -1:
        return text
    end = text.find("/>", idx)
    if end == -1:
        return text
    insert_at = end + 2
    return text[:insert_at] + "\n      " + link + text[insert_at:]


def _ensure_nav_js_version(text: str) -> str:
    return re.sub(
        r'(<script src="/static/signin/halcyon-nav\.js)\?v=\d+("></script>)',
        rf"\1?v={NAV_VERSION}\2",
        text,
    )
