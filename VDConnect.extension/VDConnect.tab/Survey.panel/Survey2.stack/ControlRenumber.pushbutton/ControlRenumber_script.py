# -*- coding: utf-8 -*-

from pyrevit import revit, DB, UI, forms, script

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


# -----------------------------------------------------------------------------
# Selection Filter
# -----------------------------------------------------------------------------

class FamilySelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance)

    def AllowReference(self, reference, point):
        return False


# -----------------------------------------------------------------------------
# Pick Example Family
# -----------------------------------------------------------------------------

try:
    picked_ref = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element,
        FamilySelectionFilter(),
        "Select a family instance to renumber"
    )
except:
    script.exit()

sample = doc.GetElement(picked_ref.ElementId)

if not isinstance(sample, DB.FamilyInstance):
    forms.alert(
        "Selected element is not a Family Instance.",
        exitscript=True
    )

family_id = sample.Symbol.Family.Id
family_name = sample.Symbol.Family.Name


# -----------------------------------------------------------------------------
# User Input
# -----------------------------------------------------------------------------

prefix = forms.ask_for_string(
    default="",
    prompt="Enter prefix (Example: FD-)",
    title="Mark Prefix"
)

if prefix is None:
    script.exit()

start_text = forms.ask_for_string(
    default="1",
    prompt="Starting Number",
    title="Start Number"
)

if start_text is None:
    script.exit()

try:
    start_number = int(start_text)
except:
    forms.alert(
        "Starting number must be an integer.",
        exitscript=True
    )


# -----------------------------------------------------------------------------
# Collect Matching Family Instances In Active View
# -----------------------------------------------------------------------------

instances = []

collector = (
    DB.FilteredElementCollector(doc, view.Id)
    .OfClass(DB.FamilyInstance)
    .WhereElementIsNotElementType()
)

for inst in collector:

    try:
        if inst.Symbol.Family.Id.IntegerValue != family_id.IntegerValue:
            continue

        point = None

        if isinstance(inst.Location, DB.LocationPoint):
            point = inst.Location.Point

        elif isinstance(inst.Location, DB.LocationCurve):
            point = inst.Location.Curve.Evaluate(0.5, True)

        if point:
            instances.append((inst, point))

    except:
        pass


if not instances:
    forms.alert(
        "No matching family instances found in current view.",
        exitscript=True
    )


# -----------------------------------------------------------------------------
# Sort
# Top -> Bottom
# Left -> Right
# -----------------------------------------------------------------------------

sorted_instances = sorted(
    instances,
    key=lambda x: (-x[1].Y, x[1].X)
)


# -----------------------------------------------------------------------------
# Confirm
# -----------------------------------------------------------------------------

proceed = forms.alert(
    "Family: {}\n\nFound {} instances in this view.\n\nProceed with numbering?".format(
        family_name,
        len(sorted_instances)
    ),
    yes=True,
    no=True
)

if not proceed:
    script.exit()


# -----------------------------------------------------------------------------
# Numbering Transaction
# -----------------------------------------------------------------------------

t = DB.Transaction(doc, "Renumber Family Marks")
t.Start()

try:

    # -------------------------------------------------------------------------
    # PASS 1
    # Assign Temporary Marks
    # -------------------------------------------------------------------------

    temp_counter = 1000000

    for inst, pt in sorted_instances:

        mark_param = inst.get_Parameter(
            DB.BuiltInParameter.ALL_MODEL_MARK
        )

        if not mark_param:
            continue

        if mark_param.IsReadOnly:
            continue

        try:
            mark_param.Set(
                "TEMP_{}".format(temp_counter)
            )
            temp_counter += 1

        except:
            pass


    doc.Regenerate()


    # -------------------------------------------------------------------------
    # PASS 2
    # Assign Final Marks
    # -------------------------------------------------------------------------

    count = start_number
    updated = 0
    skipped = 0

    for inst, pt in sorted_instances:

        mark_param = inst.get_Parameter(
            DB.BuiltInParameter.ALL_MODEL_MARK
        )

        if not mark_param:
            skipped += 1
            continue

        if mark_param.IsReadOnly:
            skipped += 1
            continue

        new_mark = "{}{:03d}".format(
            prefix,
            count
        )

        try:

            mark_param.Set(new_mark)

            updated += 1
            count += 1

        except:
            skipped += 1

    t.Commit()

except Exception as ex:

    t.RollBack()

    forms.alert(
        "Script failed:\n\n{}".format(str(ex)),
        exitscript=True
    )


# -----------------------------------------------------------------------------
# Results
# -----------------------------------------------------------------------------

forms.alert(
    "Completed.\n\n"
    "Family: {}\n"
    "Updated: {}\n"
    "Skipped: {}\n"
    "Last Mark: {}{:03d}".format(
        family_name,
        updated,
        skipped,
        prefix,
        count - 1
    ),
    title="Renumber Complete"
)