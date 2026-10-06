"""A composite LLM port that routes each request to an adapter by its kind (DESIGN.md §11.9).

The E-08 arm is measured as a mixed run (A65.2): the main passes come from a recorded run at
$0 while the verifier requests reach a live (or fake) adapter. ``RoutingLLM`` keeps that
choice outside the wrapper: the wrapper still sees one port, and every request goes to the
main or the verifier adapter as the request itself says.
"""

from collections.abc import Callable

from oris_matcher.llm.base import LLMPort, LLMRequest, LLMResult

RequestKind = Callable[[LLMRequest], bool]


class RoutingLLM:
    """Sends verifier requests to one adapter and every other request to another."""

    def __init__(self, *, main: LLMPort, verifier: LLMPort, is_verifier: RequestKind) -> None:
        """Bind the two adapters and the test that tells the request kinds apart.

        Args:
            main: Answers the main passes.
            verifier: Answers the verifier requests.
            is_verifier: Tells whether a request is a verifier request.

        """
        self.main = main
        self.verifier = verifier
        self.is_verifier = is_verifier

    @property
    def routes(self) -> tuple[LLMPort, LLMPort]:
        """The main adapter, then the verifier adapter."""
        return self.main, self.verifier

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Forward one request to the adapter of its kind.

        Args:
            req: The request.

        Returns:
            That adapter's result.

        """
        adapter = self.verifier if self.is_verifier(req) else self.main
        return await adapter.complete(req)
