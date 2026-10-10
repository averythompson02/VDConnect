# -*- coding: utf-8 -*-
"""Get linked elements in current view crop region from visible linked models - OPTIMIZED."""

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, RevitLinkInstance, LocationCurve, LocationPoint,
    XYZ, Category, ElementId, BoundingBoxIntersectsFilter, Outline, 
    BuiltInCategory, ElementCategoryFilter, LogicalOrFilter
)
import Autodesk.Revit.DB
import sys

doc = revit.doc
uidoc = revit.uidoc
view = uidoc.ActiveView

element_info = []

if not hasattr(view, 'CropBoxActive') or not view.CropBoxActive:
    forms.alert("The crop region is not active in this view.", title="Error")
    sys.exit()

try:
    crop_manager = view.GetCropRegionShapeManager()
    crop_shape = crop_manager.GetCropShape()[0]
except:
    forms.alert("Could not get crop region shape.", title="Error")
    sys.exit()

crop_curves = [curve for curve in crop_shape]

# Get crop bounding box for quick filtering
min_x = min(curve.GetEndPoint(0).X for curve in crop_curves)
max_x = max(curve.GetEndPoint(0).X for curve in crop_curves)
min_y = min(curve.GetEndPoint(0).Y for curve in crop_curves)
max_y = max(curve.GetEndPoint(0).Y for curve in crop_curves)

try:
    view_range = view.GetViewRange()
    cut_plane_id = view_range.GetLevelId(Autodesk.Revit.DB.PlanViewPlane.CutPlane)
    cut_offset = view_range.GetOffset(Autodesk.Revit.DB.PlanViewPlane.CutPlane) or 0
    bottom_plane_id = view_range.GetLevelId(Autodesk.Revit.DB.PlanViewPlane.ViewDepthPlane)
    bottom_offset = view_range.GetOffset(Autodesk.Revit.DB.PlanViewPlane.ViewDepthPlane) or 0
    top_plane_id = view_range.GetLevelId(Autodesk.Revit.DB.PlanViewPlane.TopClipPlane)
    top_offset = view_range.GetOffset(Autodesk.Revit.DB.PlanViewPlane.TopClipPlane) or 0

    cut_level = doc.GetElement(cut_plane_id)
    bottom_level = doc.GetElement(bottom_plane_id)
    top_level = doc.GetElement(top_plane_id)

    if not cut_level or not bottom_level or not top_level:
        forms.alert("One or more view range levels could not be retrieved.", title="Error")
        sys.exit()

    cut_elevation = cut_level.Elevation + cut_offset
    bottom_elevation = bottom_level.Elevation + bottom_offset
    top_elevation = top_level.Elevation + top_offset

except Exception as e:
    forms.alert("Could not get view range: {}".format(str(e)), title="Error")
    sys.exit()

def point_in_polygon(point, polygon_curves):
    x, y = point.X, point.Y
    inside = False
    points = [curve.GetEndPoint(0) for curve in polygon_curves]
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i].X, points[i].Y
        xj, yj = points[j].X, points[j].Y
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def line_segment_intersection_2d(p1, p2, p3, p4):
    x1, y1 = p1.X, p1.Y
    x2, y2 = p2.X, p2.Y
    x3, y3 = p3.X, p3.Y
    x4, y4 = p4.X, p4.Y
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    if 0 <= t <= 1 and 0 <= u <= 1:
        int_x = x1 + t * (x2 - x1)
        int_y = y1 + t * (y2 - y1)
        int_z = p1.Z + t * (p2.Z - p1.Z)
        return XYZ(int_x, int_y, int_z)
    return None

