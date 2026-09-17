"""地图路线主备切换适配器（C1-1d）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from lighttrail.domain.chase import (
    GeoPoint,
    TransportMode,
    TravelProviderError,
    TravelRoute,
    TravelTimeProvider,
)


class FallbackTravelTimeProvider:
    """主服务失败时调用备用服务；双失败时把错误如实抛出。"""

    def __init__(self, primary: TravelTimeProvider, fallback: TravelTimeProvider) -> None:
        """保存主、备用路线服务。"""
        self._primary = primary
        self._fallback = fallback

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        transport_mode: TransportMode,
        depart_at: datetime,
        *,
        transit_city: str = "",
    ) -> TravelRoute:
        """优先主服务；失败时透明切换备用服务。"""
        try:
            return self._primary.route(
                origin, destination, transport_mode, depart_at, transit_city=transit_city
            )
        except TravelProviderError as primary_error:
            try:
                route = self._fallback.route(
                    origin, destination, transport_mode, depart_at, transit_city=transit_city
                )
            except TravelProviderError as fallback_error:
                raise TravelProviderError(
                    f"主服务失败（{primary_error}）；备用服务失败（{fallback_error}）"
                ) from fallback_error
            reason = f"主地图服务失败，已切换备用服务：{primary_error}"
            return replace(route, degraded_reason=reason)


__all__ = ["FallbackTravelTimeProvider"]
