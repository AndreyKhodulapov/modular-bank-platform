"""Services that coordinate the domain models: the bank, security, currency, transactions, history, audit, risk
and reports."""

import logging

from services.audit_log import (
    AccountEvent,
    AuditCategory,
    AuditEvent,
    AuditLevel,
    AuditLog,
    ClientEvent,
    RiskEvent,
    TransactionEvent,
)
from services.audit_report import AuditReport, ClientRiskProfile, ErrorStatistics, SuspiciousOperationsReport
from services.bank import Bank
from services.bank_report import BalanceSummary, BankReport, ClientRanking, TransactionStatistics
from services.client_portal import ClientPortal
from services.currency import CurrencyConverter
from services.fees import FeePolicy
from services.risk import (
    HighFrequencyRule,
    LargeAmountRule,
    NewRecipientRule,
    NightOperationRule,
    RiskAnalyzer,
    RiskAssessment,
    RiskContext,
    RiskFactor,
    RiskHistory,
    RiskLevel,
    RiskRule,
)
from services.security import SecurityGuard, SuspicionReason, SuspiciousActivity
from services.session import ClientSession, SessionStore
from services.transaction_history import BalanceMovement, MovementKind, TransactionHistory
from services.transaction_processor import ProcessingReport, TransactionErrorRecord, TransactionProcessor
from services.transaction_queue import TransactionQueue

# a library does not decide where its logs go: without the program's configuration they are dropped quietly
logging.getLogger("bank").addHandler(logging.NullHandler())

__all__ = [
    "AccountEvent",
    "AuditCategory",
    "AuditEvent",
    "AuditLevel",
    "AuditLog",
    "AuditReport",
    "BalanceMovement",
    "BalanceSummary",
    "Bank",
    "BankReport",
    "ClientEvent",
    "ClientPortal",
    "ClientRanking",
    "ClientRiskProfile",
    "ClientSession",
    "CurrencyConverter",
    "ErrorStatistics",
    "FeePolicy",
    "HighFrequencyRule",
    "LargeAmountRule",
    "MovementKind",
    "NewRecipientRule",
    "NightOperationRule",
    "ProcessingReport",
    "RiskAnalyzer",
    "RiskAssessment",
    "RiskContext",
    "RiskEvent",
    "RiskFactor",
    "RiskHistory",
    "RiskLevel",
    "RiskRule",
    "SecurityGuard",
    "SessionStore",
    "SuspicionReason",
    "SuspiciousActivity",
    "SuspiciousOperationsReport",
    "TransactionErrorRecord",
    "TransactionEvent",
    "TransactionHistory",
    "TransactionProcessor",
    "TransactionQueue",
    "TransactionStatistics",
]
