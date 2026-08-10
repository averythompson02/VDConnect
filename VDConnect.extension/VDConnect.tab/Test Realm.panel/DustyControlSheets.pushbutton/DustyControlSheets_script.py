# -*- coding: utf-8 -*-
"""
Dusty Control - Create Sheet, Polygon Crop Views, Auto Fit, Optional Grid Overlay Alignment

pyRevit / IronPython script

Modes:
1. No grid alignment:
   - Creates cropped views.
   - Places them in a 2-column sheet layout.

2. Grid alignment:
   - Select two straight grid lines.
   - Their intersection becomes the common alignment anchor.
   - All cropped views overlap on the sheet.
   - All viewports are shifted so the selected grid intersection lands on the same sheet point.
   - The combined overlay is scaled to fit and fill the sheet work area.

Includes duplicate method option:
- Duplicate only
- Duplicate with detailing
- Duplicate as dependent
"""

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
import math
import re

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()


# ------------------------------------------------------------
# Selection Filters
# ------------------------------------------------------------

class FamilyInstanceSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.FamilyInstance)

    def AllowReference(self, reference, position):
        return False


class GridSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.Grid)

    def AllowReference(self, reference, position):
        return False


# ------------------------------------------------------------
# Basic Helpers
# ------------------------------------------------------------

def safe_element_name(elem):
    if elem is None:
        return ""

    try:
        return DB.Element.Name.GetValue(elem)
    except Exception:
        pass

    try:
        return elem.Name
    except Exception:
        pass

    try:
        return str(elem.Id.IntegerValue)
    except Exception:
        return "Unnamed"


def clean_name(name):
    invalid_chars = ['\\', ':', '{', '}', '[', ']', '|', ';', '<', '>', '?', '`', '~']
    clean = name or ""

    for ch in invalid_chars:
        clean = clean.replace(ch, "-")

    clean = clean.strip()

    if not clean:
        clean = "Dusty View"

    return clean


def parse_distance_to_feet(raw_text):
    if raw_text is None:
        return None

    text = raw_text.strip().lower()

    if not text:
        return None

    text = text.replace("feet", "ft").replace("foot", "ft")
    text = text.replace("inches", "in").replace("inch", "in")

    if "'" in text:
        feet_value = 0.0
        inch_value = 0.0

        feet_match = re.search(r"([-+]?\d*\.?\d+)\s*'", text)

        if feet_match:
            feet_value = float(feet_match.group(1))

        after_feet = text.split("'", 1)[1]
        inch_match = re.search(r"([-+]?\d*\.?\d+)", after_feet)

        if inch_match:
            inch_value = float(inch_match.group(1))

        return feet_value + (inch_value / 12.0)

    inch_match = re.match(r"^\s*([-+]?\d*\.?\d+)\s*(\"|in)\s*$", text)

    if inch_match:
        return float(inch_match.group(1)) / 12.0

    feet_match = re.match(r"^\s*([-+]?\d*\.?\d+)\s*(ft)?\s*$", text)

    if feet_match:
        return float(feet_match.group(1))

    return None


def parse_float(raw_text):
    if raw_text is None:
        return None

    try:
        return float(raw_text.strip())
    except Exception:
        return None


def parse_int(raw_text):
    if raw_text is None:
        return None

    try:
        return int(raw_text.strip())
    except Exception:
        return None


def inches_to_feet(value):
    return float(value) / 12.0


def get_existing_view_names():
    views = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.View)
        .WhereElementIsNotElementType()
    )

    names = set()

    for view in views:
        try:
            if not view.IsTemplate:
                names.add(view.Name)
        except Exception:
            pass

    return names


def get_existing_sheet_numbers():
    sheets = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.ViewSheet)
        .WhereElementIsNotElementType()
    )

    numbers = set()

    for sheet in sheets:
        try:
            numbers.add(sheet.SheetNumber)
        except Exception:
            pass

    return numbers


def make_unique_view_name(base_name, existing_names):
    base_name = clean_name(base_name)

    if base_name not in existing_names:
        existing_names.add(base_name)
        return base_name

    index = 1

    while True:
        candidate = "{} ({})".format(base_name, index)

        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate

        index += 1


def make_unique_sheet_number(base_number):
    existing_numbers = get_existing_sheet_numbers()

    if base_number not in existing_numbers:
        return base_number

    index = 1

    while True:
        candidate = "{}-{}".format(base_number, index)

        if candidate not in existing_numbers:
            return candidate

        index += 1


