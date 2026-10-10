# -*- coding: utf-8 -*-
"""
Duplicate Selected Sheets

Main duplicate tool that uses settings saved by the separate
"Duplicate Sheet Settings" script.

Workflow:
    1. Select sheets in the Project Browser.
    2. Run this tool.
    3. Tool reads saved settings.
    4. Sheets are duplicated.

Compatibility:
    - Uses native ViewSheet.Duplicate(SheetDuplicateOption) when available.
    - Falls back to creating empty sheets with the same title block type when
      native sheet duplication options are not available.

Settings Config Section:
    DuplicateSelectedSheets

Expected settings saved by settings tool:
    duplicate_option_key
    copies_per_sheet
    sheet_number_suffix
    sheet_name_suffix
    append_copy_index
    open_last_created
"""

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()
output = script.get_output()

CONFIG_SECTION = "DuplicateSelectedSheets"


# ============================================================
# Config helpers
# ============================================================

def get_config_value(cfg, attr_name, default_value):
    """Safely get a value from pyRevit config."""
    try:
        value = getattr(cfg, attr_name)
        if value is None:
            return default_value
        return value
    except Exception:
        return default_value


def bool_from_config(value, default_value=False):
    """Convert stored config value to bool."""
    try:
        if value is None:
            return default_value
        return str(value) == "True"
    except Exception:
        return default_value


def int_from_config(value, default_value=1):
    """Convert stored config value to positive int."""
    try:
        result = int(value)
        if result < 1:
            return default_value
        return result
    except Exception:
        return default_value


def get_settings():
    """
    Read saved settings from the separate settings button.

    These defaults match the recommended settings script.
    """
    cfg = script.get_config(CONFIG_SECTION)

    settings = {
        "duplicate_option_key": get_config_value(
            cfg,
            "duplicate_option_key",
            "DuplicateSheetWithViewsAndDetailing"
        ),
        "copies_per_sheet": int_from_config(
            get_config_value(cfg, "copies_per_sheet", 1),
            1
        ),
        "sheet_number_suffix": get_config_value(
            cfg,
            "sheet_number_suffix",
            "-COPY"
        ),
        "sheet_name_suffix": get_config_value(
            cfg,
            "sheet_name_suffix",
            " Copy"
        ),
        "append_copy_index": bool_from_config(
            get_config_value(cfg, "append_copy_index", "True"),
            True
        ),
        "open_last_created": bool_from_config(
            get_config_value(cfg, "open_last_created", "False"),
            False
        )
    }

    return settings


# ============================================================
# Revit version / API helpers
# ============================================================

def native_sheet_duplicate_available():
    """
    Native sheet duplicate options are available when DB.SheetDuplicateOption exists.

    This is the safest cross-version check. Older Revit APIs will not have this enum.
    """
    return hasattr(DB, "SheetDuplicateOption")


def get_duplicate_option(option_key):
    """
    Convert saved duplicate method key to DB.SheetDuplicateOption.

    Returns:
        DB.SheetDuplicateOption value, or None if not supported.
    """
    if not native_sheet_duplicate_available():
        return None

    option_map = {
        "DuplicateEmptySheet": DB.SheetDuplicateOption.DuplicateEmptySheet,
        "DuplicateSheetWithDetailing": DB.SheetDuplicateOption.DuplicateSheetWithDetailing,
        "DuplicateSheetWithViewsOnly": DB.SheetDuplicateOption.DuplicateSheetWithViewsOnly,
        "DuplicateSheetWithViewsAndDetailing": DB.SheetDuplicateOption.DuplicateSheetWithViewsAndDetailing
    }

    return option_map.get(
        option_key,
        DB.SheetDuplicateOption.DuplicateEmptySheet
    )


def get_element_id_value(element):
    """Safely return an element id integer/string for reporting."""
    try:
        return element.Id.IntegerValue
    except Exception:
        try:
            return element.Id.Value
        except Exception:
            return "Unknown"


# ============================================================
# Sheet selection
# ============================================================

def is_real_sheet(element):
    """Return True for non-placeholder ViewSheet elements."""
    if element is None:
        return False

    if not isinstance(element, DB.ViewSheet):
        return False

    try:
        if element.IsPlaceholder:
            return False
    except Exception:
        pass

    return True


