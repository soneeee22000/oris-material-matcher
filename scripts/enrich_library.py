r"""Offline, deterministic arm C enrichment of one library (DESIGN.md §6, A68.2).

Reads the library CSV, the term map and the glossary supplement, and nothing else: never BoQ
text, labels or reviewer notes. Writes one YAML file whose bytes depend on those inputs only
(sorted keys, LF line ends, no timestamps), with the library's SHA-256, the generator and its
version, the two source hashes, the reviewer, the ``types``, ``usages`` and ``subtypes`` node
lists with their ``also`` equivalents, and the supplement's glossary entries.

Usage::

    python scripts/enrich_library.py --library data/oris_materials_global.csv \
        --output data/enrichment/global.yaml [--term-map data/enrichment/term_map.yaml] \
        [--supplement data/enrichment/glossary_supplement.yaml] [--reviewed-by "name, date"]

Exit codes: 0 written, 2 an input cannot be read or is invalid (nothing is written).
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from oris_matcher.domain.library import LibraryError, load_library
from oris_matcher.enrichment import EnrichmentSources, dump_enrichment, generate

DEFAULT_TERM_MAP = Path("data") / "enrichment" / "term_map.yaml"
DEFAULT_SUPPLEMENT = Path("data") / "enrichment" / "glossary_supplement.yaml"
OUTPUT_ENCODING = "utf-8"
EXIT_OK = 0
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--term-map", type=Path, default=DEFAULT_TERM_MAP)
    parser.add_argument("--supplement", type=Path, default=DEFAULT_SUPPLEMENT)
    parser.add_argument("--reviewed-by", default="")
    return parser


def render(args: argparse.Namespace) -> str:
    """Generate the enrichment text from the parsed arguments.

    Args:
        args: The parsed arguments.

    Returns:
        The YAML text to write.

    Raises:
        OSError: An input cannot be read.
        ValueError: The library, the term map or the supplement is invalid.

    """
    library = load_library(args.library.read_bytes())
    sources = EnrichmentSources(
        term_map=args.term_map.read_bytes(),
        supplement=args.supplement.read_bytes(),
        reviewed_by=args.reviewed_by,
    )
    return dump_enrichment(generate(library, sources))


def main(argv: Sequence[str] | None = None) -> int:
    """Write the enrichment file; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.

    Returns:
        0 when written, 2 when an input cannot be read or is invalid.

    """
    args = build_parser().parse_args(argv)
    try:
        text = render(args)
    except (OSError, ValueError, LibraryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(text.encode(OUTPUT_ENCODING))
    print(f"wrote {args.output.as_posix()}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
