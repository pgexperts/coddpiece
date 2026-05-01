# Releasing coddpiece

This is the operator runbook for cutting a PyPI release. Read it
top-to-bottom for the first release; subsequent releases can skip the
one-time setup and start at the **Pre-flight** section.

PyPI is **append-only** — a version number, once published, cannot be
re-published with different contents. You can `pypi.org/project/coddpiece/`
yank a release (hide it from the default resolver), but the bytes
remain forever and anyone who pinned that version will still receive
them. Treat every release as final from the moment `twine upload`
returns success.

---

## One-time setup

You need three things before the first release: a PyPI account, a way
for `twine` to authenticate, and the build/upload toolchain installed.

### 1. PyPI account

Create accounts on **both** PyPI and TestPyPI — they are separate
registries. TestPyPI is the dry-run target.

- https://pypi.org/account/register/
- https://test.pypi.org/account/register/

Enable 2FA on both accounts (PyPI now requires it for new uploads).

### 2. Authentication

Two options. **Trusted publishing is the recommended one** — it avoids
long-lived secrets and uses OIDC identity from GitHub Actions.

**Option A — Trusted Publishing (recommended for automated releases).**
Configure a "trusted publisher" on PyPI:
1. Go to https://pypi.org/manage/account/publishing/
2. Add a new publisher pointing at this repo's release workflow
3. Repeat on TestPyPI for dry-runs

This requires a GitHub Actions release workflow to actually use it
(see "Future enhancement" at the bottom). For manual releases from a
local machine, use Option B.

**Option B — API tokens (manual releases).**
1. Generate a project-scoped token: https://pypi.org/manage/account/token/
2. Repeat on TestPyPI
3. Save them to `~/.pypirc`:

   ```ini
   [distutils]
   index-servers =
       pypi
       testpypi

   [pypi]
   username = __token__
   password = pypi-AgEIcHlwaS5vcmcC...   # your real token

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-AgENdGVzdC5weXBp...   # your TestPyPI token
   ```

   `chmod 600 ~/.pypirc` afterward.

### 3. Toolchain

Build and upload tools (the project ships these in the `[dev]` extra
plus `build` and `twine` separately):

```bash
pip install --upgrade build twine
```

Or via uv (matches what CI uses):

```bash
uv tool install build
uv tool install twine
```

---

## Pre-flight

Run from a **clean main branch** with no uncommitted changes.

```bash
# 1. Ensure main is clean and up to date.
git checkout main
git status                       # must report "nothing to commit, working tree clean"
git pull --ff-only

# 2. Run the full local test matrix (mirrors CI).
ruff check coddpiece tests
mypy coddpiece
python -m pytest tests/ -v       # SQLite: 87 tests
DATABASE_URL=postgresql:///coddpiece_test \
    python -m pytest tests/test_postgres.py -v   # PG: 12 tests

# 3. Confirm CI is green on the latest main commit.
#    https://github.com/pgexperts/coddpiece/actions
```

If anything is red, **stop and fix before bumping the version**. The
worst kind of broken release is a green local build against a known-bad
remote tree.

---

## Cut the release

### 1. Bump the version

The version lives in **one place**: `pyproject.toml`. The
`__version__` attribute on the package is read from installed metadata,
so it tracks `pyproject.toml` automatically.

```bash
# Edit pyproject.toml, change version = "X.Y.Z"
$EDITOR pyproject.toml
```

