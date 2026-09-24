"""Investment portfolio: money distributed between virtual asset types."""

from collections.abc import Mapping
from decimal import Decimal

from exceptions import InsufficientFundsError, InvalidOperationError
from models.enums import AssetType
from utils import to_money, to_rate


class Portfolio:
    """Holds amounts allocated to ``AssetType`` values.

    The portfolio knows nothing about accounts or cash: it only tracks how
    much is allocated to each asset type and projects growth. Moving money
    between cash and the portfolio is the account's responsibility.
    """

    def __init__(self) -> None:
        self._holdings: dict[AssetType, Decimal] = {}

    @staticmethod
    def resolve_asset_type(asset_type: AssetType | str) -> AssetType:
        if isinstance(asset_type, AssetType):
            return asset_type
        try:
            return AssetType(str(asset_type).lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in AssetType)
            raise InvalidOperationError(f"Unsupported asset type {asset_type!r}; allowed: {allowed}.") from exc

    @property
    def holdings(self) -> dict[AssetType, Decimal]:
        """A copy of the allocation; mutating it does not affect the portfolio."""
        return dict(self._holdings)

    @property
    def total(self) -> Decimal:
        return sum(self._holdings.values(), Decimal("0.00"))

    def get(self, asset_type: AssetType | str) -> Decimal:
        return self._holdings.get(self.resolve_asset_type(asset_type), Decimal("0.00"))

    def add(self, asset_type: AssetType | str, amount: object) -> Decimal:
        asset = self.resolve_asset_type(asset_type)
        value = to_money(amount, require="positive")
        self._holdings[asset] = self.get(asset) + value
        return self._holdings[asset]

    def remove(self, asset_type: AssetType | str, amount: object) -> Decimal:
        asset = self.resolve_asset_type(asset_type)
        value = to_money(amount, require="positive")
        held = self.get(asset)
        if value > held:
            raise InsufficientFundsError(requested=value, available=held, hint=f"Holding in {asset.value}.")
        remaining = held - value
        # an empty position disappears so that snapshots and projections stay clean
        if remaining == 0:
            del self._holdings[asset]
        else:
            self._holdings[asset] = remaining
        return remaining

    def project_yearly_growth(self, growth_rates: Mapping[AssetType | str, object]) -> Decimal:
        """Return the expected growth over one year.

        ``growth_rates`` maps every asset type held in the portfolio to its
        yearly rate as a fraction (``0.10`` means 10%). Extra keys are allowed;
        a missing key or the same asset given twice (``"stocks"`` and
        ``AssetType.STOCKS``) raises ``InvalidOperationError``.
        """
        if not isinstance(growth_rates, Mapping):
            raise InvalidOperationError("growth_rates must be a mapping of asset type to yearly rate.")

        rates: dict[AssetType, Decimal] = {}
        for key, rate in growth_rates.items():
            asset = self.resolve_asset_type(key)
            if asset in rates:
                raise InvalidOperationError(f"growth_rates has more than one rate for {asset.value}.")
            rates[asset] = to_rate(rate, field=f"growth_rates[{asset.value}]", allow_negative=True)
        missing = [asset.value for asset in self._holdings if asset not in rates]
        if missing:
            raise InvalidOperationError(f"growth_rates is missing rates for: {', '.join(missing)}.")

        growth = sum((amount * rates[asset] for asset, amount in self._holdings.items()), Decimal("0"))
        return to_money(growth, field="growth")

    def to_dict(self) -> dict[str, str]:
        return {asset.value: str(amount) for asset, amount in self._holdings.items()}

    def __repr__(self) -> str:
        return f"Portfolio({self.to_dict()})"
