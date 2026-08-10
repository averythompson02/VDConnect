# -*- coding: utf-8 -*-
"""
Dusty Robotics Sequential Control Layout Generator

Purpose:
- Select multiple individual Floor elements.
- Read each floor's Comments parameter as placement order.
- Process placements in numeric order.
- Place LaserTrackerLocation family instances.
- Place Control Location family instances.
- Reuse previously placed control points where possible.
- Draw only tracker-to-control detail lines.
- Do not draw coverage circles.

Required loaded families:
- LaserTrackerLocation
- Control Location

Important behavior:
- Tracker and control family instances are placed on the associated level of the active view.
- Offset/elevation parameters are set to 0'-0" where supported.
- Linked structural columns are treated as obstructions.
- Tracker and new control point candidate locations are kept at least 4 ft from placement area edges.
- Revit internal units are feet.

Author: PyRevit Script Expert
"""

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
import math


doc = revit.doc
uidoc = revit.uidoc
active_view = doc.ActiveView
output = script.get_output()
logger = script.get_logger()


LASER_TRACKER_FAMILY_NAME = "LaserTrackerLocation"
CONTROL_POINT_FAMILY_NAME = "Control Location"

# Fixed requirement from user.
FIXED_EDGE_CLEARANCE = 4.0


# ------------------------------------------------------------
# Selection filters
# ------------------------------------------------------------

class FloorSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return elem.Category and elem.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_Floors)
        except:
            return False

    def AllowReference(self, ref, point):
        return False


# ------------------------------------------------------------
# User input helpers
# ------------------------------------------------------------

def ask_float(prompt, default_value):
    raw = forms.ask_for_string(
        default=str(default_value),
        prompt=prompt,
        title="Dusty Sequential Control Layout"
    )

    if raw is None:
        script.exit()

    try:
        value = float(raw)
        if value <= 0:
            forms.alert("Value must be greater than zero.", exitscript=True)
        return value
    except:
        forms.alert("Invalid number: {0}".format(raw), exitscript=True)


def ask_yes_no(prompt, title="Dusty Sequential Control Layout"):
    return forms.alert(prompt, title=title, yes=True, no=True)


# ------------------------------------------------------------
# Level and family helpers
# ------------------------------------------------------------

def get_active_level(view):
    """
    Returns the associated level for the active view.
    Floor plans and engineering plans usually expose GenLevel.
    """
    try:
        if hasattr(view, "GenLevel") and view.GenLevel:
            return view.GenLevel
    except:
        pass

    try:
        p = view.get_Parameter(DB.BuiltInParameter.PLAN_VIEW_LEVEL)
        if p:
            level = doc.GetElement(p.AsElementId())
            if isinstance(level, DB.Level):
                return level
    except:
        pass

    levels = DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements()
    if levels:
        return sorted(levels, key=lambda x: x.Elevation)[0]

    return None


def get_level_z(level):
    if level:
        return level.Elevation
    return 0.0


def find_family_symbol_by_family_name(family_name):
    symbols = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()

    exact_family_matches = []
    type_name_matches = []

    for sym in symbols:
        try:
            if sym.Family and sym.Family.Name == family_name:
                exact_family_matches.append(sym)
            elif sym.Name == family_name:
                type_name_matches.append(sym)
        except:
            pass

    if exact_family_matches:
        return exact_family_matches[0]

    if type_name_matches:
        return type_name_matches[0]

    return None


def set_param_if_writable(elem, bip, value):
    try:
        p = elem.get_Parameter(bip)
        if p and not p.IsReadOnly:
            p.Set(value)
            return True
    except:
        pass

    return False


def set_lookup_param_if_writable(elem, names, value):
    for name in names:
        try:
            p = elem.LookupParameter(name)
            if p and not p.IsReadOnly:
                if p.StorageType == DB.StorageType.Double:
                    p.Set(float(value))
                    return True
                elif p.StorageType == DB.StorageType.String:
                    p.Set(str(value))
                    return True
                elif p.StorageType == DB.StorageType.Integer:
                    p.Set(int(value))
                    return True
                elif p.StorageType == DB.StorageType.ElementId and isinstance(value, DB.ElementId):
                    p.Set(value)
                    return True
        except:
            pass

    return False


def set_instance_mark(inst, mark_value):
    if inst is None:
        return

    if set_param_if_writable(inst, DB.BuiltInParameter.ALL_MODEL_MARK, mark_value):
        return

    set_lookup_param_if_writable(inst, ["Mark"], mark_value)


def set_instance_comments(inst, comment_value):
    if inst is None:
        return

    try:
        p = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p and not p.IsReadOnly:
            p.Set(comment_value)
            return
    except:
        pass

    set_lookup_param_if_writable(inst, ["Comments"], comment_value)


