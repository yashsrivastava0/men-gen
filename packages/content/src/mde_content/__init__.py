"""Data plus loaders: questions, domain packs, recipes, themes, stack profile, policies.

Use the submodules directly (``mde_content.questions``, ``.packs``, ``.themes``, ...) or load
everything at once with ``load_bundle()`` and check consistency with ``validate_bundle()``.
"""

from mde_content.bundle import ContentBundle, load_bundle, validate_bundle

__version__ = "0.1.0"

__all__ = ["ContentBundle", "__version__", "load_bundle", "validate_bundle"]
