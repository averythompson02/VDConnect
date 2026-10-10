# -*- coding: utf-8 -*-
"""
Grouped Linked Element Footprints

Creates one reusable Detail Group Type for every unique linked Family Type
in the user's selection.

Workflow:
1. Select a detail line style.
2. Select linked FamilyInstances.
3. Extract visible solid geometry.
4. Project the geometry onto the active view plane.
5. Calculate the convex outer perimeter.
6. Create one Detail Group Type per linked Family Type.
7. Place the same group type at every matching linked instance.
8. Rotate each group instance to match the linked family orientation.

Editing one generated detail group updates all matching footprints.

Designed for:
- Air Terminals
- Mechanical Equipment
- Electrical Fixtures
- Plumbing Fixtures
- Generic Models
- Specialty Equipment
- Most point-based linked FamilyInstances

Compatible with:
- pyRevit
- Revit 2022+
- IronPython and CPython engines
"""

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List

import math
import re


doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------

GROUP_NAME_PREFIX = "LINKED FOOTPRINT"

GEOMETRY_DETAIL_LEVEL = DB.ViewDetailLevel.Fine

# Revit internal volume units are cubic feet.
# Increase slightly if tiny family solids distort an outline.
MINIMUM_SOLID_VOLUME = 0.0

# 1/256 inch in feet.
POINT_TOLERANCE = 1.0 / (256.0 * 12.0)

# 1/64 inch in feet.
MINIMUM_LINE_LENGTH = 1.0 / (64.0 * 12.0)

COLLINEAR_TOLERANCE = 0.000001

# Use the FamilySymbol bounding box when no solid geometry is available.
USE_BOUNDING_BOX_FALLBACK = True

# Add the linked document title to each generated group type name.
INCLUDE_LINK_NAME_IN_GROUP_NAME = True


# ----------------------------------------------------------------------
# VIEW VALIDATION
# ----------------------------------------------------------------------

def is_supported_view(active_view):
    """Return True when the active view can contain detail curves."""

    if active_view is None:
        return False

    if active_view.IsTemplate:
        return False

    supported_view_types = [
        DB.ViewType.FloorPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.EngineeringPlan,
        DB.ViewType.AreaPlan,
        DB.ViewType.Section,
        DB.ViewType.Elevation,
        DB.ViewType.Detail,
        DB.ViewType.DraftingView
    ]

    return active_view.ViewType in supported_view_types


# ----------------------------------------------------------------------
# REVIT VERSION HELPERS
# ----------------------------------------------------------------------

def element_id_value(element_id):
    """Return the numerical value of an ElementId."""

    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


# ----------------------------------------------------------------------
# SAFE GROUP NAMES
# ----------------------------------------------------------------------

INVALID_GROUP_NAME_CHARACTERS = re.compile(
    r'[\\/:{}\[\]\|;<>?"\*`~\r\n\t]'
)


def sanitize_name(value):
    """
    Create a conservative Revit-safe name.

    The complete assembled group name is passed through this function,
    including the family, type, and linked document names.
    """

    if value is None:
        value = ""

    try:
        value = str(value)
    except Exception:
        value = "Unnamed"

    # Normalize punctuation sometimes found in consultant content.
    replacements = {
        u"\u2013": "-",       # en dash
        u"\u2014": "-",       # em dash
        u"\u2018": "'",       # left single quote
        u"\u2019": "'",       # right single quote
        u"\u201c": "'",       # left double quote
        u"\u201d": "'",       # right double quote
        u"\u00a0": " "        # nonbreaking space
    }

    for old_character, new_character in replacements.items():
        value = value.replace(
            old_character,
            new_character
        )

    # Replace known invalid characters.
    value = INVALID_GROUP_NAME_CHARACTERS.sub(
        " ",
        value
    )

    # Remove remaining ASCII control characters.
    cleaned_characters = []

    for character in value:
        try:
            if ord(character) >= 32:
                cleaned_characters.append(character)
        except Exception:
            continue

    value = "".join(cleaned_characters)

    # Collapse whitespace.
    value = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    # Collapse repeated hyphens.
    value = re.sub(
        r"-{2,}",
        "-",
        value
    )

    # Avoid terminal punctuation that Revit can reject.
    value = value.strip(" .-_")

    if not value:
        value = "Unnamed"

    return value[:180].rstrip(" .-_")


