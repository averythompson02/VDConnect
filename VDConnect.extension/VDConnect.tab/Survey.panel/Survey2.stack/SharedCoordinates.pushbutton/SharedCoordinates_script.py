# -*- coding: utf-8 -*-
__title__ = "Export Survey Points"
__doc__ = "Exports survey point, project base point coordinates, and angle to true north to Excel"

from Autodesk.Revit.DB import *
import xlsxwriter
import os
import math
from pyrevit import script, forms

doc = __revit__.ActiveUIDocument.Document

def get_survey_point_coordinates(document, doc_name="Document"):
    """Get Survey Point shared coordinates"""
    survey_point = None
    
    print("\n--- Getting Survey Point from: {} ---".format(doc_name))
    
    # Try to find Survey Point
    try:
        survey_points = FilteredElementCollector(document)\
            .OfCategory(BuiltInCategory.OST_SharedBasePoint)\
            .WhereElementIsNotElementType()\
            .ToElements()
        if survey_points and len(survey_points) > 0:
            survey_point = survey_points[0]
            print("  Found Survey Point using OST_SharedBasePoint")
    except:
        pass
    
    if not survey_point:
        try:
            all_basepoints = FilteredElementCollector(document)\
                .OfClass(BasePoint)\
                .WhereElementIsNotElementType()\
                .ToElements()
            for bp in all_basepoints:
                try:
                    if bp.IsShared:
                        survey_point = bp
                        print("  Found Survey Point using IsShared property")
                        break
                except:
                    pass
        except:
            pass
    
    if survey_point:
        try:
            # Get shared coordinates directly from parameters
            ns_param = survey_point.get_Parameter(BuiltInParameter.BASEPOINT_NORTHSOUTH_PARAM)
            ew_param = survey_point.get_Parameter(BuiltInParameter.BASEPOINT_EASTWEST_PARAM)
            elev_param = survey_point.get_Parameter(BuiltInParameter.BASEPOINT_ELEVATION_PARAM)
            
            if ns_param and ew_param and elev_param:
                ns = ns_param.AsDouble()
                ew = ew_param.AsDouble()
                elev = elev_param.AsDouble()
                
                if ns is not None and ew is not None and elev is not None:
                    print("  Successfully read Survey Point shared coordinates")
                    print("    N/S: {:.4f}, E/W: {:.4f}, Elev: {:.4f} (internal units)".format(ns, ew, elev))
                    return ns, ew, elev
        except Exception as e:
            print("  Error reading Survey Point: {}".format(str(e)))
    
    print("  WARNING: Could not find or read Survey Point!")
    return None, None, None

