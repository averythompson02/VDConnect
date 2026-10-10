# -*- coding: utf-8 -*-

from pyrevit import forms
from pyrevit import script
from pyrevit import DB

CONFIG_SECTION = "DuplicateSelectedSheets"


# ------------------------------------------------------
# Helpers
# ------------------------------------------------------

def get_config_value(cfg, name, default):
    try:
        value = getattr(cfg, name)

        if value is None:
            return default

        return value

    except:
        return default


def bool_from_config(value, default=False):
    try:
        return str(value) == "True"
    except:
        return default


def ask_yes_no(message, current_value=False):

    current_text = "Yes" if current_value else "No"

    result = forms.CommandSwitchWindow.show(
        ["Yes", "No"],
        message="{}\n\nCurrent: {}".format(
            message,
            current_text
        )
    )

    if result is None:
        return current_value

    return result == "Yes"


# ------------------------------------------------------
# Load Config
# ------------------------------------------------------

cfg = script.get_config(CONFIG_SECTION)

current_method = get_config_value(
    cfg,
    "duplicate_option_key",
    "DuplicateSheetWithViewsAndDetailing"
)

current_copies = str(
    get_config_value(
        cfg,
        "copies_per_sheet",
        1
    )
)

current_number_suffix = get_config_value(
    cfg,
    "sheet_number_suffix",
    "-COPY"
)

current_name_suffix = get_config_value(
    cfg,
    "sheet_name_suffix",
    " Copy"
)

current_append_index = bool_from_config(
    get_config_value(
        cfg,
        "append_copy_index",
        "True"
    ),
    True
)

current_open_last = bool_from_config(
    get_config_value(
        cfg,
        "open_last_created",
        "False"
    ),
    False
)


# ------------------------------------------------------
# Duplicate Method
# ------------------------------------------------------

if hasattr(DB, "SheetDuplicateOption"):

    method_map = {
        "Views + Detailing":
            "DuplicateSheetWithViewsAndDetailing",

        "Views Only":
            "DuplicateSheetWithViewsOnly",

        "Detailing Only":
            "DuplicateSheetWithDetailing",

        "Empty Sheet":
            "DuplicateEmptySheet"
    }

else:

    method_map = {
        "Empty Sheet":
            "DuplicateEmptySheet"
    }


current_display = "Empty Sheet"

for display_name, method_key in method_map.items():

    if method_key == current_method:
        current_display = display_name
        break


selected_method = forms.CommandSwitchWindow.show(
    sorted(method_map.keys()),
    message="Select Duplicate Method\n\nCurrent: {}".format(
        current_display
    )
)

if selected_method is not None:

    cfg.duplicate_option_key = method_map[
        selected_method
    ]


# ------------------------------------------------------
# Copies Per Sheet
# ------------------------------------------------------

copies = forms.ask_for_string(
    default=current_copies,
    prompt="Copies Per Selected Sheet",
    title="Duplicate Sheet Settings"
)

if copies is not None:

    try:
        copies_value = int(copies)

        if copies_value < 1:
            copies_value = 1

    except:
        copies_value = 1

    cfg.copies_per_sheet = copies_value


# ------------------------------------------------------
# Sheet Number Suffix
# ------------------------------------------------------

number_suffix = forms.ask_for_string(
    default=current_number_suffix,
    prompt="Sheet Number Suffix",
    title="Duplicate Sheet Settings"
)

if number_suffix is not None:
    cfg.sheet_number_suffix = number_suffix


# ------------------------------------------------------
# Sheet Name Suffix
# ------------------------------------------------------

name_suffix = forms.ask_for_string(
    default=current_name_suffix,
    prompt="Sheet Name Suffix",
    title="Duplicate Sheet Settings"
)

if name_suffix is not None:
    cfg.sheet_name_suffix = name_suffix


# ------------------------------------------------------
# Append Copy Index
# ------------------------------------------------------

append_index = ask_yes_no(
    "Append Copy Index?\n\nExample:\nA101-COPY1\nA101-COPY2",
    current_append_index
)

cfg.append_copy_index = str(
    append_index
)


# ------------------------------------------------------
# Open Last Created Sheet
# ------------------------------------------------------

open_last = ask_yes_no(
    "Open Last Created Sheet After Duplication?",
    current_open_last
)

cfg.open_last_created = str(
    open_last
)


# ------------------------------------------------------
# Save Config
# ------------------------------------------------------

script.save_config()


# ------------------------------------------------------
# Success Message
# ------------------------------------------------------

forms.alert(
    "Settings Saved Successfully\n\n"
    "Method: {}\n"
    "Copies: {}\n"
    "Number Suffix: {}\n"
    "Name Suffix: {}\n"
    "Append Index: {}\n"
    "Open Last Sheet: {}".format(
        cfg.duplicate_option_key,
        cfg.copies_per_sheet,
        cfg.sheet_number_suffix,
        cfg.sheet_name_suffix,
        cfg.append_copy_index,
        cfg.open_last_created
    ),
    title="Duplicate Sheet Settings"
)