def force_instance_to_level_zero_offset(inst, level):
    """
    Best-effort setter.
    Some families expose level and offset parameters, some do not.
    This safely sets whatever is writable.
    """
    if inst is None or level is None:
        return

    level_param_candidates = [
        DB.BuiltInParameter.FAMILY_LEVEL_PARAM,
        DB.BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM,
        DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM,
        DB.BuiltInParameter.LEVEL_PARAM
    ]

    for bip in level_param_candidates:
        try:
            p = inst.get_Parameter(bip)
            if p and not p.IsReadOnly and p.StorageType == DB.StorageType.ElementId:
                p.Set(level.Id)
                break
        except:
            pass

    offset_param_candidates = [
        DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
        DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM,
        DB.BuiltInParameter.SCHEDULE_BASE_LEVEL_OFFSET_PARAM,
        DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM
    ]

    for bip in offset_param_candidates:
        try:
            p = inst.get_Parameter(bip)
            if p and not p.IsReadOnly and p.StorageType == DB.StorageType.Double:
                p.Set(0.0)
        except:
            pass

    set_lookup_param_if_writable(
        inst,
        ["Offset", "Elevation from Level", "Elevation", "Base Offset"],
        0.0
    )


def place_family_instance(symbol, xy_point, level, view, mark_value, comment_value):
    """
    Attempts placement as:
    1. View-based family, such as Generic Annotation or Detail Component.
    2. Level-based model family.
    3. Non-hosted model family fallback.
    """
    if symbol is None:
        return None

    if level is None:
        return None

    z_value = get_level_z(level)
    pt = DB.XYZ(xy_point[0], xy_point[1], z_value)

    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    inst = None

    # View-based family placement.
    try:
        inst = doc.Create.NewFamilyInstance(pt, symbol, view)
        set_instance_mark(inst, mark_value)
        set_instance_comments(inst, comment_value)
        force_instance_to_level_zero_offset(inst, level)
        return inst
    except:
        pass

    # Level-based model family placement.
    try:
        inst = doc.Create.NewFamilyInstance(
            pt,
            symbol,
            level,
            DB.Structure.StructuralType.NonStructural
        )
        set_instance_mark(inst, mark_value)
        set_instance_comments(inst, comment_value)
        force_instance_to_level_zero_offset(inst, level)
        return inst
    except:
        pass

    # Non-hosted model family fallback.
    try:
        inst = doc.Create.NewFamilyInstance(
            pt,
            symbol,
            DB.Structure.StructuralType.NonStructural
        )
        set_instance_mark(inst, mark_value)
        set_instance_comments(inst, comment_value)
        force_instance_to_level_zero_offset(inst, level)
        return inst
    except Exception as ex:
        try:
            fam_name = symbol.Family.Name
        except:
            fam_name = str(symbol.Id.IntegerValue)
        logger.warning("Could not place family {0}: {1}".format(fam_name, ex))

    return None


# ------------------------------------------------------------
# Parameter helpers
# ------------------------------------------------------------

def get_comments_value(elem):
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p:
            return p.AsString()
    except:
        pass

    try:
        p = elem.LookupParameter("Comments")
        if p:
            return p.AsString()
    except:
        pass

    return None


def parse_placement_order(floor):
    raw = get_comments_value(floor)

    if raw is None:
        return None

    raw = raw.strip()

    if not raw:
        return None

    try:
        return int(raw)
    except:
        return None


# ------------------------------------------------------------
# 2D geometry helpers
# ------------------------------------------------------------

def xyz_to_xy(p):
    return (p.X, p.Y)


def distance_xy(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def clean_loop(points, tol=0.001):
    cleaned = []

    for p in points:
        if not cleaned or distance_xy(cleaned[-1], p) > tol:
            cleaned.append(p)

    if len(cleaned) > 1 and distance_xy(cleaned[0], cleaned[-1]) < tol:
        cleaned.pop()

    return cleaned


def polygon_area(poly):
    if len(poly) < 3:
        return 0.0

    area = 0.0

    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1

    return area / 2.0


def point_in_polygon(pt, poly):
    x, y = pt
    inside = False

    if len(poly) < 3:
        return False

    j = len(poly) - 1

    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]

        if (yi > y) != (yj > y):
            denom = yj - yi
            if abs(denom) < 0.0000001:
                denom = 0.0000001

            x_cross = ((xj - xi) * (y - yi) / denom) + xi

            if x < x_cross:
                inside = not inside

        j = i

    return inside


def point_to_segment_distance(pt, a, b):
    px, py = pt
    ax, ay = a
    bx, by = b

    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay

    length_sq = vx * vx + vy * vy

    if length_sq < 0.0000001:
        return distance_xy(pt, a)

    t = (wx * vx + wy * vy) / length_sq

    if t < 0.0:
        return distance_xy(pt, a)

    if t > 1.0:
        return distance_xy(pt, b)

    proj = (ax + t * vx, ay + t * vy)
    return distance_xy(pt, proj)


def point_to_polygon_edges_distance(pt, poly):
    if len(poly) < 2:
        return 999999.0

    min_dist = 999999.0

    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        d = point_to_segment_distance(pt, a, b)

        if d < min_dist:
            min_dist = d

    return min_dist


def triangle_area(a, b, c):
    return abs(
        (
            a[0] * (b[1] - c[1]) +
            b[0] * (c[1] - a[1]) +
            c[0] * (a[1] - b[1])
        ) / 2.0
    )


def points_are_non_collinear(points, min_area):
    if len(points) < 3:
        return False

    n = len(points)

    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                if triangle_area(points[i], points[j], points[k]) >= min_area:
                    return True

    return False


