"""Deterministic EUR cost calculation and auditable persistence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.models import ModelProfileRecord, PricingInput, SQLiteModelProfileRepository
from evaluator.time import to_utc_iso, utc_now

_MILLION = Decimal(1_000_000)
_MINIMUM_DECIMAL_PLACES = 8


class CostStatus(StrEnum):
    CALCULATED = "calculated"
    UNKNOWN = "unknown"


class TokenUsage(BaseModel):
    """Provider usage. Missing subset details remain unknown, never inferred as zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class CostCalculationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage: TokenUsage
    chargeable: bool


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    reasoning_per_million: Decimal | None = Field(default=None, ge=0)
    fixed_per_request: Decimal | None = Field(default=None, ge=0)
    context_threshold: int | None = Field(default=None, gt=0)
    high_context_input_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_output_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_reasoning_per_million: Decimal | None = Field(default=None, ge=0)

    @classmethod
    def from_pricing(cls, pricing: PricingInput) -> PricingSnapshot:
        return cls.model_validate(pricing.model_dump())

    def as_pricing(self) -> PricingInput:
        return PricingInput.model_validate(self.model_dump())


class CostCalculation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    model_profile_id: UUID
    status: CostStatus
    reason: str | None = None
    usage: TokenUsage
    pricing: PricingSnapshot
    selected_tier: str | None = None
    regular_input_tokens: int | None = None
    regular_output_tokens: int | None = None
    input_cost: Decimal | None = None
    cached_input_cost: Decimal | None = None
    output_cost: Decimal | None = None
    reasoning_cost: Decimal | None = None
    fixed_cost: Decimal = Decimal("0")
    total_cost: Decimal | None = None
    chargeable: bool
    created_at: datetime


def calculate_cost(
    *,
    model_profile_id: UUID,
    usage: TokenUsage,
    pricing: PricingSnapshot,
    chargeable: bool,
    calculation_id: UUID | None = None,
    created_at: datetime | None = None,
) -> CostCalculation:
    """Calculate exact EUR costs. Unknown or inconsistent usage never gets estimated."""

    fixed = (pricing.fixed_per_request or Decimal("0")) if chargeable else Decimal("0")
    base = {
        "id": calculation_id or uuid4(),
        "model_profile_id": model_profile_id,
        "usage": usage,
        "pricing": pricing,
        "fixed_cost": fixed,
        "chargeable": chargeable,
        "created_at": created_at or utc_now(),
    }
    values = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cached_input_tokens,
        usage.reasoning_tokens,
    )
    if any(value is None for value in values):
        return CostCalculation(
            status=CostStatus.UNKNOWN,
            reason="required_usage_missing",
            **base,
        )

    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cached_tokens = usage.cached_input_tokens
    reasoning_tokens = usage.reasoning_tokens
    assert input_tokens is not None
    assert output_tokens is not None
    assert cached_tokens is not None
    assert reasoning_tokens is not None
    if cached_tokens > input_tokens or reasoning_tokens > output_tokens:
        return CostCalculation(
            status=CostStatus.UNKNOWN,
            reason="usage_subset_exceeds_total",
            **base,
        )

    prices = pricing.as_pricing()
    input_rate, output_rate, cached_rate, reasoning_rate = prices.effective_rates(input_tokens)
    regular_input = input_tokens - cached_tokens
    regular_output = output_tokens - reasoning_tokens
    input_cost = Decimal(regular_input) / _MILLION * input_rate
    cached_cost = Decimal(cached_tokens) / _MILLION * cached_rate
    output_cost = Decimal(regular_output) / _MILLION * output_rate
    reasoning_cost = Decimal(reasoning_tokens) / _MILLION * reasoning_rate
    total = input_cost + cached_cost + output_cost + reasoning_cost + fixed
    high = pricing.context_threshold is not None and input_tokens > pricing.context_threshold
    return CostCalculation(
        status=CostStatus.CALCULATED,
        selected_tier="high_context" if high else "standard",
        regular_input_tokens=regular_input,
        regular_output_tokens=regular_output,
        input_cost=input_cost,
        cached_input_cost=cached_cost,
        output_cost=output_cost,
        reasoning_cost=reasoning_cost,
        total_cost=total,
        **base,
    )