def get_family_display_name(elem):
    family_name = "Family"
    type_name = "Type"

    try:
        symbol = elem.Symbol
        if symbol:
            type_name = safe_element_name(symbol)

            if symbol.Family:
                family_name = safe_element_name(symbol.Family)
    except Exception:
        pass

    return clean_name("{} - {} - {}".format(
        family_name,
        type_name,
        elem.Id.IntegerValue
    ))


def get_family_center_point(elem):
    loc = elem.Location

    if isinstance(loc, DB.LocationPoint):
        return loc.Point

    if isinstance(loc, DB.LocationCurve):
        try:
            return loc.Curve.Evaluate(0.5, True)
        except Exception:
            pass

    bbox = elem.get_BoundingBox(None)

    if bbox:
        return DB.XYZ(
            (bbox.Min.X + bbox.Max.X) / 2.0,
            (bbox.Min.Y + bbox.Max.Y) / 2.0,
            (bbox.Min.Z + bbox.Max.Z) / 2.0
        )

    return None


def get_selected_family_instances():
    selected_ids = list(uidoc.Selection.GetElementIds())
    picked = []

    for elem_id in selected_ids:
        elem = doc.GetElement(elem_id)

        if isinstance(elem, DB.FamilyInstance):
            picked.append(elem)

    if picked:
        return picked

    try:
        refs = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            FamilyInstanceSelectionFilter(),
            "Select one or more family instances."
        )
    except OperationCanceledException:
        return []

    for ref in refs:
        elem = doc.GetElement(ref.ElementId)

        if isinstance(elem, DB.FamilyInstance):
            picked.append(elem)

    return picked


# ------------------------------------------------------------
# View / Sheet Helpers
# ------------------------------------------------------------

def validate_active_view(view):
    if view is None:
        return False, "No active view found."

    if view.IsTemplate:
        return False, "Open a normal model view, not a view template."

    if isinstance(view, DB.ViewSheet):
        return False, "Open the source model view, not a sheet."

    if view.CropBox is None:
        return False, "The active view does not have a crop box."

    try:
        if not view.CanViewBeDuplicated(DB.ViewDuplicateOption.Duplicate):
            return False, "The active view cannot be duplicated."
    except Exception:
        return False, "Could not verify whether the active view can be duplicated."

    return True, ""


def select_duplicate_option(source_view):
    duplicate_choice = forms.CommandSwitchWindow.show(
        [
            "Duplicate only",
            "Duplicate with detailing",
            "Duplicate as dependent"
        ],
        message="How should the generated Dusty views be duplicated?"
    )

    if not duplicate_choice:
        script.exit()

    if duplicate_choice == "Duplicate with detailing":
        duplicate_option = DB.ViewDuplicateOption.WithDetailing
    elif duplicate_choice == "Duplicate as dependent":
        duplicate_option = DB.ViewDuplicateOption.AsDependent
    else:
        duplicate_option = DB.ViewDuplicateOption.Duplicate

    try:
        if not source_view.CanViewBeDuplicated(duplicate_option):
            forms.alert(
                "The active source view cannot be duplicated using:\n\n{}\n\nTry 'Duplicate only' instead.".format(
                    duplicate_choice
                ),
                title="Dusty Sheet Layout",
                exitscript=True
            )
    except Exception:
        forms.alert(
            "Could not verify whether the active source view supports:\n\n{}".format(
                duplicate_choice
            ),
            title="Dusty Sheet Layout",
            exitscript=True
        )

    return duplicate_option, duplicate_choice


def remove_view_template(view):
    try:
        if view.ViewTemplateId and view.ViewTemplateId != DB.ElementId.InvalidElementId:
            view.ViewTemplateId = DB.ElementId.InvalidElementId
    except Exception:
        pass


def collect_titleblocks():
    titleblocks = list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements()
    )

    def sort_key(tb):
        family_name = ""
        type_name = safe_element_name(tb)

        try:
            if hasattr(tb, "Family") and tb.Family:
                family_name = safe_element_name(tb.Family)
        except Exception:
            family_name = ""

        return "{} - {} - {}".format(family_name, type_name, tb.Id.IntegerValue)

    titleblocks.sort(key=sort_key)

    return titleblocks