def get_symbol_type_name(family_symbol):
    """Return the Family Type name."""

    if family_symbol is None:
        return "Unnamed Type"

    try:
        parameter = family_symbol.get_Parameter(
            DB.BuiltInParameter.SYMBOL_NAME_PARAM
        )

        if parameter:
            value = parameter.AsString()

            if value:
                return value
    except Exception:
        pass

    try:
        if family_symbol.Name:
            return family_symbol.Name
    except Exception:
        pass

    return "Unnamed Type"


def get_family_name(family_symbol):
    """Return the Family name."""

    if family_symbol is None:
        return "Unnamed Family"

    try:
        family = family_symbol.Family

        if family and family.Name:
            return family.Name
    except Exception:
        pass

    return "Unnamed Family"


def build_group_name(link_document, family_symbol):
    """Build and sanitize the complete Detail Group Type name."""

    name_parts = [
        GROUP_NAME_PREFIX,
        get_family_name(family_symbol),
        get_symbol_type_name(family_symbol)
    ]

    if INCLUDE_LINK_NAME_IN_GROUP_NAME:
        try:
            link_name = link_document.Title
        except Exception:
            link_name = "Linked Model"

        name_parts.append(link_name)

    return sanitize_name(
        " - ".join(name_parts)
    )


def make_unique_group_name(base_name, used_names):
    """Return a unique and sanitized group name."""

    base_name = sanitize_name(base_name)

    if base_name not in used_names:
        return base_name

    number = 2

    while True:
        suffix = " {}".format(number)

        trimmed_base = base_name[
            :180 - len(suffix)
        ].rstrip(" .-_")

        candidate = sanitize_name(
            "{}{}".format(
                trimmed_base,
                suffix
            )
        )

        if candidate not in used_names:
            return candidate

        number += 1


def set_safe_group_type_name(group_type, requested_name, used_names):
    """
    Set a safe group type name.

    If the descriptive name is still rejected, a simple numerical fallback
    is used so group creation can continue.
    """

    safe_name = make_unique_group_name(
        requested_name,
        used_names
    )

    try:
        group_type.Name = safe_name
        return safe_name

    except Exception as naming_error:
        logger.warning(
            "Revit rejected group name '{}': {}".format(
                safe_name,
                naming_error
            )
        )

    fallback_name = make_unique_group_name(
        "Linked Footprint {}".format(
            element_id_value(group_type.Id)
        ),
        used_names
    )

    group_type.Name = fallback_name

    return fallback_name


def get_family_type_key(link_document, family_symbol):
    """
    Return the unique key used to match instances across the selection.

    The linked document is included so equally named types from separate
    linked documents remain separate.
    """

    try:
        symbol_key = family_symbol.UniqueId
    except Exception:
        symbol_key = str(
            element_id_value(family_symbol.Id)
        )

    try:
        document_key = link_document.PathName
    except Exception:
        document_key = None

    if not document_key:
        try:
            document_key = link_document.Title
        except Exception:
            document_key = "Linked Document"

    return "{}|{}".format(
        document_key,
        symbol_key
    )


# ----------------------------------------------------------------------
# LINE STYLE SELECTION
# ----------------------------------------------------------------------

class LineStyleOption(object):
    """Line style display wrapper for the pyRevit picker."""

    def __init__(self, category, graphics_style):
        self.category = category
        self.graphics_style = graphics_style
        self.name = category.Name

    def __str__(self):
        return self.name


def get_detail_line_styles():
    """Return available Projection line-style GraphicsStyles."""

    styles = []

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

            if graphics_style is None:
                continue

            styles.append(
                LineStyleOption(
                    subcategory,
                    graphics_style
                )
            )

        except Exception:
            continue

    return sorted(
        styles,
        key=lambda item: item.name.lower()
    )


def select_line_style():
    """Prompt for the desired footprint detail line style."""

    styles = get_detail_line_styles()

    if not styles:
        forms.alert(
            "No usable detail line styles were found in this project.",
            title="Grouped Linked Footprints",
            exitscript=True
        )

    selected_style = forms.SelectFromList.show(
        styles,
        title="Select Footprint Line Style",
        button_name="Use Line Style",
        multiselect=False,
        width=500
    )

    if selected_style is None:
        script.exit()

    return selected_style.graphics_style


