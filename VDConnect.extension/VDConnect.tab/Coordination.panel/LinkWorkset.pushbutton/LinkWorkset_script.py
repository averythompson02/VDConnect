import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from Autodesk.Revit.DB import *
from pyrevit import revit, forms
from System.Windows.Forms import Form, ListView, ListViewItem, Button, ColumnHeader, DockStyle, View, DialogResult, MessageBox, MessageBoxButtons, MessageBoxIcon, Label
from System.Drawing import Color, Font, FontStyle

# Custom failure handler to suppress specific warnings and errors
class CustomFailureHandler(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        failures = failuresAccessor.GetFailureMessages()
        for failure in failures:
            failure_id = failure.GetFailureDefinitionId()
            if failure_id == BuiltInFailures.GeneralFailures.NoOpenView:
                failuresAccessor.DeleteWarning(failure)
            elif failure_id == BuiltInFailures.GeneralFailures.NoGoodView:
                failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue

# Get the active document and UI document using pyRevit
doc = revit.doc
uidoc = revit.uidoc

if doc is None:
    forms.alert("No active document found. Please open a Revit document and try again.", exitscript=True)
else:
    # Collect all Revit links
    link_instances = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()

    # Create a dictionary to store link names, their worksets, and their status
    link_worksets = {}

    for link in link_instances:
        workset_id = link.WorksetId
        workset = doc.GetWorksetTable().GetWorkset(workset_id)
        workset_status = "Open" if workset.IsOpen else "Closed"
        link_type = doc.GetElement(link.GetTypeId())
        link_status = "Loaded" if link_type.GetLinkedFileStatus() == LinkedFileStatus.Loaded else "Unloaded"
        link_worksets[link.Name] = (workset_id, workset.Name, workset_status, link_status)

    # Create a form to select links and open their worksets
    class LinkSelectionForm(Form):
        def __init__(self, links):
            self.Text = "Select Links to Open Worksets"
            self.Width = 800
            self.Height = 600

            self.listview = ListView()
            self.listview.Dock = DockStyle.Fill
            self.listview.View = View.Details
            self.listview.CheckBoxes = True

            self.listview.Columns.Add(ColumnHeader(Text="Link Name", Width=250))
            self.listview.Columns.Add(ColumnHeader(Text="Workset Name", Width=250))
            self.listview.Columns.Add(ColumnHeader(Text="Workset Status", Width=150))
            self.listview.Columns.Add(ColumnHeader(Text="Link Status", Width=150))

            self.populate_listview(links)

            self.info_label = Label()
            self.info_label.Text = "Hit cancel or close on all following popups"
            self.info_label.Dock = DockStyle.Bottom
            self.info_label.Font = Font("Arial", 10, FontStyle.Bold)
            self.info_label.ForeColor = Color.Red

            self.ok_button = Button()
            self.ok_button.Text = "OK"
            self.ok_button.Dock = DockStyle.Bottom
            self.ok_button.Height = 30
            self.ok_button.DialogResult = DialogResult.OK

            self.Controls.Add(self.info_label)
            self.Controls.Add(self.ok_button)
            self.Controls.Add(self.listview)

        def populate_listview(self, links):
            self.listview.Items.Clear()
            sorted_links = sorted(links.items(), key=lambda x: x[0])  # Sort links by link name
            for link, (workset_id, workset_name, workset_status, link_status) in sorted_links:
                item = ListViewItem(link)
                item.SubItems.Add(workset_name)
                item.SubItems.Add(workset_status)
                item.SubItems.Add(link_status)
                if workset_status == "Open":
                    item.BackColor = Color.LightGreen
                else:
                    item.BackColor = Color.LightCoral
                self.listview.Items.Add(item)

    form = LinkSelectionForm(link_worksets)
    result = form.ShowDialog()

    if result == DialogResult.OK:
        selected_links = [item.Text for item in form.listview.CheckedItems]

        workset_link_count = {}
        for link_name in selected_links:
            workset_name = link_worksets[link_name][1]
            workset_link_count[workset_name] = workset_link_count.get(workset_name, 0) + 1

        for workset_name, count in workset_link_count.items():
            if count >= 2:
                MessageBox.Show("Warning: The workset '{}' contains 2 or more links. Opening this workset may result in long load times.".format(workset_name), 
                              "Warning", MessageBoxButtons.OK, MessageBoxIcon.Warning)

        with Transaction(doc, 'Open Worksets') as t:
            failure_options = t.GetFailureHandlingOptions()
            failure_options.SetFailuresPreprocessor(CustomFailureHandler())
            t.SetFailureHandlingOptions(failure_options)
            t.Start()
            for link_name in selected_links:
                workset_id = link_worksets[link_name][0]
                # Collect elements in the workset
                elements_in_workset = FilteredElementCollector(doc).WherePasses(ElementWorksetFilter(workset_id)).ToElementIds()
                uidoc.ShowElements(elements_in_workset)
            t.Commit()