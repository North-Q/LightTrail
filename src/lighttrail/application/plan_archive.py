"""将已验证的结构化计划转换为不含经纬度的离线回放快照。"""

from __future__ import annotations

from lighttrail.contracts.plan_archive import (
    ArchivedArea,
    ArchivedPlanningRequest,
    ArchivedPlanSite,
    ArchivedSpotPlan,
    PlanArchive,
)
from lighttrail.contracts.spot_plan import SpotPlan


def archive_spot_plan(plan: SpotPlan) -> PlanArchive:
    """保留计划语义并删除结构化位置坐标。

    Args:
        plan: 由 SpotPlan 契约验证过的原始计划。

    Returns:
        可离线导入回放的版本化脱敏快照。
    """
    request = plan.request
    archived_request = ArchivedPlanningRequest(
        subject_type=request.subject_type,
        target_date=request.target_date,
        date_range=request.date_range,
        tz_offset=request.tz_offset,
        spot=request.spot,
        area=(
            ArchivedArea(
                name=request.area.name,
                radius_km=request.area.radius_km,
                coordinate_system=request.area.center.coordinate_system,
            )
            if request.area is not None
            else None
        ),
    )
    site = plan.site
    archived_site = (
        ArchivedPlanSite(
            name=site.name,
            site_id=site.site_id,
            coordinate_system=site.point.coordinate_system,
            source=site.source,
        )
        if site is not None
        else None
    )
    archived_plan = ArchivedSpotPlan(
        status=plan.status,
        selected_date=plan.selected_date,
        site=archived_site,
        direction=plan.direction,
        arrival_window=plan.arrival_window,
        setup_window=plan.setup_window,
        shoot_window=plan.shoot_window,
        departure_window=plan.departure_window,
        focal_length_mm=plan.focal_length_mm,
        gear=plan.gear,
        parameters=plan.parameters,
        risks=plan.risks,
        evidence_refs=plan.evidence_refs,
    )
    return PlanArchive(request=archived_request, plan=archived_plan)


__all__ = ["archive_spot_plan"]