# ----------------------------------------------------------------------
# ACTIVE VIEW COORDINATE SYSTEM
# ----------------------------------------------------------------------

view_origin = view.Origin
view_right = view.RightDirection.Normalize()
view_up = view.UpDirection.Normalize()
view_normal = view.ViewDirection.Normalize()


def project_point_to_view_plane(point):
    """Project a host-coordinate point onto the active view plane."""

    vector = point - view_origin
    distance = vector.DotProduct(view_normal)

    return point - view_normal.Multiply(distance)


def point_to_view_xy(point):
    """Convert a host XYZ point into active-view 2D coordinates."""

    projected_point = project_point_to_view_plane(point)
    vector = projected_point - view_origin

    return (
        vector.DotProduct(view_right),
        vector.DotProduct(view_up)
    )


def view_xy_to_point(point_2d):
    """Convert active-view 2D coordinates into a host XYZ point."""

    return (
        view_origin +
        view_right.Multiply(point_2d[0]) +
        view_up.Multiply(point_2d[1])
    )


def direction_angle_in_view(direction):
    """Return a host direction's signed angle in the active view plane."""

    planar_direction = (
        direction -
        view_normal.Multiply(
            direction.DotProduct(view_normal)
        )
    )

    if planar_direction.GetLength() < 0.000001:
        return 0.0

    planar_direction = planar_direction.Normalize()

    x_component = planar_direction.DotProduct(view_right)
    y_component = planar_direction.DotProduct(view_up)

    return math.atan2(
        y_component,
        x_component
    )


def normalize_angle(angle):
    """Normalize an angle to between -pi and +pi."""

    while angle > math.pi:
        angle -= 2.0 * math.pi

    while angle < -math.pi:
        angle += 2.0 * math.pi

    return angle


def rotate_2d(vector_2d, angle):
    """Rotate a 2D vector."""

    cosine = math.cos(angle)
    sine = math.sin(angle)

    return (
        vector_2d[0] * cosine - vector_2d[1] * sine,
        vector_2d[0] * sine + vector_2d[1] * cosine
    )


# ----------------------------------------------------------------------
# TRANSFORM HELPERS
# ----------------------------------------------------------------------

def multiply_transforms(parent_transform, child_transform):
    """Combine child and parent transforms."""

    if parent_transform is None:
        return child_transform

    if child_transform is None:
        return parent_transform

    return parent_transform.Multiply(child_transform)


def get_link_transform(link_instance):
    """Return the linked-model-to-host transform."""

    try:
        return link_instance.GetTotalTransform()
    except Exception:
        return link_instance.GetTransform()


def get_family_instance_origin(link_instance, family_instance):
    """Return the linked FamilyInstance origin in host coordinates."""

    link_transform = get_link_transform(link_instance)

    try:
        instance_transform = family_instance.GetTransform()
        link_origin = instance_transform.Origin

        return link_transform.OfPoint(link_origin)
    except Exception:
        pass

    try:
        location = family_instance.Location

        if isinstance(location, DB.LocationPoint):
            return link_transform.OfPoint(location.Point)
    except Exception:
        pass

    return None


def get_family_x_direction(link_instance, family_instance):
    """Return the FamilyInstance local X direction in host coordinates."""

    link_transform = get_link_transform(link_instance)

    try:
        instance_transform = family_instance.GetTransform()

        link_direction = instance_transform.OfVector(
            DB.XYZ.BasisX
        )

        return link_transform.OfVector(
            link_direction
        )
    except Exception:
        pass

    try:
        location = family_instance.Location

        if isinstance(location, DB.LocationPoint):
            angle = location.Rotation

            link_direction = DB.XYZ(
                math.cos(angle),
                math.sin(angle),
                0.0
            )

            return link_transform.OfVector(
                link_direction
            )
    except Exception:
        pass

    return view_right


# ----------------------------------------------------------------------
# SOLID GEOMETRY EXTRACTION
# ----------------------------------------------------------------------

