# -*- coding: utf-8 -*-

__title__ = "Export Family\nCoordinates"

__author__ = "Avery Thompson"

__doc__ = """
Export Family Coordinates

Exports all instances of a selected family visible in the active view.

CSV Output:
• Northing
• Easting
• Elevation
• Description (Mark)

Workflow:
1. Start tool.
2. Select a family instance.
3. Script finds all matching family instances in the active view.
4. Coordinates are converted to Shared Coordinates.
5. Data is exported to CSV.
"""

from pyrevit import revit, DB, UI, forms, script
import csv

doc = revit.doc
uidoc = revit.uidoc


# ------------------------------------------------------
# Selection Filter
# ------------------------------------------------------
class FamilyInstanceSelectionFilter(UI.Selection.ISelectionFilter):

    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance)

    def AllowReference(self, reference, point):
        return False


# ------------------------------------------------------
# Pick Family Instance
# ------------------------------------------------------
try:
    reference = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element,
        FamilyInstanceSelectionFilter(),
        "Select a family instance"
    )

except Exception:
    script.exit()

selected_instance = doc.GetElement(reference.ElementId)
selected_family = selected_instance.Symbol.Family


# ------------------------------------------------------
# Save CSV Location
# ------------------------------------------------------
csv_path = forms.save_file(
    file_ext='csv',
    title='Save Coordinate Export'
)

if not csv_path:
    script.exit()

if not csv_path.lower().endswith(".csv"):
    csv_path += ".csv"


# ------------------------------------------------------
# Collect Matching Family Instances
# ------------------------------------------------------
collector = (
    DB.FilteredElementCollector(doc, doc.ActiveView.Id)
    .OfClass(DB.FamilyInstance)
    .WhereElementIsNotElementType()
)

matching_instances = []

for inst in collector:

    try:
        if inst.Symbol.Family.Id == selected_family.Id:
            matching_instances.append(inst)

    except Exception:
        continue


# ------------------------------------------------------
# Export CSV
# ------------------------------------------------------
rows_exported = 0

with open(csv_path, 'wb') as csvfile:

    writer = csv.writer(csvfile)

    writer.writerow([
        "Northing",
        "Easting",
        "Elevation",
        "Description"
    ])

    project_location = doc.ActiveProjectLocation

    for inst in matching_instances:

        try:
            location = inst.Location

            # Point-based family
            if isinstance(location, DB.LocationPoint):
                point = location.Point

            # Line-based family
            elif isinstance(location, DB.LocationCurve):
                point = location.Curve.Evaluate(0.5, True)

            else:
                continue

            shared_position = project_location.GetProjectPosition(point)

            northing = "{:.4f}".format(
                shared_position.NorthSouth
            )

            easting = "{:.4f}".format(
                shared_position.EastWest
            )

            elevation = "{:.4f}".format(
                shared_position.Elevation
            )

            description = ""

            mark_param = inst.get_Parameter(
                DB.BuiltInParameter.ALL_MODEL_MARK
            )

            if mark_param:
                mark_value = mark_param.AsString()

                if mark_value:
                    description = mark_value

            writer.writerow([
                northing,
                easting,
                elevation,
                description
            ])

            rows_exported += 1

        except Exception:
            continue


# ------------------------------------------------------
# Complete
# ------------------------------------------------------
forms.alert(
    "{} coordinates exported.\n\n{}".format(
        rows_exported,
        csv_path
    ),
    title="Export Complete"
)