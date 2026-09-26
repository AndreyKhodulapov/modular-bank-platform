"""Risk analysis of transactions: independent rules add up to a score and a risk level."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import IntEnum

from exceptions import InvalidOperationError
from models.enums import TransactionType
from models.transaction import Transaction
from services.security import SecurityGuard
from utils import to_money, to_positive_int


class RiskLevel(IntEnum):
    """How dangerous a transaction looks; ordered, so levels compare with ``<`` and ``>=``."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class RiskContext:
    """What the bank knows about a transaction at the moment it is assessed.

    ``client_id`` is the owner of the account the money leaves, or of the
    account it arrives at for a deposit. ``recipient_opened_at`` is ``None``
    when there is no recipient in this bank (a withdrawal, an external
    transfer).
    """

    transaction: Transaction
    client_id: str
    moment: datetime
    amount_in_base: Decimal
    recipient_opened_at: datetime | None = None


@dataclass(frozen=True)
class RiskFactor:
    """One reason a transaction looks risky and how much it adds to the score."""

    rule: str
    score: int
    description: str


@dataclass(frozen=True)
class RiskAssessment:
    """The result of assessing one attempt of a transaction."""

    transaction_id: str
    client_id: str
    moment: datetime
    amount_in_base: Decimal
    score: int
    level: RiskLevel
    factors: tuple[RiskFactor, ...]

    @property
    def blocked(self) -> bool:
        return self.level is RiskLevel.HIGH

    @property
    def rules(self) -> tuple[str, ...]:
        return tuple(factor.rule for factor in self.factors)

    def __str__(self) -> str:
        rules = ", ".join(self.rules) or "-"
        return f"{self.level.name.lower():<6} score {self.score:>3} | {rules}"


class RiskHistory:
    """What the analyzer remembers between assessments.

    - when each transaction was first assessed, per client: a retry of the
      same transaction is not a new operation for the frequency rule;
    - which sender -> recipient pairs already completed a transfer.
    """

    def __init__(self) -> None:
        self._first_seen: dict[str, tuple[str, datetime]] = {}
        self._known_pairs: set[tuple[str, str]] = set()

    def remember(self, transaction_id: str, client_id: str, moment: datetime) -> None:
        self._first_seen.setdefault(transaction_id, (client_id, moment))

    def count_recent(self, client_id: str, since: datetime, until: datetime, *, current: str | None = None) -> int:
        """Distinct transactions of the client first seen in ``(since, until]``.

        ``current`` is always counted, even when a retry of it was first seen
        before ``since``.
        """
        others = sum(
            1
            for transaction_id, (owner, first_seen) in self._first_seen.items()
            if transaction_id != current and owner == client_id and since < first_seen <= until
        )
        return others + (1 if current is not None else 0)

    def record_transfer(self, sender_id: str, recipient_id: str) -> None:
        self._known_pairs.add((sender_id, recipient_id))

    def has_sent(self, sender_id: str, recipient_id: str) -> bool:
        return (sender_id, recipient_id) in self._known_pairs


class RiskRule(ABC):
    """A single, independent check; the analyzer adds up the scores of the rules that fire."""

    name: str

    def __init__(self, score: int) -> None:
        self.score = to_positive_int(score, field="score")

    @abstractmethod
    def evaluate(self, context: RiskContext, history: RiskHistory) -> RiskFactor | None:
        """Return a factor when the rule fires for ``context``, otherwise ``None``."""


class LargeAmountRule(RiskRule):
    """A large amount in the base currency; a very large one scores enough to be blocked on its own.

    The default ``threshold`` is the amount the security guard already
    reviews, so both use one notion of "large".
    """

    name = "large_amount"

    def __init__(
        self,
        threshold: object = SecurityGuard.LARGE_OPERATION_THRESHOLD,
        score: int = 40,
        *,
        critical_threshold: object = "2000000",
        critical_score: int = 70,
    ) -> None:
        super().__init__(score)
        self.threshold = to_money(threshold, field="threshold", require="positive")
        self.critical_threshold = to_money(critical_threshold, field="critical_threshold", require="positive")
        if self.critical_threshold <= self.threshold:
            raise InvalidOperationError("critical_threshold must be above threshold.")
        if not isinstance(critical_score, int) or critical_score <= score:
            raise InvalidOperationError("critical_score must be an integer above score.")
        self.critical_score = critical_score

    def evaluate(self, context: RiskContext, history: RiskHistory) -> RiskFactor | None:
        amount = context.amount_in_base
        if amount >= self.critical_threshold:
            return RiskFactor(self.name, self.critical_score, f"{amount} is at least {self.critical_threshold}")
        if amount >= self.threshold:
            return RiskFactor(self.name, self.score, f"{amount} is at least {self.threshold}")
        return None