def add_solid_vertices(solid, transform, output_points):
    """Triangulate a solid and collect transformed mesh vertices."""

    if solid is None:
        return

    try:
        if solid.Faces.Size == 0:
            return
    except Exception:
        return

    try:
        if solid.Volume <= MINIMUM_SOLID_VOLUME:
            return
    except Exception:
        # Some otherwise valid Revit solids fail when Volume is evaluated.
        pass

    for face in solid.Faces:
        try:
            mesh = face.Triangulate()
        except Exception:
            continue

        if mesh is None:
            continue

        for vertex_index in range(mesh.NumVertices):
            try:
                vertex = mesh.get_Vertex(vertex_index)

                if transform is not None:
                    vertex = transform.OfPoint(vertex)

                output_points.append(vertex)

            except Exception:
                continue


def collect_geometry_vertices(
        geometry_element,
        accumulated_transform,
        output_points,
        recursion_depth=0
):
    """Recursively collect vertices from solids and nested geometry."""

    if geometry_element is None:
        return

    if recursion_depth > 20:
        return

    for geometry_object in geometry_element:

        if isinstance(geometry_object, DB.Solid):
            add_solid_vertices(
                geometry_object,
                accumulated_transform,
                output_points
            )
            continue

        if isinstance(geometry_object, DB.GeometryInstance):
            try:
                combined_transform = multiply_transforms(
                    accumulated_transform,
                    geometry_object.Transform
                )

                symbol_geometry = (
                    geometry_object.GetSymbolGeometry()
                )

                collect_geometry_vertices(
                    symbol_geometry,
                    combined_transform,
                    output_points,
                    recursion_depth + 1
                )

            except Exception as nested_error:
                logger.debug(
                    "Could not process nested geometry: {}".format(
                        nested_error
                    )
                )

            continue

        if isinstance(geometry_object, DB.Mesh):
            try:
                for vertex_index in range(
                        geometry_object.NumVertices
                ):
                    vertex = geometry_object.get_Vertex(
                        vertex_index
                    )

                    if accumulated_transform is not None:
                        vertex = accumulated_transform.OfPoint(
                            vertex
                        )

                    output_points.append(vertex)

            except Exception:
                continue


def get_host_solid_points(link_instance, linked_element):
    """Return visible solid vertices in host coordinates."""

    options = DB.Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    options.DetailLevel = GEOMETRY_DETAIL_LEVEL

    try:
        geometry_element = linked_element.get_Geometry(
            options
        )
    except Exception:
        return []

    if geometry_element is None:
        return []

    host_points = []

    collect_geometry_vertices(
        geometry_element,
        get_link_transform(link_instance),
        host_points
    )

    return host_points


# ----------------------------------------------------------------------
# BOUNDING BOX FALLBACK
# ----------------------------------------------------------------------

def get_symbol_bbox_host_points(link_instance, family_instance):
    """Return transformed FamilySymbol bounding-box corner points."""

    try:
        family_symbol = family_instance.Symbol
        bounding_box = family_symbol.get_BoundingBox(None)
        instance_transform = family_instance.GetTransform()
    except Exception:
        return []

    if bounding_box is None:
        return []

    link_transform = get_link_transform(link_instance)

    minimum = bounding_box.Min
    maximum = bounding_box.Max
    middle_z = (minimum.Z + maximum.Z) * 0.5

    symbol_points = [
        DB.XYZ(minimum.X, minimum.Y, middle_z),
        DB.XYZ(maximum.X, minimum.Y, middle_z),
        DB.XYZ(maximum.X, maximum.Y, middle_z),
        DB.XYZ(minimum.X, maximum.Y, middle_z)
    ]

    host_points = []

    for symbol_point in symbol_points:
        try:
            link_point = instance_transform.OfPoint(
                symbol_point
            )

            host_point = link_transform.OfPoint(
                link_point
            )

            host_points.append(host_point)

        except Exception:
            continue

    return host_points


# ----------------------------------------------------------------------
# CONVEX HULL
# ----------------------------------------------------------------------

def projected_point_key(point_2d):
    """Return a tolerance-based point key."""

    return (
        int(round(point_2d[0] / POINT_TOLERANCE)),
        int(round(point_2d[1] / POINT_TOLERANCE))
    )


def deduplicate_points(points_2d):
    """Remove duplicate projected points."""

    unique_points = {}

    for point in points_2d:
        key = projected_point_key(point)

        if key not in unique_points:
            unique_points[key] = point

    return list(unique_points.values())


