# -*- coding: utf-8 -*-

"""
pyRevit Script: 100' Filled Region Circle Clipped to Floor

Purpose:
    Creates a 100'-0" radius Filled Region around each selected element,
    clipped to the footprint of a selected Floor.

Workflow:
    1. Select elements first.
    2. Run script.
    3. Choose Filled Region Type.
    4. Pick Floor used as clipping boundary.
    5. Script creates clipped filled regions in the active view.

Notes:
    - This does not physically cut the floor.
    - This creates view-specific Filled Regions.
    - Run from a plan-type view.
"""

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
import math


doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


# ============================================================
# USER SETTINGS
# ============================================================

RADIUS_FEET = 100.0
EXTRUSION_DEPTH = 1.0
MIN_CURVE_LENGTH = 0.0005


# ============================================================
# SELECTION FILTERS
# ============================================================

class FloorSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        if elem and elem.Category:
            return elem.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_Floors)
        return False

    def AllowReference(self, reference, position):
        return True


# ============================================================
# GENERAL HELPERS
# ============================================================

def safe_exit(message):
    forms.alert(message, exitscript=True)


def get_revit_element_name(elem):
    """
    Safely gets the name of Revit elements and element types in IronPython.
    Some ElementType.Name calls fail in pyRevit/IronPython, so use fallbacks.
    """
    if not elem:
        return "<None>"

    # IronPython-safe method for Element.Name
    try:
        name = DB.Element.Name.GetValue(elem)
        if name:
            return name
    except Exception:
        pass

    # Common fallback for ElementType names
    try:
        param = elem.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if param:
            name = param.AsString()
            if name:
                return name
    except Exception:
        pass

    # Secondary fallback
    try:
        param = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if param:
            name = param.AsString()
            if name:
                return name
    except Exception:
        pass

    # Last fallback
    try:
        return "ElementId {}".format(elem.Id.IntegerValue)
    except Exception:
        return "<Unnamed>"


def get_view_plane_z(active_view):
    """
    Returns the active view sketch plane elevation.
    Filled Region boundaries need to be in the active view plane.
    """
    try:
        if active_view.SketchPlane:
            return active_view.SketchPlane.GetPlane().Origin.Z
    except Exception:
        pass

    return 0.0


def project_point_to_z(point, z_value):
    return DB.XYZ(point.X, point.Y, z_value)


def get_element_center(elem, active_view):
    """
    Gets a practical center point for most elements.

    Priority:
        1. LocationPoint
        2. LocationCurve midpoint
        3. Bounding box center
    """
    try:
        loc = elem.Location

        if isinstance(loc, DB.LocationPoint):
            return loc.Point

        if isinstance(loc, DB.LocationCurve):
            return loc.Curve.Evaluate(0.5, True)

    except Exception:
        pass

    bbox = None

    try:
        bbox = elem.get_BoundingBox(active_view)
    except Exception:
        bbox = None

    if not bbox:
        try:
            bbox = elem.get_BoundingBox(None)
        except Exception:
            bbox = None

    if bbox:
        return DB.XYZ(
            (bbox.Min.X + bbox.Max.X) / 2.0,
            (bbox.Min.Y + bbox.Max.Y) / 2.0,
            (bbox.Min.Z + bbox.Max.Z) / 2.0
        )

    return None


# ============================================================
# CURVE HELPERS
# ============================================================

def curve_length(curve):
    try:
        return curve.Length
    except Exception:
        return 0.0


def make_line_if_valid(p1, p2):
    if p1.DistanceTo(p2) <= MIN_CURVE_LENGTH:
        return None
    return DB.Line.CreateBound(p1, p2)


def project_curve_to_z(curve, z_value):
    """
    Converts common curve types to the active view plane.
    Lines and arcs are preserved when possible.
    Other curve types are tessellated into line segments.
    """
    projected_curves = []

    try:
        if isinstance(curve, DB.Line):
            p0 = project_point_to_z(curve.GetEndPoint(0), z_value)
            p1 = project_point_to_z(curve.GetEndPoint(1), z_value)
            new_line = make_line_if_valid(p0, p1)

            if new_line:
                projected_curves.append(new_line)

            return projected_curves

        if isinstance(curve, DB.Arc):
            p0 = project_point_to_z(curve.GetEndPoint(0), z_value)
            p1 = project_point_to_z(curve.GetEndPoint(1), z_value)
            pm = project_point_to_z(curve.Evaluate(0.5, True), z_value)

            if p0.DistanceTo(p1) > MIN_CURVE_LENGTH:
                try:
                    new_arc = DB.Arc.Create(p0, p1, pm)
                    projected_curves.append(new_arc)
                    return projected_curves
                except Exception:
                    pass

        # Fallback for ellipses, splines, nurbs, etc.
        tess_points = list(curve.Tessellate())

        for i in range(len(tess_points) - 1):
            p0 = project_point_to_z(tess_points[i], z_value)
            p1 = project_point_to_z(tess_points[i + 1], z_value)
            new_line = make_line_if_valid(p0, p1)

            if new_line:
                projected_curves.append(new_line)

    except Exception as ex:
        logger.debug("Failed projecting curve: {}".format(ex))

    return projected_curves


