# -*- coding: utf-8 -*-

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.UI.Selection import ObjectType

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


def get_family_name(element):
    """Get family name for an element."""
    try:
        if hasattr(element, "Symbol") and element.Symbol:
            return element.Symbol.Family.Name
    except:
        pass

    return None


def get_location_point(element):
    """Get best point for connecting."""
    location = element.Location

    if isinstance(location, DB.LocationPoint):
        return location.Point

    if isinstance(location, DB.LocationCurve):
        return location.Curve.Evaluate(0.5, True)

    bbox = element.get_BoundingBox(view)

    if not bbox:
        bbox = element.get_BoundingBox(None)

    if not bbox:
        return None

    return DB.XYZ(
        (bbox.Min.X + bbox.Max.X) / 2.0,
        (bbox.Min.Y + bbox.Max.Y) / 2.0,
        (bbox.Min.Z + bbox.Max.Z) / 2.0
    )


def project_to_view(point):
    """Project point onto active view plane."""
    normal = view.ViewDirection
    origin = view.Origin

    v = point - origin
    d = v.DotProduct(normal)

    return point - normal.Multiply(d)


def get_thin_lines_style():
    """Get <Thin Lines> style."""
    lines_cat = doc.Settings.Categories.get_Item(
        DB.BuiltInCategory.OST_Lines
    )

    for subcat in lines_cat.SubCategories:
        if subcat.Name == "<Thin Lines>":
            return subcat.GetGraphicsStyle(
                DB.GraphicsStyleType.Projection
            )

    return None


def get_selected_elements():

    sel_ids = list(uidoc.Selection.GetElementIds())

    if sel_ids:
        return [doc.GetElement(x) for x in sel_ids]

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select Laser Trackers and Control Points"
    )

    return [doc.GetElement(r.ElementId) for r in refs]


# ============================================================
# MAIN
# ============================================================

elements = get_selected_elements()

if len(elements) < 2:
    forms.alert(
        "Select at least two elements.",
        exitscript=True
    )

# Group by family
family_groups = {}

for elem in elements:

    family_name = get_family_name(elem)

    if not family_name:
        continue

    family_groups.setdefault(family_name, []).append(elem)

if len(family_groups) != 2:
    forms.alert(
        "Selection must contain elements from exactly TWO families.\n\n"
        "Found {} families:\n\n{}".format(
            len(family_groups),
            "\n".join(sorted(family_groups.keys()))
        ),
        exitscript=True
    )

family_names = list(family_groups.keys())

group_a = family_groups[family_names[0]]
group_b = family_groups[family_names[1]]

thin_style = get_thin_lines_style()

if not thin_style:
    forms.alert(
        "Could not find line style <Thin Lines>.",
        exitscript=True
    )

created = 0

with revit.Transaction("Connect Different Families"):

    for elem_a in group_a:

        pt_a = get_location_point(elem_a)

        if not pt_a:
            continue

        pt_a = project_to_view(pt_a)

        for elem_b in group_b:

            pt_b = get_location_point(elem_b)

            if not pt_b:
                continue

            pt_b = project_to_view(pt_b)

            if pt_a.DistanceTo(pt_b) < 0.0001:
                continue

            try:
                detail_line = doc.Create.NewDetailCurve(
                    view,
                    DB.Line.CreateBound(pt_a, pt_b)
                )

                detail_line.LineStyle = thin_style

                created += 1

            except:
                pass

forms.alert(
    "Created {} detail lines.".format(created),
    title="Complete"
)