def cross_product_2d(origin, point_a, point_b):
    """Return the signed two-dimensional cross product."""

    return (
        (point_a[0] - origin[0]) *
        (point_b[1] - origin[1]) -
        (point_a[1] - origin[1]) *
        (point_b[0] - origin[0])
    )


def convex_hull(points_2d):
    """Calculate a convex hull using Andrew's monotonic chain."""

    points = deduplicate_points(points_2d)

    if len(points) < 3:
        return []

    points = sorted(
        points,
        key=lambda point: (point[0], point[1])
    )

    lower_hull = []

    for point in points:
        while (
            len(lower_hull) >= 2 and
            cross_product_2d(
                lower_hull[-2],
                lower_hull[-1],
                point
            ) <= COLLINEAR_TOLERANCE
        ):
            lower_hull.pop()

        lower_hull.append(point)

    upper_hull = []

    for point in reversed(points):
        while (
            len(upper_hull) >= 2 and
            cross_product_2d(
                upper_hull[-2],
                upper_hull[-1],
                point
            ) <= COLLINEAR_TOLERANCE
        ):
            upper_hull.pop()

        upper_hull.append(point)

    return lower_hull[:-1] + upper_hull[:-1]


def distance_2d(point_a, point_b):
    """Return distance between two 2D points."""

    delta_x = point_b[0] - point_a[0]
    delta_y = point_b[1] - point_a[1]

    return math.sqrt(
        delta_x * delta_x +
        delta_y * delta_y
    )


def point_to_segment_distance(point, segment_start, segment_end):
    """Return the shortest 2D distance from a point to a segment."""

    segment_x = segment_end[0] - segment_start[0]
    segment_y = segment_end[1] - segment_start[1]

    length_squared = (
        segment_x * segment_x +
        segment_y * segment_y
    )

    if length_squared <= 0.0:
        return distance_2d(
            point,
            segment_start
        )

    parameter = (
        (point[0] - segment_start[0]) * segment_x +
        (point[1] - segment_start[1]) * segment_y
    ) / length_squared

    parameter = max(
        0.0,
        min(1.0, parameter)
    )

    closest_point = (
        segment_start[0] + parameter * segment_x,
        segment_start[1] + parameter * segment_y
    )

    return distance_2d(
        point,
        closest_point
    )


def remove_collinear_points(hull_points):
    """Remove unnecessary nearly collinear hull points."""

    if len(hull_points) <= 3:
        return hull_points

    cleaned_points = list(hull_points)
    changed = True

    while changed and len(cleaned_points) > 3:
        changed = False
        new_points = []
        point_count = len(cleaned_points)

        for index in range(point_count):
            previous_point = cleaned_points[
                (index - 1) % point_count
            ]

            current_point = cleaned_points[index]

            next_point = cleaned_points[
                (index + 1) % point_count
            ]

            distance = point_to_segment_distance(
                current_point,
                previous_point,
                next_point
            )

            if distance <= POINT_TOLERANCE:
                changed = True
                continue

            new_points.append(current_point)

        if len(new_points) >= 3:
            cleaned_points = new_points
        else:
            break

    return cleaned_points


# ----------------------------------------------------------------------
# OUTLINE DATA
# ----------------------------------------------------------------------

def get_outline_data(link_instance, family_instance):
    """Calculate the world outline, instance origin, and instance angle."""

    host_points = get_host_solid_points(
        link_instance,
        family_instance
    )

    method = "Solid projection"

    if len(host_points) < 3 and USE_BOUNDING_BOX_FALLBACK:
        host_points = get_symbol_bbox_host_points(
            link_instance,
            family_instance
        )

        method = "Bounding-box fallback"

    if len(host_points) < 3:
        return None

    projected_points = [
        point_to_view_xy(point)
        for point in host_points
    ]

    hull = convex_hull(projected_points)
    hull = remove_collinear_points(hull)

    if len(hull) < 3:
        return None

    host_origin = get_family_instance_origin(
        link_instance,
        family_instance
    )

    if host_origin is None:
        minimum_x = min(point[0] for point in hull)
        maximum_x = max(point[0] for point in hull)
        minimum_y = min(point[1] for point in hull)
        maximum_y = max(point[1] for point in hull)

        origin_2d = (
            (minimum_x + maximum_x) * 0.5,
            (minimum_y + maximum_y) * 0.5
        )
    else:
        origin_2d = point_to_view_xy(host_origin)

    host_x_direction = get_family_x_direction(
        link_instance,
        family_instance
    )

    angle = direction_angle_in_view(
        host_x_direction
    )

    return {
        "hull": hull,
        "origin": origin_2d,
        "angle": angle,
        "method": method
    }