def edge_array_to_projected_curve_loop(edge_array, z_value):
    """
    Converts a Revit EdgeArray into a CurveLoop on the active view plane.
    """
    loop = DB.CurveLoop()

    for edge in edge_array:
        try:
            source_curve = edge.AsCurve()
            projected_curves = project_curve_to_z(source_curve, z_value)

            for curve in projected_curves:
                if curve_length(curve) > MIN_CURVE_LENGTH:
                    loop.Append(curve)

        except Exception as ex:
            logger.debug("Failed converting edge to curve: {}".format(ex))

    try:
        if loop.IsOpen():
            return None
    except Exception:
        pass

    return loop


def reverse_curve_loop(curve_loop):
    """
    Reverses a CurveLoop by reversing order and each curve direction.
    """
    curves = list(curve_loop)
    reversed_loop = DB.CurveLoop()

    for curve in reversed(curves):
        reversed_loop.Append(curve.CreateReversed())

    return reversed_loop


def curve_loop_area_xy(curve_loop):
    """
    Approximate signed area using tessellated XY coordinates.
    Positive normally means counter-clockwise in XY.
    """
    pts = []

    for curve in curve_loop:
        try:
            tess = list(curve.Tessellate())
        except Exception:
            tess = []

        for p in tess:
            if not pts:
                pts.append(p)
            else:
                if pts[-1].DistanceTo(p) > MIN_CURVE_LENGTH:
                    pts.append(p)

    if len(pts) < 3:
        return 0.0

    if pts[0].DistanceTo(pts[-1]) <= MIN_CURVE_LENGTH:
        pts.pop()

    area = 0.0

    for i in range(len(pts)):
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        area += (p1.X * p2.Y) - (p2.X * p1.Y)

    return area / 2.0


def is_counterclockwise_xy(curve_loop):
    """
    Uses Revit native IsCounterclockwise when available.
    Falls back to tessellated area.
    """
    try:
        return curve_loop.IsCounterclockwise(DB.XYZ.BasisZ)
    except Exception:
        return curve_loop_area_xy(curve_loop) > 0.0


def normalize_profile_loops_for_extrusion(curve_loops):
    """
    For extrusion profiles:
        - Largest loop is treated as outer boundary.
        - Outer loop should be counter-clockwise.
        - Inner loops should be clockwise.
    """
    if not curve_loops:
        return []

    sorted_loops = sorted(
        curve_loops,
        key=lambda cl: abs(curve_loop_area_xy(cl)),
        reverse=True
    )

    normalized = []

    for index, curve_loop in enumerate(sorted_loops):
        loop_is_ccw = is_counterclockwise_xy(curve_loop)

        if index == 0:
            # Outer loop should be CCW.
            if not loop_is_ccw:
                curve_loop = reverse_curve_loop(curve_loop)
        else:
            # Inner loops should be CW.
            if loop_is_ccw:
                curve_loop = reverse_curve_loop(curve_loop)

        normalized.append(curve_loop)

    return normalized


def create_circle_curve_loop(center, radius, z_value):
    """
    Creates a closed circle CurveLoop using two Revit arcs.
    """
    c = project_point_to_z(center, z_value)

    arc_1 = DB.Arc.Create(
        c,
        radius,
        0.0,
        math.pi,
        DB.XYZ.BasisX,
        DB.XYZ.BasisY
    )

    arc_2 = DB.Arc.Create(
        c,
        radius,
        math.pi,
        2.0 * math.pi,
        DB.XYZ.BasisX,
        DB.XYZ.BasisY
    )

    loop = DB.CurveLoop()
    loop.Append(arc_1)
    loop.Append(arc_2)

    if not is_counterclockwise_xy(loop):
        loop = reverse_curve_loop(loop)

    return loop


# ============================================================
# FLOOR GEOMETRY HELPERS
# ============================================================

