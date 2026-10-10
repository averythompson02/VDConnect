# -*- coding: utf-8 -*-
"""
Dusty Robotics - Linked Fixture Outer Outlines

Select individual fixtures/components inside loaded Revit links.
The script projects their geometry vertically onto the active host plan
and creates rotation-correct outer Detail Line silhouettes.

Workflow:
1. Open the host plan used for Dusty Robotics export.
2. Run this pyRevit command.
3. Pick individual elements inside loaded Revit links.
4. Click Finish on Revit's Options Bar.
5. Choose whether to replace previous generated outlines.
6. Review the generated Detail Lines.
7. Hide the linked fixture graphics in the export view.
8. Export the host plan to DWG.

Important:
- Linked models are read only.
- Detail Lines are created in the host document.
- Linked geometry is transformed into host coordinates.
- Nested family subcomponents are processed recursively.
- Solids, meshes, curves, and polylines are processed.
- The outer silhouette is a convex hull.
- Concave openings will be bridged.
"""

from pyrevit import revit, DB, UI, forms, script
from System.Collections.Generic import List


# ----------------------------------------------------------------------
# Revit context
# ----------------------------------------------------------------------

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

output = script.get_output()
logger = script.get_logger()


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

LINE_STYLE_NAME = "Dusty Robotics - Fixture Outline"

# Revit internal units are feet.
POINT_TOLERANCE = 1.0 / (256.0 * 12.0)       # 1/256 inch
CROSS_TOLERANCE = POINT_TOLERANCE ** 2
MIN_SEGMENT_LENGTH = 1.0 / (32.0 * 12.0)    # 1/32 inch

# The active view must look nearly straight upward or downward.
VERTICAL_VIEW_TOLERANCE = 0.999

# Generated-line appearance.
LINE_COLOR = DB.Color(255, 0, 255)
LINE_WEIGHT = 3

# Prevent malformed nested families from causing endless recursion.
MAX_RECURSION_DEPTH = 20


# ----------------------------------------------------------------------
# Utility functions
# ----------------------------------------------------------------------

def get_element_id_value(element_id):
    """
    Return a numeric ElementId value across common Revit API versions.
    """
    if element_id is None:
        return -1

    try:
        return element_id.IntegerValue
    except Exception:
        pass

    try:
        return element_id.Value
    except Exception:
        return -1


def get_link_name(link_instance):
    """
    Return a readable Revit link-instance name.
    """
    try:
        return link_instance.Name
    except Exception:
        return "Link Instance {0}".format(
            get_element_id_value(link_instance.Id)
        )


def get_category_name(element):
    """
    Return an element's category name.
    """
    try:
        if element.Category is not None:
            return element.Category.Name
    except Exception:
        pass

    return "<No Category>"


def get_element_description(element):
    """
    Return family and type information when available.
    """
    try:
        if isinstance(element, DB.FamilyInstance):
            family_name = element.Symbol.Family.Name
            type_name = element.Symbol.Name

            return "{0}: {1}".format(
                family_name,
                type_name
            )
    except Exception:
        pass

    try:
        return element.Name
    except Exception:
        return "Element {0}".format(
            get_element_id_value(element.Id)
        )


# ----------------------------------------------------------------------
# Linked-element selection filter
# ----------------------------------------------------------------------

class AnyLinkedElementFilter(UI.Selection.ISelectionFilter):
    """
    Allows selection of individual elements inside loaded Revit links.

    Category filtering is intentionally not used. Some linked fixtures,
    manufacturer families, and nested components may use unexpected
    categories.
    """

    def __init__(self, host_document):
        self.host_document = host_document

    def AllowElement(self, element):
        """
        For ObjectType.LinkedElement, Revit supplies the host
        RevitLinkInstance at this stage.
        """
        return isinstance(element, DB.RevitLinkInstance)

    def AllowReference(self, reference, position):
        """
        Confirm that the reference resolves to an element in a loaded link.
        """
        try:
            link_instance = self.host_document.GetElement(
                reference.ElementId
            )

            if not isinstance(link_instance, DB.RevitLinkInstance):
                return False

            link_document = link_instance.GetLinkDocument()

            if link_document is None:
                return False

            linked_element_id = reference.LinkedElementId

            if linked_element_id is None:
                return False

            if linked_element_id == DB.ElementId.InvalidElementId:
                return False

            linked_element = link_document.GetElement(
                linked_element_id
            )

            return linked_element is not None

        except Exception as filter_error:
            logger.debug(
                "Linked selection filter error: {0}".format(
                    filter_error
                )
            )
            return False