def select_titleblock():
    titleblocks = collect_titleblocks()

    if not titleblocks:
        forms.alert(
            "No titleblock types found in this project.",
            title="Dusty Sheet Layout",
            exitscript=True
        )

    display_items = []
    lookup = {}

    for tb in titleblocks:
        family_name = ""

        try:
            if hasattr(tb, "Family") and tb.Family:
                family_name = safe_element_name(tb.Family)
        except Exception:
            family_name = ""

        type_name = safe_element_name(tb)

        display = "{} - {} [{}]".format(
            family_name,
            type_name,
            tb.Id.IntegerValue
        )

        display_items.append(display)
        lookup[display] = tb

    selected = forms.SelectFromList.show(
        display_items,
        title="Select Titleblock",
        button_name="Use Titleblock",
        multiselect=False
    )

    if not selected:
        script.exit()

    return lookup[selected]


def activate_symbol_if_needed(symbol):
    try:
        if hasattr(symbol, "IsActive") and not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass


def round_up_to_standard_scale(required_scale):
    standard_scales = [
        1, 2, 3, 4, 6, 8,
        12, 16, 24, 32, 48,
        60, 72, 84, 96, 120,
        144, 180, 192, 240, 300,
        360, 480, 600, 720, 960, 1200
    ]

    required_scale = max(1.0, float(required_scale))

    for scale in standard_scales:
        if scale >= required_scale:
            return int(scale)

    return int(math.ceil(required_scale))


def scale_to_label(scale):
    try:
        scale = int(scale)
    except Exception:
        return str(scale)

    if scale == 1:
        return '12" = 1\'-0"'

    if scale % 12 == 0:
        feet = scale / 12
        return '1" = {}\'-0"'.format(int(feet))

    return "1:{} custom".format(scale)


def get_sheet_work_area(sheet, margin_feet, right_clearance_feet):
    outline = sheet.Outline

    min_x = outline.Min.U + margin_feet
    max_x = outline.Max.U - margin_feet - right_clearance_feet
    min_y = outline.Min.V + margin_feet
    max_y = outline.Max.V - margin_feet

    if max_x <= min_x or max_y <= min_y:
        raise Exception("Sheet margin or right-side clearance is too large.")

    return min_x, max_x, min_y, max_y


def can_add_view_to_sheet(sheet, view):
    try:
        return DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id)
    except Exception:
        return True


def set_view_scale(view, scale):
    try:
        view.Scale = int(scale)
        return True
    except Exception as ex:
        logger.debug("Could not set scale on view {}: {}".format(safe_element_name(view), ex))
        return False


# ------------------------------------------------------------
# Grid Alignment Helpers
# ------------------------------------------------------------

def get_grid_line_xy(grid):
    try:
        curve = grid.Curve
    except Exception:
        return None

    if not isinstance(curve, DB.Line):
        return None

    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)

    return p1, p2


def intersect_lines_xy(line_a, line_b):
    p1, p2 = line_a
    p3, p4 = line_b

    x1 = p1.X
    y1 = p1.Y
    x2 = p2.X
    y2 = p2.Y

    x3 = p3.X
    y3 = p3.Y
    x4 = p4.X
    y4 = p4.Y

    denom = ((x1 - x2) * (y3 - y4)) - ((y1 - y2) * (x3 - x4))

    if abs(denom) < 0.0000001:
        return None

    px_num = ((x1 * y2 - y1 * x2) * (x3 - x4)) - ((x1 - x2) * (x3 * y4 - y3 * x4))
    py_num = ((x1 * y2 - y1 * x2) * (y3 - y4)) - ((y1 - y2) * (x3 * y4 - y3 * x4))

    px = px_num / denom
    py = py_num / denom

    return DB.XYZ(px, py, 0)


def pick_grid_alignment_point():
    try:
        refs = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            GridSelectionFilter(),
            "Select TWO straight grid lines to define the alignment intersection."
        )
    except OperationCanceledException:
        return None

    if not refs or len(refs) < 2:
        forms.alert(
            "Grid alignment needs two straight grid lines. Continuing without grid alignment.",
            title="Dusty Sheet Layout"
        )
        return None

    grid_1 = doc.GetElement(refs[0].ElementId)
    grid_2 = doc.GetElement(refs[1].ElementId)

    line_1 = get_grid_line_xy(grid_1)
    line_2 = get_grid_line_xy(grid_2)

    if not line_1 or not line_2:
        forms.alert(
            "One or both selected grids are not straight grid lines. Continuing without grid alignment.",
            title="Dusty Sheet Layout"
        )
        return None

    intersection = intersect_lines_xy(line_1, line_2)

    if intersection is None:
        forms.alert(
            "Selected grid lines do not intersect or are parallel. Continuing without grid alignment.",
            title="Dusty Sheet Layout"
        )
        return None

    return intersection