def get_project_base_point_shared_coords(document, doc_name="Document", link_transform=None):
    """Get Project Base Point shared coordinates using GetProjectPosition API"""
    
    print("\n--- Getting Project Base Point from: {} ---".format(doc_name))
    
    # Find the Project Base Point element
    project_base_point = None
    
    try:
        base_points = FilteredElementCollector(document)\
            .OfCategory(BuiltInCategory.OST_ProjectBasePoint)\
            .WhereElementIsNotElementType()\
            .ToElements()
        if base_points and len(base_points) > 0:
            project_base_point = base_points[0]
            print("  Found Project Base Point using OST_ProjectBasePoint")
    except:
        pass
    
    if not project_base_point:
        try:
            all_basepoints = FilteredElementCollector(document)\
                .OfClass(BasePoint)\
                .WhereElementIsNotElementType()\
                .ToElements()
            for bp in all_basepoints:
                try:
                    if not bp.IsShared:
                        project_base_point = bp
                        print("  Found Project Base Point using IsShared property")
                        break
                except:
                    pass
        except:
            pass
    
    if not project_base_point:
        print("  WARNING: Could not find Project Base Point!")
        return None, None, None
    
    try:
        # Get the internal position of the Project Base Point
        base_internal_position = project_base_point.Position
        print("  Project Base Point internal position: X={:.4f}, Y={:.4f}, Z={:.4f}".format(
            base_internal_position.X, base_internal_position.Y, base_internal_position.Z))
        
        # If this is a linked document, transform the position to the host's internal coordinates
        if link_transform:
            print("  Applying link transform...")
            base_internal_position = link_transform.OfPoint(base_internal_position)
            print("  After link transform: X={:.4f}, Y={:.4f}, Z={:.4f}".format(
                base_internal_position.X, base_internal_position.Y, base_internal_position.Z))
        
        # Use GetProjectPosition to convert internal coordinates to shared coordinates
        # This is the key: we use the HOST document's ProjectLocation
        project_location = doc.ActiveProjectLocation
        project_position = project_location.GetProjectPosition(base_internal_position)
        
        # ProjectPosition gives us the shared coordinates
        ns = project_position.NorthSouth
        ew = project_position.EastWest
        elev = project_position.Elevation
        
        print("  Project Base Point shared coordinates (via GetProjectPosition):")
        print("    N/S: {:.4f}, E/W: {:.4f}, Elev: {:.4f} (internal units)".format(ns, ew, elev))
        
        return ns, ew, elev
        
    except Exception as e:
        print("  Error getting Project Base Point coordinates: {}".format(str(e)))
        import traceback
        print("  " + traceback.format_exc())
        return None, None, None

def convert_to_project_units(value, spec_type_id):
    """Convert internal units to project display units"""
    if value is not None:
        project_units = doc.GetUnits()
        format_options = project_units.GetFormatOptions(spec_type_id)
        display_units = format_options.GetUnitTypeId()
        return UnitUtils.ConvertFromInternalUnits(value, display_units)
    return None

def get_angle_to_true_north(document):
    """Get angle to true north in degrees"""
    try:
        project_location = document.ActiveProjectLocation
        site_location = project_location.GetSiteLocation()
        angle_radians = site_location.AngleToTrueNorth
        angle_degrees = angle_radians * (180.0 / math.pi)
        return angle_degrees
    except:
        try:
            angle = document.ActiveProjectLocation.GetProjectPosition(XYZ(0, 0, 0)).Angle
            return angle * (180.0 / math.pi)
        except:
            return None

def get_coordinates_from_doc(document, doc_name="Document", link_transform=None):
    """Get all coordinates from a document"""
    
    # Get Survey Point
    survey_ns, survey_ew, survey_elev = get_survey_point_coordinates(document, doc_name)
    survey_ns_converted = convert_to_project_units(survey_ns, SpecTypeId.Length) if survey_ns is not None else None
    survey_ew_converted = convert_to_project_units(survey_ew, SpecTypeId.Length) if survey_ew is not None else None
    survey_elev_converted = convert_to_project_units(survey_elev, SpecTypeId.Length) if survey_elev is not None else None
    
    # Get Project Base Point using the correct API method
    base_ns, base_ew, base_elev = get_project_base_point_shared_coords(document, doc_name, link_transform)
    base_ns_converted = convert_to_project_units(base_ns, SpecTypeId.Length) if base_ns is not None else None
    base_ew_converted = convert_to_project_units(base_ew, SpecTypeId.Length) if base_ew is not None else None
    base_elev_converted = convert_to_project_units(base_elev, SpecTypeId.Length) if base_elev is not None else None
    
    # Get Angle to True North
    angle_to_true_north = get_angle_to_true_north(document)
    
    return {
        'survey_ns': survey_ns_converted,
        'survey_ew': survey_ew_converted,
        'survey_elev': survey_elev_converted,
        'base_ns': base_ns_converted,
        'base_ew': base_ew_converted,
        'base_elev': base_elev_converted,
        'angle_true_north': angle_to_true_north
    }

# Get coordinates from the main document
print("\n" + "="*60)
print("STARTING SURVEY POINT EXPORT")
print("="*60)

