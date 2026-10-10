# -*- coding: utf-8 -*-
"""
Convert Selected DWG Geometry to Revit Detail Lines

Converts line-based geometry from an ImportInstance into native
view-specific Revit Detail Curves.

Supported source geometry:
    DB.Line
    DB.Arc
    DB.PolyLine
    Other DB.Curve types, tessellated into straight segments
    Nested GeometryInstance objects / CAD blocks

Result:
    Native Revit Detail Lines and Detail Arcs in the active view.

Important:
    - Select an imported or linked DWG.
    - Run from the view where the DWG is visible.
    - Imported CAD polylines are recreated as individual straight pieces.
    - All geometry is projected onto the active view plane.
    - Duplicate geometry is suppressed.

IronPython 2.7 compatible.
"""

from pyrevit import revit, DB, UI, forms, script
from System.Collections.Generic import List
import math


# ============================================================================
# Revit context
# ============================================================================

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

output = script.get_output()
logger = script.get_logger()


# ============================================================================
# Settings
# ============================================================================

SHORT_TOLERANCE = doc.Application.ShortCurveTolerance

# Used only for duplicate detection.
# Revit internal units are feet.
DUPLICATE_TOLERANCE = 0.0005

# Marks generated Detail Curves in Comments.
GENERATED_MARKER = "DWG_REDRAW_DETAIL_CURVE"


# ============================================================================
# General helpers
# ============================================================================

def stop_script(message):
    forms.alert(
        message,
        title="DWG to Detail Lines",
        exitscript=True
    )


def safe_text(value):
    if value is None:
        return ""

    try:
        return unicode(value)
    except NameError:
        return str(value)


def id_value(element_id):
    if element_id is None:
        return -1

    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def is_valid_detail_view(target_view):
    """
    Reject view types where Detail Curves cannot reasonably be created.
    """

    if target_view is None:
        return False

    try:
        if target_view.IsTemplate:
            return False
    except Exception:
        return False

    invalid_types = [
        DB.ViewType.ProjectBrowser,
        DB.ViewType.SystemBrowser,
        DB.ViewType.Internal,
        DB.ViewType.Report,
        DB.ViewType.Schedule,
        DB.ViewType.ColumnSchedule,
        DB.ViewType.PanelSchedule,
        DB.ViewType.Rendering,
        DB.ViewType.Walkthrough
    ]

    return target_view.ViewType not in invalid_types


def get_import_name(import_instance):
    if import_instance is None:
        return "<Unknown DWG>"

    try:
        return safe_text(import_instance.Name)
    except Exception:
        pass

    try:
        import_type = doc.GetElement(
            import_instance.GetTypeId()
        )

        if import_type is not None:
            parameter = import_type.get_Parameter(
                DB.BuiltInParameter.SYMBOL_NAME_PARAM
            )

            if parameter is not None:
                value = parameter.AsString()

                if value:
                    return safe_text(value)

    except Exception:
        pass

    return "ImportInstance {0}".format(
        id_value(import_instance.Id)
    )


# ============================================================================
# ImportInstance selection
# ============================================================================

class ImportInstanceSelectionFilter(UI.Selection.ISelectionFilter):

    def AllowElement(self, element):
        return isinstance(
            element,
            DB.ImportInstance
        )

    def AllowReference(self, reference, position):
        return False


def get_preselected_import_instance():
    selected_ids = uidoc.Selection.GetElementIds()

    for selected_id in selected_ids:
        element = doc.GetElement(
            selected_id
        )

        if isinstance(element, DB.ImportInstance):
            return element

    return None


def select_import_instance():
    """
    Use a preselected ImportInstance when available.

    Otherwise prompt the user to pick one.
    """

    preselected = get_preselected_import_instance()

    if preselected is not None:
        return preselected

    try:
        reference = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            ImportInstanceSelectionFilter(),
            "Select the imported or linked DWG to redraw"
        )

    except Exception:
        script.exit()

    selected_element = doc.GetElement(
        reference.ElementId
    )

    if not isinstance(selected_element, DB.ImportInstance):
        stop_script(
            "The selected element is not an imported or linked DWG."
        )

    return selected_element


