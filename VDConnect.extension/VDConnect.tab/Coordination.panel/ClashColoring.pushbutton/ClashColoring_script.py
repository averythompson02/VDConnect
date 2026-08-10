# -*- coding: utf-8 -*-
from pyrevit import revit, DB, script
import clr
import re
import os
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import OpenFileDialog, DialogResult
from System.Collections.Generic import List

# Add references for Excel handling
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    import csv

output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

def normalize_text(text):
    """Normalize text for comparison by removing special characters and converting to lowercase"""
    if not text:
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', str(text)).lower()

def find_element_ids_in_row(row_data, headers):
    """Find all potential element IDs in a row, checking all columns"""
    element_ids = set()
    
    # Common element ID column names to prioritize
    priority_columns = ['elementid', 'element_id', 'id', 'revit_id', 'elementidentifier']
    
    # First check priority columns
    for header in headers:
        normalized_header = normalize_text(header)
        if any(priority in normalized_header for priority in priority_columns):
            try:
                col_index = headers.index(header)
                if col_index < len(row_data):
                    val = str(row_data[col_index]).strip()
                    if val and val.lower() != 'nan':
                        eid = int(float(val))
                        if eid > 0:  # Valid Revit element IDs are positive
                            element_ids.add(eid)
            except (ValueError, IndexError):
                continue
    
    # If no IDs found in priority columns, check all columns for numeric values
    if not element_ids:
        for val in row_data:
            try:
                val_str = str(val).strip()
                if val_str and val_str.lower() != 'nan':
                    eid = int(float(val_str))
                    if eid > 0:  # Valid Revit element IDs are positive
                        element_ids.add(eid)
            except (ValueError, TypeError):
                continue
    
    return element_ids

def find_document_title_column(headers):
    """Find the document title column in headers"""
    title_keywords = ['document', 'title', 'doc', 'project', 'model', 'file']
    
    for i, header in enumerate(headers):
        normalized_header = normalize_text(header)
        if any(keyword in normalized_header for keyword in title_keywords):
            return i
    
    return None

def find_workset_column(headers):
    """Find the workset column in headers"""
    workset_keywords = ['workset', 'work_set', 'ws']
    
    for i, header in enumerate(headers):
        normalized_header = normalize_text(header)
        if any(keyword in normalized_header for keyword in workset_keywords):
            return i
    
    return None

def get_active_document_title():
    """Get the active Revit document title"""
    title = doc.Title
    if not title:
        title = os.path.splitext(os.path.basename(doc.PathName))[0] if doc.PathName else "Untitled"
    return title

def read_excel_file(file_path):
    """Read Excel file using pandas if available, otherwise use basic CSV reading"""
    if PANDAS_AVAILABLE and (file_path.endswith('.xlsx') or file_path.endswith('.xls')):
        try:
            df = pd.read_excel(file_path)
            headers = df.columns.tolist()
            data = df.values.tolist()
            return headers, data
        except Exception as e:
            output.print_md("⚠️ **Error reading Excel file with pandas:** {}".format(str(e)))
            return None, None
    else:
        # Fallback to CSV reading - compatible with older Python versions
        try:
            # Try different approaches for CSV reading
            try:
                # First try with UTF-8 BOM handling (newer Python)
                with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
                    reader = csv.reader(csvfile)
                    headers = next(reader)
                    data = list(reader)
                    return headers, data
            except TypeError:
                # Fallback for older Python versions without encoding parameter
                with open(file_path, 'rb') as csvfile:
                    # Read as binary and decode
                    content = csvfile.read()
                    # Try to decode UTF-8 with BOM, then fallback to UTF-8, then latin-1
                    try:
                        if content.startswith(b'\xef\xbb\xbf'):
                            content = content[3:]  # Remove BOM
                        decoded_content = content.decode('utf-8')
                    except UnicodeDecodeError:
                        try:
                            decoded_content = content.decode('latin-1')
                        except UnicodeDecodeError:
                            decoded_content = content.decode('cp1252')
                    
                    # Parse the decoded content
                    lines = decoded_content.splitlines()
                    reader = csv.reader(lines)
                    headers = next(reader)
                    data = list(reader)
                    return headers, data
        except Exception as e:
            output.print_md("⚠️ **Error reading file:** {}".format(str(e)))
            return None, None

# Main execution
dialog = OpenFileDialog()
dialog.Title = "Select Excel or CSV File"
dialog.Filter = "Excel files (*.xlsx;*.xls)|*.xlsx;*.xls|CSV files (*.csv)|*.csv|All files (*.*)|*.*"
dialog.Multiselect = False

