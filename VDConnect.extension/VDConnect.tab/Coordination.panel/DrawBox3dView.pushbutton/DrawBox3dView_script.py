__title__ = "Draw Box\n3d View"

from pyrevit import revit, DB
from pyrevit import forms
from Autodesk.Revit.UI import Selection
import getpass

# Get the current document
doc = revit.doc
uidoc = revit.uidoc

# Get the user name of the account currently using Revit
user_name = getpass.getuser()
view_name = "3d - " + user_name

try:
    # Start a transaction
    t = DB.Transaction(doc, "Create 3D View from Selection")
    t.Start()

    # Check for existing views with the same name and delete them
    existing_views = DB.FilteredElementCollector(doc).OfClass(DB.View3D).ToElements()
    for view in existing_views:
        if view.Name == view_name:
            if not view.IsTemplate:  # Ensure the view is not a template
                try:
                    doc.Delete(view.Id)
                except Exception as delete_error:
                    print("Error deleting view {}: {}".format(view.Name, delete_error))
            break

    # Get the active view and ensure it's a floor plan or RCP
    active_view = uidoc.ActiveView
    if not isinstance(active_view, DB.ViewPlan):
        raise ValueError("Active view is not a floor plan or RCP")

    # Get the view range from the active view
    view_range = active_view.GetViewRange()
    top_clip_plane = view_range.GetOffset(DB.PlanViewPlane.TopClipPlane)
    bottom_clip_plane = view_range.GetOffset(DB.PlanViewPlane.BottomClipPlane)

    # Get the levels associated with the top and bottom clip planes
    top_level_id = view_range.GetLevelId(DB.PlanViewPlane.TopClipPlane)
    bottom_level_id = view_range.GetLevelId(DB.PlanViewPlane.BottomClipPlane)
    top_level = doc.GetElement(top_level_id)
    bottom_level = doc.GetElement(bottom_level_id)

    # Add the elevation of the levels to the view range offsets
    top_clip_plane_elevation = top_clip_plane + top_level.Elevation
    bottom_clip_plane_elevation = bottom_clip_plane + bottom_level.Elevation

    # Prompt user to select a rectangular area
    selection_box = uidoc.Selection.PickBox(Selection.PickBoxStyle.Enclosing, "Select an area to create 3D view")

    # Ensure the selection box is valid
    if selection_box is None:
        raise ValueError("Invalid selection box")

    # Adjust the Z values of the selection box before creating the view
    selection_box_min = DB.XYZ(selection_box.Min.X, selection_box.Min.Y, bottom_clip_plane_elevation)
    selection_box_max = DB.XYZ(selection_box.Max.X, selection_box.Max.Y, top_clip_plane_elevation)

    # Ensure min and max values are in the correct order
    min_x = min(selection_box_min.X, selection_box_max.X)
    min_y = min(selection_box_min.Y, selection_box_max.Y)
    min_z = min(selection_box_min.Z, selection_box_max.Z)
    max_x = max(selection_box_min.X, selection_box_max.X)
    max_y = max(selection_box_min.Y, selection_box_max.Y)
    max_z = max(selection_box_min.Z, selection_box_max.Z)

    corrected_min = DB.XYZ(min_x, min_y, min_z)
    corrected_max = DB.XYZ(max_x, max_y, max_z)

    # Create a new 3D view
    view_family_type = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType).ToElements()
    view_family_type_3d = [v for v in view_family_type if v.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
    new_3d_view = DB.View3D.CreateIsometric(doc, view_family_type_3d.Id)

    # Create a bounding box from the corrected selection box
    bounding_box = DB.BoundingBoxXYZ()
    bounding_box.Min = corrected_min
    bounding_box.Max = corrected_max

    # Ensure the bounding box is not empty with a more explicit check
    if (bounding_box.Min.X >= bounding_box.Max.X or
        bounding_box.Min.Y >= bounding_box.Max.Y or
        bounding_box.Min.Z >= bounding_box.Max.Z):
        raise ValueError("Bounding box cannot be empty")

    new_3d_view.SetSectionBox(bounding_box)

    # Name the new view with "3d - " followed by the user name of the account currently using Revit
    new_3d_view.Name = view_name

    # Commit the transaction
    doc.Regenerate()
    t.Commit()

    # Save the document to ensure the view is retained
    doc.Save()

    # Activate the new view and zoom to fit
    uidoc.ActiveView = new_3d_view
    uidoc.RefreshActiveView()

    forms.alert("3D view '{}' created and activated successfully!".format(view_name), exitscript=True)

except Exception as e:
    # Roll back the transaction if an error occurs
    if t.HasStarted():
        t.RollBack()
    forms.alert("An error occurred: {}".format(e), exitscript=True)