# ----------------------------------------------------------------------
# DETAIL CURVES AND GROUPS
# ----------------------------------------------------------------------

def create_detail_curves(outline_points, graphics_style):
    """Create detail curves from a closed 2D outline."""

    curve_ids = []
    point_count = len(outline_points)

    for index in range(point_count):
        start_2d = outline_points[index]
        end_2d = outline_points[
            (index + 1) % point_count
        ]

        if distance_2d(
                start_2d,
                end_2d
        ) < MINIMUM_LINE_LENGTH:
            continue

        start_point = view_xy_to_point(start_2d)
        end_point = view_xy_to_point(end_2d)

        detail_line = DB.Line.CreateBound(
            start_point,
            end_point
        )

        detail_curve = doc.Create.NewDetailCurve(
            view,
            detail_line
        )

        if graphics_style is not None:
            detail_curve.LineStyle = graphics_style

        curve_ids.append(detail_curve.Id)

    return curve_ids


def create_group_type(
        outline_points,
        graphics_style,
        requested_name,
        used_names
):
    """Create a detail group from the first selected instance outline."""

    curve_ids = create_detail_curves(
        outline_points,
        graphics_style
    )

    if len(curve_ids) < 3:
        raise Exception(
            "Fewer than three usable detail lines were created."
        )

    group_member_ids = List[DB.ElementId]()

    for curve_id in curve_ids:
        group_member_ids.Add(curve_id)

    group_instance = doc.Create.NewGroup(
        group_member_ids
    )

    group_type = group_instance.GroupType

    assigned_name = set_safe_group_type_name(
        group_type,
        requested_name,
        used_names
    )

    return (
        group_instance,
        group_type,
        assigned_name
    )


def get_group_location_2d(group_instance):
    """Return the group insertion point in active-view coordinates."""

    try:
        location = group_instance.Location

        if isinstance(location, DB.LocationPoint):
            return point_to_view_xy(
                location.Point
            )
    except Exception:
        pass

    try:
        bounding_box = group_instance.get_BoundingBox(view)

        if bounding_box is not None:
            midpoint = (
                bounding_box.Min +
                bounding_box.Max
            ).Multiply(0.5)

            return point_to_view_xy(midpoint)
    except Exception:
        pass

    return None


def rotate_group(group_instance, rotation_center_2d, angle):
    """Rotate a group around the active-view normal."""

    angle = normalize_angle(angle)

    if abs(angle) < 0.0000001:
        return

    rotation_center = view_xy_to_point(
        rotation_center_2d
    )

    rotation_axis = DB.Line.CreateBound(
        rotation_center,
        rotation_center + view_normal
    )

    DB.ElementTransformUtils.RotateElement(
        doc,
        group_instance.Id,
        rotation_axis,
        angle
    )


def calculate_group_insertion_point(
        target_family_origin,
        base_insertion_offset,
        rotation_delta
):
    """
    Calculate where the group insertion point should be placed.

    The base offset accounts for Revit choosing its own insertion point when
    the first detail group is created.
    """

    rotated_offset = rotate_2d(
        base_insertion_offset,
        rotation_delta
    )

    return (
        target_family_origin[0] + rotated_offset[0],
        target_family_origin[1] + rotated_offset[1]
    )


def place_matching_group(
        group_definition,
        target_origin,
        target_angle
):
    """Place and rotate another instance of an existing group type."""

    rotation_delta = normalize_angle(
        target_angle -
        group_definition["base_angle"]
    )

    insertion_point_2d = calculate_group_insertion_point(
        target_origin,
        group_definition["base_insertion_offset"],
        rotation_delta
    )

    insertion_point = view_xy_to_point(
        insertion_point_2d
    )

    group_instance = doc.Create.PlaceGroup(
        insertion_point,
        group_definition["group_type"]
    )

    rotate_group(
        group_instance,
        insertion_point_2d,
        rotation_delta
    )

    return group_instance


