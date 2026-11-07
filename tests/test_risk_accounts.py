from __future__ import annotations

from server.risk_accounts import load_account_registry
from server.risk_types import AccountConfigStatus, RiskIssueCode


def configured_environment() -> dict[str, str]:
    return {
        "BINANCE_OPTIONS_ACCOUNT_IDS": "main,income",
        "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID": "main",
        "BINANCE_OPTIONS_ACCOUNT_MAIN_LABEL": "Main",
        "BINANCE_OPTIONS_ACCOUNT_MAIN_API_KEY": "synthetic-key",
        "BINANCE_OPTIONS_ACCOUNT_MAIN_SECRET_KEY": "synthetic-secret",
        "BINANCE_OPTIONS_ACCOUNT_INCOME_LABEL": "Income",
        "BINANCE_OPTIONS_ACCOUNT_INCOME_API_KEY": "",
        "BINANCE_OPTIONS_ACCOUNT_INCOME_SECRET_KEY": "",
    }


def test_account_registry_parses_configured_and_missing_accounts_without_repr_secrets():
    registry = load_account_registry(configured_environment())

    assert registry.default_account_id == "main"
    assert [account.account_id for account in registry.accounts] == ["main", "income"]
    assert [account.status for account in registry.accounts] == [
        AccountConfigStatus.CONFIGURED,
        AccountConfigStatus.MISSING_CREDENTIALS,
    ]
    assert registry.configured_accounts == (registry.accounts[0],)
    representation = repr(registry)
    assert "synthetic-key" not in representation
    assert "synthetic-secret" not in representation


def test_account_registry_rejects_invalid_duplicate_and_unknown_default_ids():
    values = configured_environment()
    values["BINANCE_OPTIONS_ACCOUNT_IDS"] = "main,main,Bad-ID"
    values["BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID"] = "missing"

    registry = load_account_registry(values)

    assert registry.default_account_id is None
    assert [account.account_id for account in registry.accounts] == ["main"]
    assert {issue.code for issue in registry.issues} == {
        RiskIssueCode.CONFIGURATION_ERROR
    }
    assert {dict(issue.context)["field"] for issue in registry.issues} == {
        "account_id",
        "default_account_id",
        "duplicate_account_id",
    }


def test_account_registry_marks_blank_label_as_configuration_error():
    values = configured_environment()
    values["BINANCE_OPTIONS_ACCOUNT_MAIN_LABEL"] = "  "

    registry = load_account_registry(values)

    main = registry.find("main")
    assert main is not None
    assert main.status is AccountConfigStatus.CONFIGURATION_ERROR
    assert main.label == "main"
    assert main.issue is not None
    assert main.issue.code is RiskIssueCode.CONFIGURATION_ERROR
