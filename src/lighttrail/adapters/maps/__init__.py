"""地图路线服务适配器。

业务代码只依赖 ``domain.chase.TravelTimeProvider``；新增地图供应商时在本包补充
适配器即可，禁止让供应商响应结构渗入领域排程器。
"""

from __future__ import annotations

from lighttrail.adapters.maps.amap import AMapRoutingAdapter
from lighttrail.adapters.maps.baidu import BaiduRoutingAdapter
from lighttrail.adapters.maps.fallback import FallbackTravelTimeProvider

__all__ = ["AMapRoutingAdapter", "BaiduRoutingAdapter", "FallbackTravelTimeProvider"]
