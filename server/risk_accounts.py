from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

try:
    from .risk_types import (
        AccountConfigStatus,
        CalculationIssue,
        RiskIssueCode,
    )
except ImportError:
    from risk_types import AccountConfigStatus, CalculationIssue, RiskIssueCode


ACCOUNT_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class RiskAccountConfig:
    account_id: str
    label: str
    status: AccountConfigStatus
    api_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    issue: CalculationIssue | None = None


@dataclass(frozen=True, slots=True)
class RiskAccountRegistry:
    default_account_id: str | None
    accounts: tuple[RiskAccountConfig, ...]
    issues: tuple[CalculationIssue, ...] = ()

    def find(self, account_id: str) -> RiskAccountConfig | None:
        return next(
            (account for account in self.accounts if account.account_id == account_id),
            None,
        )

    @property
    def configured_accounts(self) -> tuple[RiskAccountConfig, ...]:
        return tuple(
            account
            for account in self.accounts
            if account.status is AccountConfigStatus.CONFIGURED
        )


def _issue(code: RiskIssueCode, **context: str) -> CalculationIssue:
    return CalculationIssue(code=code, context=tuple(sorted(context.items())))


def _unique_issues(
    issues: list[CalculationIssue],
) -> tuple[CalculationIssue, ...]:
    return tuple(
        sorted(
            set(issues),
            key=lambda issue: (issue.code.value, repr(issue.context)),
        )
    )


def load_account_registry(
    environ: Mapping[str, str] | None = None,
) -> RiskAccountRegistry:
    values = os.environ if environ is None else environ
    issues: list[CalculationIssue] = []
    raw_ids = values.get("BINANCE_OPTIONS_ACCOUNT_IDS", "")
    requested_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]

    if not requested_ids:
        issues.append(
            _issue(RiskIssueCode.CONFIGURATION_ERROR, field="account_ids")
        )

    valid_ids: list[str] = []
    seen: set[str] = set()
    for account_id in requested_ids:
        if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
            issues.append(
                _issue(
                    RiskIssueCode.CONFIGURATION_ERROR,
                    field="account_id",
                    accountId=account_id,
                )
            )
            continue
        if account_id in seen:
            issues.append(
                _issue(
                    RiskIssueCode.CONFIGURATION_ERROR,
                    field="duplicate_account_id",
                    accountId=account_id,
                )
            )
            continue
        seen.add(account_id)
        valid_ids.append(account_id)

    default_account_id = values.get(
        "BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID", ""
    ).strip()
    if default_account_id not in valid_ids:
        issues.append(
            _issue(RiskIssueCode.CONFIGURATION_ERROR, field="default_account_id")
        )
        default_account_id = ""

    accounts: list[RiskAccountConfig] = []
    for account_id in valid_ids:
        suffix = account_id.upper()
        prefix = f"BINANCE_OPTIONS_ACCOUNT_{suffix}_"
        label = values.get(f"{prefix}LABEL", account_id).strip()
        api_key = values.get(f"{prefix}API_KEY", "").strip()
        secret_key = values.get(f"{prefix}SECRET_KEY", "").strip()

        issue: CalculationIssue | None = None
        if not label:
            status = AccountConfigStatus.CONFIGURATION_ERROR
            issue = _issue(
                RiskIssueCode.CONFIGURATION_ERROR,
                field="label",
                accountId=account_id,
            )
            label = account_id
        elif not api_key or not secret_key:
            status = AccountConfigStatus.MISSING_CREDENTIALS
            issue = _issue(
                RiskIssueCode.MISSING_CREDENTIALS,
                accountId=account_id,
            )
        else:
            status = AccountConfigStatus.CONFIGURED

        accounts.append(
            RiskAccountConfig(
                account_id=account_id,
                label=label,
                status=status,
                api_key=api_key,
                secret_key=secret_key,
                issue=issue,
            )
        )

    return RiskAccountRegistry(
        default_account_id=default_account_id or None,
        accounts=tuple(accounts),
        issues=_unique_issues(issues),
    )
