from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from exceptions import InvalidOperationError
from models import Transaction
from services import (
    HighFrequencyRule,
    LargeAmountRule,
    NewRecipientRule,
    NightOperationRule,
    RiskAnalyzer,
    RiskContext,
    RiskFactor,
    RiskHistory,
    RiskLevel,
    RiskRule,
)

NOW = datetime(2026, 9, 24, 14, 0)


def transfer(sender="S", recipient="R", amount=1_000, kind="transfer") -> Transaction:
    return Transaction(kind, amount, "RUB", sender_id=sender, recipient_id=recipient, created_at=NOW)


def context(
    transaction: Transaction | None = None,
    *,
    amount: object = "1000",
    moment: datetime = NOW,
    client_id: str = "C",
    opened_at: datetime | None = None,
) -> RiskContext:
    return RiskContext(
        transaction=transaction if transaction is not None else transfer(),
        client_id=client_id,
        moment=moment,
        amount_in_base=Decimal(amount),
        recipient_opened_at=opened_at,
    )


class FixedRule(RiskRule):
    """Always fires with its score; lets the analyzer be tested apart from the real rules."""

    name = "fixed"

    def evaluate(self, context, history):
        return RiskFactor(self.name, self.score, "always")


# LargeAmountRule


@pytest.mark.parametrize(
    ("amount", "score"),
    [("499999.99", None), ("500000", 40), ("1999999.99", 40), ("2000000", 70)],
)
def test_large_amount_thresholds(amount, score):
    factor = LargeAmountRule().evaluate(context(amount=amount), RiskHistory())
    assert (factor.score if factor else None) == score


@pytest.mark.parametrize(
    "params",
    [
        {"threshold": 0},
        {"threshold": 100, "critical_threshold": 100},
        {"score": 40, "critical_score": 40},
        {"score": 0},
        {"score": True},
    ],
)
def test_large_amount_rejects_invalid_settings(params):
    with pytest.raises(InvalidOperationError):
        LargeAmountRule(**params)


# HighFrequencyRule


def test_frequency_fires_from_the_fifth_transaction_in_the_window():
    history = RiskHistory()
    rule = HighFrequencyRule()
    for number in range(4):
        history.remember(f"T{number}", "C", NOW - timedelta(minutes=9))
    current = transfer()
    history.remember(current.transaction_id, "C", NOW)
    factor = rule.evaluate(context(current), history)
    assert factor is not None and factor.score == 30
    assert "5 transactions within 10 minutes" in factor.description


def test_frequency_ignores_older_transactions_and_other_clients():
    history = RiskHistory()
    for number in range(3):
        history.remember(f"T{number}", "C", NOW - timedelta(minutes=10))  # just outside the window
        history.remember(f"O{number}", "other", NOW)
    history.remember("A", "C", NOW)
    history.remember("B", "C", NOW)
    assert HighFrequencyRule().evaluate(context(), history) is None


def test_retries_of_one_transaction_count_once():
    history = RiskHistory()
    for _ in range(5):
        history.remember("same", "C", NOW)
    assert history.count_recent("C", NOW - timedelta(minutes=10), NOW) == 1


@pytest.mark.parametrize("params", [{"max_count": 1}, {"window": timedelta(0)}, {"score": -1}])
def test_frequency_rejects_invalid_settings(params):
    with pytest.raises(InvalidOperationError):
        HighFrequencyRule(**params)


# NewRecipientRule


def test_recently_opened_recipient_account_fires():
    history = RiskHistory()
    history.record_transfer("S", "R")
    factor = NewRecipientRule().evaluate(context(opened_at=NOW - timedelta(days=6, hours=23)), history)
    assert factor is not None and "opened" in factor.description


def test_first_transfer_to_an_old_account_fires():
    factor = NewRecipientRule().evaluate(context(opened_at=NOW - timedelta(days=30)), RiskHistory())
    assert factor is not None and factor.description == "first transfer to this recipient"


def test_known_recipient_with_an_old_account_is_fine():
    history = RiskHistory()
    history.record_transfer("S", "R")
    assert NewRecipientRule().evaluate(context(opened_at=NOW - timedelta(days=7)), history) is None


def test_external_recipient_is_new_until_paid_once():
    external = transfer(recipient="DE-0001", kind="external_transfer")
    history = RiskHistory()
    assert NewRecipientRule().evaluate(context(external), history) is not None
    history.record_transfer("S", "DE-0001")
    assert NewRecipientRule().evaluate(context(external), history) is None


@pytest.mark.parametrize(
    "transaction",
    [
        Transaction("deposit", 100, "RUB", recipient_id="R", created_at=NOW),
        Transaction("withdrawal", 100, "RUB", sender_id="S", created_at=NOW),
    ],
)
def test_new_recipient_applies_to_transfers_only(transaction):
    assert NewRecipientRule().evaluate(context(transaction, opened_at=NOW), RiskHistory()) is None


