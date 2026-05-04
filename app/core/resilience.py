from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class ErrorKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TRANSIENT_SERVER = "transient_server"
    TRANSIENT_NETWORK = "transient_network"
    UNKNOWN = "unknown"
    NON_RETRYABLE = "non_retryable"


class OperationTimeoutError(Exception):
    """Raised when an operation exceeds timeout (wait-side)."""


class RetryExhaustedError(Exception):
    """Raised when all retry attempts fail."""


@dataclass
class RetryPolicy:
    timeout_seconds: int = 80
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.3


@dataclass
class AttemptRecord:
    attempt_index: int  # 0-based
    error_kind: ErrorKind
    error_message: str
    sleep_seconds: float | None = None


@dataclass
class RetryOutcome(Generic[T]):
    value: T
    attempts: int  # total tries including the successful one
    records: list[AttemptRecord] = field(default_factory=list)

    @property
    def retry_count(self) -> int:
        return max(0, self.attempts - 1)


def run_with_timeout(fn: Callable[[], T], timeout_seconds: int) -> T:
    """
    Wait-side timeout. Does not guarantee cancelling in-flight HTTP.
    Prefer also configuring HTTP client timeouts at the integration layer.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            raise OperationTimeoutError(f"Operation timed out after {timeout_seconds}s") from exc


def _extract_http_status_code(exc: BaseException) -> int | None:
    # Common patterns: "429", "HTTPStatusError: 503", "status code: 502"
    text = f"{type(exc).__name__}: {exc}"
    m = re.search(r"\b(\d{3})\b", text)
    if not m:
        return None
    code = int(m.group(1))
    if 100 <= code <= 599:
        return code
    return None


def classify_error(exc: BaseException) -> tuple[bool, ErrorKind, str]:
    """
    Returns: (retryable, kind, reason)
    """
    if isinstance(exc, OperationTimeoutError):
        return True, ErrorKind.TIMEOUT, str(exc)

    status = _extract_http_status_code(exc)
    if status == 429:
        return True, ErrorKind.RATE_LIMIT, f"HTTP {status}"
    if status in (408, 409):
        # 408 request timeout; 409 conflict sometimes used for concurrency - often retryable in APIs
        return True, ErrorKind.TRANSIENT_SERVER, f"HTTP {status}"
    if status in (500, 502, 503, 504):
        return True, ErrorKind.TRANSIENT_SERVER, f"HTTP {status}"

    # Typed exceptions (best-effort; imports optional)
    try:
        import httpx  # type: ignore

        if isinstance(exc, httpx.TimeoutException):
            return True, ErrorKind.TIMEOUT, "httpx timeout"
        if isinstance(exc, httpx.TransportError):
            return True, ErrorKind.TRANSIENT_NETWORK, "httpx transport error"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code if exc.response is not None else None
            if code == 429:
                return True, ErrorKind.RATE_LIMIT, f"httpx HTTPStatusError {code}"
            if code in (500, 502, 503, 504, 408, 409):
                return True, ErrorKind.TRANSIENT_SERVER, f"httpx HTTPStatusError {code}"
            return False, ErrorKind.NON_RETRYABLE, f"httpx HTTPStatusError {code}"
    except Exception:
        pass

    try:
        import requests  # type: ignore

        if isinstance(exc, requests.Timeout):
            return True, ErrorKind.TIMEOUT, "requests timeout"
        if isinstance(exc, requests.ConnectionError):
            return True, ErrorKind.TRANSIENT_NETWORK, "requests connection error"
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            code = exc.response.status_code
            if code == 429:
                return True, ErrorKind.RATE_LIMIT, f"requests HTTPError {code}"
            if code in (500, 502, 503, 504, 408, 409):
                return True, ErrorKind.TRANSIENT_SERVER, f"requests HTTPError {code}"
            return False, ErrorKind.NON_RETRYABLE, f"requests HTTPError {code}"
    except Exception:
        pass

    # Keyword fallback (SDK-specific errors)
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return True, ErrorKind.TIMEOUT, "keyword: timeout"
    if "rate limit" in msg or "too many requests" in msg or " 429" in msg:
        return True, ErrorKind.RATE_LIMIT, "keyword: rate limit"
    if " 503" in msg or " 502" in msg or " 504" in msg or "service unavailable" in msg:
        return True, ErrorKind.TRANSIENT_SERVER, "keyword: 5xx"
    if "connection reset" in msg or "connection aborted" in msg or "econnreset" in msg:
        return True, ErrorKind.TRANSIENT_NETWORK, "keyword: network reset"
    if "temporarily unavailable" in msg or "try again" in msg:
        return True, ErrorKind.TRANSIENT_SERVER, "keyword: transient"

    # Default: unknown -> conservative non-retryable (avoid retrying bad requests forever)
    return False, ErrorKind.UNKNOWN, f"unclassified: {type(exc).__name__}"


def call_with_retry(operation_name: str, fn: Callable[[], T], policy: RetryPolicy) -> RetryOutcome[T]:
    records: list[AttemptRecord] = []
    last_exc: BaseException | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            value = run_with_timeout(fn, policy.timeout_seconds)
            return RetryOutcome(value=value, attempts=attempt + 1, records=records)
        except BaseException as exc:
            last_exc = exc
            retryable, kind, reason = classify_error(exc)

            is_last_attempt = attempt >= policy.max_retries
            if is_last_attempt or not retryable:
                records.append(
                    AttemptRecord(
                        attempt_index=attempt,
                        error_kind=kind,
                        error_message=f"{operation_name}: {reason} | {exc}",
                    )
                )
                break

            exp_delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**attempt))
            jitter = random.uniform(0, policy.jitter_seconds)
            sleep_seconds = exp_delay + jitter

            records.append(
                AttemptRecord(
                    attempt_index=attempt,
                    error_kind=kind,
                    error_message=f"{operation_name}: {reason} | {exc}",
                    sleep_seconds=sleep_seconds,
                )
            )

            # lightweight observability hook (replace with logging later)
            print(
                f"[retry] {operation_name} attempt={attempt + 1}/{policy.max_retries + 1} "
                f"kind={kind.value} sleep={sleep_seconds:.2f}s err={type(exc).__name__}"
            )
            time.sleep(sleep_seconds)

    raise RetryExhaustedError(f"{operation_name} failed after {policy.max_retries + 1} attempts") from last_exc


@dataclass
class PipelineMetrics:
    steps: dict[str, RetryOutcome[object]] = field(default_factory=dict)

    def add(self, step: str, outcome: RetryOutcome[T]) -> RetryOutcome[T]:
        # store as object-typed map for simplicity
        self.steps[step] = outcome  # type: ignore[assignment]
        return outcome

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        total_retries = 0
        for step, out in self.steps.items():
            total_retries += out.retry_count
            lines.append(
                f"- {step}: attempts={out.attempts}, retries={out.retry_count}"
            )
        lines.insert(0, f"Total retries across steps: {total_retries}")
        return lines