def rect_contains_point(pt, rect):
    minx, miny, maxx, maxy = rect
    return minx <= pt[0] <= maxx and miny <= pt[1] <= maxy


def offset_rect(rect, offset):
    minx, miny, maxx, maxy = rect
    return (
        minx - offset,
        miny - offset,
        maxx + offset,
        maxy + offset
    )


def rects_overlap(a, b):
    a_minx, a_miny, a_maxx, a_maxy = a
    b_minx, b_miny, b_maxx, b_maxy = b

    return not (
        a_maxx < b_minx or
        a_minx > b_maxx or
        a_maxy < b_miny or
        a_miny > b_maxy
    )


def ccw(a, b, c):
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d):
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def segment_intersects_rect(a, b, rect):
    minx, miny, maxx, maxy = rect

    if rect_contains_point(a, rect) or rect_contains_point(b, rect):
        return True

    corners = [
        (minx, miny),
        (maxx, miny),
        (maxx, maxy),
        (minx, maxy)
    ]

    for i in range(4):
        c = corners[i]
        d = corners[(i + 1) % 4]

        if segments_intersect(a, b, c, d):
            return True

    return False


# ------------------------------------------------------------
# Floor region extraction
# ------------------------------------------------------------

def get_floor_top_face_loops(floor):
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.IncludeNonVisibleObjects = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine

    geom = floor.get_Geometry(opt)

    if not geom:
        return []

    top_faces = []
    max_z = -999999.0

    for geom_obj in geom:
        solid = geom_obj if isinstance(geom_obj, DB.Solid) else None

        if solid is None or solid.Faces.Size == 0:
            continue

        for face in solid.Faces:
            try:
                normal = face.ComputeNormal(DB.UV(0.5, 0.5))

                if normal.Z > 0.7:
                    bb = face.GetBoundingBox()
                    mid_uv = DB.UV(
                        (bb.Min.U + bb.Max.U) / 2.0,
                        (bb.Min.V + bb.Max.V) / 2.0
                    )
                    mid_pt = face.Evaluate(mid_uv)

                    if mid_pt.Z > max_z:
                        max_z = mid_pt.Z
                        top_faces = [face]
                    elif abs(mid_pt.Z - max_z) < 0.1:
                        top_faces.append(face)
            except:
                continue

    loops = []

    for face in top_faces:
        try:
            for edge_array in face.EdgeLoops:
                pts = []

                for edge in edge_array:
                    crv = edge.AsCurve()
                    tess = crv.Tessellate()

                    for p in tess:
                        pts.append(xyz_to_xy(p))

                cleaned = clean_loop(pts)

                if len(cleaned) >= 3 and abs(polygon_area(cleaned)) > 1.0:
                    loops.append(cleaned)

        except Exception as ex:
            logger.warning("Could not read floor loop for floor {0}: {1}".format(floor.Id, ex))

    return sorted(loops, key=lambda p: abs(polygon_area(p)), reverse=True)


def build_floor_region(floor, placement_order):
    loops = get_floor_top_face_loops(floor)

    if not loops:
        return None

    return {
        "floor_id": floor.Id.IntegerValue,
        "floor_element": floor,
        "order": placement_order,
        "outer": loops[0],
        "holes": loops[1:]
    }


def get_regions_bbox(regions):
    minx = 999999999.0
    miny = 999999999.0
    maxx = -999999999.0
    maxy = -999999999.0

    for region in regions:
        for p in region["outer"]:
            minx = min(minx, p[0])
            miny = min(miny, p[1])
            maxx = max(maxx, p[0])
            maxy = max(maxy, p[1])

    return minx, miny, maxx, maxy


def point_in_region(pt, region):
    if not point_in_polygon(pt, region["outer"]):
        return False

    for hole in region["holes"]:
        if point_in_polygon(pt, hole):
            return False

    return True


def point_in_regions(pt, regions):
    for region in regions:
        if point_in_region(pt, region):
            return True

    return False


def distance_to_region_boundaries(pt, region):
    if not point_in_region(pt, region):
        return 0.0

    min_dist = point_to_polygon_edges_distance(pt, region["outer"])

    for hole in region["holes"]:
        min_dist = min(min_dist, point_to_polygon_edges_distance(pt, hole))

    return min_dist


def valid_point_in_region(pt, region, obstruction_rects, edge_clearance):
    if not point_in_region(pt, region):
        return False

    if distance_to_region_boundaries(pt, region) < edge_clearance:
        return False

    for rect in obstruction_rects:
        if rect_contains_point(pt, rect):
            return False

    return True


def valid_point_in_any_region(pt, regions, obstruction_rects, edge_clearance):
    if not point_in_regions(pt, regions):
        return False

    for rect in obstruction_rects:
        if rect_contains_point(pt, rect):
            return False

    for region in regions:
        if point_in_region(pt, region):
            if distance_to_region_boundaries(pt, region) >= edge_clearance:
                return True

    return False


