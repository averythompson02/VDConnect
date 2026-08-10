# -*- coding: utf-8 -*-

from pyrevit import revit, DB, forms
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc


def get_connectors(element):
    """Return connectors from conduit or fitting."""

    try:
        if hasattr(element, "ConnectorManager") and element.ConnectorManager:
            return element.ConnectorManager.Connectors
    except:
        pass

    try:
        if element.MEPModel and element.MEPModel.ConnectorManager:
            return element.MEPModel.ConnectorManager.Connectors
    except:
        pass

    return []


def get_connected_elements(element):
    """Return all directly connected elements."""

    connected = []

    for connector in get_connectors(element):
        try:
            for ref_connector in connector.AllRefs:

                owner = ref_connector.Owner

                if owner and owner.Id != element.Id:
                    connected.append(owner)

        except:
            pass

    return connected


def is_conduit_or_fitting(element):
    """Check if element is a conduit or conduit fitting."""

    if isinstance(element, DB.Electrical.Conduit):
        return True

    try:
        if element.Category:
            return (
                element.Category.Id.IntegerValue ==
                int(DB.BuiltInCategory.OST_ConduitFitting)
            )
    except:
        pass

    return False


def collect_run(start_element):
    """
    Walk the entire connected network and
    return all conduit + conduit fitting ids.
    """

    queue = [start_element]
    visited = set()
    run_ids = set()

    while queue:

        current = queue.pop(0)

        current_id = current.Id.IntegerValue

        if current_id in visited:
            continue

        visited.add(current_id)

        if is_conduit_or_fitting(current):
            run_ids.add(current.Id)

        for connected in get_connected_elements(current):

            if connected.Id.IntegerValue not in visited:
                queue.append(connected)

    return run_ids


# ----------------------------------------------------
# Main
# ----------------------------------------------------

selection_ids = uidoc.Selection.GetElementIds()

if not selection_ids:
    forms.alert(
        "Select one or more conduit elements first.",
        exitscript=True
    )

starting_elements = []

for element_id in selection_ids:

    element = doc.GetElement(element_id)

    if isinstance(element, DB.Electrical.Conduit):
        starting_elements.append(element)

if not starting_elements:
    forms.alert(
        "No conduit elements were selected.",
        exitscript=True
    )

result_ids = set()

for conduit in starting_elements:
    result_ids.update(collect_run(conduit))

if result_ids:

    new_selection = List[DB.ElementId]()

    for eid in result_ids:
        new_selection.Add(eid)

    uidoc.Selection.SetElementIds(new_selection)