import frappe

def execute():
    field = frappe.get_doc("DocField", {"parent": "Attendance", "fieldname": "status"})
    if "Invalid" not in field.options:
        field.options += "\nInvalid"
        field.save()
        frappe.db.commit()
