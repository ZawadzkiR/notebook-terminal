# Building and Publishing Notebook Terminal

This guide covers local validation, building `.whl` and `.tar.gz` files, pushing the repository to GitHub, testing on TestPyPI, and publishing to PyPI.

## 1. Replace repository placeholders

Search the project for:

```text
YOUR-USERNAME
```

Replace it with your GitHub username in `pyproject.toml` and `README.md`.

Confirm that the package name `notebook-terminal` is available on PyPI before the first upload. PyPI release files cannot be overwritten under the same version number.

## 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Upgrade packaging tools and install development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 3. Run tests

```bash
python -m pytest
```

## 4. Build wheel and source distribution

Remove old artifacts first.

Windows PowerShell:

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force src\notebook_terminal.egg-info -ErrorAction SilentlyContinue
```

Linux or macOS:

```bash
rm -rf build dist src/notebook_terminal.egg-info
```

Build both distributions:

```bash
python -m build
```

Expected files:

```text
dist/
├── notebook_terminal-0.1.1-py3-none-any.whl
└── notebook_terminal-0.1.1.tar.gz
```

Validate metadata and README rendering:

```bash
python -m twine check dist/*
```

## 5. Test the built wheel in a clean environment

Windows PowerShell:

```powershell
py -m venv .package-test
.\.package-test\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .\dist\notebook_terminal-0.1.1-py3-none-any.whl
python -c "import notebook_terminal; print(notebook_terminal.__version__)"
```

Linux or macOS:

```bash
python3 -m venv .package-test
source .package-test/bin/activate
python -m pip install --upgrade pip
python -m pip install ./dist/notebook_terminal-0.1.1-py3-none-any.whl
python -c "import notebook_terminal; print(notebook_terminal.__version__)"
```

The output should be:

```text
0.1.1
```

## 6. Create the GitHub repository

Create an empty repository named `notebook-terminal` on GitHub. Do not initialize it with another README or license because the project already contains those files.

From the project directory:

```bash
git init
git add .
git commit -m "Initial release 0.1.1"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/notebook-terminal.git
git push -u origin main
```

Alternatively, with GitHub CLI:

```bash
gh auth login
gh repo create notebook-terminal --public --source=. --remote=origin --push
```

## 7. Test the upload on TestPyPI

Create a TestPyPI account and API token, then run:

```bash
python -m twine upload --repository testpypi dist/*
```

Use these credentials when prompted:

```text
username: __token__
password: your TestPyPI API token
```

Install the package from TestPyPI while resolving dependencies from the main PyPI index:

```bash
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  notebook-terminal==0.1.1
```

PowerShell can use the same command on one line:

```powershell
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ notebook-terminal==0.1.1
```

## 8. Publish manually to PyPI

After TestPyPI validation:

```bash
python -m twine upload dist/*
```

Use:

```text
username: __token__
password: your PyPI API token
```

The package can then be installed with:

```bash
python -m pip install notebook-terminal
```

## 9. Recommended: PyPI Trusted Publishing through GitHub Actions

The repository includes `.github/workflows/publish.yml`. It builds and publishes whenever a tag matching `v*` is pushed.

In PyPI, configure a GitHub Actions Trusted Publisher with:

```text
Owner: YOUR-USERNAME
Repository: notebook-terminal
Workflow name: publish.yml
Environment name: pypi
```

Then create and push the release tag:

```bash
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin v0.1.1
```

The workflow will:

1. check out the tagged source,
2. build the wheel and source distribution,
3. run `twine check`,
4. upload the distributions as workflow artifacts,
5. publish them to PyPI using a short-lived OIDC credential.

## 10. Create a GitHub Release

After pushing `v0.1.1`:

1. Open the repository's **Releases** page.
2. Select **Draft a new release**.
3. Choose the `v0.1.1` tag.
4. Use the title `Notebook Terminal 0.1.1`.
5. Add release notes.
6. Optionally attach the two files from `dist/`.

## 11. Publishing a later fix

PyPI does not allow replacing an existing `0.1.1` file. For a bug fix:

1. change the version in `pyproject.toml`,
2. change `__version__` in `src/notebook_terminal/__init__.py`,
3. use a new version such as `0.1.1`,
4. rebuild and retest,
5. commit and tag `v0.1.1`.

Example:

```bash
git add .
git commit -m "Release 0.1.1"
git push
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin v0.1.1
```