def line_of_sight_clear(a, b, allowed_regions, obstruction_rects, edge_clearance, sample_step):
    """
    Approximate line-of-sight:
    - Reject if the segment intersects an obstruction rectangle.
    - Sample along the line to confirm it stays inside allowed placed regions.
    """
    for rect in obstruction_rects:
        if segment_intersects_rect(a, b, rect):
            return False

    length = distance_xy(a, b)
    steps = int(math.ceil(length / sample_step))

    if steps < 2:
        steps = 2

    for i in range(steps + 1):
        t = float(i) / float(steps)
        pt = (
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t
        )

        if not valid_point_in_any_region(pt, allowed_regions, obstruction_rects, edge_clearance):
            return False

    return True


# ------------------------------------------------------------
# Linked structural column obstruction handling
# ------------------------------------------------------------

def get_bbox_corners(bb):
    return [
        DB.XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z),
        DB.XYZ(bb.Min.X, bb.Min.Y, bb.Max.Z),
        DB.XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z),
        DB.XYZ(bb.Min.X, bb.Max.Y, bb.Max.Z),
        DB.XYZ(bb.Max.X, bb.Min.Y, bb.Min.Z),
        DB.XYZ(bb.Max.X, bb.Min.Y, bb.Max.Z),
        DB.XYZ(bb.Max.X, bb.Max.Y, bb.Min.Z),
        DB.XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z)
    ]