main_coords = get_coordinates_from_doc(doc, doc_name="Main Document", link_transform=None)

# Get all Revit links in the document
links = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()

# Show a form to select the links to include with all links pre-selected
link_names = [link.Name for link in links]

if link_names:
    selected_links = forms.SelectFromList.show(
        link_names,
        title="Select Revit Links to Export",
        multiselect=True,
        default=link_names
    )
    
    if selected_links is None:  # User cancelled
        script.exit()
else:
    selected_links = []

# Get coordinates from selected linked documents
linked_coords = []
if selected_links:
    for link in links:
        if link.Name in selected_links:
            link_doc = link.GetLinkDocument()
            if link_doc:
                # Get the link transform
                link_transform = link.GetTotalTransform()
                coords = get_coordinates_from_doc(link_doc, doc_name=link.Name, link_transform=link_transform)
                linked_coords.append((link.Name, coords))
            else:
                print("\nWARNING: Could not access linked document: {}".format(link.Name))

# Show a form to select the file path to save the Excel file
filepath = forms.save_file(file_ext='xlsx', default_name='survey_points.xlsx')

if not filepath:
    forms.alert("No file path selected. Exiting script.")
    script.exit()

# Create a new Excel workbook and add a worksheet
workbook = xlsxwriter.Workbook(filepath)
worksheet = workbook.add_worksheet("Survey Points")

# Define formats
header_format = workbook.add_format({
    'bold': True,
    'align': 'center',
    'bg_color': '#D3D3D3',
    'border': 1
})
red_highlight_format = workbook.add_format({
    'bg_color': '#FFB6C1',
    'border': 1
})
normal_format = workbook.add_format({'border': 1})

# Write headers
headers = ['File', 'Point Type', 'North/South', 'East/West', 'Elevation', 'Angle to True North']
worksheet.write_row('A1', headers, header_format)

# Track maximum column widths
col_widths = [len(header) for header in headers]

# Write main document coordinates
row = 1
main_data_survey_point = []
main_data_base_point = []

if main_coords['survey_ns'] is not None:
    main_data_survey_point = [
        'Main Document',
        'Survey Point',
        "{:.4f}".format(main_coords['survey_ns']),
        "{:.4f}".format(main_coords['survey_ew']),
        "{:.4f}".format(main_coords['survey_elev']),
        "{:.4f}°".format(main_coords['angle_true_north']) if main_coords['angle_true_north'] is not None else 'N/A'
    ]
    worksheet.write_row(row, 0, main_data_survey_point, normal_format)
    col_widths = [max(col_widths[i], len(str(main_data_survey_point[i]))) for i in range(len(main_data_survey_point))]
    row += 1

if main_coords['base_ns'] is not None:
    main_data_base_point = [
        'Main Document',
        'Project Base Point',
        "{:.4f}".format(main_coords['base_ns']),
        "{:.4f}".format(main_coords['base_ew']),
        "{:.4f}".format(main_coords['base_elev']),
        "{:.2f}°".format(main_coords['angle_true_north']) if main_coords['angle_true_north'] is not None else 'N/A'
    ]
    worksheet.write_row(row, 0, main_data_base_point, normal_format)
    col_widths = [max(col_widths[i], len(str(main_data_base_point[i]))) for i in range(len(main_data_base_point))]
    row += 1

