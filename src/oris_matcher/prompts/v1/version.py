"""Prompt versions: one per pass variant (DESIGN.md §9.4).

``prompt_version = 'v1+' + sha256(templates || generated JSON schema || glossary || renderer
source || library coding source || prompt helper source || rendering variant)[:8]``. The
library coding source is the loader and normaliser, which decide the display codes and labels
inside the cached library block. The prompt helper source is every domain function the renderer
calls to write a line (its section path and its transport id), with the values of the module
constants those functions read. Each component is hashed on its own first, so no two different
component lists can concatenate to the same bytes.

The E-08 verifier (§7.2, A65.2) has its own ``verifier_prompt_version`` over its templates, its
schema, its renderer and the same renderer, library coding and helper sources, so adding or
changing it never moves a main pass's version.
"""

import hashlib
import inspect
from collections.abc import Callable, Iterator
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any

from oris_matcher.domain import library, normalize
from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.decision import section_path_text
from oris_matcher.prompts.v1 import render, verifier
from oris_matcher.prompts.v1.render import PromptVariant

PROMPT_VERSION_PREFIX = "v1+"
PROMPT_VERSION_HASH_LENGTH = 8
HASH_ENCODING = "utf-8"
NO_GLOSSARY = ""
LIBRARY_CODING_MODULES: tuple[ModuleType, ...] = (library, normalize)
PROMPT_HELPERS: tuple[Callable[..., Any], ...] = (section_path_text, transport_id)
CONSTANT_TYPES = (str, int, float)


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


def _code_names(code: CodeType) -> Iterator[str]:
    """Yield every global name a code object or its nested code objects read."""
    yield from code.co_names
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from _code_names(constant)


def _helper_source(function: Callable[..., Any]) -> str:
    """Return one helper's source text and the module constants it reads, as ``name=repr``."""
    source = inspect.getsource(function).replace("\r\n", "\n")
    module = inspect.getmodule(function)
    names = sorted(set(_code_names(function.__code__)))
    values = ((name, getattr(module, name, None)) for name in names)
    constants = [f"{name}={value!r}" for name, value in values if isinstance(value, CONSTANT_TYPES)]
    return "\n".join([source, *constants])


def prompt_helper_source() -> str:
    """Return the source and constants of the domain helpers the renderer calls, in order."""
    return "\n".join(_helper_source(function) for function in PROMPT_HELPERS)


def version_components(variant: PromptVariant) -> tuple[str, ...]:
    """Return the texts a variant's prompt version hashes, in order.

    Args:
        variant: Profile and rendering of the pass.

    Returns:
        The variant's templates, the schema JSON, the glossary (empty for B2), the renderer
        source, the library coding source, the prompt helper source and the variant name.

    """
    templates = tuple(render.read_template(name) for name in variant.templates)
    glossary = render.glossary_text() if variant.uses_glossary else NO_GLOSSARY
    return (
        *templates,
        render.schema_json(),
        glossary,
        renderer_source(),
        library_coding_source(),
        prompt_helper_source(),
        variant.name,
    )


def _hashed_version(components: tuple[str, ...]) -> str:
    """Hash each component, then the list of hashes, into a ``v1+`` prompt version."""
    digest = hashlib.sha256()
    for component in components:
        digest.update(hashlib.sha256(component.encode(HASH_ENCODING)).hexdigest().encode())
    return f"{PROMPT_VERSION_PREFIX}{digest.hexdigest()[:PROMPT_VERSION_HASH_LENGTH]}"


def prompt_version(variant: PromptVariant) -> str:
    """Return the prompt version of one pass variant.

    Args:
        variant: Profile and rendering of the pass.

    Returns:
        ``v1+`` and the first ``PROMPT_VERSION_HASH_LENGTH`` hex characters of the hash.

    """
    return _hashed_version(version_components(variant))


def verifier_version_components() -> tuple[str, ...]:
    """Return the texts the E-08 verifier's prompt version hashes, in order.

    Returns:
        The verifier templates, its schema JSON, its renderer source, the main renderer source
        (it renders each line as the main prompt does), the library coding source, the prompt
        helper source and the verifier variant name.

    """
    templates = tuple(verifier.verifier_template(name) for name in verifier.VERIFIER_TEMPLATES)
    return (
        *templates,
        verifier.verifier_schema_json(),
        _module_source(verifier),
        renderer_source(),
        library_coding_source(),
        prompt_helper_source(),
        verifier.VERIFIER_VARIANT_NAME,
    )


def verifier_prompt_version() -> str:
    """Return the prompt version of the E-08 sibling verifier.

    Returns:
        ``v1+`` and the first ``PROMPT_VERSION_HASH_LENGTH`` hex characters of the hash.

    """
    return _hashed_version(verifier_version_components())