def bbox_to_xy_rect_from_points(points):
    xs = [p.X for p in points]
    ys = [p.Y for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def get_linked_structural_column_rects(buffer_dist, slab_bbox):
    rects = []
    link_count = 0
    skipped_links = 0
    linked_column_count = 0

    link_instances = DB.FilteredElementCollector(doc) \
        .OfClass(DB.RevitLinkInstance) \
        .ToElements()

    for link_inst in link_instances:
        try:
            link_doc = link_inst.GetLinkDocument()

            if link_doc is None:
                skipped_links += 1
                continue

            link_count += 1
            transform = link_inst.GetTotalTransform()

            columns = DB.FilteredElementCollector(link_doc) \
                .OfCategory(DB.BuiltInCategory.OST_StructuralColumns) \
                .WhereElementIsNotElementType() \
                .ToElements()

            for col in columns:
                try:
                    bb = col.get_BoundingBox(None)

                    if bb is None:
                        continue

                    host_corners = [transform.OfPoint(p) for p in get_bbox_corners(bb)]
                    rect = bbox_to_xy_rect_from_points(host_corners)

                    if abs(rect[2] - rect[0]) < 0.01 or abs(rect[3] - rect[1]) < 0.01:
                        continue

                    rect = offset_rect(rect, buffer_dist)

                    if slab_bbox and not rects_overlap(rect, slab_bbox):
                        continue

                    rects.append(rect)
                    linked_column_count += 1

                except Exception as col_ex:
                    logger.warning("Could not process linked structural column: {0}".format(col_ex))

        except Exception as link_ex:
            logger.warning("Could not process Revit link {0}: {1}".format(link_inst.Id, link_ex))

    return rects, link_count, linked_column_count, skipped_links


# ------------------------------------------------------------
# Grid generation and tracker layout
# ------------------------------------------------------------

def generate_grid_points_for_region(region, obstruction_rects, spacing, edge_clearance, triangular):
    minx, miny, maxx, maxy = get_regions_bbox([region])
    pts = []

    y = miny
    row = 0

    while y <= maxy:
        x_offset = 0.5 * spacing if row % 2 else 0.0
        x = minx + x_offset

        while x <= maxx:
            pt = (x, y)

            if valid_point_in_region(pt, region, obstruction_rects, edge_clearance):
                pts.append(pt)

            x += spacing

        if triangular:
            y += spacing * 0.866
        else:
            y += spacing

        row += 1

    return pts


def greedy_tracker_cover(current_region, sample_points, tracker_candidates,
                         obstruction_rects, coverage_radius,
                         edge_clearance, los_sample_step):
    """
    Finds tracker setup points that cover the current placement area.
    A sample is covered only if it is within range and line-of-sight is clear.
    """
    uncovered = set(range(len(sample_points)))
    selected = []
    candidate_covers = []

    allowed_regions = [current_region]

    for c in tracker_candidates:
        covered = set()

        for i, sp in enumerate(sample_points):
            if distance_xy(c, sp) <= coverage_radius:
                if line_of_sight_clear(
                    c,
                    sp,
                    allowed_regions,
                    obstruction_rects,
                    edge_clearance,
                    los_sample_step
                ):
                    covered.add(i)

        if covered:
            candidate_covers.append((c, covered))

    while uncovered:
        best_candidate = None
        best_cover = set()

        for c, covered in candidate_covers:
            gain = covered.intersection(uncovered)

            if len(gain) > len(best_cover):
                best_candidate = c
                best_cover = gain

        if best_candidate is None or len(best_cover) == 0:
            break

        selected.append(best_candidate)
        uncovered = uncovered.difference(best_cover)

    remaining = [sample_points[i] for i in uncovered]
    return selected, remaining


# ------------------------------------------------------------
# Shared control optimization by placement sequence
# ------------------------------------------------------------

def get_visible_controls_for_station(station, controls, allowed_regions,
                                     obstruction_rects, coverage_radius,
                                     edge_clearance, los_sample_step):
    visible = []

    for control in controls:
        cp = control["point"]

        if distance_xy(station, cp) > coverage_radius:
            continue

        if line_of_sight_clear(
            station,
            cp,
            allowed_regions,
            obstruction_rects,
            edge_clearance,
            los_sample_step
        ):
            visible.append(control)

    return visible


def choose_best_three_controls(station, visible_controls, min_triangle_area):
    if len(visible_controls) < 3:
        return []

    best_triplet = None
    best_score = -999999.0

    n = len(visible_controls)

    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                triplet = [visible_controls[i], visible_controls[j], visible_controls[k]]
                pts = [x["point"] for x in triplet]
                area = triangle_area(pts[0], pts[1], pts[2])

                if area < min_triangle_area:
                    continue

                avg_dist = sum([distance_xy(station, p) for p in pts]) / 3.0

                # Favor spread and farther control points.
                score = area + avg_dist

                # Slight preference to reuse existing controls.
                reused_count = len([x for x in triplet if x.get("is_existing")])
                score += reused_count * 25.0

                if score > best_score:
                    best_score = score
                    best_triplet = triplet

    return best_triplet if best_triplet else []


def optimize_controls_for_placement(stations, existing_controls, new_control_candidates,
                                    current_order, current_region, allowed_regions,
                                    obstruction_rects, coverage_radius,
                                    edge_clearance, los_sample_step,
                                    min_triangle_area):
    """
    Existing controls are free.
    New controls are added only when required.

    Returns:
    - final_new_controls
    - final_assignments
    - unresolved_station_indices
    """

    station_assignments = {}
    unresolved = set()

    # First attempt each station with existing controls only.
    for station_index, station in enumerate(stations):
        visible_existing = get_visible_controls_for_station(
            station,
            existing_controls,
            allowed_regions,
            obstruction_rects,
            coverage_radius,
            edge_clearance,
            los_sample_step
        )

        best_existing = choose_best_three_controls(
            station,
            visible_existing,
            min_triangle_area
        )

        if best_existing:
            station_assignments[station_index] = best_existing
        else:
            station_assignments[station_index] = visible_existing
            unresolved.add(station_index)

    selected_new_controls = []
    selected_new_indices = set()

    # Precompute new candidate visibility to unresolved stations.
    candidate_visibility = {}

    for cp_index, cp in enumerate(new_control_candidates):
        visible_station_indices = []

        for station_index in unresolved:
            station = stations[station_index]

            if distance_xy(station, cp) <= coverage_radius:
                if line_of_sight_clear(
                    station,
                    cp,
                    allowed_regions,
                    obstruction_rects,
                    edge_clearance,
                    los_sample_step
                ):
                    visible_station_indices.append(station_index)

        if visible_station_indices:
            candidate_visibility[cp_index] = visible_station_indices

    iterations = 0
    max_iterations = 10000

    while unresolved and iterations < max_iterations:
        iterations += 1

        best_cp_index = None
        best_score = -999999.0

        for cp_index, station_indices in candidate_visibility.items():
            if cp_index in selected_new_indices:
                continue

            score = 0.0

            for station_index in station_indices:
                if station_index not in unresolved:
                    continue

                current_controls = station_assignments.get(station_index, [])

                trial_control = {
                    "point": new_control_candidates[cp_index],
                    "mark": None,
                    "placement_order": current_order,
                    "is_existing": False,
                    "is_new_candidate": True,
                    "candidate_index": cp_index
                }

                trial_controls = current_controls + [trial_control]
                trial_pts = [x["point"] for x in trial_controls]

                if len(current_controls) < 3:
                    score += 20.0

                if len(trial_pts) >= 3 and points_are_non_collinear(trial_pts, min_triangle_area):
                    score += 75.0

                score += 5.0

            # Prefer a candidate that helps many stations.
            score += len(station_indices) * 10.0

            if score > best_score:
                best_score = score
                best_cp_index = cp_index

        if best_cp_index is None or best_score <= 0:
            break

        selected_new_indices.add(best_cp_index)

        new_control = {
            "point": new_control_candidates[best_cp_index],
            "mark": None,
            "placement_order": current_order,
            "is_existing": False,
            "is_new_candidate": True,
            "candidate_index": best_cp_index
        }

        selected_new_controls.append(new_control)

        for station_index in candidate_visibility.get(best_cp_index, []):
            station_assignments[station_index].append(new_control)

        resolved_now = []

        for station_index in unresolved:
            station = stations[station_index]
            best_three = choose_best_three_controls(
                station,
                station_assignments.get(station_index, []),
                min_triangle_area
            )

            if best_three:
                station_assignments[station_index] = best_three
                resolved_now.append(station_index)

        for station_index in resolved_now:
            unresolved.remove(station_index)

    # Final trim for every station to the best 3 controls.
    final_assignments = {}

    for station_index, controls in station_assignments.items():
        station = stations[station_index]
        best_three = choose_best_three_controls(station, controls, min_triangle_area)

        if best_three:
            final_assignments[station_index] = best_three
        else:
            final_assignments[station_index] = controls[:3]

    # Keep only new controls actually used after final trimming.
    used_new_candidate_indices = set()

    for controls in final_assignments.values():
        for control in controls:
            if control.get("is_new_candidate"):
                used_new_candidate_indices.add(control.get("candidate_index"))

    final_new_controls = []

    for control in selected_new_controls:
        if control.get("candidate_index") in used_new_candidate_indices:
            final_new_controls.append(control)

    unresolved_station_indices = []

    for station_index, controls in final_assignments.items():
        pts = [x["point"] for x in controls]

        if len(pts) < 3 or not points_are_non_collinear(pts, min_triangle_area):
            unresolved_station_indices.append(station_index)

    return final_new_controls, final_assignments, unresolved_station_indices


# ------------------------------------------------------------
# Detail line and text helpers
# ------------------------------------------------------------

def make_xyz(pt, z_value):
    return DB.XYZ(pt[0], pt[1], z_value)


def draw_detail_line(view, a, b, z_value):
    """
    This is the only detail line creation in the script.
    It creates straight tracker-to-control pickup lines.
    No circles or arcs are created.
    """
    p1 = make_xyz(a, z_value)
    p2 = make_xyz(b, z_value)
    line = DB.Line.CreateBound(p1, p2)
    return doc.Create.NewDetailCurve(view, line)


def add_label(view, text, xy_point, z_value):
    try:
        text_type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType)
        options = DB.TextNoteOptions(text_type_id)
        options.HorizontalAlignment = DB.HorizontalTextAlignment.Left

        return DB.TextNote.Create(
            doc,
            view.Id,
            DB.XYZ(xy_point[0], xy_point[1], z_value),
            text,
            options
        )

    except Exception as ex:
        logger.warning("Could not create label {0}: {1}".format(text, ex))
        return None


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    if active_view.IsTemplate:
        forms.alert("Run this from a non-template plan view.", exitscript=True)

    allowed_views = [
        DB.ViewType.FloorPlan,
        DB.ViewType.EngineeringPlan,
        DB.ViewType.AreaPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.Detail,
        DB.ViewType.DraftingView
    ]

    if active_view.ViewType not in allowed_views:
        forms.alert(
            "Run this from a floor plan, engineering plan, area plan, detail, or drafting view.",
            exitscript=True
        )

    active_level = get_active_level(active_view)

    if active_level is None:
        forms.alert(
            "Could not determine the associated level of the active view.",
            exitscript=True
        )

    view_z = get_level_z(active_level)

    laser_symbol = find_family_symbol_by_family_name(LASER_TRACKER_FAMILY_NAME)
    control_symbol = find_family_symbol_by_family_name(CONTROL_POINT_FAMILY_NAME)

    missing = []

    if laser_symbol is None:
        missing.append(LASER_TRACKER_FAMILY_NAME)

    if control_symbol is None:
        missing.append(CONTROL_POINT_FAMILY_NAME)

    if missing:
        forms.alert(
            "Missing required family/type:\n\n{0}\n\n"
            "Load the family into the project, then run the script again.".format("\n".join(missing)),
            exitscript=True
        )

    forms.alert(
        "Select all placement floor areas.\n\n"
        "Each selected floor must have a numeric Comments value:\n"
        "1, 2, 3, etc.",
        title="Dusty Sequential Control Layout"
    )

    try:
        floor_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            FloorSelectionFilter(),
            "Select placement floor areas, then press Finish"
        )
    except:
        script.exit()

    selected_floors = [doc.GetElement(r.ElementId) for r in floor_refs]

    if not selected_floors:
        forms.alert("No floor elements selected.", exitscript=True)

    floor_order_data = []
    invalid_floors = []

    for floor in selected_floors:
        order = parse_placement_order(floor)

        if order is None:
            invalid_floors.append(floor)
        else:
            floor_order_data.append((order, floor))

    if invalid_floors:
        msg = "The following selected floors do not have a numeric Comments value:\n\n"

        for f in invalid_floors:
            msg += "Floor Id {0}, Comments = {1}\n".format(
                f.Id.IntegerValue,
                get_comments_value(f)
            )

        forms.alert(msg, exitscript=True)

    floor_order_data = sorted(
        floor_order_data,
        key=lambda x: (x[0], x[1].Id.IntegerValue)
    )

    duplicate_orders = {}

    for order, floor in floor_order_data:
        duplicate_orders.setdefault(order, []).append(floor.Id.IntegerValue)

    duplicate_messages = []

    for order, ids in duplicate_orders.items():
        if len(ids) > 1:
            duplicate_messages.append(
                "Order {0}: Floor Ids {1}".format(
                    order,
                    ", ".join([str(x) for x in ids])
                )
            )

    if duplicate_messages:
        continue_with_duplicates = ask_yes_no(
            "Multiple floors share the same placement order.\n\n"
            "{0}\n\n"
            "Continue and process matching orders by ElementId?".format("\n".join(duplicate_messages))
        )

        if not continue_with_duplicates:
            script.exit()

    coverage_radius = ask_float("Laser tracker/control visibility radius in feet", 100.0)
    tracker_candidate_spacing = ask_float("Tracker candidate spacing in feet", 70.0)
    slab_sample_spacing = ask_float("Slab coverage sample spacing in feet", 20.0)
    control_candidate_spacing = ask_float("Control point candidate spacing in feet", 20.0)
    obstruction_buffer = ask_float("Obstruction buffer around linked structural columns in feet", 3.0)
    min_triangle_area = ask_float("Minimum triangle area for 3 control points in square feet", 25.0)

    create_text_labels = ask_yes_no(
        "Create text labels for tracker and control marks?\n\n"
        "Choose No if the view is already busy."
    )

    regions = []
    skipped_geometry = []

    for order, floor in floor_order_data:
        region = build_floor_region(floor, order)

        if region:
            regions.append(region)
        else:
            skipped_geometry.append(floor.Id.IntegerValue)

    if not regions:
        forms.alert(
            "Could not extract usable top-face slab boundaries from the selected floor areas.",
            exitscript=True
        )

    all_slab_bbox = get_regions_bbox(regions)

    linked_column_rects, link_count, linked_column_count, skipped_links = get_linked_structural_column_rects(
        obstruction_buffer,
        all_slab_bbox
    )

    obstruction_rects = linked_column_rects

    los_sample_step = max(2.0, min(10.0, slab_sample_spacing / 2.0))

    global_controls = []
    all_tracker_records = []
    all_pickup_line_records = []
    placement_summary_rows = []

    global_control_counter = 0
    global_tracker_counter = 0

    # Plan all placements first.
    for placement_index, region in enumerate(regions):
        current_order = region["order"]

        # Controls can only be reused from areas whose placement order is already placed.
        placed_regions = []
        for r in regions:
            if r["order"] <= current_order:
                placed_regions.append(r)

        sample_points = generate_grid_points_for_region(
            region,
            obstruction_rects,
            slab_sample_spacing,
            FIXED_EDGE_CLEARANCE,
            triangular=False
        )

        tracker_candidates = generate_grid_points_for_region(
            region,
            obstruction_rects,
            tracker_candidate_spacing,
            FIXED_EDGE_CLEARANCE,
            triangular=True
        )

        control_candidates = generate_grid_points_for_region(
            region,
            obstruction_rects,
            control_candidate_spacing,
            FIXED_EDGE_CLEARANCE,
            triangular=True
        )

        selected_stations, uncovered_samples = greedy_tracker_cover(
            region,
            sample_points,
            tracker_candidates,
            obstruction_rects,
            coverage_radius,
            FIXED_EDGE_CLEARANCE,
            los_sample_step
        )

        if not selected_stations:
            placement_summary_rows.append([
                current_order,
                region["floor_id"],
                0,
                0,
                0,
                len(sample_points),
                len(uncovered_samples),
                "NO TRACKERS"
            ])
            continue

        new_controls, station_assignments, unresolved_stations = optimize_controls_for_placement(
            selected_stations,
            global_controls,
            control_candidates,
            current_order,
            region,
            placed_regions,
            obstruction_rects,
            coverage_radius,
            FIXED_EDGE_CLEARANCE,
            los_sample_step,
            min_triangle_area
        )

        # Assign global marks to new controls.
        for control in new_controls:
            global_control_counter += 1
            control["mark"] = "CP-{0:03d}".format(global_control_counter)
            control["is_existing"] = True
            control["is_new_candidate"] = False
            global_controls.append(control)

        # Replace assignment candidate objects with actual global control objects.
        candidate_index_to_global_control = {}

        for control in new_controls:
            candidate_index_to_global_control[control.get("candidate_index")] = control

        for station_index, controls in station_assignments.items():
            updated_controls = []

            for control in controls:
                if control.get("is_new_candidate"):
                    replacement = candidate_index_to_global_control.get(control.get("candidate_index"))
                    if replacement:
                        updated_controls.append(replacement)
                    else:
                        updated_controls.append(control)
                else:
                    updated_controls.append(control)

            station_assignments[station_index] = updated_controls

        # Build tracker and pickup line records.
        for local_station_index, station in enumerate(selected_stations):
            global_tracker_counter += 1
            tracker_mark = "P{0:02d}-LT-{1:02d}".format(
                current_order,
                local_station_index + 1
            )

            tracker_record = {
                "mark": tracker_mark,
                "point": station,
                "placement_order": current_order,
                "floor_id": region["floor_id"],
                "station_index": local_station_index,
                "global_index": global_tracker_counter,
                "unresolved": local_station_index in unresolved_stations
            }

            all_tracker_records.append(tracker_record)

            for control in station_assignments.get(local_station_index, []):
                if control.get("mark"):
                    all_pickup_line_records.append({
                        "tracker": tracker_record,
                        "control": control
                    })

        reused_count = 0
        new_count = len(new_controls)

        for station_index, controls in station_assignments.items():
            for control in controls:
                if control.get("placement_order") < current_order:
                    reused_count += 1

        placement_summary_rows.append([
            current_order,
            region["floor_id"],
            len(selected_stations),
            new_count,
            reused_count,
            len(sample_points),
            len(uncovered_samples),
            "CHECK" if unresolved_stations else "OK"
        ])

    placed_tracker_count = 0
    placed_control_count = 0
    pickup_line_count = 0
    label_count = 0

    # Write to Revit.
    tg = DB.TransactionGroup(doc, "Dusty Sequential Control Layout")
    tg.Start()

    try:
        t = DB.Transaction(doc, "Place Dusty Sequential Control Layout")
        t.Start()

        # Place each shared control once.
        for control in global_controls:
            comment_value = "Dusty Control, Placement {0}".format(
                control["placement_order"]
            )

            inst = place_family_instance(
                control_symbol,
                control["point"],
                active_level,
                active_view,
                control["mark"],
                comment_value
            )

            if inst:
                placed_control_count += 1

            if create_text_labels:
                label = add_label(
                    active_view,
                    control["mark"],
                    (control["point"][0] + 2.0, control["point"][1] + 2.0),
                    view_z
                )

                if label:
                    label_count += 1

        # Place tracker locations.
        for tracker in all_tracker_records:
            comment_value = "Dusty Tracker, Placement {0}, FloorId {1}".format(
                tracker["placement_order"],
                tracker["floor_id"]
            )

            inst = place_family_instance(
                laser_symbol,
                tracker["point"],
                active_level,
                active_view,
                tracker["mark"],
                comment_value
            )

            if inst:
                placed_tracker_count += 1

            if create_text_labels:
                label = add_label(
                    active_view,
                    tracker["mark"],
                    (tracker["point"][0] + 3.0, tracker["point"][1] + 3.0),
                    view_z
                )

                if label:
                    label_count += 1

        # Draw only tracker-to-control pickup lines.
        # No coverage circles are created anywhere in this script.
        for record in all_pickup_line_records:
            tracker = record["tracker"]
            control = record["control"]

            try:
                draw_detail_line(
                    active_view,
                    tracker["point"],
                    control["point"],
                    view_z
                )
                pickup_line_count += 1
            except Exception as line_ex:
                logger.warning(
                    "Could not draw pickup line {0} to {1}: {2}".format(
                        tracker["mark"],
                        control["mark"],
                        line_ex
                    )
                )

        t.Commit()
        tg.Assimilate()

    except Exception as ex:
        logger.error(ex)

        try:
            t.RollBack()
        except:
            pass

        tg.RollBack()

        forms.alert(
            "Failed to create Dusty sequential control layout.\n\n{0}".format(ex),
            exitscript=True
        )

    # Reporting.
    output.print_md("## Dusty Sequential Control Layout Summary")
    output.print_md("- Active view associated level: **{0}**".format(active_level.Name))
    output.print_md("- Family placement elevation: **0'-0\" offset from associated level**")
    output.print_md("- Selected placement floor areas: **{0}**".format(len(selected_floors)))
    output.print_md("- Placement areas processed: **{0}**".format(len(regions)))
    output.print_md("- Floors skipped due to geometry extraction: **{0}**".format(len(skipped_geometry)))
    output.print_md("- Linked Revit files scanned: **{0}**".format(link_count))
    output.print_md("- Linked Revit files skipped or unloaded: **{0}**".format(skipped_links))
    output.print_md("- Linked structural columns used as obstructions: **{0}**".format(linked_column_count))
    output.print_md("- Edge clearance used for tracker/control placement: **{0} ft**".format(FIXED_EDGE_CLEARANCE))
    output.print_md("- Tracker locations placed: **{0}**".format(placed_tracker_count))
    output.print_md("- Shared control locations placed: **{0}**".format(placed_control_count))
    output.print_md("- Tracker-to-control pickup detail lines created: **{0}**".format(pickup_line_count))
    output.print_md("- Coverage circles created: **0**")
    output.print_md("- Text labels created: **{0}**".format(label_count))

    if skipped_geometry:
        output.print_md("")
        output.print_md("### Floors Skipped Due to Geometry Extraction")
        for floor_id in skipped_geometry:
            output.print_md("- Floor Id: `{0}`".format(floor_id))

    output.print_md("")
    output.print_md("### Placement Summary")

    output.print_table(
        table_data=placement_summary_rows,
        columns=[
            "Placement",
            "Floor Id",
            "Trackers",
            "New Controls",
            "Reused Control Uses",
            "Samples",
            "Uncovered Samples",
            "Status"
        ]
    )

    output.print_md("")
    output.print_md("### Tracker Control Assignments")

    assignment_rows = []

    for tracker in all_tracker_records:
        cp_marks = []

        for record in all_pickup_line_records:
            if record["tracker"] == tracker:
                cp_marks.append(record["control"]["mark"])

        assignment_rows.append([
            tracker["placement_order"],
            tracker["mark"],
            tracker["floor_id"],
            ", ".join(cp_marks),
            "CHECK" if tracker["unresolved"] else "OK"
        ])

    output.print_table(
        table_data=assignment_rows,
        columns=[
            "Placement",
            "Tracker",
            "Floor Id",
            "Control Points Picked Up",
            "Status"
        ]
    )

    forms.alert(
        "Dusty sequential control layout created.\n\n"
        "Tracker locations: {0}\n"
        "Shared control points: {1}\n"
        "Pickup lines: {2}\n"
        "Coverage circles: 0\n"
        "Linked structural columns used: {3}".format(
            placed_tracker_count,
            placed_control_count,
            pickup_line_count,
            linked_column_count
        ),
        title="Dusty Sequential Control Layout"
    )


main()