#!/usr/bin/env python3
"""
Email domain + SMTP handshake checker.

Install dependency:
  pip install dnspython

Run:
  python email_check.py user1@example.com user2@example.com

Optional flags:
  --dns-timeout 3
  --smtp-timeout 8
"""

from __future__ import annotations

import argparse
import re
import smtplib
import socket
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import dns.exception
    import dns.resolver
except ImportError:
    print(
        "Missing dependency: dnspython. Install with: pip install dnspython",
        file=sys.stderr,
    )
    sys.exit(2)


DOMAIN_VALID = "домен валиден"
DOMAIN_ABSENT = "домен отсутствует"
MX_INVALID = "МХ-записи отсутствуют или некорректны"

EMAIL_RE = re.compile(r"^[^@\s]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$")
POLICY_KEYWORDS = (
    "policy",
    "spam",
    "relay",
    "blocked",
    "denied",
    "blacklist",
    "authentication",
    "not permitted",
    "access denied",
)


@dataclass
class DomainCheckResult:
    status: str
    mx_hosts: List[str]
    detail: str


@dataclass
class SmtpCheckResult:
    result: str
    code: str
    detail: str


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check email domains via MX and probe recipient with SMTP handshake."
    )
    parser.add_argument("emails", nargs="+", help="Email addresses to verify")
    parser.add_argument(
        "--dns-timeout",
        type=float,
        default=3.0,
        help="DNS timeout in seconds (default: 3)",
    )
    parser.add_argument(
        "--smtp-timeout",
        type=float,
        default=8.0,
        help="SMTP timeout in seconds (default: 8)",
    )
    return parser.parse_args(argv)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def extract_domain(email: str) -> Optional[str]:
    if "@" not in email:
        return None
    _, domain = email.rsplit("@", 1)
    domain = domain.strip().lower()
    if not domain:
        return None
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def build_resolver(timeout: float) -> dns.resolver.Resolver:
    try:
        resolver = dns.resolver.Resolver()
    except dns.resolver.NoResolverConfiguration:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    resolver.timeout = timeout
    resolver.lifetime = timeout
    return resolver


def check_domain_mx(domain: str, resolver: dns.resolver.Resolver) -> DomainCheckResult:
    try:
        answers = resolver.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        return DomainCheckResult(DOMAIN_ABSENT, [], "NXDOMAIN")
    except dns.resolver.NoAnswer:
        return DomainCheckResult(MX_INVALID, [], "no_mx_answer")
    except dns.resolver.NoNameservers:
        return DomainCheckResult(MX_INVALID, [], "no_nameservers")
    except dns.resolver.YXDOMAIN:
        return DomainCheckResult(MX_INVALID, [], "yx_domain")
    except dns.exception.Timeout:
        return DomainCheckResult(MX_INVALID, [], "dns_timeout")
    except dns.resolver.LifetimeTimeout:
        return DomainCheckResult(MX_INVALID, [], "dns_lifetime_timeout")
    except dns.resolver.NoResolverConfiguration:
        return DomainCheckResult(MX_INVALID, [], "resolver_not_configured")
    except Exception as exc:  # defensive fallback for DNS layer
        return DomainCheckResult(MX_INVALID, [], f"dns_error:{type(exc).__name__}")

    parsed: List[Tuple[int, str]] = []
    for record in answers:
        try:
            host = str(record.exchange).rstrip(".")
            pref = int(record.preference)
        except Exception:
            continue

        # Null MX (".") means "do not accept email for this domain"
        if not host:
            continue
        parsed.append((pref, host))

    if not parsed:
        return DomainCheckResult(MX_INVALID, [], "mx_records_invalid_or_empty")

    parsed.sort(key=lambda item: item[0])
    mx_hosts = [host for _, host in parsed]
    return DomainCheckResult(DOMAIN_VALID, mx_hosts, "mx_ok")


def classify_smtp(code: int, message: str) -> str:
    lowered = message.lower()
    if code in (250, 251):
        return "exists_likely"
    if code in (550, 551, 553):
        if any(keyword in lowered for keyword in POLICY_KEYWORDS):
            return "server_blocked"
        return "not_exists"
    if code in (421, 450, 451, 452):
        return "temp_fail"
    if code in (530, 535, 554):
        return "server_blocked"
    if any(keyword in lowered for keyword in POLICY_KEYWORDS):
        return "server_blocked"
    return "server_blocked"