# ------------------------------------------------------------
# Polygon Crop Helpers
# ------------------------------------------------------------

def get_polygon_rotation(side_count):
    # Flat side up.
    return math.pi / 2.0 + (math.pi / float(side_count))


def build_polygon_crop_loop(view, center_point, apothem_feet, side_count):
    crop = view.CropBox

    if crop is None:
        raise Exception("View has no crop box.")

    crop_transform = crop.Transform
    inverse_transform = crop_transform.Inverse

    local_center = inverse_transform.OfPoint(center_point)

    vertex_radius = apothem_feet / math.cos(math.pi / float(side_count))
    rotation = get_polygon_rotation(side_count)

    local_points = []
    model_points = []

    for i in range(side_count):
        angle = rotation + (2.0 * math.pi * float(i) / float(side_count))

        local_x = local_center.X + vertex_radius * math.cos(angle)
        local_y = local_center.Y + vertex_radius * math.sin(angle)
        local_z = local_center.Z

        local_point = DB.XYZ(local_x, local_y, local_z)
        model_point = crop_transform.OfPoint(local_point)

        local_points.append(local_point)
        model_points.append(model_point)

    loop = DB.CurveLoop()

    for i in range(side_count):
        start_point = model_points[i]
        end_point = model_points[(i + 1) % side_count]

        if start_point.DistanceTo(end_point) <= 0.000001:
            raise Exception("Polygon has a zero-length edge.")

        loop.Append(DB.Line.CreateBound(start_point, end_point))

    min_x = min([p.X for p in local_points])
    max_x = max([p.X for p in local_points])
    min_y = min([p.Y for p in local_points])
    max_y = max([p.Y for p in local_points])

    crop_width = max_x - min_x
    crop_height = max_y - min_y

    min_rel_x = min_x - local_center.X
    max_rel_x = max_x - local_center.X
    min_rel_y = min_y - local_center.Y
    max_rel_y = max_y - local_center.Y

    return {
        "loop": loop,
        "crop_width": crop_width,
        "crop_height": crop_height,
        "local_center": local_center,
        "crop_transform": crop_transform,
        "min_rel_x": min_rel_x,
        "max_rel_x": max_rel_x,
        "min_rel_y": min_rel_y,
        "max_rel_y": max_rel_y
    }


def apply_polygon_crop(view, center_point, apothem_feet, side_count):
    view.CropBoxActive = True
    view.CropBoxVisible = True

    shape_manager = view.GetCropRegionShapeManager()

    if shape_manager is None:
        raise Exception("Could not access crop region shape manager.")

    if not shape_manager.CanHaveShape:
        raise Exception("This view type does not support non-rectangular crop regions.")

    try:
        shape_manager.RemoveSplit()
    except Exception:
        pass

    data = build_polygon_crop_loop(
        view,
        center_point,
        apothem_feet,
        side_count
    )

    try:
        if not shape_manager.IsCropRegionShapeValid(data["loop"]):
            raise Exception("Generated polygon crop shape is not valid.")
    except AttributeError:
        pass
    except Exception:
        pass

    shape_manager.SetCropShape(data["loop"])

    return data


# ------------------------------------------------------------
# Layout Helpers
# ------------------------------------------------------------

def get_work_area_data(sheet, margin_feet, right_clearance_feet):
    min_x, max_x, min_y, max_y = get_sheet_work_area(
        sheet,
        margin_feet,
        right_clearance_feet
    )

    width = max_x - min_x
    height = max_y - min_y

    return {
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "width": width,
        "height": height,
        "center": DB.XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0)
    }


