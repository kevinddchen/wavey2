"""
Shared library code for the wavey2 data pipeline.

The pipeline's entry points live in `wavey2.apps`, one module per step; anything
they need to share — paths, file-naming conventions, the binary format — belongs
here so there is a single definition of it. Installed from `py/` (see
`[build-system]` in `pyproject.toml`), so `import wavey2` works in the venv.
"""
