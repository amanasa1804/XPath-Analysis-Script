#!/usr/bin/env python3
"""
xpath_metrics.py — Generate a rich HTML metrics dashboard for XPath health
across a Selenium repository.

Usage:
    python xpath_metrics.py path/to/repo/ --output report.html
"""

import re
import os
import json
import argparse
import datetime
from pathlib import Path
from collections import defaultdict, Counter
from typing import Optional


# ─────────────────────────────────────────────
# Re-use extraction logic from scanner
# ─────────────────────────────────────────────

LOCATOR_PATTERNS = [
    re.compile(
        r'find_elements?\s*\(\s*By\.XPATH\s*,\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
    re.compile(
        r'find_elements?_by_xpath\s*\(\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
    re.compile(
        r'(?:XPATH|xpath|_xpath|_XPATH|locator)\s*=\s*(?P<q>["\'])(?P<xpath>//.*?)(?P=q)',
    ),
    re.compile(
        r'By\.XPATH\s*[,:]\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
]

NON_CONVENTIONAL_RULES = [
    ("absolute_xpath",      re.compile(r'^/html/'),                             "critical"),
    ("deep_hierarchy",      re.compile(r'(/[a-zA-Z]+){5,}'),                   "critical"),
    ("dynamic_id",          re.compile(r'@id\s*=\s*["\'][^"\']*\d{3,}[^"\']*["\']'), "critical"),
    ("positional_index",    re.compile(r'\[\d+\]'),                             "warning"),
    ("text_equals",         re.compile(r'text\(\)\s*=\s*["\']'),               "warning"),
    ("following_preceding", re.compile(r'(following|preceding)::'),            "warning"),
    ("contains_text",       re.compile(r'contains\(text\(\)'),                 "info"),
    ("wildcard_star",       re.compile(r'/\*/'),                               "info"),
]


def extract_xpaths(root: Path, exclude: set[str]) -> dict:
    """Return structured data about every XPath found."""
    xpath_data = []   # [{xpath, file, line, depth, length}]
    file_counts = defaultdict(int)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fname in filenames:
            if not fname.endswith('.py'):
                continue
            fpath = Path(dirpath) / fname
            try:
                source = fpath.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for lineno, line in enumerate(source.splitlines(), 1):
                for pat in LOCATOR_PATTERNS:
                    for m in pat.finditer(line):
                        xpath = m.group('xpath').strip()
                        if not xpath:
                            continue
                        depth = xpath.count('/')
                        xpath_data.append({
                            "xpath":  xpath,
                            "file":   str(fpath.relative_to(root)),
                            "line":   lineno,
                            "depth":  depth,
                            "length": len(xpath),
                        })
                        file_counts[str(fpath.relative_to(root))] += 1

    return {"entries": xpath_data, "file_counts": dict(file_counts)}


def compute_metrics(entries: list[dict]) -> dict:
    xpaths = [e["xpath"] for e in entries]
    counter = Counter(xpaths)
    duplicates = {k: v for k, v in counter.items() if v > 1}

    rule_hits = defaultdict(int)
    severity_counts = {"critical": 0, "warning": 0, "info": 0}
    for entry in entries:
        for rule_name, pattern, severity in NON_CONVENTIONAL_RULES:
            if pattern.search(entry["xpath"]):
                rule_hits[rule_name] += 1
                severity_counts[severity] += 1
                break   # only count worst rule per xpath

    depths = [e["depth"] for e in entries]
    lengths = [e["length"] for e in entries]

    return {
        "total":           len(entries),
        "unique":          len(set(xpaths)),
        "duplicates":      len(duplicates),
        "dup_xpaths":      list(duplicates.items())[:20],
        "severity":        severity_counts,
        "rule_hits":       dict(rule_hits),
        "avg_depth":       round(sum(depths) / len(depths), 2) if depths else 0,
        "max_depth":       max(depths, default=0),
        "avg_length":      round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "max_length":      max(lengths, default=0),
        "health_score":    _health_score(len(entries), len(duplicates), severity_counts),
    }


def _health_score(total: int, dupes: int, severity: dict) -> int:
    if total == 0:
        return 100
    penalty = (
        severity.get("critical", 0) * 10 +
        severity.get("warning", 0)  * 4  +
        severity.get("info", 0)     * 1  +
        dupes * 5
    )
    score = max(0, 100 - int(penalty / max(total, 1) * 100))
    return min(100, score)


# ─────────────────────────────────────────────
# HTML Dashboard
# ─────────────────────────────────────────────

def build_html(metrics: dict, file_counts: dict, root: str) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rule_rows = "\n".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in sorted(metrics["rule_hits"].items(), key=lambda x: -x[1])
    )
    dup_rows = "\n".join(
        f"<tr><td class='mono'>{xpath[:80]}</td><td>{count}</td></tr>"
        for xpath, count in metrics["dup_xpaths"]
    )
    file_rows = "\n".join(
        f"<tr><td class='mono'>{f}</td><td>{c}</td></tr>"
        for f, c in sorted(file_counts.items(), key=lambda x: -x[1])[:20]
    )
    score = metrics["health_score"]
    score_color = "#22c55e" if score >= 75 else "#f59e0b" if score >= 50 else "#ef4444"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>XPath Health Dashboard</title>
  <style>
    :root {{
      --bg: #0f172a; --surface: #1e293b; --border: #334155;
      --text: #e2e8f0; --muted: #94a3b8;
      --critical: #ef4444; --warning: #f59e0b; --info: #3b82f6; --ok: #22c55e;
      --accent: #6366f1;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 2rem; }}
    h1 {{ font-size: 1.6rem; margin-bottom: .25rem; }}
    h2 {{ font-size: 1.1rem; color: var(--muted); margin: 2rem 0 .75rem; text-transform: uppercase; letter-spacing: .08em; }}
    .meta {{ color: var(--muted); font-size: .85rem; margin-bottom: 2rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; }}
    .card .label {{ font-size: .75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; }}
    .card .value {{ font-size: 2rem; font-weight: 700; margin-top: .3rem; }}
    .card.critical .value {{ color: var(--critical); }}
    .card.warning  .value {{ color: var(--warning); }}
    .card.info     .value {{ color: var(--info); }}
    .card.ok       .value {{ color: var(--ok); }}
    .score-ring {{ width: 120px; height: 120px; margin: 0 auto 1rem; position: relative; }}
    .score-ring svg {{ transform: rotate(-90deg); }}
    .score-ring .val {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
                        font-size: 2rem; font-weight: 800; color: {score_color}; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
    th, td {{ padding: .55rem .75rem; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .mono {{ font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: .78rem; word-break: break-all; }}
    section {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; margin-bottom: 1.5rem; }}
  </style>
</head>
<body>
  <h1>🔍 XPath Health Dashboard</h1>
  <p class="meta">Repository: <strong>{root}</strong> &nbsp;·&nbsp; Scanned: {now}</p>

  <!-- Health Score -->
  <section style="text-align:center; max-width:220px;">
    <div class="label" style="margin-bottom:.5rem; font-size:.75rem; color:var(--muted); text-transform:uppercase;">Health Score</div>
    <div class="score-ring">
      <svg viewBox="0 0 36 36" width="120" height="120">
        <circle cx="18" cy="18" r="15.9155" fill="none" stroke="#334155" stroke-width="3.5"/>
        <circle cx="18" cy="18" r="15.9155" fill="none" stroke="{score_color}" stroke-width="3.5"
                stroke-dasharray="{score} {100-score}" stroke-linecap="round"/>
      </svg>
      <div class="val">{score}</div>
    </div>
  </section>

  <!-- Summary Cards -->
  <h2>Summary</h2>
  <div class="grid">
    <div class="card"><div class="label">Total XPaths</div><div class="value">{metrics['total']}</div></div>
    <div class="card"><div class="label">Unique</div><div class="value">{metrics['unique']}</div></div>
    <div class="card critical"><div class="label">Duplicates</div><div class="value">{metrics['duplicates']}</div></div>
    <div class="card critical"><div class="label">Critical Issues</div><div class="value">{metrics['severity']['critical']}</div></div>
    <div class="card warning"><div class="label">Warnings</div><div class="value">{metrics['severity']['warning']}</div></div>
    <div class="card info"><div class="label">Info</div><div class="value">{metrics['severity']['info']}</div></div>
    <div class="card"><div class="label">Avg Depth</div><div class="value">{metrics['avg_depth']}</div></div>
    <div class="card"><div class="label">Max Depth</div><div class="value">{metrics['max_depth']}</div></div>
    <div class="card"><div class="label">Avg Length</div><div class="value">{metrics['avg_length']}</div></div>
  </div>

  <!-- Rule Hits -->
  <h2>Issue Breakdown by Rule</h2>
  <section>
    <table>
      <tr><th>Rule</th><th>Count</th></tr>
      {rule_rows if rule_rows else '<tr><td colspan="2">No issues detected 🎉</td></tr>'}
    </table>
  </section>

  <!-- Duplicates -->
  <h2>Top Duplicate XPaths</h2>
  <section>
    <table>
      <tr><th>XPath</th><th>Uses</th></tr>
      {dup_rows if dup_rows else '<tr><td colspan="2">No duplicates found 🎉</td></tr>'}
    </table>
  </section>

  <!-- Files -->
  <h2>Top Files by XPath Count</h2>
  <section>
    <table>
      <tr><th>File</th><th>XPaths</th></tr>
      {file_rows if file_rows else '<tr><td colspan="2">No files found</td></tr>'}
    </table>
  </section>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(
        prog="xpath_metrics",
        description="Generate an HTML XPath health dashboard for a Selenium repo.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Repository root directory")
    parser.add_argument("--output", default="xpath_report.html", help="Output HTML file")
    parser.add_argument(
        "--exclude", nargs="+",
        default=[".git", "__pycache__", ".venv", "venv", "node_modules"],
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    print(f"\n📊 XPath Metrics — scanning {root} …")

    data = extract_xpaths(root, set(args.exclude))
    entries = data["entries"]
    file_counts = data["file_counts"]

    print(f"   Extracted {len(entries)} XPaths from {len(file_counts)} file(s)")

    metrics = compute_metrics(entries)
    html = build_html(metrics, file_counts, str(root))

    out = Path(args.output)
    out.write_text(html, encoding='utf-8')
    print(f"   Dashboard written → {out.resolve()}")
    print(f"   Health score: {metrics['health_score']}/100\n")


if __name__ == "__main__":
    main()
