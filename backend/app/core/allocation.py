from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.core.money import Money, decimal_from_string, decimal_to_string


@dataclass(frozen=True)
class Allocation:
    shares: tuple[tuple[str, Decimal], ...]

    @classmethod
    def from_shares(cls, shares: Mapping[str, str]) -> Allocation:
        if not shares:
            raise ValueError("allocation must include at least one participant")
        parsed: list[tuple[str, Decimal]] = []
        for participant_id, share in sorted(shares.items()):
            if not participant_id:
                raise ValueError("participant id cannot be empty")
            value = decimal_from_string(share, label="allocation share")
            if value < 0:
                raise ValueError("allocation shares cannot be negative")
            parsed.append((participant_id, value))
        if sum((share for _, share in parsed), start=Decimal(0)) != Decimal(1):
            raise ValueError("allocation shares must total exactly one")
        return cls(shares=tuple(parsed))

    @classmethod
    def from_weights(cls, weights: Mapping[str, str]) -> Allocation:
        if not weights:
            raise ValueError("allocation must include at least one participant")
        parsed_weights: list[tuple[str, Decimal]] = []
        for participant_id, raw_weight in sorted(weights.items()):
            if not participant_id:
                raise ValueError("participant id cannot be empty")
            value = decimal_from_string(raw_weight, label="allocation weight")
            if value <= 0:
                raise ValueError("allocation weights must be positive")
            parsed_weights.append((participant_id, value))

        total = sum((weight for _, weight in parsed_weights), start=Decimal(0))
        shares: list[tuple[str, Decimal]] = []
        assigned = Decimal(0)
        for index, (participant_id, parsed_weight) in enumerate(parsed_weights):
            if index == len(parsed_weights) - 1:
                share = Decimal(1) - assigned
            else:
                share = parsed_weight / total
                assigned += share
            shares.append((participant_id, share))
        return cls(shares=tuple(shares))

    def as_dict(self) -> dict[str, Decimal]:
        return dict(self.shares)

    @property
    def participant_ids(self) -> tuple[str, ...]:
        return tuple(participant_id for participant_id, _ in self.shares)


def person_allocation(participant_id: str) -> Allocation:
    return Allocation.from_shares({participant_id: "1"})


def equal_allocation(participant_ids: Sequence[str]) -> Allocation:
    if len(set(participant_ids)) != len(participant_ids):
        raise ValueError("participant ids must be unique")
    return Allocation.from_weights(
        {participant_id: "1" for participant_id in participant_ids}
    )


def allocate_decimal(amount: Decimal, allocation: Allocation) -> dict[str, Decimal]:
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be Decimal")
    result: dict[str, Decimal] = {}
    allocated = Decimal(0)
    for index, (participant_id, share) in enumerate(allocation.shares):
        if index == len(allocation.shares) - 1:
            participant_amount = amount - allocated
        else:
            participant_amount = amount * share
            allocated += participant_amount
        result[participant_id] = participant_amount
    return result


def allocate_amount(amount: Money, allocation: Allocation) -> dict[str, Money]:
    return {
        participant_id: Money(
            currency=amount.currency,
            amount=decimal_to_string(participant_amount),
        )
        for participant_id, participant_amount in allocate_decimal(
            amount.decimal, allocation
        ).items()
    }