def decimal_text(value: Decimal | None) -> str | None:
    """Serialize exactly, padding rather than rounding to at least eight decimal places."""

    if value is None:
        return None
    text = format(value, "f")
    whole, dot, fraction = text.partition(".")
    if not dot:
        fraction = ""
    return f"{whole}.{fraction.ljust(_MINIMUM_DECIMAL_PLACES, '0')}"


def calculation_to_dict(calculation: CostCalculation) -> dict[str, Any]:
    result = calculation.model_dump(mode="json")
    for field in (
        "input_cost",
        "cached_input_cost",
        "output_cost",
        "reasoning_cost",
        "fixed_cost",
        "total_cost",
    ):
        result[field] = decimal_text(getattr(calculation, field))
    pricing = calculation.pricing.model_dump()
    result["pricing"] = {
        key: decimal_text(value) if isinstance(value, Decimal) else value
        for key, value in pricing.items()
    }
    return result


class SQLiteCostRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def save(self, calculation: CostCalculation) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO cost_calculations(
                        id, model_profile_id, status, reason, usage_json, pricing_json,
                        selected_tier, regular_input_tokens, regular_output_tokens,
                        input_cost, cached_input_cost, output_cost, reasoning_cost,
                        fixed_cost, total_cost, chargeable, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(calculation.id),
                        str(calculation.model_profile_id),
                        calculation.status.value,
                        calculation.reason,
                        calculation.usage.model_dump_json(),
                        calculation.pricing.model_dump_json(),
                        calculation.selected_tier,
                        calculation.regular_input_tokens,
                        calculation.regular_output_tokens,
                        decimal_text(calculation.input_cost),
                        decimal_text(calculation.cached_input_cost),
                        decimal_text(calculation.output_cost),
                        decimal_text(calculation.reasoning_cost),
                        decimal_text(calculation.fixed_cost),
                        decimal_text(calculation.total_cost),
                        int(calculation.chargeable),
                        to_utc_iso(calculation.created_at),
                    ),
                )
        finally:
            connection.close()

    def get(self, calculation_id: UUID) -> CostCalculation | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM cost_calculations WHERE id = ?", (str(calculation_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return CostCalculation(
            id=UUID(row["id"]),
            model_profile_id=UUID(row["model_profile_id"]),
            status=CostStatus(row["status"]),
            reason=row["reason"],
            usage=TokenUsage.model_validate_json(row["usage_json"]),
            pricing=PricingSnapshot.model_validate_json(row["pricing_json"]),
            selected_tier=row["selected_tier"],
            regular_input_tokens=row["regular_input_tokens"],
            regular_output_tokens=row["regular_output_tokens"],
            input_cost=_decimal(row["input_cost"]),
            cached_input_cost=_decimal(row["cached_input_cost"]),
            output_cost=_decimal(row["output_cost"]),
            reasoning_cost=_decimal(row["reasoning_cost"]),
            fixed_cost=Decimal(row["fixed_cost"]),
            total_cost=_decimal(row["total_cost"]),
            chargeable=bool(row["chargeable"]),
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        )


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


class CostService:
    def __init__(
        self,
        repository: SQLiteCostRepository,
        profile_repository: SQLiteModelProfileRepository,
    ) -> None:
        self._repository = repository
        self._profiles = profile_repository

    def calculate_for_profile(self, profile_id: UUID, payload: Any) -> CostCalculation:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise NotFoundError("Modelprofiel is niet gevonden.")
        try:
            request = CostCalculationInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        calculation = self._calculate(profile, request.usage, request.chargeable)
        self._repository.save(calculation)
        stored = self._repository.get(calculation.id)
        assert stored is not None
        return stored

    @staticmethod
    def _calculate(
        profile: ModelProfileRecord, usage: TokenUsage, chargeable: bool
    ) -> CostCalculation:
        return calculate_cost(
            model_profile_id=profile.id,
            usage=usage,
            pricing=PricingSnapshot.from_pricing(profile.data.pricing),
            chargeable=chargeable,
        )

    def get(self, calculation_id: UUID) -> CostCalculation:
        result = self._repository.get(calculation_id)
        if result is None:
            raise NotFoundError("Kostenberekening is niet gevonden.")
        return result
