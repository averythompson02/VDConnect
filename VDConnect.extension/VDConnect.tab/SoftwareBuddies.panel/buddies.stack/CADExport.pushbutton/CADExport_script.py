# -*- coding: utf-8 -*-

from pyrevit import revit, DB, forms
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc

# --------------------------------------------------
# Get selected views / sheets
# --------------------------------------------------

selected_views = []

for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)

    if isinstance(el, DB.View):
        if not el.IsTemplate:
            selected_views.append(el)

if not selected_views:
    forms.alert(
        "Select sheets or views in the Project Browser first.",
        exitscript=True
    )

# --------------------------------------------------
# Output Folder
# --------------------------------------------------

output_folder = forms.pick_folder()

if not output_folder:
    forms.alert("No folder selected.", exitscript=True)

# --------------------------------------------------
# DWG Version
# --------------------------------------------------

versions = [
    "AutoCAD 2018",
    "AutoCAD 2013",
    "AutoCAD 2010",
    "AutoCAD 2007"
]

selected_version = forms.SelectFromList.show(
    versions,
    title="Select DWG Version"
)

if not selected_version:
    forms.alert("No DWG version selected.", exitscript=True)

# --------------------------------------------------
# Export Options
# --------------------------------------------------

dwg_options = DB.DWGExportOptions()

if selected_version == "AutoCAD 2018":
    dwg_options.FileVersion = DB.ACADVersion.R2018

elif selected_version == "AutoCAD 2013":
    dwg_options.FileVersion = DB.ACADVersion.R2013

elif selected_version == "AutoCAD 2010":
    dwg_options.FileVersion = DB.ACADVersion.R2010

elif selected_version == "AutoCAD 2007":
    dwg_options.FileVersion = DB.ACADVersion.R2007

# Shared Coordinates?
use_shared = forms.alert(
    "Use Shared Coordinates?",
    yes=True,
    no=True
)

if use_shared:
    dwg_options.SharedCoords = True

# Merge views?
merge_views = forms.alert(
    "Merge views on sheets?",
    yes=True,
    no=True
)

dwg_options.MergedViews = merge_views

# --------------------------------------------------
# Export
# --------------------------------------------------

success = 0
failed = 0

for view in selected_views:

    try:

        export_ids = List[DB.ElementId]()
        export_ids.Add(view.Id)

        filename = view.Name.replace("/", "-")

        result = doc.Export(
            output_folder,
            filename,
            export_ids,
            dwg_options
        )

        if result:
            success += 1
        else:
            failed += 1

    except:
        failed += 1

# --------------------------------------------------
# Results
# --------------------------------------------------

forms.alert(
    "DWG Export Complete\n\n"
    "Exported: {}\n"
    "Failed: {}".format(success, failed),
    title="DWG Export"
)