def get_selected_sheets_from_project_browser():
    """
    Get sheets selected in the Project Browser.

    Revit exposes selected Project Browser sheets via uidoc.Selection.GetElementIds().
    """
    selected_sheets = []

    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []

    for element_id in selected_ids:
        element = doc.GetElement(element_id)
        if is_real_sheet(element):
            selected_sheets.append(element)

    return selected_sheets


class SheetListItem(object):
    """Display wrapper for SelectFromList."""
    def __init__(self, sheet):
        self.sheet = sheet
        self.name = "{} - {}".format(sheet.SheetNumber, sheet.Name)

    def __str__(self):
        return self.name


def get_all_real_sheets():
    """Collect all non-placeholder sheets in the model."""
    sheets = []

    collector = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet)

    for sheet in collector:
        if is_real_sheet(sheet):
            sheets.append(sheet)

    sheets = sorted(sheets, key=lambda x: x.SheetNumber)

    return sheets


def pick_sheets_from_list():
    """
    Fallback sheet picker if the user did not select sheets in the Project Browser.
    """
    sheets = get_all_real_sheets()

    if not sheets:
        forms.alert(
            "No non-placeholder sheets were found in this model.",
            title="Duplicate Selected Sheets",
            exitscript=True
        )

    items = [SheetListItem(sheet) for sheet in sheets]

    selected_items = forms.SelectFromList.show(
        items,
        title="Select Sheets to Duplicate",
        multiselect=True,
        button_name="Duplicate Selected Sheets"
    )

    if not selected_items:
        return []

    return [item.sheet for item in selected_items]


def get_source_sheets():
    """
    Prefer Project Browser selection.
    If no valid sheets are selected, show a picker.
    """
    selected_sheets = get_selected_sheets_from_project_browser()

    if selected_sheets:
        return selected_sheets

    return pick_sheets_from_list()


# ============================================================
# Naming helpers
# ============================================================

def collect_existing_sheet_numbers():
    """Collect existing sheet numbers for conflict prevention."""
    existing_numbers = set()

    collector = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet)

    for sheet in collector:
        try:
            existing_numbers.add(sheet.SheetNumber)
        except Exception:
            pass

    return existing_numbers


def make_base_sheet_number(original_number, suffix, copy_index, copies_per_sheet, append_copy_index):
    """
    Build desired new sheet number before uniqueness check.

    If multiple copies are being made, the copy index is forced on even if the
    setting is off. This prevents repeated duplicate numbers.
    """
    if append_copy_index or copies_per_sheet > 1:
        return "{}{}{}".format(original_number, suffix, copy_index)

    return "{}{}".format(original_number, suffix)


def make_base_sheet_name(original_name, suffix, copy_index, copies_per_sheet, append_copy_index):
    """
    Build desired new sheet name.
    """
    if append_copy_index or copies_per_sheet > 1:
        return "{}{} {}".format(original_name, suffix, copy_index)

    return "{}{}".format(original_name, suffix)


def get_unique_sheet_number(base_number, existing_numbers):
    """
    Revit sheet numbers must be unique.

    If base_number already exists, this creates:
        base_number.1
        base_number.2
        base_number.3
    """
    if base_number not in existing_numbers:
        existing_numbers.add(base_number)
        return base_number

    counter = 1

    while True:
        candidate = "{}.{}".format(base_number, counter)

        if candidate not in existing_numbers:
            existing_numbers.add(candidate)
            return candidate

        counter += 1


# ============================================================
# Title block / fallback helpers
# ============================================================

