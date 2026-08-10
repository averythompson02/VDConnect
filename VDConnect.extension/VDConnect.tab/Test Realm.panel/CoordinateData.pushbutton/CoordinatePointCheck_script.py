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

class PointChecker:
    def __init__(self):
        self.coord_points = self.get_coordinate_points()
    
    def get_coordinate_points(self):
        """Get all CoordinatePoint families in the document."""
        collector = FilteredElementCollector(doc).OfClass(FamilyInstance).WhereElementIsNotElementType()
        coord_points = [elem for elem in collector if elem.Symbol.Family.Name == 'CoordinatePoint']
        return coord_points
    
    def check_parameters(self):
        """Check if Northing, Easting, and Elevation parameters are filled out."""
        missing_params = []
        for point in self.coord_points:
            northing = point.LookupParameter('Northing')
            easting = point.LookupParameter('Easting')
            elevation = point.LookupParameter('Elevation')
            if not (northing and northing.AsDouble() and easting and easting.AsDouble() and elevation and elevation.AsDouble()):
                missing_params.append(point)
        return missing_params

class ParameterCheckForm(Form):
    def __init__(self):
        self.Text = 'Parameter Check'
        self.Width = 400
        self.Height = 350
        self.groupBox = GroupBox()
        self.groupBox.Text = 'Missing Parameters'
        self.groupBox.SetBounds(20, 20, 350, 250)
        self.Controls.Add(self.groupBox)
        self.submitButton = Button()
        self.submitButton.Text = 'Check Parameters'
        self.submitButton.SetBounds(150, 280, 150, 30)
        self.submitButton.Click += self.check_parameters
        self.Controls.Add(self.submitButton)
    
    def check_parameters(self, sender, event):
        checker = PointChecker()
        missing_params = checker.check_parameters()
        if missing_params:
            mark_numbers = [point.LookupParameter('Mark').AsString() for point in missing_params]
            TaskDialog.Show('Missing Parameters', '{0} CoordinatePoint families have missing parameters:\n{1}'.format(len(missing_params), ', '.join(mark_numbers)))
            self.fill_missing_parameters(missing_params)
        else:
            TaskDialog.Show('Success', 'All CoordinatePoint families have Northing, Easting, and Elevation parameters filled out.')
    
    def fill_missing_parameters(self, missing_params):
        """Fill out the missing parameters."""
        t = Transaction(doc, 'Fill Missing Parameters')
        t.Start()
        try:
            for point in missing_params:
                northing = point.LookupParameter('Northing')
                easting = point.LookupParameter('Easting')
                elevation = point.LookupParameter('Elevation')
                if not northing.AsDouble():
                    northing.Set(0.0)  # Set a default value or calculate as needed
                if not easting.AsDouble():
                    easting.Set(0.0)  # Set a default value or calculate as needed
                if not elevation.AsDouble():
                    elevation.Set(0.0)  # Set a default value or calculate as needed
            t.Commit()
            TaskDialog.Show('Success', 'Missing parameters have been filled out.')
        except Exception as e:
            t.RollBack()
            import traceback
            error_msg = "Error: {0}\n\nStack trace:\n{1}".format(str(e), traceback.format_exc())
            TaskDialog.Show('Error', error_msg)

if doc is not None:
    form = ParameterCheckForm()
    form.ShowDialog()
else:
    TaskDialog.Show('Error', "Cannot access Revit document. Please ensure a document is open.")