def get_top_planar_face_from_floor(floor):
    """
    Finds the largest upward-facing planar face of the selected floor.
    """
    options = DB.Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False

    geom = floor.get_Geometry(options)

    if not geom:
        return None

    best_face = None
    best_area = 0.0

    for geo_obj in geom:
        solid = geo_obj if isinstance(geo_obj, DB.Solid) else None

        if not solid:
            continue

        if solid.Faces.Size == 0:
            continue

        for face in solid.Faces:
            planar_face = face if isinstance(face, DB.PlanarFace) else None

            if not planar_face:
                continue

            try:
                normal = planar_face.FaceNormal
            except Exception:
                continue

            # Upward-facing top face
            if normal.Z > 0.90:
                try:
                    area = planar_face.Area
                except Exception:
                    area = 0.0

                if area > best_area:
                    best_area = area
                    best_face = planar_face

    return best_face


def get_floor_profile_loops_on_view_plane(floor, z_value):
    """
    Gets the floor top face boundary loops and projects them to the active view plane.
    """
    top_face = get_top_planar_face_from_floor(floor)

    if not top_face:
        return []

    loops = []

    for edge_array in top_face.EdgeLoops:
        loop = edge_array_to_projected_curve_loop(edge_array, z_value)

        if loop:
            try:
                if not loop.IsOpen():
                    loops.append(loop)
            except Exception:
                loops.append(loop)

    return normalize_profile_loops_for_extrusion(loops)


# ============================================================
# SOLID AND FILLED REGION HELPERS
# ============================================================

def make_extrusion_solid(profile_loops):
    """
    Creates a shallow extrusion solid from one or more closed CurveLoops.
    """
    loop_list = List[DB.CurveLoop]()

    for loop in profile_loops:
        loop_list.Add(loop)

    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        loop_list,
        DB.XYZ.BasisZ,
        EXTRUSION_DEPTH
    )


def boolean_intersection_solid(solid_a, solid_b):
    """
    Returns boolean intersection solid, or None if it fails.
    """
    try:
        return DB.BooleanOperationsUtils.ExecuteBooleanOperation(
            solid_a,
            solid_b,
            DB.BooleanOperationsType.Intersect
        )
    except Exception as ex:
        logger.debug("Boolean intersection failed: {}".format(ex))
        return None


def get_top_face_edge_loops_from_solid(solid):
    """
    Gets the top face edge loops from a boolean result solid.
    """
    if not solid:
        return []

    try:
        if solid.Faces.Size == 0:
            return []
    except Exception:
        return []

    best_face = None
    best_area = 0.0

    for face in solid.Faces:
        planar_face = face if isinstance(face, DB.PlanarFace) else None

        if not planar_face:
            continue

        try:
            normal = planar_face.FaceNormal
        except Exception:
            continue

        if normal.Z > 0.90:
            try:
                area = planar_face.Area
            except Exception:
                area = 0.0

            if area > best_area:
                best_area = area
                best_face = planar_face

    if not best_face:
        return []

    return list(best_face.EdgeLoops)


def build_filled_region_loops_from_intersection(intersection_solid, z_value):
    """
    Converts the top face of the intersected solid into FilledRegion boundary loops.
    """
    edge_loops = get_top_face_edge_loops_from_solid(intersection_solid)

    if not edge_loops:
        return []

    result_loops = []

    for edge_array in edge_loops:
        loop = edge_array_to_projected_curve_loop(edge_array, z_value)

        if loop:
            try:
                if not loop.IsOpen():
                    result_loops.append(loop)
            except Exception:
                result_loops.append(loop)

    return normalize_profile_loops_for_extrusion(result_loops)


def create_filled_region(doc, active_view, filled_region_type_id, curve_loops):
    """
    Creates one Filled Region from the supplied boundary loops.
    """
    loop_list = List[DB.CurveLoop]()

    for loop in curve_loops:
        loop_list.Add(loop)

    return DB.FilledRegion.Create(
        doc,
        filled_region_type_id,
        active_view.Id,
        loop_list
    )


# ============================================================
# UI HELPERS
# ============================================================

def choose_filled_region_type():
    """
    Lets the user select a Filled Region Type.
    Uses IronPython-safe name access.
    """
    region_types = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FilledRegionType)
        .ToElements()
    )

    if not region_types:
        safe_exit("No Filled Region Types were found in this project.")

    region_types = sorted(
        region_types,
        key=lambda rt: get_revit_element_name(rt).lower()
    )

    name_map = {}

    for rt in region_types:
        base_name = get_revit_element_name(rt)
        display_name = base_name

        if display_name in name_map:
            display_name = "{} [{}]".format(base_name, rt.Id.IntegerValue)

        name_map[display_name] = rt

    selected_name = forms.SelectFromList.show(
        sorted(name_map.keys()),
        title="Select Filled Region Type",
        multiselect=False
    )

    if not selected_name:
        script.exit()

    return name_map[selected_name]


