# -*- coding: utf-8 -*-

from pyrevit import revit, DB, forms, script
import math

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

CROP_OFFSET_FEET = 3.0
SHEET_MARGIN_FEET = 0.25

COMMON_SCALES = [
    1, 2, 4, 5, 8, 10, 12, 16, 20, 24, 30, 32,
    40, 48, 50, 60, 64, 80, 96, 100, 120, 128,
    160, 192, 200, 240, 256, 300, 384, 400, 480, 500
]


# ------------------------------------------------------------
# UI WRAPPERS
# ------------------------------------------------------------

class TitleBlockOption(object):
    def __init__(self, element):
        self.element = element
        self.element_id = element.Id

        fam_name = ""
        type_name = ""

        try:
            fam_name = element.FamilyName
        except:
            pass

        try:
            type_name = element.get_Parameter(
                DB.BuiltInParameter.SYMBOL_NAME_PARAM
            ).AsString()
        except:
            try:
                type_name = element.Name
            except:
                type_name = str(element.Id.IntegerValue)

        if fam_name:
            self.name = "{} : {}".format(fam_name, type_name)
        else:
            self.name = type_name

    def __repr__(self):
        return self.name


class DuplicateOption(object):
    def __init__(self, display_name, api_option):
        self.name = display_name
        self.api_option = api_option

    def __repr__(self):
        return self.name


class PlacementModeOption(object):
    def __init__(self, display_name, mode_key):
        self.name = display_name
        self.mode_key = mode_key

    def __repr__(self):
        return self.name


# ------------------------------------------------------------
# BASIC HELPERS
# ------------------------------------------------------------

def get_element_id_int(element):
    try:
        return element.Id.IntegerValue
    except:
        try:
            return int(element.Id)
        except:
            return -1


def get_selected_elements():
    elements = []

    for element_id in list(uidoc.Selection.GetElementIds()):
        element = doc.GetElement(element_id)
        if element:
            elements.append(element)

    return elements


def get_titleblock_types():
    titleblocks = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements()
    )

    options = [TitleBlockOption(tb) for tb in titleblocks]
    options.sort(key=lambda x: x.name.lower())
    return options


def safe_element_name(element):
    try:
        if element.Name:
            return element.Name
    except:
        pass

    try:
        return element.Category.Name
    except:
        return "Element"


def clean_name(value):
    invalid_chars = [
        "\\", "/", ":", ";", "{", "}", "[", "]",
        "|", "<", ">", "?", "`", "~"
    ]

    cleaned = value

    for ch in invalid_chars:
        cleaned = cleaned.replace(ch, "-")

    cleaned = cleaned.strip()

    if not cleaned:
        cleaned = "Element"

    return cleaned


def get_existing_view_names():
    names = set()

    views = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.View)
        .ToElements()
    )

    for view in views:
        try:
            names.add(view.Name)
        except:
            pass

    return names


def get_existing_sheet_numbers():
    numbers = set()

    sheets = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.ViewSheet)
        .ToElements()
    )

    for sheet in sheets:
        try:
            numbers.add(sheet.SheetNumber)
        except:
            pass

    return numbers


def get_unique_name(base_name, existing_names):
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


def get_unique_sheet_number(base_number, existing_numbers):
    if base_number not in existing_numbers:
        existing_numbers.add(base_number)
        return base_number

    index = 1

    while True:
        candidate = "{}-{}".format(base_number, index)

        if candidate not in existing_numbers:
            existing_numbers.add(candidate)
            return candidate

        index += 1


def alert_and_exit(message, title="Selection to Sheet"):
    forms.alert(message, title=title, exitscript=True)


# ------------------------------------------------------------
# VIEW / CROP HELPERS
# ------------------------------------------------------------

