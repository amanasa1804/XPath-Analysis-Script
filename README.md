# XPath Analyzer Toolkit

A suite of Python scripts to scan, audit, and refactor XPaths across Selenium automation repositories. Detects duplicate, hardcoded, and non-conventional XPaths — with actionable fix suggestions and an HTML health dashboard.

---

## Tools at a Glance

| Script | Purpose |
|---|---|
| `xpath_scanner.py` | Core analyzer — detects all three issue types, outputs text or JSON |
| `xpath_refactor.py` | Suggests (or applies) safe automated rewrites |
| `xpath_metrics.py` | Generates an HTML health-score dashboard |

---

## Requirements

- Python 3.10+
- No third-party dependencies — uses only the standard library (`re`, `ast`, `pathlib`, `argparse`, `json`, `collections`)

---

## Quick Start

```bash
# Clone / copy the three scripts into your project root

# Run the full audit
python xpath_scanner.py ./my_selenium_repo/

# Generate the HTML dashboard
python xpath_metrics.py ./my_selenium_repo/ --output report.html

# Preview refactoring suggestions (dry run)
python xpath_refactor.py ./my_selenium_repo/

# Apply safe automatic fixes
python xpath_refactor.py ./my_selenium_repo/ --apply
```

---

## `xpath_scanner.py` — Core Analyzer

### What It Detects

**Duplicate XPaths**
Identical XPath strings used in more than one location. Cross-file duplicates are `critical`; same-file repeats are `warning`. A single DOM change breaking multiple tests is the most common source of flaky suite failures.

**Hardcoded XPaths**
XPath string literals found directly inside test functions (via Python AST), rather than declared as named constants in a Page Object class. Makes refactoring painful and hides intent.

**Non-Conventional XPaths**
Nine pattern rules ranked by severity:

| Rule | Severity | Trigger Example |
|---|---|---|
| Absolute path from root | 🔴 Critical | `/html/body/div/form/button` |
| Deep hierarchy (5+ levels) | 🔴 Critical | `//div/section/nav/ul/li/a` |
| Auto-generated / dynamic ID | 🔴 Critical | `@id='err-48392-x'` |
| Positional index `[N]` | 🟡 Warning | `//li[2]` |
| `text()` equality | 🟡 Warning | `//a[text()='Login']` |
| `following`/`preceding` axis | 🟡 Warning | `//following::button[1]` |
| `contains(text(), ...)` | 🔵 Info | `//span[contains(text(),'OK')]` |
| Wildcard `*` in path | 🔵 Info | `//div/*/span` |
| Tag with no attribute anchor | 🔵 Info | `//button` |

### Usage

```bash
python xpath_scanner.py [ROOT] [OPTIONS]
```

| Argument | Default | Description |
|---|---|---|
| `ROOT` | `.` | Repository root directory |
| `--checks` | all | Which checks to run: `duplicates` `hardcoded` `non_conventional` |
| `--exclude` | `.git __pycache__ .venv venv node_modules` | Directories to skip |
| `--format` | `text` | Output format: `text` or `json` |
| `--output FILE` | stdout | Write report to a file |
| `--verbose / -v` | off | Print per-file progress |

**Examples**

```bash
# Scan current directory, all checks, print to terminal
python xpath_scanner.py

# Scan a specific repo, JSON output to file
python xpath_scanner.py ./tests/ --format json --output audit.json

# Only check for duplicates, verbose
python xpath_scanner.py ./tests/ --checks duplicates --verbose

# Exclude additional directories
python xpath_scanner.py ./repo/ --exclude .git build dist migrations
```

**Exit Codes**

| Code | Meaning |
|---|---|
| `0` | No critical issues found |
| `1` | One or more critical issues found (useful in CI pipelines) |

### XPath Locator Patterns Recognized

The scanner captures XPaths from all common Selenium Python patterns:

```python
driver.find_element(By.XPATH, "//...")
driver.find_elements(By.XPATH, "//...")
driver.find_element_by_xpath("//...")
driver.find_elements_by_xpath("//...")
LOCATOR = (By.XPATH, "//...")          # tuple constant
By.XPATH: "//..."                      # dict style
SUBMIT_BTN = "//..."                   # plain string constant
```

---

## `xpath_refactor.py` — Auto-Fixer

Generates concrete rewrite suggestions for each flagged XPath, and can optionally apply safe fixes in-place.

### Transformations

| Issue | Automated Fix |
|---|---|
| Absolute path `/html/body/…` | Converts to relative `//…` |
| `text()='X'` equality | Relaxes to `contains(text(), 'X')` |
| Positional index `[N]` | Annotates with `FIXME:was_index_N` placeholder |
| Deep hierarchy (5+ levels) | Shortens to `//last-segment # REVIEW` |
| Dynamic/auto-generated ID | Suggests `[@data-testid="FIXME:add-testid-here"]` |

