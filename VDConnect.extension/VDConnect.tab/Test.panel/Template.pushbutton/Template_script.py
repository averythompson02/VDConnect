# -*- coding: utf-8 -*-
"""
Simplify Exploded CAD Detail Lines

Select Detail Lines created from exploded CAD and run this tool.

The script merges connected collinear straight Detail Lines while:
    - Preserving corners
    - Preserving line styles
    - Ignoring arcs and other curves
    - Ignoring pinned and grouped lines
    - Reporting ignored elements
    - Selecting the newly created lines

Designed for pyRevit IronPython 2.7.
"""

from pyrevit import revit, DB, UI, forms, script
from System.Collections.Generic import List
import math


doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()


# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------

# Revit internal units are feet.
#
# 1/32 inch = 1 / 384 feet
# 1/16 inch = 1 / 192 feet
# 1/8 inch  = 1 / 96 feet
# 1/4 inch  = 1 / 48 feet

MINIMUM_LINE_LENGTH = 0.0001
NUMERICAL_TOLERANCE = 0.000000001


# ---------------------------------------------------------------------
# ELEMENT ID HELPERS
# ---------------------------------------------------------------------

def id_value(element_id):
    """Return an ElementId value for older and newer Revit versions."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def ids_equal(first_id, second_id):
    """Compare two Revit ElementIds."""
    return id_value(first_id) == id_value(second_id)


def is_invalid_id(element_id):
    """Check for ElementId.InvalidElementId."""
    return ids_equal(element_id, DB.ElementId.InvalidElementId)


# ---------------------------------------------------------------------
# GEOMETRY HELPERS
# ---------------------------------------------------------------------

def canonical_direction(direction):
    """
    Normalize a direction and make opposite directions equivalent.

    A line pointing left and a line pointing right are therefore grouped
    using the same axis direction.
    """
    direction = direction.Normalize()

    reverse = False

    if direction.X < -NUMERICAL_TOLERANCE:
        reverse = True

    elif abs(direction.X) <= NUMERICAL_TOLERANCE:
        if direction.Y < -NUMERICAL_TOLERANCE:
            reverse = True

        elif abs(direction.Y) <= NUMERICAL_TOLERANCE:
            if direction.Z < -NUMERICAL_TOLERANCE:
                reverse = True

    if reverse:
        return direction.Negate()

    return direction


def smallest_parallel_angle(direction_a, direction_b):
    """
    Return the smallest angle between two line directions.

    Opposite directions are treated as parallel.
    """
    direction_a = direction_a.Normalize()
    direction_b = direction_b.Normalize()

    dot_value = abs(direction_a.DotProduct(direction_b))
    dot_value = max(-1.0, min(1.0, dot_value))

    return math.acos(dot_value)


def point_to_axis_distance(point, axis_origin, axis_direction):
    """Return perpendicular distance from a point to an infinite line."""
    offset = point - axis_origin
    return offset.CrossProduct(axis_direction).GetLength()


def curve_midpoint(curve):
    """Return the midpoint of a bounded line."""
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)

    return DB.XYZ(
        (start.X + end.X) / 2.0,
        (start.Y + end.Y) / 2.0,
        (start.Z + end.Z) / 2.0
    )


def get_style_id(detail_curve):
    """Return the line-style ElementId assigned to a DetailCurve."""
    line_style = detail_curve.LineStyle

    if line_style is None:
        return DB.ElementId.InvalidElementId

    return line_style.Id


def lines_share_axis(curve_a,
                     curve_b,
                     axis_tolerance,
                     angle_tolerance):
    """
    Test whether two lines belong to the same straight infinite axis.

    Parallel lines on different sides of a profile remain separate because
    their perpendicular distance exceeds axis_tolerance.
    """
    direction_a = canonical_direction(curve_a.Direction)
    direction_b = canonical_direction(curve_b.Direction)

    angle = smallest_parallel_angle(direction_a, direction_b)

    if angle > angle_tolerance:
        return False

    origin_a = curve_a.GetEndPoint(0)

    midpoint_distance = point_to_axis_distance(
        curve_midpoint(curve_b),
        origin_a,
        direction_a
    )

    if midpoint_distance > axis_tolerance:
        return False

    endpoint_tolerance = axis_tolerance * 2.0

    start_distance = point_to_axis_distance(
        curve_b.GetEndPoint(0),
        origin_a,
        direction_a
    )

    end_distance = point_to_axis_distance(
        curve_b.GetEndPoint(1),
        origin_a,
        direction_a
    )

    return (
        start_distance <= endpoint_tolerance
        and end_distance <= endpoint_tolerance
    )


# ---------------------------------------------------------------------
# USER INPUT
# ---------------------------------------------------------------------

def get_selected_elements():
    """
    Return the current selection.

    Prompt for a selection if nothing is already selected.
    """
    selected_ids = list(uidoc.Selection.GetElementIds())

    if selected_ids:
        elements = []

        for element_id in selected_ids:
            element = doc.GetElement(element_id)

            if element is not None:
                elements.append(element)

        return elements

    try:
        references = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            "Select exploded CAD Detail Lines"
        )

        elements = []

        for reference in references:
            element = doc.GetElement(reference.ElementId)

            if element is not None:
                elements.append(element)

        return elements

    except Exception:
        script.exit()


def choose_tolerance():
    """Ask the user to select a cleanup tolerance."""
    options = [
        "Strict - 1/32 inch gaps",
        "Normal - 1/16 inch gaps",
        "Moderate - 1/8 inch gaps",
        "Aggressive - 1/4 inch gaps"
    ]

    selected = forms.CommandSwitchWindow.show(
        options,
        message=(
            "Choose the maximum CAD gap to merge.\n\n"
            "Normal is the recommended starting point."
        )
    )

    if not selected:
        script.exit()

    presets = {
        "Strict - 1/32 inch gaps": {
            "gap": 1.0 / 384.0,
            "axis": 1.0 / 768.0,
            "angle": 0.05
        },
        "Normal - 1/16 inch gaps": {
            "gap": 1.0 / 192.0,
            "axis": 1.0 / 384.0,
            "angle": 0.10
        },
        "Moderate - 1/8 inch gaps": {
            "gap": 1.0 / 96.0,
            "axis": 1.0 / 192.0,
            "angle": 0.15
        },
        "Aggressive - 1/4 inch gaps": {
            "gap": 1.0 / 48.0,
            "axis": 1.0 / 96.0,
            "angle": 0.25
        }
    }

    result = presets[selected]

    return {
        "name": selected,
        "gap": result["gap"],
        "axis": result["axis"],
        "angle_degrees": result["angle"],
        "angle_radians": math.radians(result["angle"])
    }


# ---------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------

def empty_classification():
    """Create classification storage."""
    return {
        "straight": [],
        "arcs": [],
        "other_curves": [],
        "pinned": [],
        "grouped": [],
        "other_view": [],
        "import_instances": [],
        "other_elements": []
    }


def classify_elements(elements):
    """
    Separate eligible straight Detail Lines from ignored elements.
    """
    result = empty_classification()
    active_view_id = doc.ActiveView.Id

    for element in elements:
        if element is None:
            continue

        if isinstance(element, DB.ImportInstance):
            result["import_instances"].append(element)
            continue

        if not isinstance(element, DB.DetailCurve):
            result["other_elements"].append(element)
            continue

        geometry_curve = element.GeometryCurve

        # Curves are reported before checking whether they are pinned.
        if isinstance(geometry_curve, DB.Arc):
            result["arcs"].append(element)
            continue

        if not isinstance(geometry_curve, DB.Line):
            result["other_curves"].append(element)
            continue

        if not ids_equal(element.OwnerViewId, active_view_id):
            result["other_view"].append(element)
            continue

        if element.Pinned:
            result["pinned"].append(element)
            continue

        if not is_invalid_id(element.GroupId):
            result["grouped"].append(element)
            continue

        result["straight"].append(element)

    return result


# ---------------------------------------------------------------------
# AXIS GROUPING
# ---------------------------------------------------------------------

def build_axis_groups(straight_lines,
                      axis_tolerance,
                      angle_tolerance):
    """
    Group straight Detail Lines by line style and infinite axis.

    Longer segments are processed first to provide more stable reference
    axes for small CAD fragments.
    """
    ordered_lines = sorted(
        straight_lines,
        key=lambda item: item.GeometryCurve.Length,
        reverse=True
    )

    groups = []

    for detail_line in ordered_lines:
        curve = detail_line.GeometryCurve
        style_id = get_style_id(detail_line)
        matching_group = None

        for group in groups:
            if not ids_equal(style_id, group["style_id"]):
                continue

            if lines_share_axis(
                    curve,
                    group["reference_curve"],
                    axis_tolerance,
                    angle_tolerance):

                matching_group = group
                break

        if matching_group is None:
            groups.append({
                "style_id": style_id,
                "line_style": detail_line.LineStyle,
                "reference_curve": curve,
                "elements": [detail_line]
            })

        else:
            matching_group["elements"].append(detail_line)

            if curve.Length > matching_group["reference_curve"].Length:
                matching_group["reference_curve"] = curve

    return groups


# ---------------------------------------------------------------------
# CONNECTED RUN DETECTION
# ---------------------------------------------------------------------

def project_to_interval(detail_line, origin, direction):
    """
    Project both endpoints of a Detail Line onto a one-dimensional axis.
    """
    curve = detail_line.GeometryCurve

    value_a = (
        curve.GetEndPoint(0) - origin
    ).DotProduct(direction)

    value_b = (
        curve.GetEndPoint(1) - origin
    ).DotProduct(direction)

    return {
        "element": detail_line,
        "start": min(value_a, value_b),
        "end": max(value_a, value_b)
    }


def split_group_into_runs(group, gap_tolerance):
    """
    Split one collinear group wherever there is a gap larger than tolerance.

    Overlapping and duplicate segments remain in the same run.
    """
    reference_curve = group["reference_curve"]
    origin = reference_curve.GetEndPoint(0)
    direction = canonical_direction(reference_curve.Direction)

    intervals = []

    for detail_line in group["elements"]:
        intervals.append(
            project_to_interval(
                detail_line,
                origin,
                direction
            )
        )

    intervals.sort(
        key=lambda item: (item["start"], item["end"])
    )

    runs = []
    current = None

    for interval in intervals:
        if current is None:
            current = {
                "origin": origin,
                "direction": direction,
                "start": interval["start"],
                "end": interval["end"],
                "elements": [interval["element"]],
                "line_style": group["line_style"]
            }
            continue

        gap = interval["start"] - current["end"]

        if gap <= gap_tolerance:
            current["end"] = max(
                current["end"],
                interval["end"]
            )

            current["elements"].append(
                interval["element"]
            )

        else:
            runs.append(current)

            current = {
                "origin": origin,
                "direction": direction,
                "start": interval["start"],
                "end": interval["end"],
                "elements": [interval["element"]],
                "line_style": group["line_style"]
            }

    if current is not None:
        runs.append(current)

    return runs


def find_merge_runs(straight_lines, settings):
    """
    Find all connected collinear runs containing two or more lines.
    """
    axis_groups = build_axis_groups(
        straight_lines,
        settings["axis"],
        settings["angle_radians"]
    )

    merge_runs = []
    single_runs = []

    for group in axis_groups:
        runs = split_group_into_runs(
            group,
            settings["gap"]
        )

        for run in runs:
            if len(run["elements"]) > 1:
                merge_runs.append(run)
            else:
                single_runs.append(run)

    return merge_runs, single_runs, axis_groups


# ---------------------------------------------------------------------
# MODEL MODIFICATION
# ---------------------------------------------------------------------

def create_replacement(run):
    """Create one Detail Line covering an entire straight run."""
    start_point = (
        run["origin"]
        + run["direction"].Multiply(run["start"])
    )

    end_point = (
        run["origin"]
        + run["direction"].Multiply(run["end"])
    )

    if start_point.DistanceTo(end_point) <= MINIMUM_LINE_LENGTH:
        raise Exception("Replacement line is too short.")

    geometry = DB.Line.CreateBound(
        start_point,
        end_point
    )

    replacement = doc.Create.NewDetailCurve(
        doc.ActiveView,
        geometry
    )

    if replacement is None:
        raise Exception(
            "Revit did not create a replacement Detail Line."
        )

    if run["line_style"] is not None:
        replacement.LineStyle = run["line_style"]

    return replacement


def process_merge_runs(merge_runs):
    """
    Process each merge run in its own SubTransaction.

    A failed run is rolled back and its original lines remain unchanged.
    """
    results = {
        "created_ids": [],
        "removed_count": 0,
        "successful_runs": 0,
        "failed_runs": []
    }

    transaction = DB.Transaction(
        doc,
        "Simplify CAD Detail Lines"
    )

    try:
        transaction.Start()

        for run_number, run in enumerate(merge_runs, 1):
            subtransaction = DB.SubTransaction(doc)

            try:
                subtransaction.Start()

                replacement = create_replacement(run)

                for source_element in run["elements"]:
                    doc.Delete(source_element.Id)

                subtransaction.Commit()

                results["created_ids"].append(replacement.Id)
                results["removed_count"] += len(run["elements"])
                results["successful_runs"] += 1

            except Exception as run_error:
                try:
                    subtransaction.RollBack()
                except Exception:
                    pass

                results["failed_runs"].append({
                    "number": run_number,
                    "ids": [
                        id_value(item.Id)
                        for item in run["elements"]
                    ],
                    "error": str(run_error)
                })

                logger.warning(
                    "Run {0} failed: {1}".format(
                        run_number,
                        run_error
                    )
                )

        transaction.Commit()

    except Exception as transaction_error:
        try:
            transaction.RollBack()
        except Exception:
            pass

        forms.alert(
            "The transaction failed and all changes were rolled back."
            "\n\nRevit reported:\n{0}".format(transaction_error),
            title="Simplify CAD Detail Lines",
            warn_icon=True
        )

        script.exit()

    return results


def select_created_lines(created_ids):
    """Select all successfully created replacement lines."""
    selection = List[DB.ElementId]()

    for element_id in created_ids:
        selection.Add(element_id)

    uidoc.Selection.SetElementIds(selection)


# ---------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------

def print_clickable_elements(title, elements):
    """Print clickable Revit element IDs."""
    if not elements:
        return

    output.print_md("### {0}".format(title))

    for element in elements:
        output.print_md(
            "- {0}".format(output.linkify(element.Id))
        )


def print_report(classification,
                 settings,
                 axis_groups,
                 merge_runs,
                 single_runs,
                 results):
    """Print a detailed report in the pyRevit output panel."""
    created_count = len(results["created_ids"])

    net_reduction = (
        results["removed_count"] - created_count
    )

    report_rows = [
        ["Eligible straight Detail Lines",
         len(classification["straight"])],

        ["Collinear axes detected",
         len(axis_groups)],

        ["Mergeable straight runs",
         len(merge_runs)],

        ["Successfully merged runs",
         results["successful_runs"]],

        ["Original lines removed",
         results["removed_count"]],

        ["Replacement lines created",
         created_count],

        ["Net line reduction",
         net_reduction],

        ["Single straight lines unchanged",
         len(single_runs)],

        ["Arcs ignored",
         len(classification["arcs"])],

        ["Other Detail Curves ignored",
         len(classification["other_curves"])],

        ["Pinned straight lines ignored",
         len(classification["pinned"])],

        ["Grouped straight lines ignored",
         len(classification["grouped"])],

        ["Lines from another view ignored",
         len(classification["other_view"])],

        ["ImportInstances ignored",
         len(classification["import_instances"])],

        ["Other elements ignored",
         len(classification["other_elements"])],

        ["Failed runs left unchanged",
         len(results["failed_runs"])]
    ]

    output.print_md("# CAD Detail Line Cleanup Report")
    output.print_md(
        "**Tolerance:** {0}".format(settings["name"])
    )

    output.print_table(
        table_data=report_rows,
        columns=["Result", "Count"]
    )

    print_clickable_elements(
        "Ignored Arcs",
        classification["arcs"]
    )

    print_clickable_elements(
        "Ignored Other Detail Curves",
        classification["other_curves"]
    )

    print_clickable_elements(
        "Ignored Pinned Lines",
        classification["pinned"]
    )

    print_clickable_elements(
        "Ignored Grouped Lines",
        classification["grouped"]
    )

    if results["failed_runs"]:
        output.print_md("### Failed Merge Runs")

        for failure in results["failed_runs"]:
            output.print_md(
                "**Run {0}**".format(failure["number"])
            )

            output.print_md(
                "- Element IDs: `{0}`".format(
                    ", ".join(
                        str(element_id)
                        for element_id in failure["ids"]
                    )
                )
            )

            output.print_md(
                "- Error: `{0}`".format(failure["error"])
            )


def show_summary(classification,
                 axis_groups,
                 merge_runs,
                 single_runs,
                 results):
    """Show final cleanup counts."""
    created_count = len(results["created_ids"])

    net_reduction = (
        results["removed_count"] - created_count
    )

    message = (
        "CAD Detail Line cleanup complete.\n\n"
        "Eligible straight lines: {0}\n"
        "Straight axes detected: {1}\n"
        "Mergeable runs detected: {2}\n"
        "Successfully merged runs: {3}\n\n"
        "Original lines removed: {4}\n"
        "Replacement lines created: {5}\n"
        "Net line reduction: {6}\n\n"
        "Arcs ignored: {7}\n"
        "Other curves ignored: {8}\n"
        "Single lines unchanged: {9}\n"
        "Pinned lines ignored: {10}\n"
        "Grouped lines ignored: {11}\n"
        "Failed runs unchanged: {12}"
    ).format(
        len(classification["straight"]),
        len(axis_groups),
        len(merge_runs),
        results["successful_runs"],
        results["removed_count"],
        created_count,
        net_reduction,
        len(classification["arcs"]),
        len(classification["other_curves"]),
        len(single_runs),
        len(classification["pinned"]),
        len(classification["grouped"]),
        len(results["failed_runs"])
    )

    forms.alert(
        message,
        title="Simplify CAD Detail Lines"
    )


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

selected_elements = get_selected_elements()

if not selected_elements:
    forms.alert(
        "No elements were selected.",
        title="Simplify CAD Detail Lines",
        warn_icon=True
    )
    script.exit()


classification = classify_elements(selected_elements)
straight_lines = classification["straight"]


if len(straight_lines) < 2:
    message = (
        "At least two eligible straight Detail Lines are required."
        "\n\nStraight Detail Lines found: {0}"
        "\nArcs found and ignored: {1}"
        "\nOther curves found and ignored: {2}"
        "\nImportInstances found: {3}"
    ).format(
        len(straight_lines),
        len(classification["arcs"]),
        len(classification["other_curves"]),
        len(classification["import_instances"])
    )

    forms.alert(
        message,
        title="Simplify CAD Detail Lines",
        warn_icon=True
    )

    script.exit()


settings = choose_tolerance()


merge_runs, single_runs, axis_groups = find_merge_runs(
    straight_lines,
    settings
)


if not merge_runs:
    empty_results = {
        "created_ids": [],
        "removed_count": 0,
        "successful_runs": 0,
        "failed_runs": []
    }

    print_report(
        classification,
        settings,
        axis_groups,
        merge_runs,
        single_runs,
        empty_results
    )

    forms.alert(
        "No mergeable straight runs were found."
        "\n\nStraight Detail Lines: {0}"
        "\nStraight axes detected: {1}"
        "\nArcs ignored: {2}"
        "\n\nTry the Moderate tolerance if the CAD geometry "
        "contains small gaps or alignment errors.".format(
            len(straight_lines),
            len(axis_groups),
            len(classification["arcs"])
        ),
        title="Simplify CAD Detail Lines"
    )

    script.exit()


results = process_merge_runs(merge_runs)


if results["created_ids"]:
    select_created_lines(results["created_ids"])


print_report(
    classification,
    settings,
    axis_groups,
    merge_runs,
    single_runs,
    results
)


show_summary(
    classification,
    axis_groups,
    merge_runs,
    single_runs,
    results
)