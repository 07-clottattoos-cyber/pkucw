from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
from getpass import getpass

from .auth import Credentials
from .models import AccountRecord
from .state import load_accounts, save_accounts, utc_now_iso


KEYCHAIN_SERVICE = "pkucw"


class AccountError(RuntimeError):
    """Raised when local account management fails."""


class AccountSecretMissingError(AccountError):
    """已保存账号在 Keychain 中没有找到密码。"""


def list_accounts() -> list[AccountRecord]:
    return sorted(
        load_accounts(),
        key=lambda item: (
            0 if item.is_default else 1,
            item.label or "",
            item.username.lower(),
        ),
    )


def get_default_account() -> AccountRecord | None:
    accounts = list_accounts()
    for account in accounts:
        if account.is_default:
            return account
    if len(accounts) == 1:
        return accounts[0]
    return None


def resolve_account(query: str) -> AccountRecord:
    normalized = query.strip().lower()
    if not normalized:
        raise AccountError("账号查询条件不能为空。")

    accounts = list_accounts()
    exact_matches = [
        account
        for account in accounts
        if account.username.lower() == normalized or (account.label or "").strip().lower() == normalized
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    partial_matches = [
        account
        for account in accounts
        if normalized in account.username.lower() or normalized in (account.label or "").strip().lower()
    ]
    if len(partial_matches) == 1:
        return partial_matches[0]
    if not partial_matches and not exact_matches:
        raise AccountError(f"无法定位账号：{query}")

    candidates = ", ".join(_account_title(account) for account in exact_matches or partial_matches)
    raise AccountError(f"账号匹配不唯一：{query}。可选项：{candidates}")


def prompt_for_credentials(*, username: str | None = None, password_stdin: bool = False) -> Credentials:
    resolved_username = (username or "").strip()
    if not resolved_username:
        resolved_username = input("请输入北大账号：").strip()
    if not resolved_username:
        raise AccountError("必须提供北大账号。")

    if password_stdin:
        password = sys.stdin.read().rstrip("\r\n")
        if not password:
            raise AccountError("已指定从 stdin 读取密码，但 stdin 为空。")
        source = "标准输入"
    else:
        password = getpass("请输入北大密码：").strip()
        if not password:
            raise AccountError("必须提供北大密码。")
        source = "终端交互输入"

    return Credentials(
        username=resolved_username,
        password=password,
        source=source,
    )


def credentials_for_account(account: AccountRecord) -> Credentials:
    return Credentials(
        username=account.username,
        password=_load_password(account.username),
        source=f"已保存账号 {account.username}",
    )


def has_saved_password(account: AccountRecord) -> bool:
    try:
        _load_password(account.username)
        return True
    except AccountSecretMissingError:
        return False


def upsert_account(
    *,
    username: str,
    password: str,
    label: str | None = None,
    make_default: bool = True,
    mark_login: bool = False,
) -> AccountRecord:
    now = utc_now_iso()
    accounts = load_accounts()
    existing = next((account for account in accounts if account.username == username), None)
    normalized_label = (label or "").strip() or None

    if existing is None:
        record = AccountRecord(
            username=username,
            label=normalized_label,
            is_default=make_default or not accounts,
            created_at=now,
            updated_at=now,
            last_used_at=now,
            last_login_at=now if mark_login else None,
        )
        accounts.append(record)
    else:
        record = replace(
            existing,
            label=normalized_label if normalized_label is not None else existing.label,
            is_default=existing.is_default or make_default,
            updated_at=now,
            last_used_at=now,
            last_login_at=now if mark_login else existing.last_login_at,
        )
        accounts = [record if account.username == username else account for account in accounts]

    if record.is_default:
        accounts = [
            replace(account, is_default=(account.username == username))
            for account in accounts
        ]
        record = next(account for account in accounts if account.username == username)

    _save_password(username, password)
    save_accounts(accounts)
    return record


def set_default_account(query: str) -> AccountRecord:
    target = resolve_account(query)
    now = utc_now_iso()
    accounts = [
        replace(
            account,
            is_default=(account.username == target.username),
            updated_at=now if account.username == target.username else account.updated_at,
            last_used_at=now if account.username == target.username else account.last_used_at,
        )
        for account in load_accounts()
    ]
    save_accounts(accounts)
    return next(account for account in accounts if account.username == target.username)


def remove_account(query: str) -> AccountRecord:
    target = resolve_account(query)
    accounts = [account for account in load_accounts() if account.username != target.username]
    if accounts and not any(account.is_default for account in accounts):
        accounts[0] = replace(accounts[0], is_default=True, updated_at=utc_now_iso())
    save_accounts(accounts)
    _delete_password(target.username)
    return target


def _account_title(account: AccountRecord) -> str:
    if account.label:
        return f"{account.label} <{account.username}>"
    return account.username


def _save_password(username: str, password: str) -> None:
    _require_keychain()
    command = [
        "security",
        "add-generic-password",
        "-U",
        "-a",
        username,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
        password,
    ]
    _run_security(command)


def _load_password(username: str) -> str:
    _require_keychain()
    command = [
        "security",
        "find-generic-password",
        "-a",
        username,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return result.stdout.rstrip("\r\n")
    stderr = (result.stderr or "").strip()
    if "could not be found" in stderr.lower():
        raise AccountSecretMissingError(
            f"在 Keychain 中没有找到账号 {username} 的已保存密码。请重新添加账号，或运行 `pkucw login --username {username}`。"
        )
    raise AccountError(f"读取账号 {username} 的已保存密码失败：{stderr or '未知错误'}")


def _delete_password(username: str) -> None:
    _require_keychain()
    command = [
        "security",
        "delete-generic-password",
        "-a",
        username,
        "-s",
        KEYCHAIN_SERVICE,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip().lower()
    if "could not be found" in stderr:
        return
    raise AccountError(f"删除账号 {username} 的已保存密码失败：{result.stderr.strip() or '未知错误'}")


def _run_security(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return
    raise AccountError(result.stderr.strip() or "macOS Keychain 命令执行失败。")


def _require_keychain() -> None:
    if sys.platform != "darwin":
        raise AccountError("当前已保存账号功能依赖 macOS Keychain。")
    if shutil.which("security") is None:
        raise AccountError("未找到 macOS `security` 命令，暂时无法使用账号保存功能。")
