"""Build the public console shell from the UI assets shipped in this release."""

from functools import cache
from importlib.resources import files


@cache
def console_html() -> str:
    assets = files("agentos.observability")
    template = assets.joinpath("dashboard.html").read_text(encoding="utf-8")
    styles = assets.joinpath("console.css").read_text(encoding="utf-8")
    scripts = "\n".join(
        assets.joinpath(name).read_text(encoding="utf-8") for name in ("components.js", "console.js")
    )
    return template.replace("<!-- console styles -->", f"<style>\n{styles}</style>").replace(
        "<!-- console scripts -->", f"<script>\n{scripts}</script>"
    )
