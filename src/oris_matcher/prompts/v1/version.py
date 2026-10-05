"""Prompt versions: one per pass variant (DESIGN.md §9.4).

``prompt_version = 'v1+' + sha256(templates || generated JSON schema || glossary || renderer
source || library coding source || rendering variant)[:8]``. The library coding source is the
loader and normaliser, which decide the display codes and labels inside the cached library block.
Each component is hashed on its own first, so no two different component lists can concatenate
to the same bytes.
"""

import hashlib
from pathlib import Path
from types import ModuleType

from oris_matcher.domain import library, normalize
from oris_matcher.prompts.v1 import render
from oris_matcher.prompts.v1.render import PromptVariant

PROMPT_VERSION_PREFIX = "v1+"
PROMPT_VERSION_HASH_LENGTH = 8
HASH_ENCODING = "utf-8"
NO_GLOSSARY = ""
LIBRARY_CODING_MODULES: tuple[ModuleType, ...] = (library, normalize)


def _module_source(module: ModuleType) -> str:
    """Return one module's source text with LF line endings."""
    if module.__file__ is None:
        raise ValueError(f"module {module.__name__} has no source file")
    return Path(module.__file__).read_text(encoding=HASH_ENCODING).replace("\r\n", "\n")


def renderer_source() -> str:
    """Return the renderer's source text with LF line endings."""
    return _module_source(render)


def library_coding_source() -> str:
    """Return the library loader's and normaliser's source texts, LF line endings, in order."""
    return "\n".join(_module_source(module) for module in LIBRARY_CODING_MODULES)


def version_components(variant: PromptVariant) -> tuple[str, ...]:
    """Return the texts a variant's prompt version hashes, in order.

    Args:
        variant: Profile and rendering of the pass.

    Returns:
        The variant's templates, the schema JSON, the glossary (empty for B2), the renderer
        source, the library coding source and the variant name.

    """
    templates = tuple(render.read_template(name) for name in variant.templates)
    glossary = render.glossary_text() if variant.uses_glossary else NO_GLOSSARY
    return (
        *templates,
        render.schema_json(),
        glossary,
        renderer_source(),
        library_coding_source(),
        variant.name,
    )


def prompt_version(variant: PromptVariant) -> str:
    """Return the prompt version of one pass variant.

    Args:
        variant: Profile and rendering of the pass.

    Returns:
        ``v1+`` and the first ``PROMPT_VERSION_HASH_LENGTH`` hex characters of the hash.

    """
    digest = hashlib.sha256()
    for component in version_components(variant):
        digest.update(hashlib.sha256(component.encode(HASH_ENCODING)).hexdigest().encode())
    return f"{PROMPT_VERSION_PREFIX}{digest.hexdigest()[:PROMPT_VERSION_HASH_LENGTH]}"
