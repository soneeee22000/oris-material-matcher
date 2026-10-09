"""Line-level progress of a running job, observed without touching ``MatchService``.

``ProgressWrapper`` is an ``LLMWrapper`` rebuilt from the run's own wrapper (same adapter,
pricing, ledger, limits and collaborators, as the service itself builds its fallback wrapper),
so the run behaves exactly as an unobserved one. After each main-pass batch returns, with an
answer or a failure for each of its lines, it counts the batch's lines; a line is done once all
``passes_k`` of its main passes returned. Rule-decided lines never reach the model, so they
count only when the job finishes. The running spend is the shared ledger's.
"""

from collections import Counter
from collections.abc import Callable, Sequence

from oris_matcher.llm.wrapper import (
    MAIN_ANSWERS,
    AnswerSchema,
    BatchOutcome,
    BudgetLedger,
    LLMWrapper,
    RequestBuilder,
)

WrapperHook = Callable[[LLMWrapper], LLMWrapper]


class RunProgress:
    """What a running job has finished so far."""

    def __init__(self, passes: int) -> None:
        """Start with nothing done.

        Args:
            passes: Main passes per routed line (``Settings.passes_k``).

        """
        self.passes = passes
        self.ledger: BudgetLedger | None = None
        self._returned: Counter[str] = Counter()

    def record(self, line_ids: Sequence[str]) -> None:
        """Count one returned main pass for each line of a batch.

        Args:
            line_ids: The batch's line ids.

        """
        self._returned.update(line_ids)

    @property
    def lines_done(self) -> int:
        """Lines whose every main pass has returned."""
        return sum(count >= self.passes for count in self._returned.values())

    @property
    def spent_usd(self) -> float:
        """The run's spend so far, from its budget ledger; 0 before the run starts."""
        return self.ledger.spent_usd if self.ledger is not None else 0.0


class ProgressWrapper(LLMWrapper):
    """The run's wrapper, rebuilt to report each returned main-pass batch to a ``RunProgress``."""

    def __init__(self, base: LLMWrapper, progress: RunProgress) -> None:
        """Rebuild a fresh wrapper from its parts and attach the progress.

        Args:
            base: The wrapper ``make_wrapper`` built for the run, not yet used.
            progress: Receives the counts and the ledger.

        """
        super().__init__(base.adapter, base.pricing, base.ledger, base.policy, base.deps)
        progress.ledger = base.ledger
        self.progress = progress

    async def run_batch(
        self,
        line_ids: Sequence[str],
        build: RequestBuilder,
        answers: AnswerSchema = MAIN_ANSWERS,
    ) -> BatchOutcome:
        """Run one batch as ``LLMWrapper`` does, then count its lines for a main pass.

        Args:
            line_ids: Distinct line ids, in batch order.
            build: Renders the request for any subset of the ids.
            answers: The answer schema; only ``MAIN_ANSWERS`` batches count.

        Returns:
            The batch's outcome, unchanged.

        """
        outcome = await super().run_batch(line_ids, build, answers)
        if answers is MAIN_ANSWERS:
            self.progress.record(tuple(line_ids))
        return outcome


def observed(progress: RunProgress) -> WrapperHook:
    """Return the hook that swaps a run's wrapper for one reporting to ``progress``.

    Args:
        progress: The job's progress.

    Returns:
        The hook.

    """

    def hook(wrapper: LLMWrapper) -> LLMWrapper:
        """Rebuild the wrapper with progress reporting."""
        return ProgressWrapper(wrapper, progress)

    return hook
