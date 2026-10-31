"""Choose one available account per source without rotating through FloodWait."""


def is_flood_wait_error(exc: Exception) -> bool:
    return type(exc).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}


def select_source_routes(candidates, connected_account_ids, flood_wait_account_ids=()):
    connected = set(connected_account_ids)
    flood_wait = set(flood_wait_account_ids)
    routes = {}
    stopped = set()
    for row in candidates:
        source_id = row["source_id"]
        account_id = row["account_id"]
        if source_id in routes or source_id in stopped:
            continue
        if account_id in flood_wait:
            stopped.add(source_id)
        elif account_id in connected:
            routes[source_id] = row
    return routes