def get_bbox_corners(bbox):
    """
    Returns the eight bbox corners in model coordinates.
    """
    transform = bbox.Transform
    min_pt = bbox.Min
    max_pt = bbox.Max

    raw_points = [
        DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z),
        DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z),
        DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z),
        DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z),
        DB.XYZ(min_pt.X, min_pt.Y, max_pt.Z),
        DB.XYZ(max_pt.X, min_pt.Y, max_pt.Z),
        DB.XYZ(min_pt.X, max_pt.Y, max_pt.Z),
        DB.XYZ(max_pt.X, max_pt.Y, max_pt.Z),
    ]

    points = []

    for pt in raw_points:
        try:
            points.append(transform.OfPoint(pt))
        except:
            points.append(pt)

    return points


def can_crop_view(view):
    try:
        if view.CropBox is None:
            return False
    except:
        return False

    try:
        original_value = view.CropBoxActive
        view.CropBoxActive = original_value
    except:
        return False

    return True


def get_view_plane_origin(view):
    """
    Gets a stable origin for projecting model points into the view plane.
    """
    try:
        return view.Origin
    except:
        pass

    try:
        return view.CropBox.Transform.Origin
    except:
        pass

    return DB.XYZ(0, 0, 0)


def get_view_axes(view):
    """
    Returns normalized right/up/view direction vectors.
    """
    try:
        right = view.RightDirection.Normalize()
    except:
        right = DB.XYZ(1, 0, 0)

    try:
        up = view.UpDirection.Normalize()
    except:
        up = DB.XYZ(0, 1, 0)

    try:
        view_dir = view.ViewDirection.Normalize()
    except:
        view_dir = DB.XYZ(0, 0, 1)

    return right, up, view_dir


def project_point_to_view_uv(point, origin, right, up):
    """
    Converts a model point into 2D view plane coordinates.
    """
    vector = point - origin
    u = vector.DotProduct(right)
    v = vector.DotProduct(up)
    return u, v


def make_model_point_from_view_uv(origin, right, up, u, v):
    """
    Converts 2D view plane coordinates back to a model point.
    """
    return origin + (right * u) + (up * v)


def make_rect_curve_loop(origin, right, up, min_u, min_v, max_u, max_v):
    """
    Builds a rectangular crop shape as model-space curves in the view plane.
    """
    p1 = make_model_point_from_view_uv(origin, right, up, min_u, min_v)
    p2 = make_model_point_from_view_uv(origin, right, up, max_u, min_v)
    p3 = make_model_point_from_view_uv(origin, right, up, max_u, max_v)
    p4 = make_model_point_from_view_uv(origin, right, up, min_u, max_v)

    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(p1, p2))
    loop.Append(DB.Line.CreateBound(p2, p3))
    loop.Append(DB.Line.CreateBound(p3, p4))
    loop.Append(DB.Line.CreateBound(p4, p1))

    return loop


def build_crop_info(elements, source_view):
    """
    Creates crop information from selected elements.

    This avoids relying on View.CropBox as the primary crop setter.
    For bad floor views, the issue is often a scope box or non-rectangular
    sketched crop region. This function builds a clean rectangular crop loop.
    """
    origin = get_view_plane_origin(source_view)
    right, up, view_dir = get_view_axes(source_view)

    min_u = None
    min_v = None
    max_u = None
    max_v = None

    skipped = []
    found_count = 0

    for element in elements:
        bbox = None

        try:
            bbox = element.get_BoundingBox(source_view)
        except:
            bbox = None

        if not bbox:
            skipped.append(get_element_id_int(element))
            continue

        found_count += 1

        for point in get_bbox_corners(bbox):
            u, v = project_point_to_view_uv(point, origin, right, up)

            if min_u is None:
                min_u = max_u = u
                min_v = max_v = v
            else:
                min_u = min(min_u, u)
                min_v = min(min_v, v)
                max_u = max(max_u, u)
                max_v = max(max_v, v)

    if found_count == 0:
        return None, skipped

    min_u -= CROP_OFFSET_FEET
    min_v -= CROP_OFFSET_FEET
    max_u += CROP_OFFSET_FEET
    max_v += CROP_OFFSET_FEET

    crop_loop = make_rect_curve_loop(
        origin,
        right,
        up,
        min_u,
        min_v,
        max_u,
        max_v
    )

    width = abs(max_u - min_u)
    height = abs(max_v - min_v)

    crop_info = {
        "loop": crop_loop,
        "width": width,
        "height": height,
        "min_u": min_u,
        "min_v": min_v,
        "max_u": max_u,
        "max_v": max_v
    }

    return crop_info, skipped


