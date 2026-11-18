"""Sphinx configuration for dartfx-workspace-api documentation."""

import os
import sys

project = "Data Artifex Workspace API"
copyright = "2026, Pascal Heus"
author = "Pascal Heus"
release = "0.1.0"

sys.path.insert(0, os.path.abspath("../../../dartfx-workspace/src"))
sys.path.insert(0, os.path.abspath("../../src/dartfx"))
sys.path.insert(0, os.path.abspath("../../src"))

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "myst_parser",
]
templates_path = ["_templates"]
autodoc_mock_imports = ["blake3", "rdflib", "psycopg", "duckdb", "polars", "pyreadstat"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