# ============================================================================
# Detail Line style selection
# ============================================================================

def collect_detail_line_styles():
    styles = {}

    try:
        lines_category = doc.Settings.Categories.get_Item(
            DB.BuiltInCategory.OST_Lines
        )
    except Exception:
        return styles

    if lines_category is None:
        return styles

    try:
        subcategories = lines_category.SubCategories
    except Exception:
        return styles

    for subcategory in subcategories:
        try:
            graphics_style = subcategory.GetGraphicsStyle(
                DB.GraphicsStyleType.Projection
            )

            if graphics_style is not None:
                styles[subcategory.Name] = graphics_style

        except Exception:
            continue

    return styles


def select_detail_line_style(styles):
    style_names = sorted(
        styles.keys()
    )

    if not style_names:
        stop_script(
            "No Revit Detail Line styles were found in this project."
        )

    selected_name = forms.SelectFromList.show(
        style_names,
        title="Select Revit Line Style for Redrawn DWG Geometry",
        button_name="Use Line Style",
        multiselect=False,
        width=750,
        height=650
    )

    if not selected_name:
        script.exit()

    return styles[selected_name]


# ============================================================================
# View-plane projection
# ============================================================================

def get_or_create_sketch_plane(target_view):
    """
    Return the view's SketchPlane or create one.

    Must be called inside an open transaction if creation is required.
    """

    try:
        existing_plane = target_view.SketchPlane

        if existing_plane is not None:
            return existing_plane

    except Exception:
        pass

    reference_plane = DB.Plane.CreateByNormalAndOrigin(
        target_view.ViewDirection,
        target_view.Origin
    )

    return DB.SketchPlane.Create(
        doc,
        reference_plane
    )


def project_point_to_plane(point, sketch_plane):
    plane = sketch_plane.GetPlane()

    vector_to_point = point - plane.Origin

    signed_distance = vector_to_point.DotProduct(
        plane.Normal
    )

    return point - plane.Normal.Multiply(
        signed_distance
    )


def project_line_to_plane(source_line, sketch_plane):
    start = project_point_to_plane(
        source_line.GetEndPoint(0),
        sketch_plane
    )

    end = project_point_to_plane(
        source_line.GetEndPoint(1),
        sketch_plane
    )

    if start.DistanceTo(end) <= SHORT_TOLERANCE:
        return None

    return DB.Line.CreateBound(
        start,
        end
    )


def project_arc_to_plane(source_arc, sketch_plane):
    start = project_point_to_plane(
        source_arc.GetEndPoint(0),
        sketch_plane
    )

    end = project_point_to_plane(
        source_arc.GetEndPoint(1),
        sketch_plane
    )

    middle = project_point_to_plane(
        source_arc.Evaluate(0.5, True),
        sketch_plane
    )

    if start.DistanceTo(end) <= SHORT_TOLERANCE:
        return None

    try:
        return DB.Arc.Create(
            start,
            end,
            middle
        )
    except Exception:
        return None


def points_to_line_segments(points, sketch_plane):
    """
    Convert a sequence of source points into projected straight pieces.
    """

    segments = []

    if points is None:
        return segments

    point_list = list(points)

    if len(point_list) < 2:
        return segments

    projected_points = []

    for point in point_list:
        try:
            projected_points.append(
                project_point_to_plane(
                    point,
                    sketch_plane
                )
            )
        except Exception:
            continue

    if len(projected_points) < 2:
        return segments

    for index in range(len(projected_points) - 1):
        start = projected_points[index]
        end = projected_points[index + 1]

        if start.DistanceTo(end) <= SHORT_TOLERANCE:
            continue

        try:
            segments.append(
                DB.Line.CreateBound(
                    start,
                    end
                )
            )
        except Exception:
            continue

    return segments