# ============================================================
# MAIN
# ============================================================

def main():
    if view.IsTemplate:
        safe_exit("Active view is a view template. Run this from a normal plan view.")

    allowed_view_types = [
        DB.ViewType.FloorPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.EngineeringPlan,
        DB.ViewType.AreaPlan
    ]

    if view.ViewType not in allowed_view_types:
        safe_exit(
            "Run this from a plan-type view.\n\n"
            "Supported view types:\n"
            "- Floor Plan\n"
            "- Ceiling Plan\n"
            "- Engineering Plan\n"
            "- Area Plan"
        )

    selected_ids = list(uidoc.Selection.GetElementIds())

    if not selected_ids:
        safe_exit("Select one or more elements first, then run the script.")

    selected_elements = []

    for elem_id in selected_ids:
        elem = doc.GetElement(elem_id)
        if elem:
            selected_elements.append(elem)

    if not selected_elements:
        safe_exit("No valid selected elements were found.")

    filled_region_type = choose_filled_region_type()

    try:
        floor_ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            FloorSelectionFilter(),
            "Pick the Floor that clips the 100' filled region circles"
        )
    except Exception:
        script.exit()

    floor = doc.GetElement(floor_ref.ElementId)

    if not floor:
        safe_exit("No valid Floor was selected.")

    drawing_z = get_view_plane_z(view)

    floor_profile_loops = get_floor_profile_loops_on_view_plane(floor, drawing_z)

    if not floor_profile_loops:
        safe_exit(
            "Could not read the selected Floor boundary.\n\n"
            "Make sure the selected element is a native Revit Floor with valid horizontal top-face geometry."
        )

    try:
        floor_solid = make_extrusion_solid(floor_profile_loops)
    except Exception as ex:
        safe_exit(
            "Could not create a temporary floor clipping solid.\n\n"
            "Reason:\n{}".format(ex)
        )

    created_count = 0
    skipped_no_center = []
    skipped_no_intersection = []
    failed_elements = []

    with DB.Transaction(doc, "Create 100' Clipped Filled Region Circles") as t:
        t.Start()

        for elem in selected_elements:
            # Do not create a region around the clipping floor if it was part of the initial selection.
            if elem.Id == floor.Id:
                continue

            center = get_element_center(elem, view)

            if not center:
                skipped_no_center.append(elem.Id.IntegerValue)
                continue

            try:
                circle_loop = create_circle_curve_loop(center, RADIUS_FEET, drawing_z)
                circle_solid = make_extrusion_solid([circle_loop])

                intersection_solid = boolean_intersection_solid(floor_solid, circle_solid)

                if not intersection_solid:
                    skipped_no_intersection.append(elem.Id.IntegerValue)
                    continue

                try:
                    if intersection_solid.Volume <= 0.000001:
                        skipped_no_intersection.append(elem.Id.IntegerValue)
                        continue
                except Exception:
                    pass

                region_loops = build_filled_region_loops_from_intersection(
                    intersection_solid,
                    drawing_z
                )

                if not region_loops:
                    skipped_no_intersection.append(elem.Id.IntegerValue)
                    continue

                create_filled_region(
                    doc,
                    view,
                    filled_region_type.Id,
                    region_loops
                )

                created_count += 1

            except Exception as ex:
                logger.debug(
                    "Failed creating clipped filled region for element {}: {}".format(
                        elem.Id.IntegerValue,
                        ex
                    )
                )
                failed_elements.append(elem.Id.IntegerValue)

        t.Commit()

    message = (
        "100' clipped filled region circles complete.\n\n"
        "Selected elements processed: {}\n"
        "Filled Regions created: {}\n"
        "Radius: 100'-0\"\n"
        "Filled Region Type: {}\n\n"
        "Skipped, no center found: {}\n"
        "Skipped, no portion inside floor: {}\n"
        "Failed: {}"
    ).format(
        len(selected_elements),
        created_count,
        get_revit_element_name(filled_region_type),
        len(skipped_no_center),
        len(skipped_no_intersection),
        len(failed_elements)
    )

    if skipped_no_center:
        message += "\n\nNo center found element IDs:\n{}".format(
            ", ".join([str(x) for x in skipped_no_center])
        )

    if skipped_no_intersection:
        message += "\n\nNo clipped area inside floor element IDs:\n{}".format(
            ", ".join([str(x) for x in skipped_no_intersection])
        )

    if failed_elements:
        message += "\n\nFailed element IDs:\n{}".format(
            ", ".join([str(x) for x in failed_elements])
        )

    forms.alert(message, title="100' Clipped Filled Regions")


main()