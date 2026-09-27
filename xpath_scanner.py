#!/usr/bin/env python3
"""
XPath Scanner - Detects duplicate, hardcoded, and non-conventional XPaths
in Selenium automation repositories.
"""

import os
import re
import ast
import json
import argparse
import hashlib
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────

@dataclass
class XPathOccurrence:
    file: str
    line: int
    column: int
    context: str          # surrounding code snippet
    variable_name: str    # variable/method the XPath is assigned to


@dataclass
class XPathIssue:
    xpath: str
    issue_type: str       # duplicate | hardcoded | non_conventional
    severity: str         # critical | warning | info
    occurrences: list[XPathOccurrence] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    details: str = ""


# ─────────────────────────────────────────────
# XPath Pattern Rules
# ─────────────────────────────────────────────

# Patterns that indicate non-conventional / fragile XPaths
NON_CONVENTIONAL_PATTERNS = [
    {
        "name": "absolute_xpath",
        "pattern": re.compile(r'^/html/'),
        "severity": "critical",
        "details": "Absolute XPath from root — breaks on any structural change.",
        "suggestion": "Use a relative XPath anchored to a stable attribute (id, data-testid).",
    },
    {
        "name": "deep_hierarchy",
        "pattern": re.compile(r'(/[a-zA-Z]+){5,}'),
        "severity": "critical",
        "details": "XPath traverses 5+ levels — extremely brittle.",
        "suggestion": "Shorten by targeting a unique ancestor or stable attribute.",
    },
    {
        "name": "positional_index",
        "pattern": re.compile(r'\[\d+\]'),
        "severity": "warning",
        "details": "Positional index [N] — breaks when sibling order changes.",
        "suggestion": "Replace index with a distinguishing attribute predicate.",
    },
    {
        "name": "text_equals",
        "pattern": re.compile(r'text\(\)\s*=\s*["\']'),
        "severity": "warning",
        "details": "text() equality — breaks on copy changes or whitespace variation.",
        "suggestion": "Use contains(text(), ...) or a data-testid attribute instead.",
    },
    {
        "name": "contains_text",
        "pattern": re.compile(r'contains\(text\(\)'),
        "severity": "info",
        "details": "contains(text()) — more resilient than equality but still text-dependent.",
        "suggestion": "Prefer a stable data-testid or aria-label attribute.",
    },
    {
        "name": "dynamic_id",
        "pattern": re.compile(r'@id\s*=\s*["\'][^"\']*\d{3,}[^"\']*["\']'),
        "severity": "critical",
        "details": "ID looks auto-generated (contains long numeric sequence).",
        "suggestion": "Target a static data-testid or class attribute instead.",
    },
    {
        "name": "following_preceding",
        "pattern": re.compile(r'(following|preceding)::(sibling::)?'),
        "severity": "warning",
        "details": "Axis traversal (following/preceding) — tightly coupled to DOM structure.",
        "suggestion": "Restructure to target the element directly via a stable attribute.",
    },
    {
        "name": "wildcard_star",
        "pattern": re.compile(r'/\*/'),
        "severity": "info",
        "details": "Wildcard * in path — reduces readability and specificity.",
        "suggestion": "Use the actual element tag or a descriptive predicate.",
    },
    {
        "name": "no_attribute_anchor",
        "pattern": re.compile(r'^//[a-zA-Z]+(?:\[[^\]]*\])?\s*$'),
        "severity": "info",
        "details": "XPath targets only an element tag with no attribute — may match many elements.",
        "suggestion": "Add an @id, @name, or @data-testid predicate.",
    },
]