def create_two_column_slots(sheet, view_data, margin_feet, gap_feet, right_clearance_feet):
    work = get_work_area_data(sheet, margin_feet, right_clearance_feet)

    columns = 2
    rows = int(math.ceil(float(len(view_data)) / float(columns)))

    total_gap_x = gap_feet * float(columns - 1)
    total_gap_y = gap_feet * float(max(rows - 1, 0))

    slot_width = (work["width"] - total_gap_x) / float(columns)
    slot_height = (work["height"] - total_gap_y) / float(rows)

    if slot_width <= 0 or slot_height <= 0:
        raise Exception("Not enough usable sheet space for the selected margin and gap.")

    slots = []

    for index, item in enumerate(view_data):
        row = index // columns
        col = index % columns

        slot_min_x = work["min_x"] + float(col) * (slot_width + gap_feet)
        slot_max_x = slot_min_x + slot_width

        slot_max_y = work["max_y"] - float(row) * (slot_height + gap_feet)
        slot_min_y = slot_max_y - slot_height

        center_x = (slot_min_x + slot_max_x) / 2.0
        center_y = (slot_min_y + slot_max_y) / 2.0

        slots.append({
            "data": item,
            "point": DB.XYZ(center_x, center_y, 0),
            "available_width": slot_width,
            "available_height": slot_height,
            "slot_min_x": slot_min_x,
            "slot_max_x": slot_max_x,
            "slot_min_y": slot_min_y,
            "slot_max_y": slot_max_y
        })

    return slots


def get_required_scale_for_slot(item, slot, fill_factor):
    width_scale = item["crop_width"] / (slot["available_width"] * fill_factor)
    height_scale = item["crop_height"] / (slot["available_height"] * fill_factor)

    return max(width_scale, height_scale)


def set_uniform_scale_for_slots(slots, fill_factor):
    max_required = 1.0

    for slot in slots:
        required = get_required_scale_for_slot(slot["data"], slot, fill_factor)
        max_required = max(max_required, required)

    uniform_scale = round_up_to_standard_scale(max_required)

    for slot in slots:
        view = slot["data"]["view"]
        set_view_scale(view, uniform_scale)
        slot["data"]["scale"] = int(uniform_scale)

    return int(uniform_scale)


def calculate_overlay_extents(view_data):
    union_min_x = None
    union_max_x = None
    union_min_y = None
    union_max_y = None

    for item in view_data:
        # Extents relative to selected grid alignment point.
        item_min_x = item["min_rel_x"] - item["align_offset_x"]
        item_max_x = item["max_rel_x"] - item["align_offset_x"]
        item_min_y = item["min_rel_y"] - item["align_offset_y"]
        item_max_y = item["max_rel_y"] - item["align_offset_y"]

        if union_min_x is None:
            union_min_x = item_min_x
            union_max_x = item_max_x
            union_min_y = item_min_y
            union_max_y = item_max_y
        else:
            union_min_x = min(union_min_x, item_min_x)
            union_max_x = max(union_max_x, item_max_x)
            union_min_y = min(union_min_y, item_min_y)
            union_max_y = max(union_max_y, item_max_y)

    return {
        "min_x": union_min_x,
        "max_x": union_max_x,
        "min_y": union_min_y,
        "max_y": union_max_y,
        "width": union_max_x - union_min_x,
        "height": union_max_y - union_min_y
    }


def set_uniform_scale_for_overlay(view_data, work, fill_factor):
    extents = calculate_overlay_extents(view_data)

    required_width = extents["width"] / (work["width"] * fill_factor)
    required_height = extents["height"] / (work["height"] * fill_factor)

    required_scale = max(required_width, required_height, 1.0)
    uniform_scale = round_up_to_standard_scale(required_scale)

    for item in view_data:
        set_view_scale(item["view"], uniform_scale)
        item["scale"] = int(uniform_scale)

    return int(uniform_scale)


def get_overlay_sheet_anchor(work):
    return work["center"]


def get_overlay_viewport_center(item, sheet_anchor, scale):
    # The selected grid intersection should land on sheet_anchor.
    return DB.XYZ(
        sheet_anchor.X - (item["align_offset_x"] / float(scale)),
        sheet_anchor.Y - (item["align_offset_y"] / float(scale)),
        0
    )


def get_outline_size(outline):
    min_pt = outline.MinimumPoint
    max_pt = outline.MaximumPoint

    width = max_pt.X - min_pt.X
    height = max_pt.Y - min_pt.Y

    return width, height


def viewport_inside_bounds(viewport, min_x, max_x, min_y, max_y):
    tolerance = 1.0 / 192.0

    outline = viewport.GetBoxOutline()
    min_pt = outline.MinimumPoint
    max_pt = outline.MaximumPoint

    return (
        min_pt.X >= min_x - tolerance and
        max_pt.X <= max_x + tolerance and
        min_pt.Y >= min_y - tolerance and
        max_pt.Y <= max_y + tolerance
    )


