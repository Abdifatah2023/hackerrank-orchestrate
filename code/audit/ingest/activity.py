"""The global `system_activity.csv` event stream."""

from __future__ import annotations

from ..dataset import SYSTEM_ACTIVITY_CSV, parse_time, read_csv
from ..observations import RESOURCE_ID_RE, Activity, extract_identifiers, system_for


def load_activities() -> list[Activity]:
    activities = []
    for row in read_csv(SYSTEM_ACTIVITY_CSV):
        resource_id = row["resource_id"]
        details = row["details"]
        activities.append(
            Activity(
                activity_id=row["activity_id"],
                event_time=parse_time(row["event_time"]),
                event_type=row["event_type"],
                source_system=row["source_system"],
                target_system=row["target_system"],
                system_id=system_for(resource_id, row["target_system"]),
                resource_id=resource_id,
                reported_result=row["reported_result"],
                details=details,
                mentioned_resource_ids=tuple(
                    sorted({rid for rid in RESOURCE_ID_RE.findall(details) if rid != resource_id})
                ),
                identifiers=extract_identifiers({}, details),
            )
        )
    return activities
