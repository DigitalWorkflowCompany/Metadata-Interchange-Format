#!/usr/bin/env python3
"""Build the Cloudflare Pages output tree for https://ns.the-dwc.com/.

Reads schemas/*.schema.json from the repo and writes dist/:

  dist/
    _headers
    sidecar/
      index.html
      v0.1/
        index.html
        artifacts.schema.json
        events.schema.json
        locks.schema.json

Pure stdlib — runs unchanged in Cloudflare Pages' default build image.
"""
import hashlib
import html
import shutil
from pathlib import Path

HERE    = Path(__file__).resolve().parent
REPO    = HERE.parent.parent
SCHEMAS = REPO / "schemas"
DIST    = REPO / "dist"
VERSION = "v0.1"
REPO_URL = "https://github.com/DigitalWorkflowCompany/Metadata-Interchange-Format"

SCHEMA_FILES = [
    ("dwc.sidecar.artifacts", "artifacts.schema.json"),
    ("dwc.sidecar.events",    "events.schema.json"),
    ("dwc.sidecar.locks",     "locks.schema.json"),
]


CSS = """\
:root {
  --text: #1a1a1a;
  --muted: #666;
  --accent: #0066cc;
  --border: #e0e0e0;
  --code-bg: #f5f5f5;
}
* { box-sizing: border-box; }
body { font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       color: var(--text); max-width: 760px; margin: 3em auto; padding: 0 1.25em;
       background: #fff; }
h1 { font-size: 1.55em; margin: 0 0 0.2em; letter-spacing: -0.01em; }
h1 + p.lede { color: var(--muted); margin: 0 0 2em; font-size: 1.05em; }
h2 { font-size: 1.05em; margin: 2.2em 0 0.5em; text-transform: uppercase;
     letter-spacing: 0.08em; color: var(--muted); font-weight: 600; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 0.9em; }
code { background: var(--code-bg); padding: 0.1em 0.35em; border-radius: 3px; }
pre { background: var(--code-bg); padding: 1em 1.1em; border-radius: 5px;
      overflow-x: auto; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.95em; }
th, td { text-align: left; padding: 0.65em 0.4em; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-weight: 600; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.08em;
     color: var(--muted); border-bottom-width: 2px; }
td.hash { font-family: ui-monospace, monospace; font-size: 0.72em; color: var(--muted);
          word-break: break-all; }
td.status { text-transform: uppercase; font-size: 0.75em; letter-spacing: 0.05em; }
td.status.active { color: #0a7a33; }
footer { margin-top: 4em; padding-top: 1.2em; border-top: 1px solid var(--border);
         color: var(--muted); font-size: 0.85em; }
footer a { color: var(--muted); text-decoration: underline; }
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def version_index(hashes: dict[str, str]) -> str:
    rows = "".join(
        f'  <tr><td><code>{domain}</code></td>'
        f'<td><a href="{fname}">{fname}</a></td>'
        f'<td class="hash">{hashes[fname]}</td></tr>\n'
        for domain, fname in SCHEMA_FILES
    )
    body = f"""<h1>DWC Sidecar Schema {VERSION}</h1>
<p class="lede">JSON Schemas for the <code>dwc.sidecar.*</code> extension payloads that compose with <a href="https://movielabs.com/production-technology/omc/">MovieLabs OMC v2.8</a>.</p>

<h2>Schemas</h2>
<table>
<thead><tr><th>Domain</th><th>Schema</th><th>SHA-256</th></tr></thead>
<tbody>
{rows}</tbody>
</table>

<h2>Usage</h2>
<p>DWC sidecar payloads live inside OMC's <code>customData</code> extension point. Each entry declares its <code>domain</code>, <code>namespace</code>, and <code>schema</code> URL; validators resolve against the URLs above.</p>
<pre>{{
  "domain":    "dwc.sidecar.artifacts",
  "namespace": "https://ns.the-dwc.com/sidecar/{VERSION}",
  "schema":    "https://ns.the-dwc.com/sidecar/{VERSION}/artifacts.schema.json",
  "value":     [ ... ]
}}</pre>

<h2>Stability</h2>
<p>The three schema URLs under <code>{VERSION}/</code> are <strong>immutable</strong>. Any change to their bytes is a breaking change and will be published under a new version directory (<code>v0.2/</code>). Old versions remain available indefinitely.</p>

<footer>
<p>Source: <a href="{REPO_URL}">{REPO_URL}</a> · <a href="/sidecar/">all versions</a></p>
</footer>"""
    return page(f"DWC Sidecar Schema {VERSION}", body)


def root_index() -> str:
    body = f"""<h1>DWC Schemas</h1>
<p class="lede">Versioned JSON Schemas for the per-clip film-industry metadata sidecar format.</p>

<h2>Versions</h2>
<table>
<thead><tr><th>Version</th><th>Status</th></tr></thead>
<tbody>
  <tr><td><a href="{VERSION}/">{VERSION}</a></td><td class="status active">active</td></tr>
</tbody>
</table>

<h2>Policy</h2>
<p>URLs under a version directory are immutable. Additive or breaking changes go into a new version directory; older versions remain accessible indefinitely.</p>

<footer>
<p>Source: <a href="{REPO_URL}">{REPO_URL}</a></p>
</footer>"""
    return page("DWC Schemas", body)


HEADERS = """\
# Index pages first (less specific): short cache, revalidate.
# Cloudflare Pages applies the LAST matching rule when headers collide,
# so schema-specific rules must come after.
/sidecar/*
  Cache-Control: public, max-age=300

# Versioned schema URLs: immutable, fully cacheable, CORS-open.
# Content-Type can't be overridden on Cloudflare Pages — files are served
# as application/json based on extension, which is interoperable for JSON
# Schema consumers.
/sidecar/*/*.schema.json
  Access-Control-Allow-Origin: *
  Cache-Control: public, max-age=31536000, immutable
"""


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    v_dir = DIST / "sidecar" / VERSION
    v_dir.mkdir(parents=True)

    hashes: dict[str, str] = {}
    for _, fname in SCHEMA_FILES:
        src = SCHEMAS / fname
        dst = v_dir / fname
        shutil.copyfile(src, dst)
        hashes[fname] = sha256(src)
        print(f"  {fname:30s} {hashes[fname]}")

    (v_dir / "index.html").write_text(version_index(hashes))
    (DIST / "sidecar" / "index.html").write_text(root_index())
    (DIST / "_headers").write_text(HEADERS)

    print(f"\nBuilt → {DIST.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