def smtp_probe(email: str, mx_host: str, timeout: float) -> SmtpCheckResult:
    try:
        with smtplib.SMTP(host=mx_host, port=25, timeout=timeout) as smtp:
            code, message = smtp.ehlo()
            if code >= 400:
                code, message = smtp.helo()
                if code >= 400:
                    decoded = _decode_smtp_message(message)
                    return SmtpCheckResult(
                        "server_blocked",
                        str(code),
                        f"helo_rejected:{decoded}",
                    )

            mail_code, mail_msg = smtp.mail("check@local.test")
            if mail_code >= 400:
                decoded = _decode_smtp_message(mail_msg)
                return SmtpCheckResult(
                    classify_smtp(mail_code, decoded),
                    str(mail_code),
                    f"mail_from_rejected:{decoded}",
                )

            rcpt_code, rcpt_msg = smtp.rcpt(email)
            decoded = _decode_smtp_message(rcpt_msg)
            return SmtpCheckResult(
                classify_smtp(rcpt_code, decoded),
                str(rcpt_code),
                decoded or "rcpt_response_empty",
            )
    except smtplib.SMTPRecipientsRefused as exc:
        refused = exc.recipients.get(email)
        if refused:
            code, msg = refused
            decoded = _decode_smtp_message(msg)
            return SmtpCheckResult(classify_smtp(code, decoded), str(code), decoded)
        return SmtpCheckResult("server_blocked", "-", "recipients_refused")
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as exc:
        return SmtpCheckResult("connection_error", "-", type(exc).__name__)
    except (TimeoutError, socket.timeout, ConnectionRefusedError, OSError) as exc:
        return SmtpCheckResult("connection_error", "-", type(exc).__name__)
    except smtplib.SMTPResponseException as exc:
        decoded = _decode_smtp_message(exc.smtp_error)
        return SmtpCheckResult(
            classify_smtp(exc.smtp_code, decoded), str(exc.smtp_code), decoded
        )
    except UnicodeEncodeError:
        return SmtpCheckResult("server_blocked", "-", "email_encoding_error")
    except Exception as exc:  # defensive fallback for SMTP layer
        return SmtpCheckResult("server_blocked", "-", f"smtp_error:{type(exc).__name__}")


def _decode_smtp_message(message: object) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace").strip()
    return str(message).strip()


def format_table(rows: List[Dict[str, str]], columns: List[str]) -> str:
    widths = {
        col: max(len(col), max((len(row.get(col, "")) for row in rows), default=0))
        for col in columns
    }
    sep = " | "
    header = sep.join(col.ljust(widths[col]) for col in columns)
    divider = "-+-".join("-" * widths[col] for col in columns)
    lines = [header, divider]
    for row in rows:
        lines.append(sep.join(row.get(col, "").ljust(widths[col]) for col in columns))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    resolver = build_resolver(args.dns_timeout)
    domain_cache: Dict[str, DomainCheckResult] = {}

    rows: List[Dict[str, str]] = []
    for email in args.emails:
        normalized_email = email.strip()

        if not is_valid_email(normalized_email):
            rows.append(
                {
                    "email": normalized_email,
                    "domain_status": MX_INVALID,
                    "smtp_result": "skipped",
                    "smtp_code": "-",
                    "details": "invalid_email_format",
                }
            )
            continue

        domain = extract_domain(normalized_email)
        if not domain:
            rows.append(
                {
                    "email": normalized_email,
                    "domain_status": MX_INVALID,
                    "smtp_result": "skipped",
                    "smtp_code": "-",
                    "details": "invalid_domain",
                }
            )
            continue

        if domain not in domain_cache:
            domain_cache[domain] = check_domain_mx(domain, resolver)
        domain_result = domain_cache[domain]

        row = {
            "email": normalized_email,
            "domain_status": domain_result.status,
            "smtp_result": "skipped",
            "smtp_code": "-",
            "details": domain_result.detail,
        }

        if domain_result.status == DOMAIN_VALID:
            primary_mx = domain_result.mx_hosts[0]
            smtp_result = smtp_probe(normalized_email, primary_mx, args.smtp_timeout)
            row["smtp_result"] = smtp_result.result
            row["smtp_code"] = smtp_result.code
            row["details"] = f"mx:{primary_mx}; {smtp_result.detail}"

        rows.append(row)

    columns = ["email", "domain_status", "smtp_result", "smtp_code", "details"]
    print(format_table(rows, columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