def clear_scope_box(view):
    """
    Clears the Scope Box parameter on the duplicated view if possible.

    If a scope box is assigned, Revit can ignore or override crop edits.
    This is a common reason one floor works and another does not.
    """
    try:
        param = view.get_Parameter(
            DB.BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP
        )
    except:
        param = None

    if not param:
        return False, "No scope box parameter found."

    try:
        current_id = param.AsElementId()
    except:
        current_id = DB.ElementId.InvalidElementId

    if current_id == DB.ElementId.InvalidElementId:
        return True, "No scope box assigned."

    if param.IsReadOnly:
        return False, "Scope box parameter is read-only. View template may control it."

    try:
        param.Set(DB.ElementId.InvalidElementId)
        return True, "Scope box cleared."
    except Exception as ex:
        return False, "Could not clear scope box: {}".format(ex)


def apply_crop_to_view(view, crop_info):
    """
    Applies a clean rectangular crop shape to the duplicated view.

    Important:
    View.CropBox alone can leave an existing sketched/non-rectangular crop
    shape in place. SetCropShape forces the visible crop region to become
    the rectangle we calculated from the selected elements.
    """
    warnings = []

    try:
        view.CropBoxActive = True
    except Exception as ex:
        warnings.append("Could not activate crop on view {}: {}".format(view.Id, ex))

    try:
        view.CropBoxVisible = True
    except Exception as ex:
        warnings.append("Could not show crop on view {}: {}".format(view.Id, ex))

    ok, msg = clear_scope_box(view)

    if not ok:
        warnings.append(msg)

    try:
        doc.Regenerate()
    except:
        pass

    manager = None

    try:
        manager = view.GetCropRegionShapeManager()
    except Exception as ex:
        warnings.append("Could not get crop region shape manager: {}".format(ex))

    if not manager:
        return False, warnings

    # Remove existing sketched crop shape if API/version supports it.
    try:
        if hasattr(manager, "ShapeSet"):
            if manager.ShapeSet:
                try:
                    manager.RemoveCropRegionShape()
                    doc.Regenerate()
                except Exception as ex:
                    warnings.append(
                        "Could not remove existing crop shape: {}".format(ex)
                    )
    except:
        pass

    # Apply the new rectangular crop shape.
    try:
        manager.SetCropShape(crop_info["loop"])
        doc.Regenerate()
        return True, warnings
    except Exception as ex:
        warnings.append("Could not set rectangular crop shape: {}".format(ex))
        return False, warnings


# ------------------------------------------------------------
# VIEW / SHEET HELPERS
# ------------------------------------------------------------

def duplicate_view(source_view, duplicate_option, new_name, existing_view_names):
    view_id = source_view.Duplicate(duplicate_option)
    new_view = doc.GetElement(view_id)

    unique_name = get_unique_name(new_name, existing_view_names)

    try:
        new_view.Name = unique_name
    except Exception as ex:
        logger.warning(
            "Could not rename duplicated view {} to {}: {}".format(
                new_view.Id,
                unique_name,
                ex
            )
        )

    return new_view