def clip_line_to_polygon(start, end, polygon_curves):
    intersections = []
    start_inside = point_in_polygon(start, polygon_curves)
    end_inside = point_in_polygon(end, polygon_curves)
    if start_inside and end_inside:
        return start.DistanceTo(end)
    if not start_inside and not end_inside:
        for crop_curve in polygon_curves:
            intersection = line_segment_intersection_2d(start, end, crop_curve.GetEndPoint(0), crop_curve.GetEndPoint(1))
            if intersection:
                intersections.append(intersection)
        if len(intersections) == 2:
            return intersections[0].DistanceTo(intersections[1])
        else:
            return 0
    for crop_curve in polygon_curves:
        intersection = line_segment_intersection_2d(start, end, crop_curve.GetEndPoint(0), crop_curve.GetEndPoint(1))
        if intersection:
            intersections.append(intersection)
    if intersections:
        return start.DistanceTo(intersections[0]) if start_inside else intersections[0].DistanceTo(end)
    return 0

def get_element_elevations_and_levels(element):
    try:
        base_constraint = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.WALL_BASE_CONSTRAINT) or \
                         element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM) or \
                         element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM) or \
                         element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_START_LEVEL_PARAM) or \
                         element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_DUCT_LEVEL_PARAM)
        
        base_offset_param = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.WALL_BASE_OFFSET) or \
                           element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM) or \
                           element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_START_OFFSET_PARAM) or \
                           element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_OFFSET_PARAM)
        
        top_constraint = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.WALL_HEIGHT_TYPE)
        unconnected_height = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.WALL_USER_HEIGHT_PARAM)

        base_level_name = "Unknown"
        top_level_name = "Unknown"
        elem_base = None
        elem_top = None

        if base_constraint and base_constraint.HasValue:
            base_level = element.Document.GetElement(base_constraint.AsElementId())
            if base_level and hasattr(base_level, 'Elevation'):
                base_offset = base_offset_param.AsDouble() if base_offset_param and base_offset_param.HasValue else 0.0
                elem_base = base_level.Elevation + base_offset
                base_level_name = base_level.Name

        if top_constraint and top_constraint.HasValue:
            top_elem_id = top_constraint.AsElementId()
            if top_elem_id and top_elem_id.IntegerValue > 0:
                top_level = element.Document.GetElement(top_elem_id)
                if top_level and hasattr(top_level, 'Elevation'):
                    top_offset_param = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.WALL_TOP_OFFSET)
                    top_offset = top_offset_param.AsDouble() if top_offset_param and top_offset_param.HasValue else 0.0
                    elem_top = top_level.Elevation + top_offset
                    top_level_name = top_level.Name

        if elem_top is None and elem_base is not None:
            if unconnected_height and unconnected_height.HasValue:
                elem_height = unconnected_height.AsDouble()
                elem_top = elem_base + elem_height

        if elem_base is None or elem_top is None:
            try:
                bbox = element.get_BoundingBox(None)
                if bbox:
                    elem_base = bbox.Min.Z if elem_base is None else elem_base
                    elem_top = bbox.Max.Z if elem_top is None else elem_top
            except:
                pass

        return elem_base, elem_top, base_level_name, top_level_name

    except Exception as e:
        return None, None, "Unknown", "Unknown"

def element_in_view_range(elem_base, elem_top, bottom_elev, top_elev):
    if elem_base is None or elem_top is None:
        return True
    return not (elem_top < bottom_elev or elem_base > top_elev)

def get_element_type_name(element, link_doc):
    try:
        elem_type = link_doc.GetElement(element.GetTypeId())
        if elem_type:
            type_param = elem_type.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.SYMBOL_NAME_PARAM) or \
                        elem_type.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            if type_param:
                return type_param.AsString()
            return elem_type.Name if hasattr(elem_type, 'Name') else "Unknown"
        return "Unknown"
    except:
        return "Unknown"

def get_element_location(element, transform):
    try:
        location = element.Location
        if isinstance(location, LocationCurve):
            curve = location.Curve
            start = transform.OfPoint(curve.GetEndPoint(0))
            end = transform.OfPoint(curve.GetEndPoint(1))
            return {'type': 'curve', 'start': start, 'end': end}
        elif isinstance(location, LocationPoint):
            point = transform.OfPoint(location.Point)
            return {'type': 'point', 'point': point}
    except:
        pass
    
    try:
        bbox = element.get_BoundingBox(None)
        if bbox:
            center = XYZ(
                (bbox.Min.X + bbox.Max.X) / 2,
                (bbox.Min.Y + bbox.Max.Y) / 2,
                (bbox.Min.Z + bbox.Max.Z) / 2
            )
            point = transform.OfPoint(center)
            return {'type': 'point', 'point': point}
    except:
        pass
    
    return None

