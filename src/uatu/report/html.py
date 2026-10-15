"""The report as one HTML file, figures inside: to open anywhere or pass on."""

import base64
import re
from pathlib import Path

import markdown

STYLE = """
body { max-width: 860px; margin: 40px auto; padding: 0 20px; color: #2b2b28; background: #fff;
       font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 26px; margin-bottom: 4px; }
h2 { margin-top: 36px; border-bottom: 1px solid #e4e2dc; padding-bottom: 4px; }
h3 { margin: 22px 0 6px; font-size: 16px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th, td { padding: 5px 8px; border-bottom: 1px solid #ecebe7; text-align: left; }
td:not(:first-child):not(:nth-child(2)) { font-variant-numeric: tabular-nums; }
img { max-width: 100%; border: 1px solid #ecebe7; border-radius: 6px; }
a { color: #2a6fd1; } li { margin: 3px 0; }
"""


def to_html(md_path: Path) -> Path:
    """informe.md and its figures beside it -> informe.html, self-contained."""
    body = markdown.markdown(md_path.read_text(), extensions=["tables"])

    def inline(match: re.Match[str]) -> str:
        image = md_path.parent / match.group(1)
        if not image.exists():
            return match.group(0)
        data = base64.b64encode(image.read_bytes()).decode()
        return f'src="data:image/png;base64,{data}"'

    body = re.sub(r'src="([^":]+\.png)"', inline, body)
    title = re.search(r"<h1>(.*?)</h1>", body)
    page = (
        '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title.group(1) if title else 'Uatu'}</title>\n<style>{STYLE}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
    path = md_path.with_suffix(".html")
    path.write_text(page)
    return path