def enforce_overlay_fit(view_data, work, starting_scale):
    scale = int(starting_scale)
    sheet_anchor = get_overlay_sheet_anchor(work)

    for attempt in range(5):
        doc.Regenerate()

        for item in view_data:
            viewport = item["viewport"]

            if viewport is not None:
                viewport.SetBoxCenter(
                    get_overlay_viewport_center(item, sheet_anchor, scale)
                )

        doc.Regenerate()

        max_ratio = 1.0

        for item in view_data:
            viewport = item["viewport"]

            if viewport is None:
                continue

            if viewport_inside_bounds(
                viewport,
                work["min_x"],
                work["max_x"],
                work["min_y"],
                work["max_y"]
            ):
                continue

            outline = viewport.GetBoxOutline()
            min_pt = outline.MinimumPoint
            max_pt = outline.MaximumPoint

            width = max_pt.X - min_pt.X
            height = max_pt.Y - min_pt.Y

            left_over = max(0.0, work["min_x"] - min_pt.X)
            right_over = max(0.0, max_pt.X - work["max_x"])
            bottom_over = max(0.0, work["min_y"] - min_pt.Y)
            top_over = max(0.0, max_pt.Y - work["max_y"])

            width_ratio = (width + left_over + right_over) / work["width"]
            height_ratio = (height + bottom_over + top_over) / work["height"]

            max_ratio = max(max_ratio, width_ratio, height_ratio)

        if max_ratio <= 1.0:
            return scale

        proposed = int(math.ceil(float(scale) * max_ratio * 1.03))
        proposed = round_up_to_standard_scale(proposed)

        if proposed <= scale:
            proposed = round_up_to_standard_scale(scale + 1)

        scale = int(proposed)

        for item in view_data:
            set_view_scale(item["view"], scale)
            item["scale"] = scale

    doc.Regenerate()

    for item in view_data:
        viewport = item["viewport"]

        if viewport is not None:
            viewport.SetBoxCenter(
                get_overlay_viewport_center(item, sheet_anchor, scale)
            )

    return scale


def enforce_slot_fit(slots, starting_scale):
    scale = int(starting_scale)

    for attempt in range(5):
        doc.Regenerate()

        for slot in slots:
            item = slot["data"]
            viewport = item["viewport"]

            if viewport is not None:
                viewport.SetBoxCenter(slot["point"])

        doc.Regenerate()

        max_ratio = 1.0

        for slot in slots:
            item = slot["data"]
            viewport = item["viewport"]

            if viewport is None:
                continue

            if viewport_inside_bounds(
                viewport,
                slot["slot_min_x"],
                slot["slot_max_x"],
                slot["slot_min_y"],
                slot["slot_max_y"]
            ):
                continue

            outline = viewport.GetBoxOutline()
            width, height = get_outline_size(outline)

            width_ratio = width / slot["available_width"]
            height_ratio = height / slot["available_height"]

            max_ratio = max(max_ratio, width_ratio, height_ratio)

        if max_ratio <= 1.0:
            return scale

        proposed = int(math.ceil(float(scale) * max_ratio * 1.03))
        proposed = round_up_to_standard_scale(proposed)

        if proposed <= scale:
            proposed = round_up_to_standard_scale(scale + 1)

        scale = int(proposed)

        for slot in slots:
            item = slot["data"]
            set_view_scale(item["view"], scale)
            item["scale"] = scale

    doc.Regenerate()

    for slot in slots:
        item = slot["data"]
        viewport = item["viewport"]

        if viewport is not None:
            viewport.SetBoxCenter(slot["point"])

    return scale


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

active_view = doc.ActiveView

valid, message = validate_active_view(active_view)

if not valid:
    forms.alert(message, title="Dusty Sheet Layout", exitscript=True)

families = get_selected_family_instances()

if not families:
    forms.alert("No family instances selected.", title="Dusty Sheet Layout", exitscript=True)

titleblock = select_titleblock()

sheet_number_text = forms.ask_for_string(
    default="DUSTY-001",
    prompt="Enter new sheet number.",
    title="New Sheet Number"
)

if not sheet_number_text:
    script.exit()