Follow [SemVer](https://semver.org/):
- **Major** — incompatible API changes (or any change that breaks
  importable names, expression-tree shapes, or compiler output that
  downstream code might rely on)
- **Minor** — new functionality, backwards-compatible
- **Patch** — bug fixes only, no API changes

### 2. Build the artifacts

```bash
# Clear stale builds first — twine will happily upload old wheels
# from previous versions if they're still in dist/.
rm -rf dist/ build/ *.egg-info/ coddpiece/*.egg-info/

python -m build                  # produces dist/coddpiece-X.Y.Z.tar.gz
                                 #          dist/coddpiece-X.Y.Z-py3-none-any.whl
```

### 3. Validate the artifacts

```bash
# twine's static checks — README rendering, classifier validity, etc.
twine check dist/*

# Smoke install in an isolated venv. This catches problems that
# `python -m build` does not, such as missing package data, broken
# entry points, or import errors that only surface at runtime.
python -m venv /tmp/coddpiece-release-test
/tmp/coddpiece-release-test/bin/pip install dist/coddpiece-*.whl
/tmp/coddpiece-release-test/bin/python -c "
import sqlite3
from coddpiece import Engine, count, __version__
print('version:', __version__)
e = Engine(sqlite3.connect(':memory:'))
r = e.create('t', {'x': int}, rows=[(1,), (2,), (3,)])
print('rows:', sorted(r.collect()))
"
rm -rf /tmp/coddpiece-release-test
```

The smoke test must print the version you just bumped to and the
three rows. If `__version__` reports `0.0.0+unknown`, the metadata
isn't being read — the wheel is broken.

### 4. Dry-run on TestPyPI

```bash
twine upload --repository testpypi dist/*
```

Then verify the release page renders correctly:
https://test.pypi.org/project/coddpiece/

And install from TestPyPI in a fresh venv:

```bash
python -m venv /tmp/coddpiece-testpypi
/tmp/coddpiece-testpypi/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    coddpiece
/tmp/coddpiece-testpypi/bin/python -c "import coddpiece; print(coddpiece.__version__)"
rm -rf /tmp/coddpiece-testpypi
```

The `--extra-index-url` is required because TestPyPI doesn't mirror
real PyPI's dependency graph — without it, `pip` may fail to resolve
`pytest`/`ruff`/etc. for the `[dev]` extra.

If TestPyPI is unhappy, **fix and re-build with a new version
number** — TestPyPI is also append-only.

### 5. Tag and push

Tag the release commit on `main`:

```bash
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin main
git push origin vX.Y.Z
```

The `v` prefix on tags is convention; skip it if you prefer.

### 6. Upload to PyPI

```bash
twine upload dist/*
```

Verify: https://pypi.org/project/coddpiece/

### 7. Publish a GitHub release

```bash
gh release create vX.Y.Z dist/coddpiece-X.Y.Z.tar.gz dist/coddpiece-X.Y.Z-py3-none-any.whl \
    --title "X.Y.Z" \
    --notes-file RELEASE_NOTES.md   # or --notes "..."
```

Or via the GitHub UI from the tag.

---

## Post-release

1. **Announce** in whatever channel is appropriate.
2. **Verify install from real PyPI** in a fresh venv:

   ```bash
   python -m venv /tmp/coddpiece-pypi-final
   /tmp/coddpiece-pypi-final/bin/pip install coddpiece
   /tmp/coddpiece-pypi-final/bin/python -c "import coddpiece; print(coddpiece.__version__)"
   rm -rf /tmp/coddpiece-pypi-final
   ```

3. **Watch the CI badge** in the README. If it goes red after the tag,
   the release is poisoned — file an issue immediately and consider
   yanking.

---

## If a release is broken

Do not delete the version on PyPI (you can't, anyway). Two options:

- **Yank** — hide the version from the default resolver. Pinned
  installs still get the broken wheel, but new installs default to
  the previous good version. Use the PyPI web UI:
  `https://pypi.org/manage/project/coddpiece/release/X.Y.Z/`
- **Patch release** — bump the patch version, fix the issue, repeat
  the procedure above. This is the right move for anything
  user-visible.

Whichever path you pick, document the broken release in the changelog
or release notes so people understand why a version is missing or
yanked.

---

## Future enhancement: GitHub Actions release workflow

A `.github/workflows/release.yml` can automate steps 2-7 on tag push
using the trusted-publisher OIDC flow. The minimal shape:

```yaml
on:
  push:
    tags: ["v*"]
jobs:
  release:
    permissions:
      id-token: write           # for trusted publishing
      contents: write           # for gh release create
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1   # uses OIDC
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
```

This requires the trusted publisher to be configured on PyPI first
(see One-time setup, Option A). Worth adding once you've shipped a
release or two manually and have a feel for the process.
