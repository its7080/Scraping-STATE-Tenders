# Improvement Suggestions (Exploration Notes)

This document captures practical improvements after a quick repository exploration.

## 1) Reliability & fault tolerance

1. **Harden exception handling and reduce silent failures**  
   There are several bare `except`/silent `pass` blocks in runtime paths. Replace with logged, typed exceptions where possible so operational failures can be diagnosed quickly.

2. **Add retry policy unification**  
   Navigation retries exist in the scraper, but retry strategy appears ad-hoc by operation. Create a small helper for retries (with backoff and jitter) used by: page load, CAPTCHA fetch, submit, and result-page extraction.

3. **Introduce structured run status artifacts**  
   Emit a single machine-readable run report (`json`) per execution including start/end time, portal counts, failed portal reasons, and output file list.

## 2) Maintainability & architecture

1. **Split `scraping.py` into modules**  
   `scraping.py` is currently monolithic (config, navigation, parsing, reporting, email). Refactor into smaller units:
   - `config.py`
   - `portal_runner.py`
   - `captcha.py`
   - `export.py`
   - `notify.py`

2. **Introduce typed models for config and criteria**  
   Move from raw dicts to `dataclass`/Pydantic-style models with validation and defaults to prevent malformed JSON from causing runtime breakage.

3. **Centralize constants and environment overrides**  
   Keep file defaults in JSON but allow environment variable override for automation and CI (SMTP credentials, output dir, headless mode).

## 3) Performance & resource usage

1. **Lazy-load OCR model once per process**  
   Confirm the OCR model lifecycle is singleton and reused for all CAPTCHA predictions in a run. Avoid repeated model load overhead.

2. **Profile and cap memory under multi-threading**  
   Current design launches multiple browser instances. Add lightweight runtime telemetry (RAM snapshots per worker) and tune worker count from config based on host capacity.

3. **Reduce repository weight for datasets/artifacts**  
   The repository contains large image sets and generated artifacts. Consider moving training datasets to external storage and adding `.gitignore` rules for generated outputs.

## 4) Testing & quality gates

1. **Add smoke tests for configuration and criteria loading**  
   Verify default generation, malformed JSON handling, and key migration behavior.

2. **Add parser unit tests using saved HTML fixtures**  
   Lock parser correctness against portal markup drift by checking extraction from representative sample pages.

3. **Add CI pipeline for lint + tests**  
   Minimal GitHub Actions workflow:
   - setup python
   - install deps
   - `python -m py_compile` (or lint)
   - run unit tests

## 5) Security & compliance

1. **Move sensitive config out of plain JSON**  
   Avoid storing plaintext credentials in project files. Use environment variables or OS credential manager integration for SMTP auth.

2. **Add input sanitization for user-editable portal URLs**  
   Validate scheme/domain format before saving and before runtime usage.

3. **Document operational safeguards**  
   Add a brief policy section for request rates, acceptable scraping intervals, and legal/ToS review expectations.

## 6) Developer experience

1. **Expand README for first-time setup and troubleshooting**  
   Include prerequisites (Playwright browser install, platform assumptions), common failures, and log locations.

2. **Add `CONTRIBUTING.md` and coding conventions**  
   Define branch/commit style, testing expectations, and release process.

3. **Add task scripts (`Makefile` or `justfile`)**  
   Common commands (`run`, `gui`, `lint`, `test`, `package`) reduce onboarding friction.

---

## Suggested execution order

1. Add tests + CI basics.
2. Introduce typed config validation.
3. Break `scraping.py` into modules.
4. Improve credentials handling and observability.
5. Optimize runtime/performance based on measured telemetry.
