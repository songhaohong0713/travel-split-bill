from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext

getcontext().prec = 28

CURRENCY_SCALES: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "CNY": 2,
    "USD": 2,
    "EUR": 2,
}

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DECIMAL_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")


def decimal_from_string(value: str, *, label: str = "decimal") -> Decimal:
    if not isinstance(value, str) or _DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a plain decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be a plain decimal string") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def decimal_to_string(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("value must be Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


@dataclass(frozen=True)
class Money:
    currency: str
    amount: str
    _decimal: Decimal = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")
        object.__setattr__(
            self,
            "_decimal",
            decimal_from_string(self.amount, label="money amount"),
        )

    @property
    def decimal(self) -> Decimal:
        return self._decimal

    @classmethod
    def from_decimal(cls, currency: str, amount: Decimal) -> Money:
        return cls(currency=currency, amount=decimal_to_string(amount))


def currency_scale(currency: str) -> int:
    try:
        return CURRENCY_SCALES[currency]
    except KeyError as error:
        raise ValueError(f"unsupported currency: {currency}") from error


def quantize_decimal(amount: Decimal, currency: str) -> Decimal:
    scale = currency_scale(currency)
    quantum = Decimal(1).scaleb(-scale)
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)


def quantize(amount: Money) -> Money:
    value = quantize_decimal(amount.decimal, amount.currency)
    scale = currency_scale(amount.currency)
    return Money(currency=amount.currency, amount=format(value, f".{scale}f"))


def quantized_money(currency: str, amount: Decimal) -> Money:
    return quantize(Money.from_decimal(currency, amount))