# ----------------------------------------------------------------------
# View validation
# ----------------------------------------------------------------------

def validate_active_view():
    """
    Require a non-template plan-based view with a vertical view direction.
    """
    if view is None:
        return False

    if view.IsTemplate:
        return False

    if not isinstance(view, DB.ViewPlan):
        return False

    try:
        view_direction = view.ViewDirection.Normalize()

        return (
            abs(view_direction.Z)
            >= VERTICAL_VIEW_TOLERANCE
        )

    except Exception:
        return False


# ----------------------------------------------------------------------
# Linked-element selection
# ----------------------------------------------------------------------

def pick_linked_elements():
    """
    Interactively select elements inside loaded Revit links.

    Returns dictionaries containing:
    - link_instance
    - link_document
    - linked_element
    - link_transform
    """
    selection_filter = AnyLinkedElementFilter(doc)

    try:
        with forms.WarningBar(
            title=(
                "Pick linked fixtures, then click Finish "
                "on Revit's Options Bar"
            )
        ):
            references = uidoc.Selection.PickObjects(
                UI.Selection.ObjectType.LinkedElement,
                selection_filter,
                "Pick individual fixtures/components inside Revit links"
            )

    except Exception as selection_error:
        logger.debug(
            "Selection canceled or interrupted: {0}".format(
                selection_error
            )
        )
        return []

    references = list(references)

    output.print_md(
        "**References returned by Revit:** {0}".format(
            len(references)
        )
    )

    selected_items = []
    unique_keys = set()

    for reference in references:
        try:
            link_instance = doc.GetElement(
                reference.ElementId
            )

            if not isinstance(link_instance, DB.RevitLinkInstance):
                logger.warning(
                    "A reference did not resolve to a RevitLinkInstance."
                )
                continue

            link_document = link_instance.GetLinkDocument()

            if link_document is None:
                logger.warning(
                    "Link instance {0} is unloaded or inaccessible.".format(
                        get_element_id_value(link_instance.Id)
                    )
                )
                continue

            linked_element_id = reference.LinkedElementId

            if linked_element_id is None:
                logger.warning(
                    "A selected reference had no linked element ID."
                )
                continue

            if linked_element_id == DB.ElementId.InvalidElementId:
                logger.warning(
                    "A selected reference returned InvalidElementId."
                )
                continue

            linked_element = link_document.GetElement(
                linked_element_id
            )

            if linked_element is None:
                logger.warning(
                    "Linked element {0} could not be retrieved.".format(
                        get_element_id_value(linked_element_id)
                    )
                )
                continue

            # Include link instance ID because one linked document can be
            # placed more than once in the host model.
            unique_key = (
                get_element_id_value(link_instance.Id),
                get_element_id_value(linked_element.Id)
            )

            if unique_key in unique_keys:
                continue

            unique_keys.add(unique_key)

            try:
                link_transform = link_instance.GetTotalTransform()
            except Exception:
                link_transform = link_instance.GetTransform()

            selected_items.append({
                "link_instance": link_instance,
                "link_document": link_document,
                "linked_element": linked_element,
                "link_transform": link_transform
            })

        except Exception as reference_error:
            logger.warning(
                "Could not resolve a linked reference: {0}".format(
                    reference_error
                )
            )

    output.print_md(
        "**Valid linked elements resolved:** {0}".format(
            len(selected_items)
        )
    )

    return selected_items


# ----------------------------------------------------------------------
# Geometry configuration
# ----------------------------------------------------------------------