if dialog.ShowDialog() == DialogResult.OK:
    file_path = dialog.FileName
    
    try:
        # Read the file
        headers, data = read_excel_file(file_path)
        
        if headers is None or data is None:
            output.print_md("❌ **Failed to read file.**")
            script.exit()
        
        # Get active document title
        active_doc_title = get_active_document_title()
        normalized_active_title = normalize_text(active_doc_title)
        
        # Find relevant columns
        doc_title_col = find_document_title_column(headers)
        workset_col = find_workset_column(headers)
        
        output.print_md("📄 **Active Document:** {}".format(active_doc_title))
        output.print_md("🔍 **Searching for matching rows...**")
        
        if doc_title_col is not None:
            output.print_md("✅ **Found document title column:** {}".format(headers[doc_title_col]))
        else:
            output.print_md("⚠️ **No document title column found - processing all rows**")
        
        # Process data
        total_rows = len(data)
        matching_rows = 0
        total_element_ids = set()
        valid_elements = []
        workset_counts = {}
        
        for row in data:
            if not row:
                continue
                
            # Check if this row matches the active document
            row_matches_doc = True  # Default to True if no doc title column found
            
            if doc_title_col is not None and doc_title_col < len(row):
                row_doc_title = str(row[doc_title_col]).strip()
                normalized_row_title = normalize_text(row_doc_title)
                row_matches_doc = normalized_active_title in normalized_row_title or normalized_row_title in normalized_active_title
            
            if not row_matches_doc:
                continue
                
            matching_rows += 1
            
            # Find element IDs in this row
            row_element_ids = find_element_ids_in_row(row, headers)
            total_element_ids.update(row_element_ids)
            
            # Get workset info if available
            workset_name = "Unknown"
            if workset_col is not None and workset_col < len(row):
                workset_name = str(row[workset_col]).strip()
                if not workset_name or workset_name.lower() == 'nan':
                    workset_name = "Unknown"
            
            # Validate elements in Revit
            for eid in row_element_ids:
                element_id = DB.ElementId(eid)
                element = doc.GetElement(element_id)
                if element and element.CanBeHidden(view):
                    valid_elements.append((element_id, element))
                    workset_counts[workset_name] = workset_counts.get(workset_name, 0) + 1
        
        if not valid_elements:
            output.print_md("⚠️ **No valid elements found to override.**")
            output.print_md("📊 **Summary:**")
            output.print_md("- Total rows in file: {}".format(total_rows))
            output.print_md("- Rows matching document: {}".format(matching_rows))
            output.print_md("- Total unique element IDs found: {}".format(len(total_element_ids)))
            output.print_md("- Valid elements in current view: 0")
            script.exit()
        
        # Apply overrides
        selection_ids = [elem[0] for elem in valid_elements]
        id_list = List[DB.ElementId](selection_ids)
        
        # Select and show elements
        uidoc.Selection.SetElementIds(id_list)
        uidoc.ShowElements(id_list)
        
        # Create override settings
        override_settings = DB.OverrideGraphicSettings()
        red = DB.Color(255, 0, 0)
        override_settings.SetProjectionLineColor(red)
        override_settings.SetSurfaceForegroundPatternColor(red)
        override_settings.SetCutForegroundPatternColor(red)
        override_settings.SetProjectionLineWeight(5)
        
        # Apply overrides in transaction
        trans = DB.Transaction(doc, "Override Selected Elements to Red")
        trans.Start()
        
        for element_id, element in valid_elements:
            view.SetElementOverrides(element_id, override_settings)
        
        trans.Commit()
        
        # Print summary
        output.print_md("## 🎯 **Override Complete!**")
        output.print_md("---")
        output.print_md("📊 **Summary:**")
        output.print_md("- **Total rows in file:** {}".format(total_rows))
        output.print_md("- **Rows matching document '{}':** {}".format(active_doc_title, matching_rows))
        output.print_md("- **Total unique element IDs found:** {}".format(len(total_element_ids)))
        output.print_md("- **Elements successfully overridden:** {}".format(len(valid_elements)))
        
        if workset_counts:
            output.print_md("📋 **Elements by Workset:**")
            for workset, count in sorted(workset_counts.items()):
                output.print_md("  - **{}:** {} elements".format(workset, count))
        
    except Exception as e:
        output.print_md("❌ **Error:** {}".format(str(e)))
        import traceback
        output.print_md("**Detailed Error:**")
        output.print_md("```")
        output.print_md(traceback.format_exc())
        output.print_md("```")

else:
    output.print_md("⚠️ **No file selected.**")