def get_element_diameter_or_size(element):
    try:
        diameter_param = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM) or \
                        element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM) or \
                        element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
        
        if diameter_param and diameter_param.HasValue:
            return round(diameter_param.AsDouble() * 12, 2)
        
        width_param = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
        height_param = element.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
        
        if width_param and width_param.HasValue and height_param and height_param.HasValue:
            width = round(width_param.AsDouble() * 12, 2)
            height = round(height_param.AsDouble() * 12, 2)
            return "{}x{}".format(width, height)
        
        return "N/A"
    except:
        return "N/A"

def quick_bbox_check(element, transform, min_x, max_x, min_y, max_y, min_z, max_z):
    """Quick bounding box check before detailed geometry analysis"""
    try:
        bbox = element.get_BoundingBox(None)
        if not bbox:
            return True  # Can't filter, let it through
        
        # Transform bbox to project coordinates
        bbox_min = transform.OfPoint(bbox.Min)
        bbox_max = transform.OfPoint(bbox.Max)
        
        # Check if bounding box intersects crop region
        if bbox_max.X < min_x or bbox_min.X > max_x:
            return False
        if bbox_max.Y < min_y or bbox_min.Y > max_y:
            return False
        if bbox_max.Z < min_z or bbox_min.Z > max_z:
            return False
        
        return True
    except:
        return True  # On error, let it through

link_instances = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()

if not link_instances:
    forms.alert("No linked files found in the project.", title="No Links")
    sys.exit()

print("Processing linked models with optimized filtering...")

categories_with_elements = {}

for link_instance in link_instances:
    if link_instance.IsHidden(view):
        continue
    
    link_doc = link_instance.GetLinkDocument()
    if not link_doc:
        continue
    
    transform = link_instance.GetTransform()
    
    print("  Processing '{}'...".format(link_instance.Name))
    
    # Use bounding box filter for initial spatial filtering
    # Transform crop box to link coordinates
    inverse_transform = transform.Inverse
    link_min = inverse_transform.OfPoint(XYZ(min_x, min_y, bottom_elevation))
    link_max = inverse_transform.OfPoint(XYZ(max_x, max_y, top_elevation))
    
    # Create outline with some buffer
    buffer = 10.0  # 10 feet buffer
    outline = Outline(
        XYZ(min(link_min.X, link_max.X) - buffer, min(link_min.Y, link_max.Y) - buffer, min(link_min.Z, link_max.Z) - buffer),
        XYZ(max(link_min.X, link_max.X) + buffer, max(link_min.Y, link_max.Y) + buffer, max(link_min.Z, link_max.Z) + buffer)
    )
    
    bbox_filter = BoundingBoxIntersectsFilter(outline)
    
    # Collect elements with spatial filter
    elements = FilteredElementCollector(link_doc) \
        .WhereElementIsNotElementType() \
        .WherePasses(bbox_filter) \
        .ToElements()
    
    print("    Spatial filter found {} candidates".format(len(elements)))
    
    processed = 0
    for element in elements:
        try:
            if not element.Category:
                continue
            
            category_name = element.Category.Name
            
            if category_name in ['Cameras', 'RVT Links', 'Entourage', 'Views']:
                continue
            
            # Quick bbox check before detailed analysis
            if not quick_bbox_check(element, transform, min_x, max_x, min_y, max_y, bottom_elevation, top_elevation):
                continue
            
            elem_base, elem_top, base_level_name, top_level_name = get_element_elevations_and_levels(element)
            
            if not element_in_view_range(elem_base, elem_top, bottom_elevation, top_elevation):
                continue
            
            loc_info = get_element_location(element, transform)
            if not loc_info:
                continue
            
            in_crop = False
            length_value = None
            
            if loc_info['type'] == 'curve':
                clipped_length = clip_line_to_polygon(loc_info['start'], loc_info['end'], crop_curves)
                if clipped_length > 0.001:
                    in_crop = True
                    length_value = round(clipped_length, 2)
            elif loc_info['type'] == 'point':
                if point_in_polygon(loc_info['point'], crop_curves):
                    in_crop = True
            
            if in_crop:
                if category_name not in categories_with_elements:
                    categories_with_elements[category_name] = 0
                categories_with_elements[category_name] += 1
                
                type_name = get_element_type_name(element, link_doc)
                size_info = get_element_diameter_or_size(element)
                
                element_info.append({
                    'Element ID': element.Id.IntegerValue,
                    'Category': category_name,
                    'Type': type_name,
                    'Length (ft)': length_value if length_value else "N/A",
                    'Size': size_info,
                    'Base Level': base_level_name,
                    'Top Level': top_level_name,
                    'Link Name': link_instance.Name
                })
                
                processed += 1
                if processed % 100 == 0:
                    print("    ...processed {} elements".format(processed))
        except:
            continue
    
    print("    Found {} elements in crop".format(processed))