def create_geometry_options():
    """
    Request detailed geometry, including visibility-controlled geometry.

    Including non-visible objects helps with manufacturer families and
    families whose usable geometry is controlled by detail level or
    visibility settings.
    """
    options = DB.Options()

    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = True
    options.DetailLevel = DB.ViewDetailLevel.Fine

    return options


def create_geometry_statistics():
    """
    Create counters used in the diagnostic report.
    """
    return {
        "elements_visited": 0,
        "elements_with_geometry": 0,
        "subcomponents": 0,
        "geometry_instances": 0,
        "solids": 0,
        "meshes": 0,
        "curves": 0,
        "polylines": 0,
        "points": 0
    }


# ----------------------------------------------------------------------
# Geometry point collection
# ----------------------------------------------------------------------

def append_transformed_point(
        source_point,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Transform one point into host coordinates and append it.
    """
    if source_point is None:
        return

    try:
        host_point = point_transform.OfPoint(source_point)

        if host_point is not None:
            host_point_list.append(host_point)
            geometry_statistics["points"] += 1

    except Exception as point_error:
        logger.debug(
            "Point transformation failed: {0}".format(
                point_error
            )
        )


def append_mesh_vertices(
        mesh,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Collect every mesh vertex in host coordinates.
    """
    if mesh is None:
        return

    if mesh.NumVertices == 0:
        return

    for vertex_index in range(mesh.NumVertices):
        append_transformed_point(
            mesh.get_Vertex(vertex_index),
            point_transform,
            host_point_list,
            geometry_statistics
        )


def append_curve_points(
        curve,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Tessellate a curve and collect its points.

    This supports lines, arcs, circles, ellipses, splines, and other
    curve-based geometry that Revit allows to be tessellated.
    """
    if curve is None:
        return

    tessellated_points = None

    try:
        tessellated_points = curve.Tessellate()
    except Exception as tessellation_error:
        logger.debug(
            "Curve tessellation failed: {0}".format(
                tessellation_error
            )
        )

    if tessellated_points is not None:
        tessellated_points = list(tessellated_points)

        if tessellated_points:
            for source_point in tessellated_points:
                append_transformed_point(
                    source_point,
                    point_transform,
                    host_point_list,
                    geometry_statistics
                )
            return

    # Endpoint fallback for curves that do not tessellate.
    try:
        append_transformed_point(
            curve.GetEndPoint(0),
            point_transform,
            host_point_list,
            geometry_statistics
        )

        append_transformed_point(
            curve.GetEndPoint(1),
            point_transform,
            host_point_list,
            geometry_statistics
        )

    except Exception as endpoint_error:
        logger.debug(
            "Curve endpoint extraction failed: {0}".format(
                endpoint_error
            )
        )


def append_polyline_points(
        polyline,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Collect all coordinates from a Revit PolyLine.
    """
    if polyline is None:
        return

    try:
        coordinates = polyline.GetCoordinates()

        for source_point in coordinates:
            append_transformed_point(
                source_point,
                point_transform,
                host_point_list,
                geometry_statistics
            )

    except Exception as polyline_error:
        logger.debug(
            "Polyline extraction failed: {0}".format(
                polyline_error
            )
        )


def append_face_edge_points(
        face,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Collect face-edge curve points if face triangulation is unavailable.
    """
    try:
        edge_loops = face.EdgeLoops

        for edge_array in edge_loops:
            for edge in edge_array:
                edge_curve = edge.AsCurve()

                append_curve_points(
                    edge_curve,
                    point_transform,
                    host_point_list,
                    geometry_statistics
                )

    except Exception as edge_error:
        logger.debug(
            "Face-edge extraction failed: {0}".format(
                edge_error
            )
        )


def append_solid_vertices(
        solid,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Triangulate every solid face and collect its vertices.

    Zero-volume solids are not automatically rejected because some family
    geometry can contain useful faces even when the volume is zero.
    """
    if solid is None:
        return

    if solid.Faces.Size == 0:
        return

    for face in solid.Faces:
        try:
            face_mesh = face.Triangulate()

            append_mesh_vertices(
                face_mesh,
                point_transform,
                host_point_list,
                geometry_statistics
            )

        except Exception as face_error:
            logger.debug(
                "Face triangulation failed: {0}".format(
                    face_error
                )
            )

            append_face_edge_points(
                face,
                point_transform,
                host_point_list,
                geometry_statistics
            )


# ----------------------------------------------------------------------
# Geometry traversal
# ----------------------------------------------------------------------

def walk_geometry(
        geometry_element,
        point_transform,
        host_point_list,
        geometry_statistics):
    """
    Recursively collect usable points from a GeometryElement.

    For direct element geometry and GetInstanceGeometry output, coordinates
    are normally expressed in the linked document's model coordinates.
    The supplied point_transform converts them into host coordinates.
    """
    if geometry_element is None:
        return

    for geometry_object in geometry_element:
        if geometry_object is None:
            continue

        if isinstance(geometry_object, DB.Solid):
            geometry_statistics["solids"] += 1

            append_solid_vertices(
                geometry_object,
                point_transform,
                host_point_list,
                geometry_statistics
            )

        elif isinstance(geometry_object, DB.Mesh):
            geometry_statistics["meshes"] += 1

            append_mesh_vertices(
                geometry_object,
                point_transform,
                host_point_list,
                geometry_statistics
            )

        elif isinstance(geometry_object, DB.Curve):
            geometry_statistics["curves"] += 1

            append_curve_points(
                geometry_object,
                point_transform,
                host_point_list,
                geometry_statistics
            )

        elif isinstance(geometry_object, DB.PolyLine):
            geometry_statistics["polylines"] += 1

            append_polyline_points(
                geometry_object,
                point_transform,
                host_point_list,
                geometry_statistics
            )

        elif isinstance(geometry_object, DB.GeometryInstance):
            geometry_statistics["geometry_instances"] += 1

            points_before_instance = len(host_point_list)
            instance_geometry = None

            try:
                instance_geometry = (
                    geometry_object.GetInstanceGeometry()
                )
            except Exception as instance_error:
                logger.debug(
                    "GetInstanceGeometry failed: {0}".format(
                        instance_error
                    )
                )

            if instance_geometry is not None:
                walk_geometry(
                    instance_geometry,
                    point_transform,
                    host_point_list,
                    geometry_statistics
                )

            # Only use SymbolGeometry when InstanceGeometry yielded no points.
            # Symbol geometry is in the geometry instance's local coordinate
            # system, so its transform must be combined with the link
            # transform.
            if len(host_point_list) == points_before_instance:
                try:
                    symbol_geometry = (
                        geometry_object.GetSymbolGeometry()
                    )

                    instance_transform = geometry_object.Transform

                    combined_transform = (
                        point_transform.Multiply(
                            instance_transform
                        )
                    )

                    walk_geometry(
                        symbol_geometry,
                        combined_transform,
                        host_point_list,
                        geometry_statistics
                    )

                except Exception as symbol_error:
                    logger.debug(
                        "GetSymbolGeometry fallback failed: {0}".format(
                            symbol_error
                        )
                    )


def collect_direct_element_geometry(
        linked_element,
        link_transform,
        geometry_options,
        host_point_list,
        geometry_statistics):
    """
    Collect geometry directly accessible from one linked element.
    """
    try:
        geometry_element = linked_element.get_Geometry(
            geometry_options
        )

        if geometry_element is None:
            return

        geometry_statistics["elements_with_geometry"] += 1

        walk_geometry(
            geometry_element,
            link_transform,
            host_point_list,
            geometry_statistics
        )

    except Exception as geometry_error:
        logger.debug(
            "Element {0} direct geometry failed: {1}".format(
                get_element_id_value(linked_element.Id),
                geometry_error
            )
        )


def collect_element_and_subcomponent_geometry(
        linked_element,
        link_document,
        link_transform,
        geometry_options,
        host_point_list,
        geometry_statistics,
        visited_element_ids,
        recursion_depth=0):
    """
    Collect direct geometry and recursively process FamilyInstance
    subcomponents.
    """
    if linked_element is None:
        return

    if recursion_depth > MAX_RECURSION_DEPTH:
        logger.warning(
            "Maximum nested-family depth reached at element {0}.".format(
                get_element_id_value(linked_element.Id)
            )
        )
        return

    element_id_value = get_element_id_value(
        linked_element.Id
    )

    if element_id_value in visited_element_ids:
        return

    visited_element_ids.add(element_id_value)
    geometry_statistics["elements_visited"] += 1

    collect_direct_element_geometry(
        linked_element,
        link_transform,
        geometry_options,
        host_point_list,
        geometry_statistics
    )

    if not isinstance(linked_element, DB.FamilyInstance):
        return

    try:
        subcomponent_ids = linked_element.GetSubComponentIds()
    except Exception as subcomponent_error:
        logger.debug(
            "Element {0} subcomponent lookup failed: {1}".format(
                element_id_value,
                subcomponent_error
            )
        )
        return

    if subcomponent_ids is None:
        return

    for subcomponent_id in subcomponent_ids:
        try:
            subcomponent = link_document.GetElement(
                subcomponent_id
            )

            if subcomponent is None:
                continue

            geometry_statistics["subcomponents"] += 1

            collect_element_and_subcomponent_geometry(
                subcomponent,
                link_document,
                link_transform,
                geometry_options,
                host_point_list,
                geometry_statistics,
                visited_element_ids,
                recursion_depth + 1
            )

        except Exception as nested_error:
            logger.debug(
                "Nested subcomponent {0} failed: {1}".format(
                    get_element_id_value(subcomponent_id),
                    nested_error
                )
            )


def get_element_host_vertices(
        linked_element,
        link_document,
        link_transform,
        geometry_options):
    """
    Return geometry points for a linked element and all accessible nested
    subcomponents, expressed in host-model coordinates.

    Returns:
        host_points
        geometry_statistics
    """
    host_points = []
    geometry_statistics = create_geometry_statistics()
    visited_element_ids = set()

    collect_element_and_subcomponent_geometry(
        linked_element,
        link_document,
        link_transform,
        geometry_options,
        host_points,
        geometry_statistics,
        visited_element_ids
    )

    return host_points, geometry_statistics


# ----------------------------------------------------------------------
# Projection onto the active host view
# ----------------------------------------------------------------------

def xyz_to_view_uv(host_point):
    """
    Orthogonally project a host-coordinate point onto the active view.

    U follows View.RightDirection.
    V follows View.UpDirection.
    Distance along ViewDirection is discarded.
    """
    relative_point = host_point - view.Origin

    u_value = relative_point.DotProduct(
        view.RightDirection
    )

    v_value = relative_point.DotProduct(
        view.UpDirection
    )

    return (u_value, v_value)


def view_uv_to_xyz(uv_point):
    """
    Convert a view-plane coordinate back onto the active view plane.
    """
    return (
        view.Origin
        + view.RightDirection.Multiply(uv_point[0])
        + view.UpDirection.Multiply(uv_point[1])
    )


# ----------------------------------------------------------------------
# Two-dimensional convex hull
# ----------------------------------------------------------------------

def quantized_point_key(point):
    """
    Create a tolerance-based point key.
    """
    return (
        int(round(point[0] / POINT_TOLERANCE)),
        int(round(point[1] / POINT_TOLERANCE))
    )


def remove_duplicate_points(points):
    """
    Remove view-plane points that coincide within POINT_TOLERANCE.
    """
    unique_lookup = {}

    for point in points:
        key = quantized_point_key(point)

        if key not in unique_lookup:
            unique_lookup[key] = point

    return list(unique_lookup.values())


def cross_2d(origin, point_a, point_b):
    """
    Return the signed two-dimensional cross product.
    """
    return (
        (point_a[0] - origin[0])
        * (point_b[1] - origin[1])
        - (point_a[1] - origin[1])
        * (point_b[0] - origin[0])
    )


def create_convex_hull(points):
    """
    Create a convex outer silhouette using Andrew's monotonic-chain
    algorithm.

    Collinear points along straight edges are removed. A rectangular
    fixture will therefore normally produce four Detail Lines.
    """
    unique_points = remove_duplicate_points(points)

    if len(unique_points) < 3:
        return []

    ordered_points = sorted(
        unique_points,
        key=lambda point: (point[0], point[1])
    )

    lower_hull = []

    for point in ordered_points:
        while (
            len(lower_hull) >= 2
            and cross_2d(
                lower_hull[-2],
                lower_hull[-1],
                point
            ) <= CROSS_TOLERANCE
        ):
            lower_hull.pop()

        lower_hull.append(point)

    upper_hull = []

    for point in reversed(ordered_points):
        while (
            len(upper_hull) >= 2
            and cross_2d(
                upper_hull[-2],
                upper_hull[-1],
                point
            ) <= CROSS_TOLERANCE
        ):
            upper_hull.pop()

        upper_hull.append(point)

    return lower_hull[:-1] + upper_hull[:-1]


def distance_2d(point_a, point_b):
    """
    Return the distance between two view-plane points.
    """
    delta_u = point_b[0] - point_a[0]
    delta_v = point_b[1] - point_a[1]

    return (
        delta_u * delta_u
        + delta_v * delta_v
    ) ** 0.5


# ----------------------------------------------------------------------
# Line-style management
# ----------------------------------------------------------------------

def get_or_create_line_style():
    """
    Get or create the dedicated Detail Line style.
    """
    lines_category = doc.Settings.Categories.get_Item(
        DB.BuiltInCategory.OST_Lines
    )

    outline_category = None

    for subcategory in lines_category.SubCategories:
        if subcategory.Name == LINE_STYLE_NAME:
            outline_category = subcategory
            break

    if outline_category is None:
        outline_category = doc.Settings.Categories.NewSubcategory(
            lines_category,
            LINE_STYLE_NAME
        )

    try:
        outline_category.LineColor = LINE_COLOR
    except Exception as color_error:
        logger.debug(
            "Could not set outline color: {0}".format(
                color_error
            )
        )

    try:
        outline_category.SetLineWeight(
            LINE_WEIGHT,
            DB.GraphicsStyleType.Projection
        )
    except Exception as weight_error:
        logger.debug(
            "Could not set outline line weight: {0}".format(
                weight_error
            )
        )

    return outline_category.GetGraphicsStyle(
        DB.GraphicsStyleType.Projection
    )


def get_existing_outline_ids(graphics_style):
    """
    Find Detail Lines in the active view using the dedicated line style.
    """
    existing_ids = []

    curve_elements = (
        DB.FilteredElementCollector(doc, view.Id)
        .OfClass(DB.CurveElement)
        .ToElements()
    )

    for curve_element in curve_elements:
        try:
            if not curve_element.ViewSpecific:
                continue

            if curve_element.OwnerViewId != view.Id:
                continue

            if curve_element.LineStyle is None:
                continue

            if curve_element.LineStyle.Id == graphics_style.Id:
                existing_ids.append(curve_element.Id)

        except Exception:
            continue

    return existing_ids


# ----------------------------------------------------------------------
# Detail Line creation
# ----------------------------------------------------------------------

def create_detail_outline(
        hull_points,
        graphics_style):
    """
    Create a closed Detail Line loop from ordered hull points.
    """
    created_ids = []

    point_count = len(hull_points)

    if point_count < 3:
        return created_ids

    for index in range(point_count):
        start_uv = hull_points[index]
        end_uv = hull_points[
            (index + 1) % point_count
        ]

        if distance_2d(start_uv, end_uv) < MIN_SEGMENT_LENGTH:
            continue

        start_xyz = view_uv_to_xyz(start_uv)
        end_xyz = view_uv_to_xyz(end_uv)

        try:
            detail_line_geometry = DB.Line.CreateBound(
                start_xyz,
                end_xyz
            )

            detail_curve = doc.Create.NewDetailCurve(
                view,
                detail_line_geometry
            )

            detail_curve.LineStyle = graphics_style

            created_ids.append(detail_curve.Id)

        except Exception as line_error:
            logger.warning(
                "Detail Line creation failed: {0}".format(
                    line_error
                )
            )

    return created_ids


# ----------------------------------------------------------------------
# Diagnostic text
# ----------------------------------------------------------------------

def build_geometry_summary(geometry_statistics):
    """
    Return a compact diagnostic summary.
    """
    return (
        "Visited {0} elements; found {1} subcomponents, "
        "{2} geometry instances, {3} solids, {4} meshes, "
        "{5} curves, {6} polylines, and {7} extracted points."
    ).format(
        geometry_statistics["elements_visited"],
        geometry_statistics["subcomponents"],
        geometry_statistics["geometry_instances"],
        geometry_statistics["solids"],
        geometry_statistics["meshes"],
        geometry_statistics["curves"],
        geometry_statistics["polylines"],
        geometry_statistics["points"]
    )


# ----------------------------------------------------------------------
# Main execution
# ----------------------------------------------------------------------

if not validate_active_view():
    forms.alert(
        "Open the host floor plan, reflected ceiling plan, or engineering "
        "plan that will be exported for Dusty Robotics.\n\n"
        "The active view must be a non-template plan with a vertical "
        "view direction.",
        title="Linked Fixture Outlines",
        exitscript=True
    )


output.print_md(
    "# Dusty Robotics Linked Fixture Outlines"
)

output.print_md(
    "**Active host view:** {0}".format(
        view.Name
    )
)


selected_items = pick_linked_elements()

if not selected_items:
    forms.alert(
        "Revit returned no valid linked elements.\n\n"
        "Verify that Select Links is enabled. Run the command again, "
        "pick the fixtures while the command is active, and click Finish "
        "on Revit's Options Bar.",
        title="Linked Fixture Outlines",
        exitscript=True
    )


replace_existing = forms.alert(
    "Revit returned {0} valid linked elements.\n\n"
    "Delete previous '{1}' Detail Lines in this view before creating "
    "the new outlines?\n\n"
    "Yes: replace existing outlines.\n"
    "No: keep existing outlines and add new ones.".format(
        len(selected_items),
        LINE_STYLE_NAME
    ),
    title="Replace Existing Outlines?",
    yes=True,
    no=True
)


geometry_options = create_geometry_options()

created_line_ids = []
report_rows = []
skipped_rows = []

deleted_count = 0
successful_count = 0


transaction = DB.Transaction(
    doc,
    "Create Linked Fixture Outlines"
)


try:
    transaction.Start()

    graphics_style = get_or_create_line_style()

    if replace_existing:
        previous_ids = get_existing_outline_ids(
            graphics_style
        )

        if previous_ids:
            deletion_collection = List[DB.ElementId]()

            for previous_id in previous_ids:
                deletion_collection.Add(previous_id)

            deleted_ids = doc.Delete(
                deletion_collection
            )

            if deleted_ids is not None:
                try:
                    deleted_count = deleted_ids.Count
                except Exception:
                    deleted_count = len(list(deleted_ids))

    for item in selected_items:
        link_instance = item["link_instance"]
        link_document = item["link_document"]
        linked_element = item["linked_element"]
        link_transform = item["link_transform"]

        link_name = get_link_name(link_instance)
        category_name = get_category_name(linked_element)
        description = get_element_description(linked_element)
        linked_element_id = get_element_id_value(
            linked_element.Id
        )

        try:
            host_vertices, geometry_statistics = (
                get_element_host_vertices(
                    linked_element,
                    link_document,
                    link_transform,
                    geometry_options
                )
            )

            geometry_summary = build_geometry_summary(
                geometry_statistics
            )

            if not host_vertices:
                skipped_rows.append([
                    link_name,
                    linked_element_id,
                    category_name,
                    description,
                    "No usable geometry. " + geometry_summary
                ])
                continue

            projected_points = []

            for host_vertex in host_vertices:
                projected_points.append(
                    xyz_to_view_uv(host_vertex)
                )

            hull_points = create_convex_hull(
                projected_points
            )

            if len(hull_points) < 3:
                skipped_rows.append([
                    link_name,
                    linked_element_id,
                    category_name,
                    description,
                    (
                        "Geometry was found, but its projection did not "
                        "form a closed outline. "
                        + geometry_summary
                    )
                ])
                continue

            element_line_ids = create_detail_outline(
                hull_points,
                graphics_style
            )

            if not element_line_ids:
                skipped_rows.append([
                    link_name,
                    linked_element_id,
                    category_name,
                    description,
                    (
                        "A hull was calculated, but no Detail Lines "
                        "were created. "
                        + geometry_summary
                    )
                ])
                continue

            created_line_ids.extend(
                element_line_ids
            )

            successful_count += 1

            report_rows.append([
                link_name,
                linked_element_id,
                category_name,
                description,
                geometry_statistics["elements_visited"],
                geometry_statistics["subcomponents"],
                len(host_vertices),
                len(hull_points),
                len(element_line_ids)
            ])

        except Exception as element_error:
            skipped_rows.append([
                link_name,
                linked_element_id,
                category_name,
                description,
                str(element_error)
            ])

            logger.warning(
                "Linked element {0} failed: {1}".format(
                    linked_element_id,
                    element_error
                )
            )

    transaction.Commit()

except Exception as main_error:
    try:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
    except Exception:
        pass

    logger.exception(main_error)

    forms.alert(
        "The transaction failed and was rolled back.\n\n"
        "Error:\n{0}".format(
            main_error
        ),
        title="Linked Fixture Outlines",
        exitscript=True
    )


# ----------------------------------------------------------------------
# Select and display generated Detail Lines
# ----------------------------------------------------------------------

if created_line_ids:
    try:
        generated_selection = List[DB.ElementId]()

        for created_id in created_line_ids:
            generated_selection.Add(created_id)

        uidoc.Selection.SetElementIds(
            generated_selection
        )

        uidoc.RefreshActiveView()

    except Exception as selection_error:
        logger.debug(
            "Could not select the generated Detail Lines: {0}".format(
                selection_error
            )
        )


# ----------------------------------------------------------------------
# Output report
# ----------------------------------------------------------------------

output.print_md(
    "## Results"
)

output.print_md(
    "**Valid linked elements:** {0}".format(
        len(selected_items)
    )
)

output.print_md(
    "**Elements outlined:** {0}".format(
        successful_count
    )
)

output.print_md(
    "**Detail Lines created:** {0}".format(
        len(created_line_ids)
    )
)

output.print_md(
    "**Previous Detail Lines deleted:** {0}".format(
        deleted_count
    )
)

output.print_md(
    "**Elements skipped:** {0}".format(
        len(skipped_rows)
    )
)


if report_rows:
    output.print_table(
        table_data=report_rows,
        columns=[
            "Revit Link",
            "Linked Element ID",
            "Category",
            "Family / Type",
            "Elements Visited",
            "Subcomponents",
            "Geometry Points",
            "Hull Points",
            "Detail Lines"
        ],
        title="Successfully Created Outlines"
    )


if skipped_rows:
    output.print_table(
        table_data=skipped_rows,
        columns=[
            "Revit Link",
            "Linked Element ID",
            "Category",
            "Family / Type",
            "Reason"
        ],
        title="Skipped Linked Elements"
    )


# ----------------------------------------------------------------------
# Completion dialog
# ----------------------------------------------------------------------

if created_line_ids:
    forms.alert(
        "Fixture outlines created successfully.\n\n"
        "Elements outlined: {0}\n"
        "Detail Lines created: {1}\n"
        "Elements skipped: {2}\n\n"
        "The generated lines are selected and use the "
        "'{3}' line style.\n\n"
        "Verify the generated geometry against known fixture dimensions "
        "before publishing the Dusty Robotics DWG.".format(
            successful_count,
            len(created_line_ids),
            len(skipped_rows),
            LINE_STYLE_NAME
        ),
        title="Linked Fixture Outlines"
    )

else:
    forms.alert(
        "Revit received {0} linked elements, but no Detail Lines were "
        "created.\n\n"
        "Review the Skipped Linked Elements table in the pyRevit output. "
        "The report now includes nested-family, solid, mesh, curve, "
        "polyline, and extracted-point diagnostics.".format(
            len(selected_items)
        ),
        title="No Outlines Created"
    )