def convert_curve_to_view_geometry(source_curve, sketch_plane):
    """
    Convert a Revit Curve from the DWG geometry into view-plane geometry.

    Lines and circular arcs remain native Lines and Arcs.
    Other curves are tessellated into straight pieces.
    """

    results = []

    if isinstance(source_curve, DB.Line):
        projected_line = project_line_to_plane(
            source_curve,
            sketch_plane
        )

        if projected_line is not None:
            results.append(projected_line)

        return results

    if isinstance(source_curve, DB.Arc):
        projected_arc = project_arc_to_plane(
            source_curve,
            sketch_plane
        )

        if projected_arc is not None:
            results.append(projected_arc)

        return results

    try:
        tessellated_points = source_curve.Tessellate()

        return points_to_line_segments(
            tessellated_points,
            sketch_plane
        )

    except Exception:
        return results


def convert_polyline_to_view_geometry(polyline, sketch_plane):
    """
    Recreate every straight piece of a CAD PolyLine.

    This is the key difference from the earlier converter.
    DB.PolyLine is a GeometryObject, not a DB.Curve.
    """

    try:
        coordinates = polyline.GetCoordinates()
    except Exception:
        return []

    return points_to_line_segments(
        coordinates,
        sketch_plane
    )


# ============================================================================
# Recursive DWG geometry traversal
# ============================================================================

def collect_view_curves_from_geometry(
        geometry_element,
        sketch_plane,
        output_curves,
        statistics,
        recursion_depth=0):
    """
    Recursively traverse imported CAD geometry.

    GetInstanceGeometry() returns the geometry for that block instance in
    the import instance's current coordinate context, avoiding the need to
    manually apply the block transform again.

    A recursion-depth guard prevents malformed or unexpectedly nested CAD
    blocks from causing an infinite recursion.
    """

    if geometry_element is None:
        return

    if recursion_depth > 50:
        statistics["depth_skipped"] += 1
        return

    for geometry_object in geometry_element:
        try:
            statistics["geometry_objects"] += 1

            # Native Revit Curve from CAD geometry.
            if isinstance(geometry_object, DB.Curve):
                statistics["source_curves"] += 1

                converted_curves = convert_curve_to_view_geometry(
                    geometry_object,
                    sketch_plane
                )

                output_curves.extend(
                    converted_curves
                )

                continue

            # CAD straight pieces are frequently returned as PolyLine.
            if isinstance(geometry_object, DB.PolyLine):
                statistics["source_polylines"] += 1

                converted_segments = (
                    convert_polyline_to_view_geometry(
                        geometry_object,
                        sketch_plane
                    )
                )

                statistics["polyline_segments"] += len(
                    converted_segments
                )

                output_curves.extend(
                    converted_segments
                )

                continue

            # Nested CAD block or DWG instance.
            if isinstance(
                geometry_object,
                DB.GeometryInstance
            ):
                statistics["geometry_instances"] += 1

                try:
                    instance_geometry = (
                        geometry_object.GetInstanceGeometry()
                    )
                except Exception:
                    statistics["instance_failures"] += 1
                    continue

                collect_view_curves_from_geometry(
                    instance_geometry,
                    sketch_plane,
                    output_curves,
                    statistics,
                    recursion_depth + 1
                )

                continue

            # Solids, meshes, points, text and hatch geometry are ignored.
            statistics["unsupported_objects"] += 1

        except Exception as geometry_error:
            statistics["geometry_failures"] += 1

            logger.debug(
                "DWG geometry object skipped: {0}".format(
                    safe_text(geometry_error)
                )
            )


# ============================================================================
# Duplicate suppression
# ============================================================================

def rounded_coordinate(value):
    return int(
        round(value / DUPLICATE_TOLERANCE)
    )


def point_key(point):
    return (
        rounded_coordinate(point.X),
        rounded_coordinate(point.Y),
        rounded_coordinate(point.Z)
    )


def line_key(line):
    start_key = point_key(
        line.GetEndPoint(0)
    )

    end_key = point_key(
        line.GetEndPoint(1)
    )

    if start_key <= end_key:
        ordered_start = start_key
        ordered_end = end_key
    else:
        ordered_start = end_key
        ordered_end = start_key

    return (
        "LINE",
        ordered_start,
        ordered_end
    )


