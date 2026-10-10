# pyRevit script: Export Element IDs inside a selected element to Excel (CSV)
from pyrevit import revit, DB, forms
import csv
import os

# Prompt user to select one element
selected_element = revit.pick_element()

if not selected_element:
    forms.alert("No element selected.", title="Error")
else:
    # Get bounding box of the selected element
    bbox = selected_element.get_BoundingBox(revit.doc.ActiveView)
    if not bbox:
        forms.alert("Selected element has no bounding box.", title="Error")
    else:
        # Create an outline and bounding box filter
        outline = DB.Outline(bbox.Min, bbox.Max)
        bbox_filter = DB.BoundingBoxIntersectsFilter(outline)

        # Collect elements that intersect with the bounding box
        collector = DB.FilteredElementCollector(revit.doc, revit.doc.ActiveView.Id)
        elements_inside = collector.WherePasses(bbox_filter).WhereElementIsNotElementType().ToElements()

        # Filter out the container element itself
        inside_ids = [el.Id.IntegerValue for el in elements_inside if el.Id != selected_element.Id]

        # Export to Excel (CSV format)
        if inside_ids:
            # Ask user to select folder
            folder_path = forms.pick_folder()
            if not folder_path:
                forms.alert("No folder selected. Export cancelled.", title="Cancelled")
            else:
                # Ask user to name the file
                file_name = forms.ask_for_string(default="ElementIDs_Inside_Selected", prompt="Enter file name (without extension):")
                if not file_name:
                    forms.alert("No file name provided. Export cancelled.", title="Cancelled")
                else:
                    output_file = os.path.join(folder_path, file_name + ".csv")

                    # Write to CSV (IronPython-compatible)
                    file = open(output_file, 'wb')  # binary mode avoids newline issues
                    writer = csv.writer(file)
                    writer.writerow(["Element ID"])
                    for eid in inside_ids:
                        writer.writerow([eid])
                    file.close()

                    forms.alert("Exported {} element IDs to:\n{}".format(len(inside_ids), output_file), title="Export Successful")
        else:
            forms.alert("No elements found inside the selected element.", title="Results")
