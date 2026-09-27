#!/usr/bin/env python3
"""
xpath_refactor.py — Suggest and optionally apply safe XPath replacements.

Usage:
    python xpath_refactor.py path/to/repo/
    python xpath_refactor.py path/to/repo/ --apply   # writes fixes in-place
"""

import re
import argparse
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
# Transformation Rules
# ─────────────────────────────────────────────

def _strip_root(xpath: str) -> str:
    """Remove /html/body/… prefix, leaving relative path."""
    return re.sub(r'^/html(/body)?', '', xpath)


def _remove_positional_index(xpath: str) -> str:
    """Replace [N] numeric indexes with a comment placeholder."""
    return re.sub(r'\[(\d+)\]', r'[FIXME:was_index_\1]', xpath)


def _shorten_deep_hierarchy(xpath: str) -> str:
    """Replace chains of 5+ tags with //-shortcut to last segment."""
    parts = xpath.split('/')
    if len([p for p in parts if p and not p.startswith('@')]) >= 5:
        last = parts[-1]
        return f"//{last}  # REVIEW: shortened from full path"
    return xpath


def suggest_refactoring(xpath: str) -> list[dict]:
    """
    Return a list of suggested transformations for a given XPath.
    Each dict has 'description' and 'refactored' keys.
    """
    suggestions = []

    # Absolute path fix
    if xpath.startswith('/html'):
        refactored = '//' + _strip_root(xpath).lstrip('/')
        suggestions.append({
            "description": "Convert absolute path to relative",
            "refactored": refactored,
        })

    # Positional index replacement
    if re.search(r'\[\d+\]', xpath):
        refactored = _remove_positional_index(xpath)
        suggestions.append({
            "description": "Replace positional index with attribute predicate",
            "refactored": refactored,
        })

    # Deep hierarchy shortcut
    if xpath.count('/') >= 5:
        refactored = _shorten_deep_hierarchy(xpath)
        suggestions.append({
            "description": "Shorten deep hierarchy with // shortcut",
            "refactored": refactored,
        })

    # text() equality → contains
    m = re.search(r"text\(\)\s*=\s*(['\"])(.+?)\1", xpath)
    if m:
        text_val = m.group(2)
        refactored = xpath.replace(m.group(0), f"contains(text(), '{text_val}')")
        suggestions.append({
            "description": "Relax text() equality to contains()",
            "refactored": refactored,
        })

    # Add data-testid recommendation for dynamic IDs
    if re.search(r'@id\s*=\s*["\'][^"\']*\d{3,}[^"\']*["\']', xpath):
        tag = re.match(r'^//(\w+)', xpath)
        tag_str = tag.group(1) if tag else '*'
        suggestions.append({
            "description": "Replace auto-generated ID with data-testid",
            "refactored": f'//{tag_str}[@data-testid="FIXME:add-testid-here"]',
        })

    if not suggestions:
        suggestions.append({
            "description": "No automated fix available — manual review required",
            "refactored": xpath,
        })

    return suggestions


# ─────────────────────────────────────────────
# File Rewriter
# ─────────────────────────────────────────────

LOCATOR_RE = re.compile(
    r'(find_elements?\s*\(\s*By\.XPATH\s*,\s*|find_elements?_by_xpath\s*\(\s*)'
    r'(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
    re.DOTALL,
)


def refactor_file(filepath: Path, apply: bool, verbose: bool) -> list[dict]:
    """Scan and optionally rewrite a single file."""
    source = filepath.read_text(encoding='utf-8', errors='replace')
    report_entries = []
    new_source = source

    for match in LOCATOR_RE.finditer(source):
        xpath = match.group('xpath').strip()
        suggestions = suggest_refactoring(xpath)
        best = suggestions[0]

        report_entries.append({
            "file": str(filepath),
            "original": xpath,
            "suggestions": suggestions,
        })

        if apply and best["refactored"] != xpath and "FIXME" not in best["refactored"]:
            q = match.group('q')
            old_str = match.group('q') + xpath + match.group('q')
            new_str  = q + best["refactored"] + q
            new_source = new_source.replace(old_str, new_str, 1)

    if apply and new_source != source:
        filepath.write_text(new_source, encoding='utf-8')
        if verbose:
            print(f"  ✏️  Rewrote: {filepath}")

    return report_entries


def refactor_repository(root: str, apply: bool, verbose: bool, exclude_dirs: set[str]):
    """Run refactoring suggestions (or apply fixes) across the repository."""
    root_path = Path(root)
    all_reports = []

    python_files = [
        p for p in root_path.rglob('*.py')
        if not any(excl in p.parts for excl in exclude_dirs)
    ]

    print(f"\n🔧 XPath Refactorer — {'APPLYING fixes' if apply else 'DRY RUN (use --apply to write changes)'}")
    print(f"   Scanning {len(python_files)} Python files in {root}\n")

    for fpath in python_files:
        entries = refactor_file(fpath, apply=apply, verbose=verbose)
        all_reports.extend(entries)

    print(f"\n{'─'*60}")
    print(f"  Total XPaths reviewed: {len(all_reports)}")
    fixable = sum(
        1 for r in all_reports
        if r["suggestions"] and r["suggestions"][0]["refactored"] != r["original"]
           and "FIXME" not in r["suggestions"][0]["refactored"]
    )
    print(f"  Auto-fixable          : {fixable}")
    print(f"  Needs manual review   : {len(all_reports) - fixable}")
    print(f"{'─'*60}\n")

    for entry in all_reports:
        print(f"  📄 {entry['file']}")
        print(f"     Original : {entry['original'][:80]}")
        for s in entry["suggestions"]:
            marker = "✅" if "FIXME" not in s["refactored"] else "🛠 "
            print(f"     {marker}  [{s['description']}]")
            print(f"          → {s['refactored'][:80]}")
        print()


def main():
    parser = argparse.ArgumentParser(
        prog="xpath_refactor",
        description="Suggest or apply XPath refactoring across a Selenium repo.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Repository root")
    parser.add_argument("--apply", action="store_true", help="Write fixes to disk (default: dry run)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--exclude", nargs="+",
        default=[".git", "__pycache__", ".venv", "venv", "node_modules"],
    )
    args = parser.parse_args()
    refactor_repository(
        root=args.root,
        apply=args.apply,
        verbose=args.verbose,
        exclude_dirs=set(args.exclude),
    )


if __name__ == "__main__":
    main()