Fixes marked `FIXME` require a human decision and are never written automatically — the `--apply` flag only writes changes it can make safely.

### Usage

```bash
python xpath_refactor.py [ROOT] [OPTIONS]
```

| Argument | Default | Description |
|---|---|---|
| `ROOT` | `.` | Repository root directory |
| `--apply` | off | Write safe fixes to disk (default: dry run) |
| `--verbose / -v` | off | Print each rewritten file path |
| `--exclude` | standard set | Directories to skip |

**Examples**

```bash
# Preview all suggestions without touching files
python xpath_refactor.py ./tests/

# Apply safe automatic fixes
python xpath_refactor.py ./tests/ --apply

# Apply fixes, show every rewritten file
python xpath_refactor.py ./tests/ --apply --verbose
```

> **Tip:** Commit your work before running `--apply`, so you can diff the changes cleanly.

---

## `xpath_metrics.py` — HTML Dashboard

Produces a self-contained dark-mode HTML dashboard with a 0–100 health score and full issue breakdown. Ideal for team review, sprint retrospectives, or as a CI artifact.

### Dashboard Sections

- **Health Score** — circular gauge (0–100). Penalizes critical issues (×10), warnings (×4), duplicates (×5), and info items (×1), normalized to the total XPath count.
- **Summary Cards** — total, unique, duplicate count, severity breakdown, average/max depth, average length.
- **Issue Breakdown by Rule** — counts per detection rule, sorted by frequency.
- **Top Duplicate XPaths** — the most-reused XPath strings (up to 20).
- **Top Files by XPath Count** — which files are densest in locators (up to 20).

### Usage

```bash
python xpath_metrics.py [ROOT] [OPTIONS]
```

| Argument | Default | Description |
|---|---|---|
| `ROOT` | `.` | Repository root directory |
| `--output FILE` | `xpath_report.html` | Output HTML file path |
| `--exclude` | standard set | Directories to skip |

**Examples**

```bash
# Generate dashboard in the current directory
python xpath_metrics.py ./tests/

# Custom output path
python xpath_metrics.py ./repo/ --output docs/xpath_health.html
```

---

## Integrating with CI

The scanner exits with code `1` when any `critical` issue is detected, making it drop-in ready for CI pipelines.

**GitHub Actions example**

```yaml
- name: XPath Audit
  run: |
    python xpath_scanner.py ./tests/ \
      --format json \
      --output xpath_audit.json

- name: Upload XPath Report
  uses: actions/upload-artifact@v4
  with:
    name: xpath-audit
    path: xpath_audit.json
  if: always()
```

**Pre-commit hook**

```bash
#!/bin/sh
python xpath_scanner.py . --checks duplicates non_conventional
```

Add to `.git/hooks/pre-commit` and `chmod +x` it.

---

## Recommended XPath Best Practices

Follow these patterns to write locators the scanner will never flag:

```python
# ✅ Stable data-testid attribute
(By.XPATH, "//button[@data-testid='submit-order']")

# ✅ Unique aria-label
(By.XPATH, "//input[@aria-label='Email address']")

# ✅ Stable name attribute
(By.XPATH, "//input[@name='username']")

# ✅ Relative path with unique class + role
(By.XPATH, "//nav[@role='navigation']//a[@class='logo']")

# ✅ contains() over text equality
(By.XPATH, "//button[contains(text(), 'Submit')]")

# ❌ Absolute path
(By.XPATH, "/html/body/div[2]/form/button")

# ❌ Positional index
(By.XPATH, "//ul/li[3]/a")

# ❌ Auto-generated ID
(By.XPATH, "//div[@id='comp-48392183']")

# ❌ Deep hierarchy
(By.XPATH, "//div/section/nav/ul/li/span/a")

# ❌ Hardcoded inline in a test method — move to Page Object
def test_login(driver):
    driver.find_element(By.XPATH, "//button[@id='login']")  # ← hardcoded
```

---

## Project Structure

```
xpath_analyzer/
├── xpath_scanner.py     # Core scanner and report generator
├── xpath_refactor.py    # Rewrite suggester and auto-fixer
├── xpath_metrics.py     # HTML dashboard generator
└── README.md
```

---

## Severity Reference

| Icon | Level | Action |
|---|---|---|
| 🔴 | Critical | Fix before merging — high flakiness risk |
| 🟡 | Warning | Address in current or next sprint |
| 🔵 | Info | Good-to-fix; low urgency |