def arc_key(arc):
    start_key = point_key(
        arc.GetEndPoint(0)
    )

    end_key = point_key(
        arc.GetEndPoint(1)
    )

    middle_key = point_key(
        arc.Evaluate(0.5, True)
    )

    if start_key <= end_key:
        ordered_start = start_key
        ordered_end = end_key
    else:
        ordered_start = end_key
        ordered_end = start_key

    return (
        "ARC",
        ordered_start,
        middle_key,
        ordered_end
    )


def geometry_key(curve):
    if isinstance(curve, DB.Line):
        return line_key(curve)

    if isinstance(curve, DB.Arc):
        return arc_key(curve)

    return None


def remove_duplicate_geometry(curves):
    unique_curves = []
    seen_keys = set()
    duplicate_count = 0

    for curve in curves:
        key = geometry_key(curve)

        if key is None:
            unique_curves.append(curve)
            continue

        if key in seen_keys:
            duplicate_count += 1
            continue

        seen_keys.add(key)
        unique_curves.append(curve)

    return unique_curves, duplicate_count


# ============================================================================
# Generated-element tracking
# ============================================================================

def mark_generated_detail_curve(detail_curve):
    try:
        parameter = detail_curve.get_Parameter(
            DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS
        )

        if parameter is None:
            return

        if parameter.IsReadOnly:
            return

        parameter.Set(
            GENERATED_MARKER
        )

    except Exception:
        pass


# ============================================================================
# Validation
# ============================================================================

if not is_valid_detail_view(view):
    stop_script(
        "Open a graphical 2D view that supports Detail Lines before "
        "running this tool."
    )

if doc.IsReadOnly:
    stop_script(
        "The current Revit document is read-only."
    )


# ============================================================================
# Select DWG and target style
# ============================================================================

import_instance = select_import_instance()

import_name = get_import_name(
    import_instance
)

line_styles = collect_detail_line_styles()

target_line_style = select_detail_line_style(
    line_styles
)


# ============================================================================
# Read DWG geometry
# ============================================================================

geometry_options = DB.Options()

try:
    geometry_options.View = view
except Exception:
    pass

try:
    geometry_options.IncludeNonVisibleObjects = False
except Exception:
    pass

try:
    geometry_options.ComputeReferences = False
except Exception:
    pass


try:
    source_geometry = import_instance.get_Geometry(
        geometry_options
    )
except Exception as geometry_error:
    stop_script(
        "The DWG geometry could not be read.\n\n{0}".format(
            safe_text(geometry_error)
        )
    )

if source_geometry is None:
    stop_script(
        "The selected DWG returned no geometry in the active view."
    )


# ============================================================================
# Create projected geometry inside a temporary transaction
#
# A SketchPlane may need to be created before geometry can be projected.
# The same transaction will create the final Detail Curves.
# ============================================================================

created_ids = []
created_count = 0
failed_count = 0
duplicate_count = 0

statistics = {
    "geometry_objects": 0,
    "geometry_instances": 0,
    "source_curves": 0,
    "source_polylines": 0,
    "polyline_segments": 0,
    "unsupported_objects": 0,
    "geometry_failures": 0,
    "instance_failures": 0,
    "depth_skipped": 0
}


transaction = DB.Transaction(
    doc,
    "Redraw DWG as Detail Lines"
)

transaction.Start()

try:
    sketch_plane = get_or_create_sketch_plane(
        view
    )

    pending_curves = []

    collect_view_curves_from_geometry(
        source_geometry,
        sketch_plane,
        pending_curves,
        statistics
    )

    if not pending_curves:
        transaction.RollBack()

        stop_script(
            "The DWG contained geometry, but no usable Lines, Arcs, "
            "or PolyLine segments were found."
        )

    unique_curves, duplicate_count = remove_duplicate_geometry(
        pending_curves
    )

    proceed = forms.alert(
        "DWG: {0}\n\n"
        "Source curves: {1}\n"
        "Source polylines: {2}\n"
        "Straight polyline pieces: {3}\n"
        "Detail curves to create: {4}\n"
        "Duplicates suppressed: {5}\n\n"
        "Create the Revit Detail Lines now?".format(
            import_name,
            statistics["source_curves"],
            statistics["source_polylines"],
            statistics["polyline_segments"],
            len(unique_curves),
            duplicate_count
        ),
        title="DWG to Detail Lines",
        yes=True,
        no=True
    )

    if not proceed:
        transaction.RollBack()
        script.exit()

    for curve_geometry in unique_curves:
        try:
            detail_curve = doc.Create.NewDetailCurve(
                view,
                curve_geometry
            )

            detail_curve.LineStyle = target_line_style

            mark_generated_detail_curve(
                detail_curve
            )

            created_ids.append(
                detail_curve.Id
            )

            created_count += 1

        except Exception as creation_error:
            failed_count += 1

            logger.debug(
                "Detail Curve creation failed: {0}".format(
                    safe_text(creation_error)
                )
            )

    transaction.Commit()