# Write linked document coordinates and highlight differences
for link_name, coords in linked_coords:
    if coords['survey_ns'] is not None:
        data_survey_point = [
            link_name,
            'Survey Point',
            "{:.4f}".format(coords['survey_ns']),
            "{:.4f}".format(coords['survey_ew']),
            "{:.4f}".format(coords['survey_elev']),
            "{:.4f}°".format(coords['angle_true_north']) if coords['angle_true_north'] is not None else 'N/A'
        ]
        
        # Write with highlighting for differences
        for col_num in range(len(data_survey_point)):
            if col_num >= 2 and col_num <= 5 and main_data_survey_point:
                if data_survey_point[col_num] != main_data_survey_point[col_num]:
                    worksheet.write(row, col_num, data_survey_point[col_num], red_highlight_format)
                else:
                    worksheet.write(row, col_num, data_survey_point[col_num], normal_format)
            else:
                worksheet.write(row, col_num, data_survey_point[col_num], normal_format)
        
        col_widths = [max(col_widths[i], len(str(data_survey_point[i]))) for i in range(len(data_survey_point))]
        row += 1

    if coords['base_ns'] is not None:
        data_base_point = [
            link_name,
            'Project Base Point',
            "{:.4f}".format(coords['base_ns']),
            "{:.4f}".format(coords['base_ew']),
            "{:.4f}".format(coords['base_elev']),
            "{:.4f}°".format(coords['angle_true_north']) if coords['angle_true_north'] is not None else 'N/A'
        ]
        
        # Write with highlighting for differences
        for col_num in range(len(data_base_point)):
            if col_num >= 2 and col_num <= 5 and main_data_base_point:
                if data_base_point[col_num] != main_data_base_point[col_num]:
                    worksheet.write(row, col_num, data_base_point[col_num], red_highlight_format)
                else:
                    worksheet.write(row, col_num, data_base_point[col_num], normal_format)
            else:
                worksheet.write(row, col_num, data_base_point[col_num], normal_format)
        
        col_widths = [max(col_widths[i], len(str(data_base_point[i]))) for i in range(len(data_base_point))]
        row += 1

# Adjust column widths
for col_num, width in enumerate(col_widths):
    worksheet.set_column(col_num, col_num, width + 2)

# Close the workbook
workbook.close()

# Print results to console
print("\n" + "="*60)
print("SURVEY POINT EXPORT COMPLETE")
print("="*60)

print("\nMain Document:")
if main_coords['survey_ns'] is not None:
    print("\n  Survey Point:")
    print("    North/South: {:.4f}".format(main_coords['survey_ns']))
    print("    East/West:   {:.4f}".format(main_coords['survey_ew']))
    print("    Elevation:   {:.4f}".format(main_coords['survey_elev']))
else:
    print("  No Survey Point found.")

if main_coords['base_ns'] is not None:
    print("\n  Project Base Point:")
    print("    North/South: {:.4f}".format(main_coords['base_ns']))
    print("    East/West:   {:.4f}".format(main_coords['base_ew']))
    print("    Elevation:   {:.4f}".format(main_coords['base_elev']))
else:
    print("  No Project Base Point found.")

if main_coords['angle_true_north'] is not None:
    print("\n  Angle to True North: {:.4f}°".format(main_coords['angle_true_north']))
else:
    print("\n  Angle to True North: N/A")

for link_name, coords in linked_coords:
    print("\n" + "-"*60)
    print("Linked File: " + link_name)
    print("-"*60)
    
    if coords['survey_ns'] is not None:
        print("\n  Survey Point:")
        print("    North/South: {:.4f}".format(coords['survey_ns']))
        print("    East/West:   {:.4f}".format(coords['survey_ew']))
        print("    Elevation:   {:.4f}".format(coords['survey_elev']))
    else:
        print("  No Survey Point found.")

    if coords['base_ns'] is not None:
        print("\n  Project Base Point:")
        print("    North/South: {:.4f}".format(coords['base_ns']))
        print("    East/West:   {:.4f}".format(coords['base_ew']))
        print("    Elevation:   {:.4f}".format(coords['base_elev']))
    else:
        print("  No Project Base Point found.")

    if coords['angle_true_north'] is not None:
        print("\n  Angle to True North: {:.4f}°".format(coords['angle_true_north']))
    else:
        print("\n  Angle to True North: N/A")

print("\n" + "="*60)
print("Excel file saved to: " + filepath)
print("="*60 + "\n")

forms.alert("Export complete! File saved to:\n{}".format(filepath), title="Success")