# ----------------------------------------------------------------------
# LINKED ELEMENT SELECTION
# ----------------------------------------------------------------------

def pick_linked_elements():
    """Prompt for multiple linked elements."""

    try:
        return uidoc.Selection.PickObjects(
            ObjectType.LinkedElement,
            "Select linked equipment, then click Finish"
        )

    except UI.Exceptions.OperationCanceledException:
        return []

    except Exception:
        return []


def resolve_linked_reference(reference):
    """Resolve a linked-element selection Reference."""

    link_instance = doc.GetElement(
        reference.ElementId
    )

    if not isinstance(
            link_instance,
            DB.RevitLinkInstance
    ):
        return None, None, None

    link_document = link_instance.GetLinkDocument()

    if link_document is None:
        return link_instance, None, None

    linked_element = link_document.GetElement(
        reference.LinkedElementId
    )

    return (
        link_instance,
        link_document,
        linked_element
    )


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

if not is_supported_view(view):
    forms.alert(
        "Run this tool from a floor plan, reflected ceiling plan, "
        "section, elevation, detail view, or drafting view.\n\n"
        "Current view type: {}".format(view.ViewType),
        title="Grouped Linked Footprints",
        exitscript=True
    )


selected_line_style = select_line_style()
picked_references = pick_linked_elements()

if not picked_references:
    script.exit()


# ----------------------------------------------------------------------
# PREPROCESS SELECTED ELEMENTS
# ----------------------------------------------------------------------

selection_items = []

skipped_unloaded_links = 0
skipped_non_family = 0
skipped_missing_elements = 0
skipped_no_outline = 0
geometry_failures = 0


for picked_reference in picked_references:
    try:
        link_instance, link_document, linked_element = (
            resolve_linked_reference(picked_reference)
        )

        if link_instance is None:
            skipped_missing_elements += 1
            continue

        if link_document is None:
            skipped_unloaded_links += 1
            continue

        if linked_element is None:
            skipped_missing_elements += 1
            continue

        if not isinstance(
                linked_element,
                DB.FamilyInstance
        ):
            skipped_non_family += 1
            continue

        family_symbol = linked_element.Symbol

        if family_symbol is None:
            skipped_non_family += 1
            continue

        outline_data = get_outline_data(
            link_instance,
            linked_element
        )

        if outline_data is None:
            skipped_no_outline += 1
            continue

        selection_items.append({
            "link_instance": link_instance,
            "link_document": link_document,
            "linked_element": linked_element,
            "family_symbol": family_symbol,
            "type_key": get_family_type_key(
                link_document,
                family_symbol
            ),
            "outline_data": outline_data
        })

    except Exception as processing_error:
        geometry_failures += 1

        logger.warning(
            "Could not process selected linked element: {}".format(
                processing_error
            )
        )


if not selection_items:
    forms.alert(
        "No compatible linked FamilyInstances were found.\n\n"
        "Unloaded links: {}\n"
        "Non-family elements: {}\n"
        "Missing elements: {}\n"
        "No usable outline: {}\n"
        "Geometry errors: {}".format(
            skipped_unloaded_links,
            skipped_non_family,
            skipped_missing_elements,
            skipped_no_outline,
            geometry_failures
        ),
        title="Grouped Linked Footprints",
        exitscript=True
    )


# ----------------------------------------------------------------------
# EXISTING GROUP NAMES
# ----------------------------------------------------------------------

used_group_names = set()

existing_group_types = (
    DB.FilteredElementCollector(doc)
    .OfClass(DB.GroupType)
)

for existing_group_type in existing_group_types:
    try:
        used_group_names.add(
            existing_group_type.Name
        )
    except Exception:
        continue


# One entry per unique linked Family Type in this run.
group_definitions = {}

created_group_types = 0
placed_group_instances = 0
solid_projection_types = 0
bounding_box_types = 0
creation_failures = 0
created_group_ids = []


transaction_group = DB.TransactionGroup(
    doc,
    "Create Grouped Linked Footprints"
)

transaction_group.Start()

transaction = DB.Transaction(
    doc,
    "Create Grouped Linked Footprints"
)


