"""Fail closed until preview supports current inventory dependency authority."""

CODE="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
MESSAGE="Use Detailed execution for inventory-bound sources. Preview processing does not establish their current inventory permissions or interval/background support."


def inventory_bound(scenario):
    if not isinstance(scenario,dict):
        return False
    if scenario.get("source_allocation") is not None:
        return True
    # Branch/operation history remains authoritative when an older client or
    # storage edition omits the convenience marker from an immutable snapshot.
    operations=scenario.get("operations",[])
    if not isinstance(operations,list):
        return True
    return any(isinstance(row,dict) and row.get("type")=="inventory_sources_replace" for row in operations)
