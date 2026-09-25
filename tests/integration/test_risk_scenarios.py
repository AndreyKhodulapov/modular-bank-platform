"""Ordinary and suspicious transactions go through risk control; the audit log and the reports tell the story."""

from datetime import datetime
from decimal import Decimal

import pytest

from models import Transaction, TransactionStatus
from services import (
    AuditLevel,
    AuditLog,
    AuditReport,
    Bank,
    RiskLevel,
    SecurityGuard,
    TransactionProcessor,
    TransactionQueue,
)
from tests.helpers import history_gaps
from utils import ManualClock

NOON = datetime(2026, 9, 24, 12, 0)


@pytest.fixture
def world(tmp_path, make_client):
    clock = ManualClock(datetime(2026, 9, 10, 10, 0))
    audit_log = AuditLog(tmp_path / "audit.jsonl")
    bank = Bank(security=SecurityGuard(clock=clock, audit_log=audit_log))
    queue = TransactionQueue(clock=bank.now)
    processor = TransactionProcessor(bank)
    maria = bank.add_client(make_client("Maria"), "maria-password")
    oleg = bank.add_client(make_client("Oleg"), "oleg-password")
    alina = bank.add_client(make_client("Alina"), "alina-password")
    # opened two weeks before the scenario, so these accounts are not new
    accounts = {
        "maria": bank.open_account(maria.client_id, currency="RUB", initial_balance=300_000),
        "oleg": bank.open_account(
            oleg.client_id, "premium", currency="USD", initial_balance=50_000, overdraft_limit=5_000
        ),
        "alina_kzt": bank.open_account(alina.client_id, currency="KZT", initial_balance=2_000_000),
        "alina_rub": bank.open_account(alina.client_id, currency="RUB", initial_balance=10_000),
    }
    clock.moment = NOON
    ivan = bank.add_client(make_client("Ivan"), "ivan-password")
    accounts["fresh"] = bank.open_account(ivan.client_id, currency="RUB")  # opened today
    clients = {"maria": maria, "oleg": oleg, "alina": alina}
    return clock, bank, queue, processor, clients, accounts


def run(queue, processor, clock, moment, *transactions):
    clock.moment = moment
    for transaction in transactions:
        queue.add(transaction)
    return processor.process_queue(queue)


def make(kind, amount, currency, sender=None, recipient=None):
    return Transaction(
        kind,
        amount,
        currency,
        sender_id=sender.account_id if sender is not None else None,
        recipient_id=recipient if isinstance(recipient, str) or recipient is None else recipient.account_id,
        created_at=NOON,
    )


def test_ordinary_and_suspicious_transactions(world, tmp_path):
    clock, bank, queue, processor, clients, accounts = world
    maria, oleg = accounts["maria"], accounts["oleg"]
    alina_kzt, alina_rub, fresh = accounts["alina_kzt"], accounts["alina_rub"], accounts["fresh"]

    # ordinary: known or long-standing accounts, moderate amounts, daytime
    ordinary = [
        make("deposit", 90_000, "RUB", recipient=maria),
        make("transfer", 5_000, "RUB", maria, alina_rub),
        make("withdrawal", 20_000, "KZT", alina_kzt),
        make("external_transfer", 300, "USD", oleg, "DE89-3704-0044-0532-0130-00"),
    ]
    report = run(queue, processor, clock, NOON, *ordinary)
    assert report.completed == ordinary
    repeat = make("transfer", 3_000, "RUB", maria, alina_rub)
    run(queue, processor, clock, NOON.replace(minute=5), repeat)
    assert repeat.status is TransactionStatus.COMPLETED

    # suspicious: a large amount to a brand-new account, then a very large one abroad
    large = make("transfer", 6_000, "USD", oleg, fresh)  # 540 000 RUB: large 40 + new account 20
    huge = make("external_transfer", 25_000, "USD", oleg, "CY17-0020-0128-0000-0012-0052-7600")  # 70 + 20
    run(queue, processor, clock, NOON.replace(hour=13), large, huge)
    assert (large.status, huge.status) == (TransactionStatus.COMPLETED, TransactionStatus.FAILED)

    # six quick transfers to the new account: the fifth and the sixth add the frequency factor
    rapid = [make("transfer", 1_000, "KZT", alina_kzt, fresh) for _ in range(6)]
    run(queue, processor, clock, NOON.replace(hour=14), *rapid)
    assert all(transaction.status is TransactionStatus.COMPLETED for transaction in rapid)

    # late evening: a small transfer to a known recipient passes, a large one to the new account is refused
    evening = make("transfer", 1_000, "RUB", maria, alina_rub)
    late_large = make("transfer", 7_000, "USD", oleg, fresh)  # large 40 + new 20 + night 20
    run(queue, processor, clock, NOON.replace(hour=23, minute=30), evening, late_large)
    assert (evening.status, late_large.status) == (TransactionStatus.COMPLETED, TransactionStatus.FAILED)

    # the hard night ban comes before risk scoring: refused, retried later, not assessed
    night = make("transfer", 1_000, "RUB", maria, alina_rub)
    run(queue, processor, clock, datetime(2026, 9, 25, 2, 0), night)
    assert night.status is TransactionStatus.PENDING

    levels = {item.transaction_id: item.level for item in bank.risk_analyzer.assessments}
    assert all(levels[transaction.transaction_id] is RiskLevel.LOW for transaction in [*ordinary, repeat, evening])
    assert [levels[transaction.transaction_id] for transaction in rapid] == [RiskLevel.LOW] * 4 + [RiskLevel.MEDIUM] * 2
    assert (levels[large.transaction_id], levels[huge.transaction_id], levels[late_large.transaction_id]) == (
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
        RiskLevel.HIGH,
    )
    assert night.transaction_id not in levels

    # blocked transactions moved no money
    assert oleg.balance == Decimal("43697.00")  # 50 000 - 303 (300 plus the 1% fee) - 6 000
    assert fresh.balance == Decimal("541080.00")  # 540 000 + 6 x 180
    assert history_gaps(bank) == {}
    assert len(bank.history.transactions(status="failed")) == 2  # the night transfer is still waiting

    audit = AuditReport(bank.audit_log, bank.risk_analyzer)
    suspicious = audit.suspicious_operations()
    assert [item.transaction_id for item in suspicious.operations] == [
        large.transaction_id,
        huge.transaction_id,
        *(transaction.transaction_id for transaction in rapid[4:]),
        late_large.transaction_id,
    ]
    # opening Oleg's account (4.5 million), then the debit and the credit of the medium-risk transfer
    assert [event.event for event in suspicious.security_events] == ["large_operation"] * 3 + ["night_operation"]

    oleg_profile = audit.client_risk_profile(clients["oleg"].client_id)
    assert (oleg_profile.transactions, oleg_profile.blocked, oleg_profile.level) == (4, 2, RiskLevel.HIGH)
    assert oleg_profile.top_factors[0] == ("new_recipient", 4)
    assert audit.client_risk_profile(clients["alina"].client_id).level is RiskLevel.MEDIUM
    assert audit.client_risk_profile(clients["maria"].client_id).level is RiskLevel.LOW

    stats = audit.error_statistics()
    assert stats.errors_by_type == {"RiskBlockedError": 2, "OperationTimeRestrictedError": 1}
    assert (stats.completed, stats.final_failures, stats.retried, stats.blocked_by_risk) == (13, 2, 1, 2)
    assert stats.events_by_level[AuditLevel.CRITICAL] == 2

    # the file holds exactly what memory holds
    assert AuditLog.load_events(tmp_path / "audit.jsonl") == bank.audit_log.events