try:
    transaction.Start()

    for item in selection_items:

        type_key = item["type_key"]
        outline_data = item["outline_data"]

        try:
            # ----------------------------------------------------------
            # MATCHING TYPE
            #
            # Place another instance of the previously created group type.
            # ----------------------------------------------------------

            if type_key in group_definitions:
                group_instance = place_matching_group(
                    group_definitions[type_key],
                    outline_data["origin"],
                    outline_data["angle"]
                )

                created_group_ids.append(
                    group_instance.Id
                )

                placed_group_instances += 1
                continue

            # ----------------------------------------------------------
            # FIRST INSTANCE OF THIS FAMILY TYPE
            #
            # Create the detail lines at the actual linked element location,
            # group them, and record the group's insertion offset.
            # ----------------------------------------------------------

            requested_group_name = build_group_name(
                item["link_document"],
                item["family_symbol"]
            )

            family_type_subtransaction = DB.SubTransaction(doc)
            family_type_subtransaction.Start()

            try:
                (
                    group_instance,
                    group_type,
                    assigned_group_name
                ) = create_group_type(
                    outline_data["hull"],
                    selected_line_style,
                    requested_group_name,
                    used_group_names
                )

                group_location_2d = get_group_location_2d(
                    group_instance
                )

                if group_location_2d is None:
                    raise Exception(
                        "Could not determine the new detail group's "
                        "insertion point."
                    )

                base_insertion_offset = (
                    group_location_2d[0] -
                    outline_data["origin"][0],
                    group_location_2d[1] -
                    outline_data["origin"][1]
                )

                family_type_subtransaction.Commit()

            except Exception:
                family_type_subtransaction.RollBack()
                raise

            used_group_names.add(
                assigned_group_name
            )

            group_definitions[type_key] = {
                "group_type": group_type,
                "group_name": assigned_group_name,
                "base_angle": outline_data["angle"],
                "base_insertion_offset": base_insertion_offset
            }

            created_group_ids.append(
                group_instance.Id
            )

            created_group_types += 1
            placed_group_instances += 1

            if outline_data["method"] == "Solid projection":
                solid_projection_types += 1
            else:
                bounding_box_types += 1

        except Exception as creation_error:
            creation_failures += 1

            try:
                linked_element_id = element_id_value(
                    item["linked_element"].Id
                )
            except Exception:
                linked_element_id = "Unknown"

            logger.warning(
                "Could not create grouped footprint for linked "
                "element {}: {}".format(
                    linked_element_id,
                    creation_error
                )
            )

    transaction.Commit()
    transaction_group.Assimilate()

except Exception as critical_error:

    try:
        if transaction.HasStarted():
            transaction.RollBack()
    except Exception:
        pass

    try:
        transaction_group.RollBack()
    except Exception:
        pass

    forms.alert(
        "The grouped footprints could not be created.\n\n{}".format(
            critical_error
        ),
        title="Grouped Linked Footprints",
        exitscript=True
    )


# ----------------------------------------------------------------------
# SELECT CREATED GROUPS
# ----------------------------------------------------------------------

if created_group_ids:
    try:
        selected_ids = List[DB.ElementId]()

        for group_id in created_group_ids:
            selected_ids.Add(group_id)

        uidoc.Selection.SetElementIds(
            selected_ids
        )

    except Exception:
        pass


# ----------------------------------------------------------------------
# SUMMARY
# ----------------------------------------------------------------------

forms.alert(
    "Grouped footprint creation complete.\n\n"
    "Unique Family Types: {}\n"
    "Detail Group Types created: {}\n"
    "Detail Group instances placed: {}\n"
    "Solid-projection group types: {}\n"
    "Bounding-box fallback group types: {}\n"
    "Creation failures: {}\n\n"
    "Selection skips:\n"
    "Unloaded links: {}\n"
    "Non-family elements: {}\n"
    "Missing elements: {}\n"
    "No usable footprint: {}\n"
    "Geometry errors: {}\n\n"
    "Line style: {}\n\n"
    "Edit any generated group to update every selected instance "
    "of that linked Family Type.".format(
        len(group_definitions),
        created_group_types,
        placed_group_instances,
        solid_projection_types,
        bounding_box_types,
        creation_failures,
        skipped_unloaded_links,
        skipped_non_family,
        skipped_missing_elements,
        skipped_no_outline,
        geometry_failures,
        selected_line_style.Name
    ),
    title="Grouped Linked Footprints"
)