# -*- coding: utf-8 -*-
"""pyRevit cabinet layout importer.
CABINET maps to Revit built-in instance Comments.
POWER, DATA, and SPARE map to same-named instance parameters.
"""
from __future__ import print_function

import csv
import os
import re
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET

from pyrevit import revit, DB, UI, forms, script

DOC = revit.doc
UIDOC = revit.uidoc
OUTPUT = script.get_output()
LOGGER = script.get_logger()

UNIT_TO_FEET = {
    "Feet": 1.0,
    "Inches": 1.0 / 12.0,
    "Millimeters": 1.0 / 304.8,
    "Meters": 1.0 / 0.3048,
}
NAMED_PARAMETER_MAP = {
    "POWER": "Power",
    "DATA": "Data",
    "SPARE": "Spare",
}
X_HEADERS = ("X", "XCOORD", "XCOORDINATE", "XCOORDINATES")
Y_HEADERS = ("Y", "YCOORD", "YCOORDINATE", "YCOORDINATES")


def text(value):
    if value is None:
        return ""
    try:
        return unicode(value).strip()  # noqa: F821, IronPython
    except NameError:
        return str(value).strip()
    except Exception:
        return str(value).strip()


def normalized(value):
    return re.sub(r"[^A-Z0-9]", "", text(value).upper())


def number(value):
    value = text(value).replace(",", "").replace("'", "").replace('"', "")
    if not value:
        raise ValueError("blank coordinate")
    return float(value)


def excel_column_index(reference):
    match = re.match(r"([A-Za-z]+)", reference or "")
    if not match:
        return 0
    result = 0
    for letter in match.group(1).upper():
        result = result * 26 + ord(letter) - ord("A") + 1
    return result - 1


def xml_text(element):
    return "" if element is None else "".join(element.itertext())


def read_csv_file(path):
    rows = []
    if sys.version_info[0] < 3:
        stream = open(path, "rb")
    else:
        stream = open(path, "r", newline="", encoding="utf-8-sig")
    try:
        for row in csv.reader(stream):
            rows.append([text(item) for item in row])
    finally:
        stream.close()
    return rows


def shared_strings(archive):
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [xml_text(item) for item in root]


def first_sheet_path(archive):
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = dict((r.attrib.get("Id"), r.attrib.get("Target")) for r in relationships)
        sheet = next(e for e in workbook.iter() if e.tag.endswith("sheet"))
        relationship_id = next(v for k, v in sheet.attrib.items()
                               if k.endswith("}id") or k == "r:id")
        target = targets[relationship_id].replace("\\", "/")
        if target.startswith("/"):
            return target.lstrip("/")
        if target.startswith("xl/"):
            return target
        while target.startswith("../"):
            target = target[3:]
        return "xl/" + target.lstrip("/")
    except Exception:
        return "xl/worksheets/sheet1.xml"


def cell_value(cell, strings):
    cell_type = cell.attrib.get("t", "")
    value_node = None
    inline_node = None
    for child in cell:
        if child.tag.endswith("}v") or child.tag == "v":
            value_node = child
        elif child.tag.endswith("}is") or child.tag == "is":
            inline_node = child
    if cell_type == "inlineStr":
        return xml_text(inline_node)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return strings[int(raw)]
        except Exception:
            return raw
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def read_xlsx_file(path):
    rows = []
    with zipfile.ZipFile(path, "r") as archive:
        strings = shared_strings(archive)
        root = ET.fromstring(archive.read(first_sheet_path(archive)))
        for row_node in root.iter():
            if not row_node.tag.endswith("}row"):
                continue
            values = {}
            for cell in row_node:
                if cell.tag.endswith("}c"):
                    index = excel_column_index(cell.attrib.get("r", ""))
                    values[index] = text(cell_value(cell, strings))
            rows.append([values.get(i, "") for i in range(max(values.keys()) + 1)]
                        if values else [])
    return rows