sheet_number = make_unique_sheet_number(sheet_number_text.strip())

sheet_name = forms.ask_for_string(
    default="DUSTY LAYOUT",
    prompt="Enter new sheet name.",
    title="New Sheet Name"
)

if sheet_name is None:
    script.exit()

distance_text = forms.ask_for_string(
    default="75",
    prompt="Polygon crop distance from family center to each side. Use 75 for about a 150' wide crop.",
    title="Polygon Crop Distance"
)

apothem_feet = parse_distance_to_feet(distance_text)

if apothem_feet is None or apothem_feet <= 0:
    forms.alert("Invalid crop distance.", title="Dusty Sheet Layout", exitscript=True)

side_count_text = forms.ask_for_string(
    default="8",
    prompt="Polygon side count. Common values: 6, 8, 12, 16.",
    title="Polygon Sides"
)

side_count = parse_int(side_count_text)

if side_count is None or side_count < 3:
    forms.alert("Invalid polygon side count.", title="Dusty Sheet Layout", exitscript=True)

margin_text = forms.ask_for_string(
    default="1",
    prompt="Sheet margin in inches.",
    title="Sheet Margin"
)

margin_inches = parse_float(margin_text)

if margin_inches is None or margin_inches < 0:
    forms.alert("Invalid sheet margin.", title="Dusty Sheet Layout", exitscript=True)

gap_text = forms.ask_for_string(
    default="1",
    prompt="Gap between side-by-side views in inches. Only used when grid alignment is off.",
    title="Viewport Gap"
)

gap_inches = parse_float(gap_text)

if gap_inches is None or gap_inches < 0:
    forms.alert("Invalid viewport gap.", title="Dusty Sheet Layout", exitscript=True)

right_clearance_text = forms.ask_for_string(
    default="3",
    prompt="Right-side titleblock clearance in inches. Use 0 if your titleblock does not have a right strip.",
    title="Titleblock Clearance"
)

right_clearance_inches = parse_float(right_clearance_text)

if right_clearance_inches is None or right_clearance_inches < 0:
    forms.alert("Invalid titleblock clearance.", title="Dusty Sheet Layout", exitscript=True)

fill_factor_text = forms.ask_for_string(
    default="0.90",
    prompt="Fill factor between 0.50 and 0.98. Higher fills more of the sheet. Recommended: 0.90",
    title="Sheet Fill Factor"
)

fill_factor = parse_float(fill_factor_text)

if fill_factor is None or fill_factor <= 0 or fill_factor > 0.98:
    forms.alert("Invalid fill factor. Use a value like 0.90.", title="Dusty Sheet Layout", exitscript=True)

prefix = forms.ask_for_string(
    default="DUSTY",
    prompt="Prefix for generated view names.",
    title="View Prefix"
)

if prefix is None:
    script.exit()

prefix = clean_name(prefix)

align_choice = forms.CommandSwitchWindow.show(
    [
        "Do not align by grids",
        "Overlay and align by selected grid intersection"
    ],
    message="Grid alignment option:"
)

if not align_choice:
    script.exit()

duplicate_option, duplicate_label = select_duplicate_option(active_view)

alignment_point_xy = None
overlay_mode = False

if align_choice == "Overlay and align by selected grid intersection":
    alignment_point_xy = pick_grid_alignment_point()

    if alignment_point_xy is not None:
        overlay_mode = True

margin_feet = inches_to_feet(margin_inches)
gap_feet = inches_to_feet(gap_inches)
right_clearance_feet = inches_to_feet(right_clearance_inches)

existing_view_names = get_existing_view_names()

view_data = []
created_count = 0
placed_count = 0
skipped_count = 0
final_scale = None
created_sheet = None

