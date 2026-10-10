# -*- coding: utf-8 -*-
__title__ = "Fixture2Points"
__doc__ = """Select connected detail line outlines, calculate the center of each group, and place a selected Generic Model family instance at each center point. Useful for converting drafted fixture boxes or symbolic detail outlines into Generic Model family instances."""

from pyrevit import revit, DB, forms

doc = revit.doc
uidoc = revit.uidoc

TOLERANCE = 0.01
# ---------------------------------------------------------------
# GEOMETRY HELPERS
# ---------------------------------------------------------------
def points_equal(pt1, pt2):
    return pt1.DistanceTo(pt2) < TOLERANCE

def get_selected_detail_lines():
    """Get selected detail curves."""
    curves = []
    for eid in uidoc.Selection.GetElementIds():
        elem = doc.GetElement(eid)
        if isinstance(elem, DB.DetailCurve):
            curves.append(elem)
    return curves

def group_connected_curves(detail_curves):
    groups = []
    remaining = set(detail_curves)
    while remaining:
        seed = remaining.pop()
        group = [seed]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                candidate_curve = candidate.GeometryCurve
                candidate_pts = [candidate_curve.GetEndPoint(0), candidate_curve.GetEndPoint(1)]
                found_connection = False
                for existing in group:
                    existing_curve = existing.GeometryCurve
                    existing_pts = [existing_curve.GetEndPoint(0), existing_curve.GetEndPoint(1)]
                    for p1 in candidate_pts:
                        for p2 in existing_pts:
                            if points_equal(p1, p2):
                                found_connection = True
                                break
                        if found_connection:
                            break
                    if found_connection:
                        break
                if found_connection:
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups.append(group)
    return groups

def get_group_center(group):
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for curve in group:
        geom = curve.GeometryCurve
        pts = [geom.GetEndPoint(0), geom.GetEndPoint(1)]
        for pt in pts:
            min_x = min(min_x, pt.X)
            min_y = min(min_y, pt.Y)
            max_x = max(max_x, pt.X)
            max_y = max(max_y, pt.Y)
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)

def get_generic_model_types():
    symbols = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
    types = {}
    for symbol in symbols:
        try:
            if symbol.Category and symbol.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_GenericModel):
                type_name = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                display_name = "{} : {}".format(symbol.Family.Name, type_name)
                types[display_name] = symbol
        except:
            pass
    return types