# Selenium locator call patterns (Python)
LOCATOR_PATTERNS = [
    # driver.find_element(By.XPATH, "...")
    re.compile(
        r'(?:find_elements?)\s*\(\s*By\.XPATH\s*,\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
    # driver.find_element_by_xpath("...")
    re.compile(
        r'find_elements?_by_xpath\s*\(\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
    # XPATH = "..."   (page-object constant)
    re.compile(
        r'(?:XPATH|xpath|_xpath|_XPATH|locator)\s*=\s*(?P<q>["\'])(?P<xpath>//.*?)(?P=q)',
    ),
    # By.XPATH: "..."  (dict / tuple style)
    re.compile(
        r'By\.XPATH\s*[,:]\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)',
        re.DOTALL,
    ),
    # (By.XPATH, "...")  tuple style
    re.compile(
        r'\(\s*By\.XPATH\s*,\s*(?P<q>["\'])(?P<xpath>.*?)(?P=q)\s*\)',
        re.DOTALL,
    ),
]

# Detect f-string / format / % — hardcoded XPath with variable substitution
DYNAMIC_INJECTION_PATTERNS = [
    re.compile(r'f["\'].*//.*\{'),          # f"//div[@id='{var}']"
    re.compile(r'["\'].*//.*["\'].format\('),# "//div[@id='{}']".format(...)
    re.compile(r'["\'].*//.*["\']\s*%\s*'), # "//div[@id='%s']" % var
]


# ─────────────────────────────────────────────
# File Scanner
# ─────────────────────────────────────────────

def iter_python_files(root: str, exclude_dirs: set[str]) -> list[Path]:
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in filenames:
            if fname.endswith('.py'):
                result.append(Path(dirpath) / fname)
    return result


def extract_xpaths_from_file(filepath: Path) -> list[XPathOccurrence]:
    """Extract all XPath strings and their locations from a Python file."""
    occurrences = []
    try:
        source = filepath.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return occurrences

    lines = source.splitlines()

    for lineno, line in enumerate(lines, start=1):
        for pattern in LOCATOR_PATTERNS:
            for match in pattern.finditer(line):
                xpath = match.group('xpath').strip()
                if not xpath:
                    continue
                col = match.start('xpath')
                # Grab surrounding context (3 lines)
                ctx_start = max(0, lineno - 2)
                ctx_end = min(len(lines), lineno + 1)
                context = '\n'.join(
                    f"  {i+1:>4}: {lines[i]}" for i in range(ctx_start, ctx_end)
                )
                # Try to guess variable name from line
                var_name = _guess_variable_name(line)
                occurrences.append(XPathOccurrence(
                    file=str(filepath),
                    line=lineno,
                    column=col,
                    context=context,
                    variable_name=var_name,
                ))
                # Attach xpath to occurrence for grouping
                occurrences[-1]._xpath = xpath  # type: ignore[attr-defined]

    return occurrences


def _guess_variable_name(line: str) -> str:
    match = re.match(r'\s*(\w+)\s*=', line)
    if match:
        return match.group(1)
    match = re.search(r'def\s+(\w+)', line)
    if match:
        return match.group(1)
    return "<anonymous>"


# ─────────────────────────────────────────────
# Analysis Engines
# ─────────────────────────────────────────────

def find_duplicates(all_occurrences: list) -> list[XPathIssue]:
    """Group identical XPath strings used in more than one location."""
    groups: dict[str, list[XPathOccurrence]] = defaultdict(list)
    for occ in all_occurrences:
        groups[occ._xpath].append(occ)  # type: ignore[attr-defined]

    issues = []
    for xpath, occs in groups.items():
        if len(occs) < 2:
            continue
        files = {o.file for o in occs}
        severity = "critical" if len(files) > 1 else "warning"
        issue = XPathIssue(
            xpath=xpath,
            issue_type="duplicate",
            severity=severity,
            occurrences=occs,
            details=(
                f"Found {len(occs)} occurrences across {len(files)} file(s). "
                "Duplication means a single DOM change breaks multiple tests."
            ),
            suggestions=[
                "Extract to a shared Page Object or locator constants module.",
                "Create a single source of truth and import where needed.",
            ],
        )
        issues.append(issue)
    return issues


def find_non_conventional(all_occurrences: list) -> list[XPathIssue]:
    """Flag XPaths that violate best-practice patterns."""
    issues = []
    seen: set[tuple] = set()  # (xpath, rule_name) — avoid duplicate reports

    for occ in all_occurrences:
        xpath = occ._xpath  # type: ignore[attr-defined]
        for rule in NON_CONVENTIONAL_PATTERNS:
            if rule["pattern"].search(xpath):
                key = (xpath, rule["name"])
                if key in seen:
                    continue
                seen.add(key)
                issues.append(XPathIssue(
                    xpath=xpath,
                    issue_type="non_conventional",
                    severity=rule["severity"],
                    occurrences=[occ],
                    details=f"[{rule['name']}] {rule['details']}",
                    suggestions=[rule["suggestion"]],
                ))
    return issues


def find_hardcoded(filepath: Path) -> list[XPathIssue]:
    """
    Detect XPaths that are inlined directly in test methods rather than
    declared as named constants in a Page Object.
    """
    issues = []
    try:
        source = filepath.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return issues

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return issues

    lines = source.splitlines()

    for node in ast.walk(tree):
        # Look for string literals inside function bodies (test methods)
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    val = child.value.strip()
                    if val.startswith('//') or val.startswith('/html'):
                        lineno = child.lineno
                        ctx_start = max(0, lineno - 2)
                        ctx_end = min(len(lines), lineno + 1)
                        context = '\n'.join(
                            f"  {i+1:>4}: {lines[i]}"
                            for i in range(ctx_start, ctx_end)
                        )
                        issues.append(XPathIssue(
                            xpath=val,
                            issue_type="hardcoded",
                            severity="warning",
                            occurrences=[XPathOccurrence(
                                file=str(filepath),
                                line=lineno,
                                column=child.col_offset,
                                context=context,
                                variable_name=func_name,
                            )],
                            details=(
                                f"XPath literal found inside function '{func_name}' "
                                "rather than a named Page Object constant."
                            ),
                            suggestions=[
                                f"Move to a PageObject class constant, e.g. "
                                f"LOGIN_BUTTON = (By.XPATH, '{val}')",
                                "Import and reference the constant in your test.",
                            ],
                        ))
    return issues


# ─────────────────────────────────────────────
# Report Generator
# ─────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
SEVERITY_ICON  = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def generate_report(issues: list[XPathIssue], output_format: str, output_path: Optional[str]) -> str:
    issues_sorted = sorted(issues, key=lambda i: SEVERITY_ORDER.get(i.severity, 9))

    by_type: dict[str, list[XPathIssue]] = defaultdict(list)
    for issue in issues_sorted:
        by_type[issue.issue_type].append(issue)

    counts = {
        "total": len(issues),
        "critical": sum(1 for i in issues if i.severity == "critical"),
        "warning":  sum(1 for i in issues if i.severity == "warning"),
        "info":     sum(1 for i in issues if i.severity == "info"),
        "duplicates":        len(by_type.get("duplicate", [])),
        "hardcoded":         len(by_type.get("hardcoded", [])),
        "non_conventional":  len(by_type.get("non_conventional", [])),
    }

    if output_format == "json":
        report = {
            "summary": counts,
            "issues": [_issue_to_dict(i) for i in issues_sorted],
        }
        text = json.dumps(report, indent=2)
    else:
        text = _build_text_report(issues_sorted, by_type, counts)

    if output_path:
        Path(output_path).write_text(text, encoding='utf-8')
        print(f"Report written to: {output_path}")
    else:
        print(text)

    return text


def _issue_to_dict(issue: XPathIssue) -> dict:
    return {
        "xpath": issue.xpath,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "details": issue.details,
        "suggestions": issue.suggestions,
        "occurrences": [
            {
                "file": o.file,
                "line": o.line,
                "column": o.column,
                "variable": o.variable_name,
            }
            for o in issue.occurrences
        ],
    }


def _build_text_report(
    issues: list[XPathIssue],
    by_type: dict[str, list[XPathIssue]],
    counts: dict,
) -> str:
    sep = "═" * 72
    thin = "─" * 72
    lines = [
        "",
        sep,
        "  🔍  XPATH ANALYZER — SCAN REPORT",
        sep,
        f"  Total issues : {counts['total']}",
        f"  🔴 Critical  : {counts['critical']}",
        f"  🟡 Warning   : {counts['warning']}",
        f"  🔵 Info      : {counts['info']}",
        thin,
        f"  Duplicates       : {counts['duplicates']}",
        f"  Hardcoded        : {counts['hardcoded']}",
        f"  Non-conventional : {counts['non_conventional']}",
        sep,
        "",
    ]

    for issue_type, label in [
        ("duplicate",       "DUPLICATE XPATHS"),
        ("hardcoded",       "HARDCODED XPATHS"),
        ("non_conventional","NON-CONVENTIONAL XPATHS"),
    ]:
        group = by_type.get(issue_type, [])
        if not group:
            continue
        lines.append(f"{'─'*3}  {label}  ({len(group)} issues)  {'─'*3}")
        lines.append("")
        for idx, issue in enumerate(group, 1):
            icon = SEVERITY_ICON.get(issue.severity, "⚪")
            lines.append(f"  [{idx:>3}] {icon} {issue.severity.upper()}")
            lines.append(f"        XPath : {issue.xpath[:100]}")
            if len(issue.xpath) > 100:
                lines.append(f"               …{issue.xpath[100:]}")
            lines.append(f"        Detail: {issue.details}")
            for sug in issue.suggestions:
                lines.append(f"        💡 {sug}")
            lines.append(f"        Occurrences ({len(issue.occurrences)}):")
            for occ in issue.occurrences[:5]:  # cap display at 5
                lines.append(f"          → {occ.file}:{occ.line}  [{occ.variable_name}]")
            if len(issue.occurrences) > 5:
                lines.append(f"          … and {len(issue.occurrences)-5} more")
            lines.append("")
        lines.append("")

    lines.append(sep)
    lines.append("  END OF REPORT")
    lines.append(sep)
    return '\n'.join(lines)


# ─────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────

def scan_repository(
    root: str,
    exclude_dirs: set[str],
    checks: set[str],
    output_format: str,
    output_path: Optional[str],
    verbose: bool,
) -> int:
    """Main scan entry point. Returns exit code (0 = no critical issues)."""
    print(f"\n📂 Scanning: {root}")
    python_files = iter_python_files(root, exclude_dirs)
    print(f"   Found {len(python_files)} Python file(s)\n")

    # Collect all xpath occurrences
    all_occurrences = []
    for fpath in python_files:
        occs = extract_xpaths_from_file(fpath)
        all_occurrences.extend(occs)
        if verbose and occs:
            print(f"  ✓ {fpath}  ({len(occs)} XPath(s))")

    print(f"\n   Total XPaths extracted: {len(all_occurrences)}\n")

    all_issues: list[XPathIssue] = []

    if "duplicates" in checks:
        dupes = find_duplicates(all_occurrences)
        all_issues.extend(dupes)
        print(f"  Duplicate check   → {len(dupes)} issue(s)")

    if "non_conventional" in checks:
        nc = find_non_conventional(all_occurrences)
        all_issues.extend(nc)
        print(f"  Convention check  → {len(nc)} issue(s)")

    if "hardcoded" in checks:
        hc_issues: list[XPathIssue] = []
        for fpath in python_files:
            hc_issues.extend(find_hardcoded(fpath))
        all_issues.extend(hc_issues)
        print(f"  Hardcoded check   → {len(hc_issues)} issue(s)")

    print()
    generate_report(all_issues, output_format, output_path)

    critical_count = sum(1 for i in all_issues if i.severity == "critical")
    return 1 if critical_count > 0 else 0


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xpath_scanner",
        description="Scan Selenium repositories for XPath issues.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Repository root directory")
    parser.add_argument(
        "--checks", nargs="+",
        choices=["duplicates", "hardcoded", "non_conventional"],
        default=["duplicates", "hardcoded", "non_conventional"],
        help="Which checks to run (default: all)",
    )
    parser.add_argument(
        "--exclude", nargs="+", default=[".git", "__pycache__", ".venv", "venv", "node_modules"],
        help="Directories to exclude",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        dest="output_format", help="Output format",
    )
    parser.add_argument("--output", metavar="FILE", help="Write report to file instead of stdout")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-file progress")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    exit_code = scan_repository(
        root=args.root,
        exclude_dirs=set(args.exclude),
        checks=set(args.checks),
        output_format=args.output_format,
        output_path=args.output,
        verbose=args.verbose,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