def get_sheet_titleblock_bbox(sheet):
    titleblock_instances = (
        DB.FilteredElementCollector(doc, sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    for tb in titleblock_instances:
        try:
            bbox = tb.get_BoundingBox(sheet)
            if bbox:
                return bbox
        except:
            pass

    return None


def get_default_sheet_bbox():
    bbox = DB.BoundingBoxXYZ()
    bbox.Min = DB.XYZ(0, 0, 0)
    bbox.Max = DB.XYZ(3.0, 2.0, 0)
    return bbox


def get_sheet_usable_area(sheet):
    bbox = get_sheet_titleblock_bbox(sheet)

    if not bbox:
        bbox = get_default_sheet_bbox()

    min_x = bbox.Min.X + SHEET_MARGIN_FEET
    min_y = bbox.Min.Y + SHEET_MARGIN_FEET
    max_x = bbox.Max.X - SHEET_MARGIN_FEET
    max_y = bbox.Max.Y - SHEET_MARGIN_FEET

    if max_x <= min_x:
        min_x = bbox.Min.X
        max_x = bbox.Max.X

    if max_y <= min_y:
        min_y = bbox.Min.Y
        max_y = bbox.Max.Y

    return min_x, min_y, max_x, max_y


def get_center_from_area(area):
    min_x, min_y, max_x, max_y = area

    return DB.XYZ(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        0
    )


def get_area_size(area):
    min_x, min_y, max_x, max_y = area
    return max_x - min_x, max_y - min_y


def choose_scale_to_fit(model_width, model_height, paper_width, paper_height):
    if model_width <= 0 or model_height <= 0:
        return 100

    if paper_width <= 0 or paper_height <= 0:
        return 100

    required_scale = max(
        model_width / paper_width,
        model_height / paper_height
    )

    for scale in COMMON_SCALES:
        if scale >= required_scale:
            return int(scale)

    return int(math.ceil(required_scale))


def apply_auto_scale(view, crop_info, sheet_or_cell_area):
    paper_width, paper_height = get_area_size(sheet_or_cell_area)
    model_width = crop_info["width"]
    model_height = crop_info["height"]

    selected_scale = choose_scale_to_fit(
        model_width,
        model_height,
        paper_width,
        paper_height
    )

    try:
        view.Scale = selected_scale
    except Exception as ex:
        logger.warning("Could not set scale on view {}: {}".format(view.Id, ex))

    return selected_scale


def create_sheet(titleblock_type_id, sheet_number, sheet_name):
    sheet = DB.ViewSheet.Create(doc, titleblock_type_id)

    try:
        sheet.SheetNumber = sheet_number
    except Exception as ex:
        logger.warning(
            "Could not set sheet number {}: {}".format(sheet_number, ex)
        )

    try:
        sheet.Name = sheet_name
    except Exception as ex:
        logger.warning(
            "Could not set sheet name {}: {}".format(sheet_name, ex)
        )

    return sheet


def create_viewport(sheet, view, center):
    try:
        if not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
            return None
    except:
        return None

    try:
        viewport = DB.Viewport.Create(doc, sheet.Id, view.Id, center)
        doc.Regenerate()

        # Re-center after Revit calculates the viewport box.
        try:
            outline = viewport.GetBoxOutline()
            actual_center = DB.XYZ(
                (outline.MinimumPoint.X + outline.MaximumPoint.X) / 2.0,
                (outline.MinimumPoint.Y + outline.MaximumPoint.Y) / 2.0,
                0
            )

            move_vector = center - actual_center

            DB.ElementTransformUtils.MoveElement(
                doc,
                viewport.Id,
                move_vector
            )
        except Exception as ex:
            logger.warning(
                "Viewport created but could not be re-centered: {}".format(ex)
            )

        return viewport

    except Exception as ex:
        logger.warning(
            "Could not place view {} on sheet {}: {}".format(
                view.Id,
                sheet.Id,
                ex
            )
        )
        return None


def get_grid_areas(sheet, item_count):
    usable = get_sheet_usable_area(sheet)
    min_x, min_y, max_x, max_y = usable

    cols = int(math.ceil(math.sqrt(item_count)))
    rows = int(math.ceil(float(item_count) / float(cols)))

    total_width = max_x - min_x
    total_height = max_y - min_y

    cell_width = total_width / float(cols)
    cell_height = total_height / float(rows)

    areas = []

    for index in range(item_count):
        row = index // cols
        col = index % cols

        cell_min_x = min_x + col * cell_width
        cell_max_x = cell_min_x + cell_width

        cell_max_y = max_y - row * cell_height
        cell_min_y = cell_max_y - cell_height

        pad_x = min(cell_width * 0.10, 0.20)
        pad_y = min(cell_height * 0.10, 0.20)

        areas.append((
            cell_min_x + pad_x,
            cell_min_y + pad_y,
            cell_max_x - pad_x,
            cell_max_y - pad_y
        ))

    return areas


def describe_element_group(elements):
    if len(elements) == 1:
        element = elements[0]

        return "{} {}".format(
            clean_name(safe_element_name(element)),
            get_element_id_int(element)
        )

    ids = [str(get_element_id_int(e)) for e in elements]
    return "Selection {}".format("-".join(ids))


# ------------------------------------------------------------
# VALIDATION AND USER INPUT
# ------------------------------------------------------------

active_view = doc.ActiveView

if active_view.IsTemplate:
    alert_and_exit(
        "The active view is a view template. Run this from a model view."
    )

if isinstance(active_view, DB.ViewSheet):
    alert_and_exit(
        "Run this from the model view you want to duplicate, not from a sheet."
    )

selected_elements = get_selected_elements()

if not selected_elements:
    alert_and_exit(
        "Select one or more elements before running this tool."
    )

if not can_crop_view(active_view):
    alert_and_exit(
        "The active view does not appear to support a crop box."
    )

titleblock_options = get_titleblock_types()

if not titleblock_options:
    alert_and_exit(
        "No title block types were found in this project."
    )

selected_titleblock = forms.SelectFromList.show(
    titleblock_options,
    title="Select Sheet Type / Title Block",
    name_attr="name",
    multiselect=False
)

if not selected_titleblock:
    alert_and_exit("No sheet type selected.")

duplicate_options = [
    DuplicateOption(
        "Duplicate",
        DB.ViewDuplicateOption.Duplicate
    ),
    DuplicateOption(
        "Duplicate With Detailing",
        DB.ViewDuplicateOption.WithDetailing
    ),
    DuplicateOption(
        "Duplicate As Dependent",
        DB.ViewDuplicateOption.AsDependent
    ),
]

valid_duplicate_options = []

for opt in duplicate_options:
    try:
        if active_view.CanViewBeDuplicated(opt.api_option):
            valid_duplicate_options.append(opt)
    except:
        pass

if not valid_duplicate_options:
    alert_and_exit(
        "The active view cannot be duplicated with the available options."
    )

selected_duplicate_option = forms.SelectFromList.show(
    valid_duplicate_options,
    title="Select View Duplication Method",
    name_attr="name",
    multiselect=False
)

if not selected_duplicate_option:
    alert_and_exit("No duplication method selected.")

if len(selected_elements) > 1:
    placement_options = [
        PlacementModeOption(
            "One combined cropped view on one sheet",
            "combined_one_sheet"
        ),
        PlacementModeOption(
            "One cropped view per element, placed on the same sheet",
            "individual_same_sheet"
        ),
        PlacementModeOption(
            "One cropped view per element, each on a separate sheet",
            "individual_separate_sheets"
        ),
    ]

    selected_placement_mode = forms.SelectFromList.show(
        placement_options,
        title="How should multiple selected elements be sheeted?",
        name_attr="name",
        multiselect=False
    )

    if not selected_placement_mode:
        alert_and_exit("No placement mode selected.")

    placement_mode = selected_placement_mode.mode_key
else:
    placement_mode = "combined_one_sheet"


# ------------------------------------------------------------
# MAIN CREATE LOGIC
# ------------------------------------------------------------

created_views = []
created_sheets = []
created_viewports = []
skipped_element_ids = []
failed_messages = []
crop_warnings = []

existing_view_names = get_existing_view_names()
existing_sheet_numbers = get_existing_sheet_numbers()

titleblock_type_id = selected_titleblock.element_id
duplicate_api_option = selected_duplicate_option.api_option

transaction_name = "Create Cropped Selection Views and Sheets"

with revit.Transaction(transaction_name):

    if placement_mode == "combined_one_sheet":
        crop_info, skipped = build_crop_info(selected_elements, active_view)
        skipped_element_ids.extend(skipped)

        if not crop_info:
            raise Exception(
                "Could not get a bounding box from the selected elements."
            )

        group_label = describe_element_group(selected_elements)

        base_view_name = clean_name("{} - Cropped".format(group_label))
        base_sheet_name = clean_name(group_label)

        sheet_number = get_unique_sheet_number(
            "AUTO-{}".format(get_element_id_int(selected_elements[0])),
            existing_sheet_numbers
        )

        new_view = duplicate_view(
            active_view,
            duplicate_api_option,
            base_view_name,
            existing_view_names
        )

        crop_ok, warnings = apply_crop_to_view(new_view, crop_info)
        crop_warnings.extend(warnings)

        if not crop_ok:
            failed_messages.append(
                "Crop shape could not be applied to view {}.".format(
                    new_view.Id.IntegerValue
                )
            )

        sheet = create_sheet(
            titleblock_type_id,
            sheet_number,
            base_sheet_name
        )

        usable_area = get_sheet_usable_area(sheet)
        scale = apply_auto_scale(new_view, crop_info, usable_area)
        doc.Regenerate()

        center = get_center_from_area(usable_area)
        viewport = create_viewport(sheet, new_view, center)

        created_views.append((new_view, scale))
        created_sheets.append(sheet)

        if viewport:
            created_viewports.append(viewport)
        else:
            failed_messages.append(
                "Could not place view {} on sheet {}.".format(
                    new_view.Id.IntegerValue,
                    sheet.SheetNumber
                )
            )

    elif placement_mode == "individual_same_sheet":
        sheet_number = get_unique_sheet_number(
            "AUTO-SEL-{}".format(get_element_id_int(selected_elements[0])),
            existing_sheet_numbers
        )

        if len(selected_elements) <= 3:
            ids_text = "-".join(
                [str(get_element_id_int(e)) for e in selected_elements]
            )
            sheet_name = "Elements {}".format(ids_text)
        else:
            sheet_name = "Selected Elements"

        sheet = create_sheet(
            titleblock_type_id,
            sheet_number,
            clean_name(sheet_name)
        )

        created_sheets.append(sheet)

        grid_areas = get_grid_areas(sheet, len(selected_elements))

        for index, element in enumerate(selected_elements):
            crop_info, skipped = build_crop_info([element], active_view)
            skipped_element_ids.extend(skipped)

            if not crop_info:
                continue

            label = describe_element_group([element])
            base_view_name = clean_name("{} - Cropped".format(label))

            new_view = duplicate_view(
                active_view,
                duplicate_api_option,
                base_view_name,
                existing_view_names
            )

            crop_ok, warnings = apply_crop_to_view(new_view, crop_info)
            crop_warnings.extend(warnings)

            if not crop_ok:
                failed_messages.append(
                    "Crop shape could not be applied to view {}.".format(
                        new_view.Id.IntegerValue
                    )
                )

            cell_area = grid_areas[index]
            scale = apply_auto_scale(new_view, crop_info, cell_area)
            doc.Regenerate()

            center = get_center_from_area(cell_area)
            viewport = create_viewport(sheet, new_view, center)

            created_views.append((new_view, scale))

            if viewport:
                created_viewports.append(viewport)
            else:
                failed_messages.append(
                    "Could not place view {} on sheet {}.".format(
                        new_view.Id.IntegerValue,
                        sheet.SheetNumber
                    )
                )

    elif placement_mode == "individual_separate_sheets":
        for element in selected_elements:
            crop_info, skipped = build_crop_info([element], active_view)
            skipped_element_ids.extend(skipped)

            if not crop_info:
                continue

            label = describe_element_group([element])

            base_view_name = clean_name("{} - Cropped".format(label))
            base_sheet_name = clean_name(label)

            sheet_number = get_unique_sheet_number(
                "AUTO-{}".format(get_element_id_int(element)),
                existing_sheet_numbers
            )

            new_view = duplicate_view(
                active_view,
                duplicate_api_option,
                base_view_name,
                existing_view_names
            )

            crop_ok, warnings = apply_crop_to_view(new_view, crop_info)
            crop_warnings.extend(warnings)

            if not crop_ok:
                failed_messages.append(
                    "Crop shape could not be applied to view {}.".format(
                        new_view.Id.IntegerValue
                    )
                )

            sheet = create_sheet(
                titleblock_type_id,
                sheet_number,
                base_sheet_name
            )

            usable_area = get_sheet_usable_area(sheet)
            scale = apply_auto_scale(new_view, crop_info, usable_area)
            doc.Regenerate()

            center = get_center_from_area(usable_area)
            viewport = create_viewport(sheet, new_view, center)

            created_views.append((new_view, scale))
            created_sheets.append(sheet)

            if viewport:
                created_viewports.append(viewport)
            else:
                failed_messages.append(
                    "Could not place view {} on sheet {}.".format(
                        new_view.Id.IntegerValue,
                        sheet.SheetNumber
                    )
                )


# ------------------------------------------------------------
# REPORT
# ------------------------------------------------------------

message_lines = []

if created_views or created_sheets or created_viewports:
    message_lines.append("Created cropped view/sheet output successfully.")
else:
    message_lines.append("No cropped views or sheets were created.")

message_lines.append("")
message_lines.append("Views created: {}".format(len(created_views)))
message_lines.append("Sheets created: {}".format(len(created_sheets)))
message_lines.append("Viewports placed: {}".format(len(created_viewports)))

if skipped_element_ids:
    unique_skipped = []
    seen = set()

    for element_id in skipped_element_ids:
        if element_id not in seen:
            seen.add(element_id)
            unique_skipped.append(element_id)

    message_lines.append("")
    message_lines.append("Skipped elements with no bounding box in active view:")
    message_lines.append(", ".join([str(x) for x in unique_skipped]))

if crop_warnings:
    unique_warnings = []
    seen_warnings = set()

    for warning in crop_warnings:
        if warning not in seen_warnings:
            seen_warnings.add(warning)
            unique_warnings.append(warning)

    message_lines.append("")
    message_lines.append("Crop warnings:")
    for warning in unique_warnings:
        message_lines.append("- {}".format(warning))

if failed_messages:
    message_lines.append("")
    message_lines.append("Warnings:")
    for msg in failed_messages:
        message_lines.append("- {}".format(msg))

if created_sheets:
    message_lines.append("")
    message_lines.append("Created sheets:")

    for sheet in created_sheets:
        try:
            message_lines.append(
                "{} - {}".format(sheet.SheetNumber, sheet.Name)
            )
        except:
            message_lines.append(str(sheet.Id.IntegerValue))

if created_views:
    message_lines.append("")
    message_lines.append("Created views and scales:")

    for view, scale in created_views:
        try:
            message_lines.append(
                "{} at 1:{}".format(view.Name, scale)
            )
        except:
            message_lines.append(
                "{} at 1:{}".format(view.Id.IntegerValue, scale)
            )

forms.alert(
    "\n".join(message_lines),
    title="Selection to Sheet Complete"
)