print("\nProcessing complete!")
print("Total elements found: {}".format(len(element_info)))
print("Categories found: {}".format(len(categories_with_elements)))

sorted_categories = sorted(categories_with_elements.items(), key=lambda x: x[1], reverse=True)
for cat, count in sorted_categories[:20]:
    print("  {}: {} elements".format(cat, count))

output = script.get_output()
output.set_title("Element Summary")

if element_info:
    element_info.sort(key=lambda x: (x['Category'], x['Type']))
    
    output_text = "Linked Elements Visible in View:\n\n"
    output_text += "Total Elements Found: {}\n".format(len(element_info))
    output_text += "Categories Found: {}\n\n".format(len(categories_with_elements))
    
    output_text += "=" * 40 + "\n"
    output_text += "Elements by Category:\n"
    output_text += "=" * 40 + "\n"
    for cat, count in sorted_categories:
        output_text += "{}: {} elements\n".format(cat, count)
    
    output_text += "\n" + "=" * 40 + "\n"
    output_text += "\nView Range:\n"
    output_text += "Top: {:.2f} ft\n".format(top_elevation)
    output_text += "Cut: {:.2f} ft\n".format(cut_elevation)
    output_text += "Bottom: {:.2f} ft".format(bottom_elevation)
    
    output.print_md("**Element Summary**")
    output.print_code(output_text)
    
    export_to_excel = forms.alert(
        "Would you like to export the element data to Excel?",
        title="Export to Excel",
        yes=True,
        no=True
    )
    
    if export_to_excel:
        available_categories = ["{} ({} elements)".format(cat, count) 
                               for cat, count in sorted_categories]
        
        selected_categories_with_counts = forms.SelectFromList.show(
            available_categories,
            title="Select Categories to Export",
            multiselect=True,
            button_name="Export Selected Categories"
        )
        
        if not selected_categories_with_counts:
            forms.alert("No categories selected. Export cancelled.", title="Export Cancelled")
        else:
            selected_categories = [cat.split(' (')[0] for cat in selected_categories_with_counts]
            filtered_element_info = [e for e in element_info if e['Category'] in selected_categories]
            
            if not filtered_element_info:
                forms.alert("No elements found for selected categories.", title="No Data")
            else:
                import clr
                clr.AddReference('Microsoft.Office.Interop.Excel')
                from Microsoft.Office.Interop import Excel
                from System.Runtime.InteropServices import Marshal
                
                try:
                    excel = Excel.ApplicationClass()
                    excel.Visible = True
                    excel.DisplayAlerts = False
                    
                    workbook = excel.Workbooks.Add()
                    worksheet = workbook.Worksheets[1]
                    worksheet.Name = "Element Data"
                    
                    headers = ["Element ID", "Category", "Type", "Length (ft)", "Size", "Base Level", "Top Level", "Link Name"]
                    for col, header in enumerate(headers, start=1):
                        cell = worksheet.Cells[1, col]
                        cell.Value2 = header
                        cell.Font.Bold = True
                        cell.Interior.Color = 15773696
                    
                    for row, elem_dict in enumerate(filtered_element_info, start=2):
                        worksheet.Cells[row, 1].Value2 = elem_dict['Element ID']
                        worksheet.Cells[row, 2].Value2 = elem_dict['Category']
                        worksheet.Cells[row, 3].Value2 = elem_dict['Type']
                        worksheet.Cells[row, 4].Value2 = elem_dict['Length (ft)']
                        worksheet.Cells[row, 5].Value2 = elem_dict['Size']
                        worksheet.Cells[row, 6].Value2 = elem_dict['Base Level']
                        worksheet.Cells[row, 7].Value2 = elem_dict['Top Level']
                        worksheet.Cells[row, 8].Value2 = elem_dict['Link Name']
                    
                    worksheet.Columns.AutoFit()
                    
                    category_summary = {}
                    for elem in filtered_element_info:
                        cat = elem['Category']
                        if cat not in category_summary:
                            category_summary[cat] = {'count': 0, 'total_length': 0}
                        category_summary[cat]['count'] += 1
                        if elem['Length (ft)'] != "N/A":
                            category_summary[cat]['total_length'] += elem['Length (ft)']
                    
                    summary_sheet = workbook.Worksheets.Add()
                    summary_sheet.Name = "Summary"
                    summary_sheet.Move(Before=worksheet)
                    
                    summary_sheet.Cells[1, 1].Value2 = "View Name:"
                    summary_sheet.Cells[1, 2].Value2 = view.Name
                    summary_sheet.Cells[2, 1].Value2 = "Total Elements (Selected Categories):"
                    summary_sheet.Cells[2, 2].Value2 = len(filtered_element_info)
                    summary_sheet.Cells[4, 1].Value2 = "View Range - Top (ft):"
                    summary_sheet.Cells[4, 2].Value2 = top_elevation
                    summary_sheet.Cells[5, 1].Value2 = "View Range - Cut (ft):"
                    summary_sheet.Cells[5, 2].Value2 = cut_elevation
                    summary_sheet.Cells[6, 1].Value2 = "View Range - Bottom (ft):"
                    summary_sheet.Cells[6, 2].Value2 = bottom_elevation
                    
                    summary_sheet.Cells[1, 1].Font.Bold = True
                    
                    summary_sheet.Cells[8, 1].Value2 = "Summary by Category"
                    summary_sheet.Cells[8, 1].Font.Bold = True
                    summary_sheet.Cells[9, 1].Value2 = "Category"
                    summary_sheet.Cells[9, 2].Value2 = "Count"
                    summary_sheet.Cells[9, 3].Value2 = "Total Length (ft)"
                    summary_sheet.Cells[9, 1].Font.Bold = True
                    summary_sheet.Cells[9, 2].Font.Bold = True
                    summary_sheet.Cells[9, 3].Font.Bold = True
                    
                    row_num = 10
                    for cat in sorted(category_summary.keys()):
                        summary_sheet.Cells[row_num, 1].Value2 = cat
                        summary_sheet.Cells[row_num, 2].Value2 = category_summary[cat]['count']
                        summary_sheet.Cells[row_num, 3].Value2 = category_summary[cat]['total_length'] if category_summary[cat]['total_length'] > 0 else "N/A"
                        row_num += 1
                    
                    summary_sheet.Columns.AutoFit()
                    
                    forms.alert(
                        "Excel export completed successfully!\n\n{} elements exported from {} selected categories.".format(
                            len(filtered_element_info), len(selected_categories)
                        ),
                        title="Success"
                    )
                    
                except Exception as e:
                    forms.alert("Error exporting to Excel: {}".format(str(e)), title="Export Error")
                    try:
                        if 'workbook' in locals():
                            workbook.Close(False)
                        if 'excel' in locals():
                            excel.Quit()
                            Marshal.ReleaseComObject(excel)
                    except:
                        pass
    
else:
    print("\nNo elements found visible in this view.")