try:
    with revit.Transaction("Create Dusty Sheet and Layout Views"):

        activate_symbol_if_needed(titleblock)

        created_sheet = DB.ViewSheet.Create(doc, titleblock.Id)
        created_sheet.SheetNumber = sheet_number
        created_sheet.Name = sheet_name

        doc.Regenerate()

        for fam in families:
            try:
                if fam is None or not fam.IsValidObject:
                    skipped_count += 1
                    continue

                center = get_family_center_point(fam)

                if center is None:
                    skipped_count += 1
                    continue

                new_view_id = active_view.Duplicate(duplicate_option)
                new_view = doc.GetElement(new_view_id)

                remove_view_template(new_view)

                view_name = make_unique_view_name(
                    "{} - {}".format(prefix, get_family_display_name(fam)),
                    existing_view_names
                )

                new_view.Name = view_name

                crop_data = apply_polygon_crop(
                    new_view,
                    center,
                    apothem_feet,
                    side_count
                )

                align_offset_x = 0.0
                align_offset_y = 0.0

                if alignment_point_xy is not None:
                    align_model_point = DB.XYZ(
                        alignment_point_xy.X,
                        alignment_point_xy.Y,
                        center.Z
                    )

                    local_align_point = crop_data["crop_transform"].Inverse.OfPoint(
                        align_model_point
                    )

                    align_offset_x = local_align_point.X - crop_data["local_center"].X
                    align_offset_y = local_align_point.Y - crop_data["local_center"].Y

                view_data.append({
                    "view": new_view,
                    "crop_width": crop_data["crop_width"],
                    "crop_height": crop_data["crop_height"],
                    "min_rel_x": crop_data["min_rel_x"],
                    "max_rel_x": crop_data["max_rel_x"],
                    "min_rel_y": crop_data["min_rel_y"],
                    "max_rel_y": crop_data["max_rel_y"],
                    "align_offset_x": align_offset_x,
                    "align_offset_y": align_offset_y,
                    "scale": None,
                    "viewport": None
                })

                created_count += 1

            except Exception as ex:
                try:
                    logger.debug("Failed creating view for element {}: {}".format(fam.Id.IntegerValue, ex))
                except Exception:
                    logger.debug("Failed creating view: {}".format(ex))

                skipped_count += 1

        if not view_data:
            raise Exception("No views were created.")

        work = get_work_area_data(
            created_sheet,
            margin_feet,
            right_clearance_feet
        )

        if overlay_mode:
            final_scale = set_uniform_scale_for_overlay(
                view_data,
                work,
                fill_factor
            )

            sheet_anchor = get_overlay_sheet_anchor(work)

            for item in view_data:
                view = item["view"]

                try:
                    if not can_add_view_to_sheet(created_sheet, view):
                        skipped_count += 1
                        continue

                    viewport_center = get_overlay_viewport_center(
                        item,
                        sheet_anchor,
                        final_scale
                    )

                    viewport = DB.Viewport.Create(
                        doc,
                        created_sheet.Id,
                        view.Id,
                        viewport_center
                    )

                    item["viewport"] = viewport

                    doc.Regenerate()
                    viewport.SetBoxCenter(viewport_center)

                    placed_count += 1

                except Exception as ex:
                    logger.debug("Failed placing view {}: {}".format(view.Name, ex))
                    skipped_count += 1

            final_scale = enforce_overlay_fit(
                view_data,
                work,
                final_scale
            )

        else:
            slots = create_two_column_slots(
                created_sheet,
                view_data,
                margin_feet,
                gap_feet,
                right_clearance_feet
            )

            final_scale = set_uniform_scale_for_slots(
                slots,
                fill_factor
            )

            for slot in slots:
                item = slot["data"]
                view = item["view"]

                try:
                    if not can_add_view_to_sheet(created_sheet, view):
                        skipped_count += 1
                        continue

                    viewport = DB.Viewport.Create(
                        doc,
                        created_sheet.Id,
                        view.Id,
                        slot["point"]
                    )

                    item["viewport"] = viewport

                    doc.Regenerate()
                    viewport.SetBoxCenter(slot["point"])

                    placed_count += 1

                except Exception as ex:
                    logger.debug("Failed placing view {}: {}".format(view.Name, ex))
                    skipped_count += 1

            final_scale = enforce_slot_fit(
                slots,
                final_scale
            )

except Exception as ex:
    forms.alert(
        "Dusty Sheet Layout failed:\n\n{}".format(ex),
        title="Dusty Sheet Layout",
        exitscript=True
    )


mode_label = "Grid overlay alignment" if overlay_mode else "Side-by-side layout"

forms.alert(
    "Sheet created: {}\nMode: {}\nDuplicate method: {}\nViews created: {}\nViews placed: {}\nSkipped: {}\nFinal scale: {}".format(
        sheet_number,
        mode_label,
        duplicate_label,
        created_count,
        placed_count,
        skipped_count,
        scale_to_label(final_scale) if final_scale else "N/A"
    ),
    title="Dusty Sheet Layout"
)