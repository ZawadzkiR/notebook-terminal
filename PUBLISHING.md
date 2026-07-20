# Publishing 0.4.1

```bash
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

Verify that the package metadata points to `ZawadzkiR/notebook-terminal` and that the version is `0.4.1` before upload.