class HighFrequencyRule(RiskRule):
    """Many transactions of one client in a short window; the ``threshold``-th one fires, the current one included."""

    name = "high_frequency"

    def __init__(self, threshold: int = 5, window: timedelta = timedelta(minutes=10), score: int = 30) -> None:
        super().__init__(score)
        if not isinstance(threshold, int) or threshold < 2:
            raise InvalidOperationError("threshold must be an integer of at least 2.")
        if not isinstance(window, timedelta) or window <= timedelta(0):
            raise InvalidOperationError("window must be a positive timedelta.")
        self.threshold = threshold
        self.window = window

    def evaluate(self, context: RiskContext, history: RiskHistory) -> RiskFactor | None:
        count = history.count_recent(
            context.client_id,
            context.moment - self.window,
            context.moment,
            current=context.transaction.transaction_id,
        )
        if count < self.threshold:
            return None
        minutes = int(self.window.total_seconds() // 60)
        return RiskFactor(self.name, self.score, f"{count} transactions within {minutes} minutes")


class NewRecipientRule(RiskRule):
    """A transfer to a recently opened account, or to a recipient the sender never paid before."""

    name = "new_recipient"
    TRANSFER_TYPES = frozenset({TransactionType.TRANSFER, TransactionType.EXTERNAL_TRANSFER})

    def __init__(self, min_account_age: timedelta = timedelta(days=7), score: int = 20) -> None:
        super().__init__(score)
        if not isinstance(min_account_age, timedelta) or min_account_age <= timedelta(0):
            raise InvalidOperationError("min_account_age must be a positive timedelta.")
        self.min_account_age = min_account_age

    def evaluate(self, context: RiskContext, history: RiskHistory) -> RiskFactor | None:
        transaction = context.transaction
        if transaction.transaction_type not in self.TRANSFER_TYPES:
            return None
        opened_at = context.recipient_opened_at
        if opened_at is not None and context.moment - opened_at < self.min_account_age:
            return RiskFactor(self.name, self.score, f"recipient account opened {opened_at:%Y-%m-%d %H:%M}")
        if not history.has_sent(transaction.sender_id, transaction.recipient_id):
            return RiskFactor(self.name, self.score, "first transfer to this recipient")
        return None


class NightOperationRule(RiskRule):
    """An operation late in the evening or at night; the window may cross midnight.

    It is wider than the bank's hard ban (00:00-05:00): at its edges an
    operation is still allowed, but looks riskier.
    """

    name = "night_operation"

    def __init__(self, start: time = time(22, 0), end: time = time(6, 0), score: int = 20) -> None:
        super().__init__(score)
        if not isinstance(start, time) or not isinstance(end, time) or start == end:
            raise InvalidOperationError("start and end must be two different times of day.")
        self.start = start
        self.end = end

    def _is_night(self, moment: time) -> bool:
        if self.start < self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end

    def evaluate(self, context: RiskContext, history: RiskHistory) -> RiskFactor | None:
        if not self._is_night(context.moment.time()):
            return None
        return RiskFactor(self.name, self.score, f"at {context.moment:%H:%M}")


def default_rules() -> list[RiskRule]:
    return [LargeAmountRule(), HighFrequencyRule(), NewRecipientRule(), NightOperationRule()]


class RiskAnalyzer:
    """Scores transactions with a set of rules (the Strategy pattern).

    The score is the sum of the factors of the rules that fire; the level
    follows from the thresholds: ``LOW`` below ``medium_threshold``,
    ``HIGH`` from ``high_threshold``. A new check is a new ``RiskRule``
    passed in ``rules`` - the analyzer itself does not change.

    The analyzer moves no money and refuses nothing: an assessment marked
    ``blocked`` is a verdict, and the bank acts on it in ``screen()``.
    ``record_completed()`` tells the analyzer which transfers went through,
    so their recipients stop being new for that sender.
    """

    def __init__(
        self,
        rules: Iterable[RiskRule] | None = None,
        *,
        medium_threshold: int = 40,
        high_threshold: int = 70,
    ) -> None:
        self._rules = list(default_rules() if rules is None else rules)
        if not all(isinstance(rule, RiskRule) for rule in self._rules):
            raise InvalidOperationError("rules must be RiskRule instances.")
        if not (isinstance(medium_threshold, int) and isinstance(high_threshold, int)):
            raise InvalidOperationError("thresholds must be integers.")
        if not 0 < medium_threshold < high_threshold:
            raise InvalidOperationError("thresholds must satisfy 0 < medium_threshold < high_threshold.")
        self._medium_threshold = medium_threshold
        self._high_threshold = high_threshold
        self._history = RiskHistory()
        self._assessments: list[RiskAssessment] = []

    @property
    def rules(self) -> list[RiskRule]:
        return list(self._rules)

    @property
    def assessments(self) -> list[RiskAssessment]:
        """A copy of every assessment, one per attempt, in order."""
        return list(self._assessments)

    def level_for(self, score: int) -> RiskLevel:
        if score >= self._high_threshold:
            return RiskLevel.HIGH
        if score >= self._medium_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def assess(self, context: RiskContext) -> RiskAssessment:
        transaction = context.transaction
        # remembered before the rules run, so the frequency rule counts the current transaction
        self._history.remember(transaction.transaction_id, context.client_id, context.moment)
        factors = tuple(factor for rule in self._rules if (factor := rule.evaluate(context, self._history)) is not None)
        score = sum(factor.score for factor in factors)
        assessment = RiskAssessment(
            transaction_id=transaction.transaction_id,
            client_id=context.client_id,
            moment=context.moment,
            amount_in_base=context.amount_in_base,
            score=score,
            level=self.level_for(score),
            factors=factors,
        )
        self._assessments.append(assessment)
        return assessment

    def record_completed(self, transaction: Transaction) -> None:
        """Remember a completed transfer, so its recipient is known to the sender from now on."""
        if transaction.sender_id is not None and transaction.recipient_id is not None:
            self._history.record_transfer(transaction.sender_id, transaction.recipient_id)
