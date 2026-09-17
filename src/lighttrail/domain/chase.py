"""多机位赶场领域模型与确定性排程（C1-1a）。

设计要点：
- 地图服务是可替换的 ``TravelTimeProvider`` 端口；排程器只认识统一路线结果；
- 首期的 ``ManualTravelTimeProvider`` 用直线距离加保守速度估算，确保无 Key、离线时仍可用；
- 最多四个机位，直接枚举访问顺序，优先保证规则透明而非追求黑盒最优。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

TransportMode = Literal["driving", "cycling", "walking", "transit"]
TrafficLevel = Literal["畅通", "缓行", "拥堵", "未知"]
Confidence = Literal["high", "medium", "low"]

_SPEED_KMH: dict[TransportMode, float] = {
    "driving": 35.0,
    "cycling": 14.0,
    "walking": 4.5,
    "transit": 22.0,
}


@dataclass(frozen=True)
class GeoPoint:
    """项目内部统一经纬度坐标。"""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        """校验坐标范围。"""
        if not -90 <= self.latitude <= 90:
            raise ValueError("纬度必须在 -90 到 90 之间")
        if not -180 <= self.longitude <= 180:
            raise ValueError("经度必须在 -180 到 180 之间")


@dataclass(frozen=True)
class ChaseSite:
    """带拍摄窗口的候选机位。"""

    name: str
    point: GeoPoint
    window_start: datetime
    window_end: datetime
    match_score: float = 0.0

    def __post_init__(self) -> None:
        """校验机位名称和时间窗口。"""
        if not self.name.strip():
            raise ValueError("机位名称不能为空")
        if self.window_end <= self.window_start:
            raise ValueError("拍摄窗口结束必须晚于开始")


@dataclass(frozen=True)
class TravelRoute:
    """地图服务标准化后的机位间路线。"""

    distance_m: int
    duration_s: int
    transport_mode: TransportMode
    traffic_level: TrafficLevel
    traffic_summary: str
    queried_at: datetime
    is_future_prediction: bool
    provider: Literal["amap", "baidu", "manual"]
    confidence: Confidence
    degraded_reason: str = ""


class TravelTimeProvider(Protocol):
    """通勤时间查询端口。"""

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        transport_mode: TransportMode,
        depart_at: datetime,
        *,
        transit_city: str = "",
    ) -> TravelRoute:
        """查询一段通勤路线。"""


class TravelProviderError(Exception):
    """地图服务不可用、权限不足或返回数据非法。"""


class ManualTravelTimeProvider:
    """无地图服务时的保守直线距离估算实现。"""

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        transport_mode: TransportMode,
        depart_at: datetime,
        *,
        transit_city: str = "",
    ) -> TravelRoute:
        """按直线距离与保守均速估算通勤时间。"""
        distance_m = _haversine_m(origin, destination)
        # 道路、骑行和公共交通均不是直线；用 1.35 倍避免把赶场说得过于乐观。
        route_distance_m = math.ceil(distance_m * 1.35)
        duration_s = max(60, math.ceil(route_distance_m / 1000 / _SPEED_KMH[transport_mode] * 3600))
        return TravelRoute(
            distance_m=route_distance_m,
            duration_s=duration_s,
            transport_mode=transport_mode,
            traffic_level="未知",
            traffic_summary="按直线距离与保守均速估算，未接入地图路况",
            queried_at=depart_at,
            is_future_prediction=False,
            provider="manual",
            confidence="low",
            degraded_reason="未配置可用地图服务，已按直线距离保守估算",
        )


@dataclass(frozen=True)
class ChaseLeg:
    """赶场计划中的一段通勤。"""

    from_site: str
    to_site: str
    leave_at: datetime
    arrive_at: datetime
    latest_leave_at: datetime
    effective_shooting_s: int
    margin_s: int
    route: TravelRoute
    buffer_s: int


@dataclass(frozen=True)
class RejectedOption:
    """不可行机位顺序及其原因。"""

    ordered_site_names: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ChasePlan:
    """完整赶场建议。"""

    ordered_site_names: tuple[str, ...]
    legs: tuple[ChaseLeg, ...]
    total_effective_shooting_s: int
    feasible: bool
    risk_level: Literal["low", "medium", "high", "unavailable"]
    explanation: str
    rejected_options: tuple[RejectedOption, ...] = field(default_factory=tuple)


class ChasePlanner:
    """最多四机位的透明赶场排程器。"""

    def __init__(self, provider: TravelTimeProvider) -> None:
        """注入路线服务。"""
        self._provider = provider

    def plan(
        self,
        sites: list[ChaseSite],
        transport_mode: TransportMode,
        depart_at: datetime,
        *,
        transition_buffer_s: int,
        min_shooting_s: int,
        transit_city: str = "",
    ) -> ChasePlan:
        """枚举机位顺序并返回最可行的计划。

        Args:
            sites: 两到四个带拍摄窗口的机位。
            transport_mode: 驾车、骑行、步行或公共交通。
            depart_at: 首次出发时间。
            transition_buffer_s: 每次机位切换附加缓冲秒数。
            min_shooting_s: 每个机位最少有效拍摄秒数。

        Returns:
            可行性最高的赶场计划；无可行顺序时返回不可行计划和原因。
        """
        if not 2 <= len(sites) <= 4:
            raise ValueError("赶场规划需要 2 到 4 个机位")
        if transition_buffer_s < 0 or min_shooting_s < 0:
            raise ValueError("缓冲时间和最少拍摄时间不能为负数")

        feasible: list[ChasePlan] = []
        rejected: list[RejectedOption] = []
        for ordered in itertools.permutations(sites):
            result = self._evaluate(
                ordered,
                transport_mode,
                depart_at,
                transition_buffer_s,
                min_shooting_s,
                transit_city,
            )
            if result.feasible:
                feasible.append(result)
            else:
                rejected.extend(result.rejected_options)

        if not feasible:
            return ChasePlan(
                ordered_site_names=(),
                legs=(),
                total_effective_shooting_s=0,
                feasible=False,
                risk_level="unavailable",
                explanation="所有机位顺序都无法满足拍摄窗口，请减少机位或调整出发时间。",
                rejected_options=tuple(rejected[:8]),
            )

        best = max(
            feasible,
            key=lambda item: (item.total_effective_shooting_s, sum(leg.margin_s for leg in item.legs)),
        )
        return ChasePlan(
            ordered_site_names=best.ordered_site_names,
            legs=best.legs,
            total_effective_shooting_s=best.total_effective_shooting_s,
            feasible=True,
            risk_level=best.risk_level,
            explanation=best.explanation,
            rejected_options=tuple(rejected[:8]),
        )

    def _evaluate(
        self,
        ordered: tuple[ChaseSite, ...],
        transport_mode: TransportMode,
        depart_at: datetime,
        transition_buffer_s: int,
        min_shooting_s: int,
        transit_city: str,
    ) -> ChasePlan:
        """评估一个固定机位顺序。"""
        current = depart_at
        total_shooting = 0
        legs: list[ChaseLeg] = []
        for index, site in enumerate(ordered):
            if index == 0:
                arrive_at = current
            else:
                previous = ordered[index - 1]
                route = self._provider.route(
                    previous.point,
                    site.point,
                    transport_mode,
                    current,
                    transit_city=transit_city,
                )
                arrive_at = current + timedelta(seconds=route.duration_s + transition_buffer_s)
                latest_leave = site.window_end - timedelta(seconds=min_shooting_s + route.duration_s + transition_buffer_s)
                effective_start = max(arrive_at, site.window_start)
                effective_shooting = int((site.window_end - effective_start).total_seconds())
                margin = int((latest_leave - current).total_seconds())
                legs.append(
                    ChaseLeg(
                        from_site=previous.name,
                        to_site=site.name,
                        leave_at=current,
                        arrive_at=arrive_at,
                        latest_leave_at=latest_leave,
                        effective_shooting_s=max(0, effective_shooting),
                        margin_s=margin,
                        route=route,
                        buffer_s=transition_buffer_s,
                    )
                )
            effective_start = max(arrive_at, site.window_start)
            effective_shooting = int((site.window_end - effective_start).total_seconds())
            if effective_shooting < min_shooting_s:
                names = tuple(item.name for item in ordered)
                return ChasePlan(
                    ordered_site_names=names,
                    legs=tuple(legs),
                    total_effective_shooting_s=total_shooting,
                    feasible=False,
                    risk_level="unavailable",
                    explanation=f"到达 {site.name} 时剩余拍摄时间不足。",
                    rejected_options=(RejectedOption(names, f"{site.name} 的有效拍摄时长不足"),),
                )
            total_shooting += effective_shooting
            current = site.window_end

        margins = [leg.margin_s for leg in legs]
        smallest_margin = min(margins) if margins else 0
        risk_level: Literal["low", "medium", "high", "unavailable"]
        if smallest_margin >= 20 * 60:
            risk_level = "low"
        elif smallest_margin >= 5 * 60:
            risk_level = "medium"
        else:
            risk_level = "high"
        names = tuple(item.name for item in ordered)
        return ChasePlan(
            ordered_site_names=names,
            legs=tuple(legs),
            total_effective_shooting_s=total_shooting,
            feasible=True,
            risk_level=risk_level,
            explanation=f"建议按 {' → '.join(names)} 赶场，预计可获得 {total_shooting // 60} 分钟有效拍摄时间。",
        )


def _haversine_m(origin: GeoPoint, destination: GeoPoint) -> int:
    """计算两坐标间球面直线距离（米）。"""
    radius_m = 6_371_000
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (origin.latitude, origin.longitude, destination.latitude, destination.longitude),
    )
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return int(2 * radius_m * math.asin(math.sqrt(a)))


__all__ = [
    "ChaseLeg",
    "ChasePlan",
    "ChasePlanner",
    "ChaseSite",
    "GeoPoint",
    "ManualTravelTimeProvider",
    "RejectedOption",
    "TransportMode",
    "TravelProviderError",
    "TravelRoute",
    "TravelTimeProvider",
]