def get_source_titleblock_type_id(source_sheet):
    """
    Return the type id of the first title block instance on the source sheet.

    If no title block exists, return InvalidElementId so Revit creates a sheet
    without a title block.
    """
    try:
        titleblocks = (
            DB.FilteredElementCollector(doc, source_sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements()
        )

        for titleblock in titleblocks:
            return titleblock.GetTypeId()

    except Exception:
        pass

    return DB.ElementId.InvalidElementId


def create_empty_sheet_fallback(source_sheet):
    """
    Create an empty sheet using the same title block type as the source sheet.

    This is used on older Revit versions where native sheet duplicate options
    are not available.
    """
    titleblock_type_id = get_source_titleblock_type_id(source_sheet)
    new_sheet = DB.ViewSheet.Create(doc, titleblock_type_id)

    return new_sheet


def try_copy_sheet_parameters(source_sheet, new_sheet):
    """
    Best-effort copy of simple writable sheet instance parameters.

    This helps fallback-created sheets retain some sheet metadata, but it avoids
    read-only parameters and ignores parameters that cannot be copied safely.
    """
    copied_count = 0
    skipped_count = 0

    try:
        source_params = list(source_sheet.Parameters)
    except Exception:
        return copied_count, skipped_count

    for source_param in source_params:
        try:
            if source_param is None:
                skipped_count += 1
                continue

            definition = source_param.Definition
            if definition is None:
                skipped_count += 1
                continue

            param_name = definition.Name

            # Do not copy sheet number and sheet name here.
            # Those are handled explicitly by the naming logic.
            if param_name in ["Sheet Number", "Sheet Name"]:
                skipped_count += 1
                continue

            target_param = new_sheet.LookupParameter(param_name)

            if target_param is None:
                skipped_count += 1
                continue

            if target_param.IsReadOnly:
                skipped_count += 1
                continue

            storage_type = source_param.StorageType

            if storage_type == DB.StorageType.String:
                target_param.Set(source_param.AsString() or "")
                copied_count += 1

            elif storage_type == DB.StorageType.Integer:
                target_param.Set(source_param.AsInteger())
                copied_count += 1

            elif storage_type == DB.StorageType.Double:
                target_param.Set(source_param.AsDouble())
                copied_count += 1

            elif storage_type == DB.StorageType.ElementId:
                target_param.Set(source_param.AsElementId())
                copied_count += 1

            else:
                skipped_count += 1

        except Exception:
            skipped_count += 1

    return copied_count, skipped_count


# ============================================================
# Duplicate helpers
# ============================================================

def duplicate_sheet(source_sheet, duplicate_option_key):
    """
    Duplicate one sheet.

    Returns:
        new ViewSheet
        used_native_api bool
    """
    duplicate_option = get_duplicate_option(duplicate_option_key)

    if duplicate_option is not None:
        try:
            new_sheet_id = source_sheet.Duplicate(duplicate_option)
            new_sheet = doc.GetElement(new_sheet_id)
            return new_sheet, True
        except Exception as ex:
            # If native duplicate fails for this source, let the caller report it.
            raise ex

    new_sheet = create_empty_sheet_fallback(source_sheet)
    return new_sheet, False


def duplicate_selected_sheets(source_sheets, settings):
    """
    Duplicate all selected source sheets.

    Returns:
        created_sheets
        skipped_items
        failed_items
        fallback_used
    """
    existing_numbers = collect_existing_sheet_numbers()

    created_sheets = []
    skipped_items = []
    failed_items = []
    fallback_used = False

    transaction_group = DB.TransactionGroup(doc, "Duplicate Multiple Sheets")
    transaction_group.Start()

    transaction = None

    try:
        transaction = DB.Transaction(doc, "Duplicate Sheets")
        transaction.Start()

        for source_sheet in source_sheets:
            if not is_real_sheet(source_sheet):
                skipped_items.append([
                    get_element_id_value(source_sheet),
                    "Placeholder or invalid sheet"
                ])
                continue

            for copy_index in range(1, settings["copies_per_sheet"] + 1):
                try:
                    new_sheet, used_native_api = duplicate_sheet(
                        source_sheet,
                        settings["duplicate_option_key"]
                    )

                    if not used_native_api:
                        fallback_used = True

                    if not is_real_sheet(new_sheet):
                        failed_items.append([
                            "{} - {}".format(source_sheet.SheetNumber, source_sheet.Name),
                            "Revit did not return a valid duplicated sheet"
                        ])
                        continue

                    base_number = make_base_sheet_number(
                        source_sheet.SheetNumber,
                        settings["sheet_number_suffix"],
                        copy_index,
                        settings["copies_per_sheet"],
                        settings["append_copy_index"]
                    )

                    base_name = make_base_sheet_name(
                        source_sheet.Name,
                        settings["sheet_name_suffix"],
                        copy_index,
                        settings["copies_per_sheet"],
                        settings["append_copy_index"]
                    )

                    unique_number = get_unique_sheet_number(
                        base_number,
                        existing_numbers
                    )

                    # Set sheet number first because Revit enforces uniqueness.
                    new_sheet.SheetNumber = unique_number
                    new_sheet.Name = base_name

                    # For fallback-created empty sheets, copy basic writable metadata.
                    if not used_native_api:
                        try_copy_sheet_parameters(source_sheet, new_sheet)

                    created_sheets.append(new_sheet)

                except Exception as ex:
                    failed_items.append([
                        "{} - {}".format(source_sheet.SheetNumber, source_sheet.Name),
                        str(ex)
                    ])

                    try:
                        logger.exception(
                            "Failed to duplicate sheet {}".format(source_sheet.Id)
                        )
                    except Exception:
                        pass

        transaction.Commit()
        transaction_group.Assimilate()

    except Exception as ex:
        try:
            if transaction and transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass

        try:
            transaction_group.RollBack()
        except Exception:
            pass

        forms.alert(
            "The duplication transaction failed and was rolled back.\n\n{}".format(ex),
            title="Duplicate Sheets Failed",
            exitscript=True
        )

    return created_sheets, skipped_items, failed_items, fallback_used


# ============================================================
# Reporting
# ============================================================

def show_summary(created_sheets, skipped_items, failed_items, fallback_used, settings):
    """Show pyRevit output summary."""
    output.close_others()

    output.print_md("# Duplicate Selected Sheets Summary")

    output.print_md("**Created sheets:** {}".format(len(created_sheets)))
    output.print_md("**Skipped sheets:** {}".format(len(skipped_items)))
    output.print_md("**Failed copies:** {}".format(len(failed_items)))

    output.print_md("## Settings Used")
    settings_rows = [
        ["Duplicate Method", settings["duplicate_option_key"]],
        ["Copies Per Selected Sheet", settings["copies_per_sheet"]],
        ["Sheet Number Suffix", settings["sheet_number_suffix"]],
        ["Sheet Name Suffix", settings["sheet_name_suffix"]],
        ["Append Copy Index", settings["append_copy_index"]],
        ["Open Last Created Sheet", settings["open_last_created"]],
    ]

    output.print_table(
        table_data=settings_rows,
        columns=["Setting", "Value"]
    )

    if fallback_used:
        output.print_md("## Compatibility Note")
        output.print_md(
            "Native sheet duplicate options were not available in this Revit API session. "
            "One or more sheets were created using the empty sheet fallback. "
            "Fallback-created sheets do not copy placed views, schedules, legends, or detailing."
        )

    if created_sheets:
        created_rows = []

        for sheet in created_sheets:
            try:
                sheet_link = output.linkify(sheet.Id)
            except Exception:
                sheet_link = str(get_element_id_value(sheet))

            created_rows.append([
                sheet.SheetNumber,
                sheet.Name,
                sheet_link
            ])

        output.print_md("## Created Sheets")
        output.print_table(
            table_data=created_rows,
            columns=["Sheet Number", "Sheet Name", "Element Id"]
        )

    if skipped_items:
        output.print_md("## Skipped")
        output.print_table(
            table_data=skipped_items,
            columns=["Element Id", "Reason"]
        )

    if failed_items:
        output.print_md("## Failed")
        output.print_table(
            table_data=failed_items,
            columns=["Source Sheet", "Error"]
        )

    message = "Created {} sheet(s).\nSkipped {}.\nFailed {}.".format(
        len(created_sheets),
        len(skipped_items),
        len(failed_items)
    )

    if fallback_used:
        message += (
            "\n\nCompatibility fallback was used for at least one sheet. "
            "Check the pyRevit output for details."
        )

    forms.alert(
        message,
        title="Duplicate Selected Sheets"
    )


# ============================================================
# Main
# ============================================================

def main():
    settings = get_settings()

    source_sheets = get_source_sheets()

    if not source_sheets:
        forms.alert(
            "No sheets were selected.",
            title="Duplicate Selected Sheets",
            exitscript=True
        )

    # Warn if saved settings request full duplication but current API only supports fallback.
    if not native_sheet_duplicate_available():
        if settings["duplicate_option_key"] != "DuplicateEmptySheet":
            forms.alert(
                "This Revit version/API does not expose native sheet duplicate options.\n\n"
                "The tool will create empty sheets with the same title block type instead.\n\n"
                "Placed views, schedules, legends, and detailing will not be copied.",
                title="Compatibility Fallback"
            )

    created_sheets, skipped_items, failed_items, fallback_used = duplicate_selected_sheets(
        source_sheets,
        settings
    )

    if created_sheets and settings["open_last_created"]:
        try:
            uidoc.ActiveView = created_sheets[-1]
        except Exception:
            pass

    show_summary(
        created_sheets,
        skipped_items,
        failed_items,
        fallback_used,
        settings
    )


main()