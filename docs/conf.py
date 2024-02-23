import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "vibrio"
copyright = "2024, notjagan"
author = "notjagan"

extensions = ["sphinx.ext.autodoc", "numpydoc"]

numpydoc_class_members_toctree = False
numpydoc_show_inherited_class_members = False
autodoc_default_options = {"members": True, "undoc-members": True}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_sidebars = {"**": ["localtoc.html"]}