except Exception as transaction_error:
    try:
        transaction.RollBack()
    except Exception:
        pass

    logger.exception(
        transaction_error
    )

    stop_script(
        "The DWG linework could not be redrawn.\n\n{0}".format(
            safe_text(transaction_error)
        )
    )


# ============================================================================
# Select and zoom to new Detail Curves
# ============================================================================

if created_ids:
    selection_ids = List[DB.ElementId]()

    for created_id in created_ids:
        selection_ids.Add(
            created_id
        )

    try:
        uidoc.Selection.SetElementIds(
            selection_ids
        )
    except Exception:
        pass

    try:
        uidoc.ShowElements(
            selection_ids
        )
    except Exception:
        try:
            uidoc.ShowElements(
                created_ids[0]
            )
        except Exception:
            pass


# ============================================================================
# Optional source-DWG deletion
# ============================================================================

dwg_deleted = False

if created_count > 0:
    delete_source = forms.alert(
        "The new Detail Lines have been created and selected.\n\n"
        "Delete the source DWG from this Revit model?",
        title="DWG to Detail Lines",
        yes=True,
        no=True
    )

    if delete_source:
        delete_transaction = DB.Transaction(
            doc,
            "Delete Converted DWG"
        )

        delete_transaction.Start()

        try:
            doc.Delete(
                import_instance.Id
            )

            delete_transaction.Commit()
            dwg_deleted = True

        except Exception as delete_error:
            delete_transaction.RollBack()

            forms.alert(
                "The Detail Lines were created, but the DWG could not "
                "be deleted.\n\n{0}".format(
                    safe_text(delete_error)
                ),
                title="DWG to Detail Lines"
            )


# ============================================================================
# Report
# ============================================================================

output.print_md(
    "# DWG to Revit Detail Lines"
)

output.print_md(
    "**DWG:** {0}".format(
        import_name
    )
)

output.print_md(
    "**Active view:** {0}".format(
        safe_text(view.Name)
    )
)

summary_rows = [
    [
        "Geometry objects inspected",
        statistics["geometry_objects"]
    ],
    [
        "Nested geometry instances",
        statistics["geometry_instances"]
    ],
    [
        "Source Curve objects",
        statistics["source_curves"]
    ],
    [
        "Source PolyLine objects",
        statistics["source_polylines"]
    ],
    [
        "Straight PolyLine pieces",
        statistics["polyline_segments"]
    ],
    [
        "Duplicates suppressed",
        duplicate_count
    ],
    [
        "Detail Curves created",
        created_count
    ],
    [
        "Detail Curves failed",
        failed_count
    ],
    [
        "Unsupported geometry objects",
        statistics["unsupported_objects"]
    ],
    [
        "Geometry traversal failures",
        statistics["geometry_failures"]
    ]
]

output.print_table(
    table_data=summary_rows,
    columns=[
        "Result",
        "Count"
    ],
    title="Conversion Summary"
)


forms.alert(
    "DWG conversion complete.\n\n"
    "Detail Curves created: {0}\n"
    "Straight PolyLine pieces found: {1}\n"
    "Duplicates suppressed: {2}\n"
    "Failed curves: {3}\n"
    "Source DWG deleted: {4}\n\n"
    "The new Detail Curves are selected and Revit has attempted "
    "to zoom to them.".format(
        created_count,
        statistics["polyline_segments"],
        duplicate_count,
        failed_count,
        "Yes" if dwg_deleted else "No"
    ),
    title="DWG to Detail Lines"
)