import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from System.Windows.Forms import Form, Label, TextBox, Button, GroupBox, HorizontalAlignment

# Get the current Revit application and document
uiapp = __revit__.Application
doc = __revit__.ActiveUIDocument.Document

def convert_internal_units(value, to_internal=True):
    """Convert units between internal and feet."""
    return UnitUtils.ConvertToInternalUnits(value, UnitTypeId.Feet) if to_internal else UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.Feet)

class PointConverter:
    def __init__(self, x, y, z, coord_sys='internal', input_units='ft'):
        """Convert coordinates into the desired coordinate system."""
        self.pt_internal = self.pt_survey = self.pt_project = None
        srvTrans, projTrans = self.GetSurveyTransform(), self.GetProjectTransform()

        if input_units == 'ft':
            x, y, z = map(lambda v: convert_internal_units(v, True), [x, y, z])

        if coord_sys.lower() == 'internal':
            self.pt_internal = XYZ(x, y, z)
            self.pt_survey = self.ApplyInverseTransformation(srvTrans, self.pt_internal)
            self.pt_project = self.ApplyInverseTransformation(projTrans, self.pt_internal)
        elif coord_sys.lower() == 'project':
            self.pt_project = XYZ(x, y, z)
            self.pt_internal = self.ApplyTransformation(projTrans, self.pt_project)
            self.pt_survey = self.ApplyInverseTransformation(srvTrans, self.pt_internal)
        elif coord_sys.lower() == 'survey':
            self.pt_survey = XYZ(x, y, z)
            self.pt_internal = self.ApplyTransformation(srvTrans, self.pt_survey)
            self.pt_project = self.ApplyInverseTransformation(projTrans, self.pt_internal)
        else:
            raise ValueError("Invalid 'coord_sys' value.")

    def GetSurveyTransform(self):
        return doc.ActiveProjectLocation.GetTotalTransform()

    def GetProjectTransform(self):
        basePtLoc = next((l for l in FilteredElementCollector(doc).OfClass(ProjectLocation).WhereElementIsNotElementType() if l.Name in ['Project', 'Projekt']), None)
        return basePtLoc.GetTotalTransform()

    def ApplyInverseTransformation(self, t, pt):
        return t.Inverse.OfPoint(pt)

    def ApplyTransformation(self, t, pt):
        return t.OfPoint(pt)

class FamilyPlacementForm(Form):
    def __init__(self):
        self.Text = 'Family Placement'
        self.Width = 400
        self.Height = 350

        self.groupBox = GroupBox()
        self.groupBox.Text = 'Point Information'
        self.groupBox.SetBounds(20, 20, 350, 250)
        self.Controls.Add(self.groupBox)

        labels = ['Point Description:', 'Group:', 'Northing:', 'Easting:', 'Elevation:']
        self.textBoxes = {}
        for i, label in enumerate(labels):
            lbl = Label()
            lbl.Text = label
            lbl.SetBounds(20, 30 + i * 40, 100, 40 if label in ['Point Description:', 'Group:'] else 20)
            self.groupBox.Controls.Add(lbl)

            txtBox = TextBox()
            txtBox.SetBounds(130, 30 + i * 40, 150, 20)
            txtBox.TextAlign = HorizontalAlignment.Left
            self.groupBox.Controls.Add(txtBox)
            self.textBoxes[label.split(':')[0].lower()] = txtBox

        self.submitButton = Button()
        self.submitButton.Text = 'Place Family'
        self.submitButton.SetBounds(150, 280, 150, 30)
        self.submitButton.Click += self.place_family
        self.Controls.Add(self.submitButton)

    def place_family(self, sender, event):
        try:
            description = self.textBoxes['point description'].Text
            group = self.textBoxes['group'].Text
            northing = float(self.textBoxes['northing'].Text)
            easting = float(self.textBoxes['easting'].Text)
            elevation = float(self.textBoxes['elevation'].Text)

            point_converter = PointConverter(easting, northing, elevation, coord_sys='survey', input_units='ft')
            internal_point = point_converter.pt_internal

            family_symbol = self.get_family_symbol('CoordinatePoint')
            if family_symbol:
                self.place_family_instance(family_symbol, internal_point, description, group, northing, easting, elevation)
            else:
                TaskDialog.Show('Error', "Could not find 'CoordinatePoint' family in the project.")
        except Exception as e:
            import traceback
            error_msg = "Error: {0}\n\nStack trace:\n{1}".format(str(e), traceback.format_exc())
            TaskDialog.Show('Error', error_msg)

    def get_family_symbol(self, family_name):
        if doc is None:
            TaskDialog.Show('Error', "Document is null. Please ensure Revit document is open.")
            return None

        collector = FilteredElementCollector(doc).OfClass(FamilySymbol).WhereElementIsElementType()
        for symbol in collector:
            if symbol.Family.Name == family_name:
                if not symbol.IsActive:
                    t = Transaction(doc, 'Activate Symbol')
                    t.Start()
                    symbol.Activate()
                    t.Commit()
                return symbol
        return None

    def place_family_instance(self, family_symbol, internal_point, description, group, northing, easting, elevation):
        t = Transaction(doc, 'Place Family')
        t.Start()
        try:
            family_instance = doc.Create.NewFamilyInstance(internal_point, family_symbol, Structure.StructuralType.NonStructural)
            
            # Set parameter values with error checking
            self.set_parameter_safely(family_instance, 'Point Description', description)
            self.set_parameter_safely(family_instance, 'Group', group)
            self.set_parameter_safely(family_instance, 'Northing', northing)
            self.set_parameter_safely(family_instance, 'Easting', easting)
            self.set_parameter_safely(family_instance, 'Elevation', elevation)
            
            t.Commit()
            TaskDialog.Show('Success', "Family placed at coordinates: ({0}, {1}, {2})".format(internal_point.X, internal_point.Y, internal_point.Z))
        except Exception as e:
            t.RollBack()
            import traceback
            error_msg = "Error: {0}\n\nStack trace:\n{1}".format(str(e), traceback.format_exc())
            TaskDialog.Show('Placement Error', error_msg)
    
    def set_parameter_safely(self, element, param_name, value):
        """Safely set parameter with error checking"""
        param = element.LookupParameter(param_name)
        if param is None:
            TaskDialog.Show('Parameter Warning', "Parameter '{0}' not found in family".format(param_name))
            return False
        
        if param.IsReadOnly:
            TaskDialog.Show('Parameter Warning', "Parameter '{0}' is read-only".format(param_name))
            return False
            
        try:
            # Make sure the value is appropriate for the parameter
            if param.StorageType == StorageType.String:
                param.Set(str(value))
            elif param.StorageType == StorageType.Double:
                param.Set(float(value))
            elif param.StorageType == StorageType.Integer:
                param.Set(int(value))
            elif param.StorageType == StorageType.ElementId:
                # This would need special handling depending on your needs
                TaskDialog.Show('Parameter Warning', "Parameter '{0}' is an ElementId type and needs special handling".format(param_name))
                return False
            else:
                TaskDialog.Show('Parameter Warning', "Parameter '{0}' has unsupported storage type: {1}".format(param_name, param.StorageType))
                return False
            return True
        except Exception as e:
            TaskDialog.Show('Parameter Error', "Error setting '{0}': {1}".format(param_name, str(e)))
            return False

if doc is not None:
    form = FamilyPlacementForm()
    form.ShowDialog()
else:
    TaskDialog.Show('Error', "Cannot access Revit document. Please ensure a document is open.")