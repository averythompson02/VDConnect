# -*- coding: utf-8 -*-

"__title__ = "Conduit Audit"
__doc__ = """Purpose:
    Propagates the Comments parameter through selected conduit runs, including conduit fittings.

Workflow:
    1. Select one or more conduits and/or conduit fittings.
    2. Run the script.
    3. Script groups connected conduit/fitting networks into separate runs.
    4. If a run has conflicting Comments values, user selects which value wins.
    5. Selected value is pushed to all conduits and conduit fittings in that run.


"""
pyRevit Script: Push Comments Through Conduit Runs

Purpose:
    Propagates the Comments parameter through selected conduit runs,
    including conduit fittings.

Workflow:
    1. Select one or more conduits and/or conduit fittings.
    2. Run the script.
    3. Script groups connected conduit/fitting networks into separate runs.
    4. If a run has conflicting Comments values, user selects which value wins.
    5. Selected value is pushed to all conduits and conduit fittings in that run.

Categories modified:
    - OST_Conduit
    - OST_ConduitFitting

Parameter modified:
    - BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS
"""

from pyrevit import revit, DB, forms, script
from collections import Counter, deque

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()
output = script.get_output()


# ------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------

TARGET_BUILTIN_PARAMETER = DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS
TARGET_PARAMETER_DISPLAY_NAME = "Comments"

CONDUIT_CAT_ID = int(DB.BuiltInCategory.OST_Conduit)
CONDUIT_FITTING_CAT_ID = int(DB.BuiltInCategory.OST_ConduitFitting)

VALID_CATEGORY_IDS = set([
    CONDUIT_CAT_ID,
    CONDUIT_FITTING_CAT_ID
])


# ------------------------------------------------------------
# BASIC HELPERS
# ------------------------------------------------------------

def get_element_id_int(element_or_id):
    """Safely get integer value from Element or ElementId."""
    try:
        eid = element_or_id.Id
    except:
        eid = element_or_id

    try:
        return eid.IntegerValue
    except:
        try:
            return int(eid.Value)
        except:
            return None


def get_category_id_int(elem):
    """Return category id integer, or None."""
    if not elem:
        return None

    cat = elem.Category
    if not cat:
        return None

    return get_element_id_int(cat.Id)


def is_conduit_or_fitting(elem):
    """True if element is conduit or conduit fitting."""
    cat_id = get_category_id_int(elem)
    return cat_id in VALID_CATEGORY_IDS


def get_category_label(elem):
    """Readable category label for reporting."""
    cat_id = get_category_id_int(elem)

    if cat_id == CONDUIT_CAT_ID:
        return "Conduit"

    if cat_id == CONDUIT_FITTING_CAT_ID:
        return "Conduit Fitting"

    return "Other"


def safe_get_element(eid_int):
    """Get Revit element from int ElementId."""
    try:
        return doc.GetElement(DB.ElementId(eid_int))
    except:
        return None


# ------------------------------------------------------------
# CONNECTOR HELPERS
# ------------------------------------------------------------

def get_connectors(elem):
    """
    Return connectors for an element.

    Conduit usually exposes ConnectorManager directly.
    Fittings usually expose connectors through MEPModel.ConnectorManager.
    """
    if not elem:
        return []

    try:
        return list(elem.ConnectorManager.Connectors)
    except:
        pass

    try:
        return list(elem.MEPModel.ConnectorManager.Connectors)
    except:
        pass

    return []


def get_connected_conduit_network_neighbors(elem):
    """
    Return connected neighboring elements that are conduit or conduit fittings.
    """
    neighbors = []

    elem_id_int = get_element_id_int(elem)

    for connector in get_connectors(elem):
        try:
            refs = connector.AllRefs
        except:
            continue

        for ref_connector in refs:
            try:
                owner = ref_connector.Owner
            except:
                continue

            if not owner:
                continue

            owner_id_int = get_element_id_int(owner)

            if owner_id_int is None:
                continue

            if owner_id_int == elem_id_int:
                continue

            if is_conduit_or_fitting(owner):
                neighbors.append(owner)

    return neighbors


# ------------------------------------------------------------
# BUILD CONNECTED RUNS
# ------------------------------------------------------------

def build_connected_runs(seed_elements):
    """
    Build separate connected conduit/fitting runs from selected seed elements.

    Each selected conduit or fitting becomes a seed.
    The script traces through connector-connected conduits and conduit fittings.
    """
    runs = []
    visited_ids = set()

    seed_ids = []
    for elem in seed_elements:
        eid_int = get_element_id_int(elem)
        if eid_int is not None:
            seed_ids.append(eid_int)

    for seed_id in seed_ids:

        if seed_id in visited_ids:
            continue

        seed_elem = safe_get_element(seed_id)

        if not seed_elem:
            continue

        if not is_conduit_or_fitting(seed_elem):
            continue

        current_run = []
        queue = deque([seed_elem])

        while queue:
            current_elem = queue.popleft()
            current_id = get_element_id_int(current_elem)

            if current_id is None:
                continue

            if current_id in visited_ids:
                continue

            visited_ids.add(current_id)

            if is_conduit_or_fitting(current_elem):
                current_run.append(current_elem)

                for neighbor in get_connected_conduit_network_neighbors(current_elem):
                    neighbor_id = get_element_id_int(neighbor)

                    if neighbor_id is None:
                        continue

                    if neighbor_id not in visited_ids:
                        queue.append(neighbor)

        if current_run:
            runs.append(current_run)

    return runs


# ------------------------------------------------------------
# PARAMETER HELPERS
# ------------------------------------------------------------

def get_target_parameter(elem):
    """
    Get the Comments parameter.

    Uses built-in parameter first because it is more stable than display name.
    Falls back to LookupParameter for unusual cases.
    """
    if not elem:
        return None

    try:
        param = elem.get_Parameter(TARGET_BUILTIN_PARAMETER)
        if param:
            return param
    except:
        pass

    try:
        return elem.LookupParameter(TARGET_PARAMETER_DISPLAY_NAME)
    except:
        return None


def get_parameter_string_value(elem):
    """Get Comments value as string."""
    param = get_target_parameter(elem)

    if not param:
        return ""

    try:
        value = param.AsString()
        if value is None:
            return ""
        return value
    except:
        return ""


def can_write_parameter(elem):
    """Check if Comments parameter exists and can be written."""
    param = get_target_parameter(elem)

    if not param:
        return False, "Missing parameter"

    if param.IsReadOnly:
        return False, "Read-only parameter"

    try:
        if param.StorageType != DB.StorageType.String:
            return False, "Parameter is not text"
    except:
        pass

    return True, ""


def set_parameter_string_value(elem, value):
    """Set Comments value. Returns tuple: success, reason."""
    param = get_target_parameter(elem)

    if not param:
        return False, "Missing parameter"

    if param.IsReadOnly:
        return False, "Read-only parameter"

    try:
        if param.StorageType != DB.StorageType.String:
            return False, "Parameter is not text"
    except:
        pass

    try:
        param.Set(value)
        return True, ""
    except Exception as ex:
        return False, str(ex)


# ------------------------------------------------------------
# CONFLICT RESOLUTION
# ------------------------------------------------------------

class CommentChoiceItem(object):
    """Wrapper so pyRevit SelectFromList can show value with count."""

    def __init__(self, value, count):
        self.value = value
        self.count = count

    def __str__(self):
        return '{}    [{} element{}]'.format(
            self.value,
            self.count,
            "" if self.count == 1 else "s"
        )


class CustomValueItem(object):
    def __str__(self):
        return "<Enter New Value>"


def get_run_stats(run):
    """Return counts by category for one run."""
    conduit_count = 0
    fitting_count = 0

    for elem in run:
        cat_id = get_category_id_int(elem)

        if cat_id == CONDUIT_CAT_ID:
            conduit_count += 1
        elif cat_id == CONDUIT_FITTING_CAT_ID:
            fitting_count += 1

    return conduit_count, fitting_count


def determine_value_for_run(run, run_number):
    """
    Decide which Comments value should be pushed through the run.

    Rules:
        - No non-blank values: ask user to enter one.
        - One non-blank value: use automatically.
        - Multiple non-blank values: user chooses winner or enters new value.
    """
    value_counter = Counter()

    for elem in run:
        value = get_parameter_string_value(elem).strip()

        if value:
            value_counter[value] += 1

    conduit_count, fitting_count = get_run_stats(run)

    title = "Run {} - {} Conduits, {} Fittings".format(
        run_number,
        conduit_count,
        fitting_count
    )

    if len(value_counter) == 0:
        entered_value = forms.ask_for_string(
            default="",
            prompt=(
                "{}\n\n"
                "No non-blank Comments value was found in this run.\n"
                "Enter the Comments value to push through this run:"
            ).format(title),
            title="Comments Value Required"
        )

        if entered_value is None:
            return None

        return entered_value

    if len(value_counter) == 1:
        return value_counter.keys()[0] if hasattr(value_counter.keys(), "__getitem__") else list(value_counter.keys())[0]

    # Multiple distinct values found.
    choice_items = []

    # Sort by count descending, then value alphabetically.
    sorted_values = sorted(
        value_counter.items(),
        key=lambda item: (-item[1], item[0])
    )

    for value, count in sorted_values:
        choice_items.append(CommentChoiceItem(value, count))

    custom_item = CustomValueItem()
    choice_items.append(custom_item)

    selected = forms.SelectFromList.show(
        choice_items,
        title="Conflicting Comments Found",
        multiselect=False,
        button_name="Use Selected Value",
        width=650,
        height=450
    )

    if not selected:
        return None

    if isinstance(selected, CustomValueItem):
        entered_value = forms.ask_for_string(
            default="",
            prompt=(
                "{}\n\n"
                "Enter the new Comments value to push through this run:"
            ).format(title),
            title="Enter Custom Comments Value"
        )

        if entered_value is None:
            return None

        return entered_value

    return selected.value


# ------------------------------------------------------------
# REPORTING HELPERS
# ------------------------------------------------------------

def make_element_link(elem):
    """Create pyRevit output link to element where possible."""
    try:
        return output.linkify(elem.Id)
    except:
        return str(get_element_id_int(elem))


def print_run_summary(runs):
    """Print run summary to pyRevit output."""
    output.print_md("## Conduit Comment Propagation")
    output.print_md("### Runs Detected")

    table_data = []

    for index, run in enumerate(runs, start=1):
        conduit_count, fitting_count = get_run_stats(run)

        table_data.append([
            index,
            conduit_count,
            fitting_count,
            len(run)
        ])

    output.print_table(
        table_data=table_data,
        columns=[
            "Run",
            "Conduits",
            "Fittings",
            "Total Elements"
        ]
    )


def print_skipped_elements(skipped_items):
    """Print skipped elements to output."""
    if not skipped_items:
        return

    output.print_md("### Skipped Elements")

    table_data = []

    for item in skipped_items:
        elem = item["element"]
        table_data.append([
            item["run"],
            make_element_link(elem),
            get_category_label(elem),
            item["reason"]
        ])

    output.print_table(
        table_data=table_data,
        columns=[
            "Run",
            "Element Id",
            "Category",
            "Reason"
        ]
    )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    selected_ids = list(uidoc.Selection.GetElementIds())

    if not selected_ids:
        forms.alert(
            "Select one or more conduits and/or conduit fittings before running this tool.",
            title="No Selection",
            exitscript=True
        )

    selected_elements = []

    ignored_count = 0

    for eid in selected_ids:
        elem = doc.GetElement(eid)

        if not elem:
            continue

        if is_conduit_or_fitting(elem):
            selected_elements.append(elem)
        else:
            ignored_count += 1

    if not selected_elements:
        forms.alert(
            "No conduit or conduit fitting elements were selected.",
            title="Invalid Selection",
            exitscript=True
        )

    runs = build_connected_runs(selected_elements)

    if not runs:
        forms.alert(
            "No connected conduit runs were detected from the current selection.",
            title="No Runs Found",
            exitscript=True
        )

    print_run_summary(runs)

    # Collect user decisions before opening the transaction.
    # This avoids leaving a Revit transaction open while dialogs are displayed.
    run_value_map = {}

    for run_number, run in enumerate(runs, start=1):
        selected_value = determine_value_for_run(run, run_number)

        # User cancelled this run. Skip it, continue with others.
        if selected_value is None:
            continue

        run_value_map[run_number] = selected_value

    if not run_value_map:
        forms.alert(
            "No runs were updated. All run prompts were cancelled.",
            title="Nothing Updated",
            exitscript=True
        )

    updated_count = 0
    skipped_items = []
    failed_count = 0

    with revit.Transaction("Push Comments Through Conduit Runs"):

        for run_number, run in enumerate(runs, start=1):

            if run_number not in run_value_map:
                continue

            value_to_set = run_value_map[run_number]

            for elem in run:
                can_write, reason = can_write_parameter(elem)

                if not can_write:
                    skipped_items.append({
                        "run": run_number,
                        "element": elem,
                        "reason": reason
                    })
                    failed_count += 1
                    continue

                success, set_reason = set_parameter_string_value(elem, value_to_set)

                if success:
                    updated_count += 1
                else:
                    skipped_items.append({
                        "run": run_number,
                        "element": elem,
                        "reason": set_reason
                    })
                    failed_count += 1

    print_skipped_elements(skipped_items)

    forms.alert(
        (
            "Conduit Comments propagation complete.\n\n"
            "Runs detected: {}\n"
            "Runs updated: {}\n"
            "Elements updated: {}\n"
            "Elements skipped: {}\n"
            "Non-conduit selected items ignored: {}"
        ).format(
            len(runs),
            len(run_value_map),
            updated_count,
            failed_count,
            ignored_count
        ),
        title="Complete"
    )


if __name__ == "__main__":
    main()