def read_file(path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".csv":
        return read_csv_file(path)
    if extension == ".xlsx":
        return read_xlsx_file(path)
    raise ValueError("Unsupported file type: {}".format(extension))


def header_map(row):
    result = {}
    for index, value in enumerate(row):
        key = normalized(value)
        if key == "CABINET":
            result["CABINET"] = index
        elif key in X_HEADERS:
            result["X"] = index
        elif key in Y_HEADERS:
            result["Y"] = index
        elif key in ("POWER", "DATA", "SPARE"):
            result[key] = index
    return result if all(k in result for k in ("CABINET", "X", "Y")) else None


def item(row, index):
    return text(row[index]) if index is not None and index < len(row) else ""


def parse_rows(rows):
    records, skipped = [], []
    headers = None
    for source_row, row in enumerate(rows, 1):
        if not row or not any(text(v) for v in row):
            if headers is not None:
                skipped.append(["", "", "", "Skipped: blank row", source_row])
            continue
        detected = header_map(row)
        if detected:
            headers = detected
            continue
        if headers is None:
            continue

        cabinet = item(row, headers.get("CABINET"))
        x_text = item(row, headers.get("X"))
        y_text = item(row, headers.get("Y"))
        missing = []
        if not cabinet:
            missing.append("Cabinet")
        if not x_text:
            missing.append("X Coordinate")
        if not y_text:
            missing.append("Y Coordinate")
        if missing:
            skipped.append([cabinet, x_text, y_text,
                            "Skipped: missing {}".format(", ".join(missing)), source_row])
            continue
        try:
            x_value, y_value = number(x_text), number(y_text)
        except Exception as error:
            skipped.append([cabinet, x_text, y_text,
                            "Skipped: invalid coordinate ({})".format(text(error)), source_row])
            continue
        records.append({
            "cabinet": cabinet, "x": x_value, "y": y_value,
            "x_text": x_text, "y_text": y_text,
            "power": item(row, headers.get("POWER")),
            "data": item(row, headers.get("DATA")),
            "spare": item(row, headers.get("SPARE")),
            "source_row": source_row, "duplicate": False,
        })
    return records, skipped


def mark_duplicates(records):
    seen, count = set(), 0
    for record in records:
        key = record["cabinet"].upper()
        if key in seen:
            record["duplicate"] = True
            count += 1
        else:
            seen.add(key)
    return count


def symbol_name(symbol):
    family = symbol.Family.Name
    parameter = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    type_name = parameter.AsString() if parameter else None
    return "{} : {}".format(family, type_name or symbol.Name)


def select_symbol():
    symbols = list(DB.FilteredElementCollector(DOC).OfClass(DB.FamilySymbol)
                   .WhereElementIsElementType().ToElements())
    if not symbols:
        forms.alert("No loadable family types were found.", exitscript=True)
    lookup = {}
    for symbol in symbols:
        base = symbol_name(symbol)
        label, suffix = base, 2
        while label in lookup:
            label = "{} [{}]".format(base, suffix)
            suffix += 1
        lookup[label] = symbol
    selected = forms.SelectFromList.show(
        sorted(lookup.keys(), key=lambda s: s.lower()),
        title="Select Cabinet Family Type", button_name="Use Family Type",
        multiselect=False, width=700, height=650)
    return lookup.get(selected) if selected else None


def find_named_parameter(element, name):
    parameter = element.LookupParameter(name)
    if parameter:
        return parameter
    for candidate in element.Parameters:
        try:
            if candidate.Definition.Name.lower() == name.lower():
                return candidate
        except Exception:
            pass
    return None


def set_named_parameter(element, name, value):
    value = text(value)
    if not value:
        return True, ""
    parameter = find_named_parameter(element, name)
    if parameter is None:
        return False, "Parameter '{}' not found".format(name)
    if parameter.IsReadOnly:
        return False, "Parameter '{}' is read-only".format(name)
    try:
        if parameter.StorageType == DB.StorageType.String:
            parameter.Set(value)
            return True, ""
        if parameter.SetValueString(value):
            return True, ""
        return False, "Parameter '{}' rejected '{}'".format(name, value)
    except Exception as error:
        return False, "Parameter '{}': {}".format(name, text(error))


def set_comments(element, cabinet):
    parameter = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if parameter is None:
        return False, "Built-in Comments parameter not found"
    if parameter.IsReadOnly:
        return False, "Built-in Comments parameter is read-only"
    try:
        parameter.Set(text(cabinet))
        return True, ""
    except Exception as error:
        return False, "Comments: {}".format(text(error))


def placement_level(z_value):
    try:
        if DOC.ActiveView.GenLevel:
            return DOC.ActiveView.GenLevel
    except Exception:
        pass
    levels = list(DB.FilteredElementCollector(DOC).OfClass(DB.Level)
                  .WhereElementIsNotElementType().ToElements())
    return min(levels, key=lambda level: abs(level.Elevation - z_value)) if levels else None


def create_instance(symbol, location, level):
    errors = []
    try:
        return DOC.Create.NewFamilyInstance(
            location, symbol, DB.Structure.StructuralType.NonStructural)
    except Exception as error:
        errors.append("point placement: {}".format(text(error)))
    if level:
        try:
            return DOC.Create.NewFamilyInstance(
                location, symbol, level, DB.Structure.StructuralType.NonStructural)
        except Exception as error:
            errors.append("level placement: {}".format(text(error)))
    raise RuntimeError("Family could not be placed. {}".format(" | ".join(errors)))


def main():
    path = forms.pick_file(files_filter="Excel or CSV (*.xlsx;*.csv)|*.xlsx;*.csv",
                           title="Select Cabinet Schedule")
    if not path:
        return
    try:
        records, skipped = parse_rows(read_file(path))
    except Exception as error:
        LOGGER.error(traceback.format_exc())
        forms.alert("The source file could not be read.\n\n{}".format(text(error)),
                    exitscript=True)
        return
    if not records:
        forms.alert("No valid rows found. Required headers: Cabinet, X Coord, Y Coord.",
                    exitscript=True)

    duplicates = mark_duplicates(records)
    symbol = select_symbol()
    if not symbol:
        return
    units = forms.CommandSwitchWindow.show(
        ["Feet", "Inches", "Millimeters", "Meters"],
        message="Select imported coordinate units:")
    if not units:
        return
    try:
        snaps = (UI.Selection.ObjectSnapTypes.Endpoints |
                 UI.Selection.ObjectSnapTypes.Intersections |
                 UI.Selection.ObjectSnapTypes.Nearest)
        base = UIDOC.Selection.PickPoint(snaps, "Pick schedule base point / origin")
    except Exception:
        return

    factor = UNIT_TO_FEET[units]
    level = placement_level(base.Z)
    placed, errors = 0, 0
    report = list(skipped)
    transaction = DB.Transaction(DOC, "Import Cabinet Layout")
    transaction.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
            DOC.Regenerate()
        with forms.ProgressBar(title="Placing Cabinets {value} of {max_value}",
                               cancellable=True) as progress:
            for index, record in enumerate(records, 1):
                if progress.cancelled:
                    break
                progress.update_progress(index, len(records))
                point = DB.XYZ(base.X + record["x"] * factor,
                               base.Y + record["y"] * factor, base.Z)
                subtransaction = DB.SubTransaction(DOC)
                subtransaction.Start()
                try:
                    instance = create_instance(symbol, point, level)
                    warnings = []
                    success, warning = set_comments(instance, record["cabinet"])
                    if not success:
                        warnings.append(warning)
                    source_values = {
                        "POWER": record["power"],
                        "DATA": record["data"],
                        "SPARE": record["spare"],
                    }
                    for source, parameter_name in NAMED_PARAMETER_MAP.items():
                        success, warning = set_named_parameter(
                            instance, parameter_name, source_values[source])
                        if not success:
                            warnings.append(warning)
                    subtransaction.Commit()
                    placed += 1
                    result = ["Placed"]
                    if record["duplicate"]:
                        result.append("Duplicate Cabinet")
                    if warnings:
                        result.append("Parameter warning: {}".format("; ".join(warnings)))
                    result = " | ".join(result)
                except Exception as error:
                    try:
                        subtransaction.RollBack()
                    except Exception:
                        pass
                    errors += 1
                    result = "Error: {}".format(text(error))
                    LOGGER.warning(traceback.format_exc())
                report.append([record["cabinet"], record["x_text"], record["y_text"],
                               result, record["source_row"]])
        transaction.Commit()
    except Exception as error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        LOGGER.error(traceback.format_exc())
        forms.alert("Import rolled back.\n\n{}".format(text(error)), exitscript=True)
        return

    report.sort(key=lambda row: row[4])
    filename = os.path.basename(path)
    OUTPUT.print_md("# Cabinet Layout Import")
    OUTPUT.print_md("**Imported File:** `{}`".format(filename))
    OUTPUT.print_md("**CABINET mapping:** Built-in instance Comments")
    OUTPUT.print_md("**Families Placed:** {}".format(placed))
    OUTPUT.print_md("**Rows Skipped:** {}".format(len(skipped)))
    OUTPUT.print_md("**Duplicates Found:** {}".format(duplicates))
    OUTPUT.print_md("**Errors:** {}".format(errors))
    OUTPUT.print_table(
        table_data=[row[:4] for row in report],
        columns=["Cabinet / Comments", "X Coordinate", "Y Coordinate", "Result"],
        title="Cabinet Placement Results")
    forms.alert(
        "Imported File: {}\n\nFamilies Placed: {}\nRows Skipped: {}\n"
        "Duplicates Found: {}\nErrors: {}".format(
            filename, placed, len(skipped), duplicates, errors),
        title="Cabinet Layout Import Complete", warn_icon=(errors > 0))


if __name__ == "__main__":
    main()