# NightOperationRule


@pytest.mark.parametrize(
    ("moment", "night"),
    [(time(21, 59), False), (time(22, 0), True), (time(0, 0), True), (time(5, 59), True), (time(6, 0), False)],
)
def test_night_window_crosses_midnight(moment, night):
    factor = NightOperationRule().evaluate(context(moment=datetime.combine(NOW.date(), moment)), RiskHistory())
    assert (factor is not None) is night


def test_night_window_within_one_day():
    rule = NightOperationRule(start=time(1, 0), end=time(3, 0))
    assert rule.evaluate(context(moment=NOW.replace(hour=2)), RiskHistory()) is not None
    assert rule.evaluate(context(moment=NOW.replace(hour=23)), RiskHistory()) is None


def test_night_rejects_an_empty_window():
    with pytest.raises(InvalidOperationError):
        NightOperationRule(start=time(1, 0), end=time(1, 0))


# RiskAnalyzer


@pytest.mark.parametrize(
    ("score", "level"),
    [(0, RiskLevel.LOW), (39, RiskLevel.LOW), (40, RiskLevel.MEDIUM), (69, RiskLevel.MEDIUM), (70, RiskLevel.HIGH)],
)
def test_score_thresholds(score, level):
    analyzer = RiskAnalyzer(rules=[FixedRule(score)] if score else [])
    assessment = analyzer.assess(context())
    assert (assessment.score, assessment.level, assessment.blocked) == (score, level, level is RiskLevel.HIGH)


def test_scores_of_fired_rules_add_up():
    analyzer = RiskAnalyzer()
    # large (40) + first transfer to this recipient (20) + night (20)
    assessment = analyzer.assess(context(amount="600000", moment=NOW.replace(hour=23)))
    assert assessment.rules == ("large_amount", "new_recipient", "night_operation")
    assert (assessment.score, assessment.level) == (80, RiskLevel.HIGH)


def test_ordinary_transfer_to_a_known_recipient_is_clean():
    analyzer = RiskAnalyzer()
    analyzer.record_completed(transfer())
    assessment = analyzer.assess(context(opened_at=NOW - timedelta(days=30)))
    assert (assessment.score, assessment.level, assessment.factors) == (0, RiskLevel.LOW, ())


def test_record_completed_ignores_one_sided_transactions():
    analyzer = RiskAnalyzer()
    analyzer.record_completed(Transaction("deposit", 100, "RUB", recipient_id="R", created_at=NOW))
    assert analyzer.assess(context()).rules == ("new_recipient",)


def test_analyzer_counts_the_current_transaction_for_frequency():
    analyzer = RiskAnalyzer(rules=[HighFrequencyRule(max_count=2)])
    assert analyzer.assess(context(transfer())).level is RiskLevel.LOW
    assert analyzer.assess(context(transfer())).rules == ("high_frequency",)


def test_assessments_are_kept_per_attempt_as_a_copy():
    analyzer = RiskAnalyzer(rules=[])
    transaction = transfer()
    analyzer.assess(context(transaction))
    analyzer.assess(context(transaction, moment=NOW + timedelta(minutes=5)))
    analyzer.assessments.clear()
    assert [item.moment for item in analyzer.assessments] == [NOW, NOW + timedelta(minutes=5)]


def test_custom_rules_and_thresholds():
    analyzer = RiskAnalyzer(rules=[FixedRule(15)], medium_threshold=10, high_threshold=15)
    assert analyzer.assess(context()).level is RiskLevel.HIGH
    assert [type(rule) for rule in analyzer.rules] == [FixedRule]


def test_default_rules():
    names = [rule.name for rule in RiskAnalyzer().rules]
    assert names == ["large_amount", "high_frequency", "new_recipient", "night_operation"]


@pytest.mark.parametrize(
    "params",
    [
        {"rules": ["not a rule"]},
        {"medium_threshold": 70, "high_threshold": 70},
        {"medium_threshold": 0},
        {"high_threshold": "70"},
    ],
)
def test_analyzer_rejects_invalid_settings(params):
    with pytest.raises(InvalidOperationError):
        RiskAnalyzer(**params)


def test_assessment_str_lists_the_rules():
    assessment = RiskAnalyzer().assess(context(amount="600000"))
    assert str(assessment) == "medium score  60 | large_amount, new_recipient"


def test_frequency_counts_a_retry_first_seen_before_the_window():
    history = RiskHistory()
    retried = transfer()
    history.remember(retried.transaction_id, "C", NOW - timedelta(minutes=15))
    for number in range(4):
        history.remember(f"T{number}", "C", NOW - timedelta(minutes=1))
    assert HighFrequencyRule().evaluate(context(retried), history) is not None
