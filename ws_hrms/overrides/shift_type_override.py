from hrms.hr.doctype.shift_type.shift_type import ShiftType, calculate_working_hours
from hrms.hr.doctype.attendance.attendance import Attendance
from datetime import timedelta
from hrms.hr.utils import (
	validate_active_employee
)
import frappe
from frappe import _
from frappe.utils import cint, create_batch, add_days, now_datetime
from hrms.hr.doctype.employee_checkin.employee_checkin import (
	calculate_working_hours,
	mark_attendance_and_link_log,
)
from itertools import groupby

EMPLOYEE_CHUNK_SIZE = 50

def custom_get_attendance(self, logs):
    if len(logs) < 2:
        return "Invalid", 0, False, False, None, None

    valid_types = {"IN", "OUT"}
    types_in_logs = {log.log_type for log in logs}
    if not types_in_logs.issubset(valid_types):
        return "Invalid", 0, False, False, None, None

    if "IN" not in types_in_logs or "OUT" not in types_in_logs:
        return "Invalid", 0, False, False, None, None

    if types_in_logs == {"IN"}:
        return "Invalid", 0, False, False, None, None

    if types_in_logs == {"OUT"}:
        return "Invalid", 0, False, False, None, None

    in_logs = [log for log in logs if log.log_type == "IN"]
    out_logs = [log for log in logs if log.log_type == "OUT"]

    first_in = in_logs[0].time if in_logs else None
    first_out = out_logs[0].time if out_logs else None

    if not in_logs or not out_logs:
        return "Invalid", 0, False, False, None, None

    if first_out < first_in:
        return "Invalid", 0, False, False, None, None

    if first_out and first_in:
        duration = (first_out - first_in).total_seconds()
        if duration < 600:  # Less than 10 mins
            return "Invalid", 0, False, False, None, None

    # --- Continue default logic ---
    late_entry = early_exit = False
    total_working_hours, in_time, out_time = calculate_working_hours(
        logs,
        self.determine_check_in_and_check_out,
        self.working_hours_calculation_based_on
    )

    if (
        cint(self.enable_late_entry_marking)
        and in_time
        and in_time > logs[0].shift_start + timedelta(minutes=cint(self.late_entry_grace_period))
    ):
        late_entry = True

    if (
        cint(self.enable_early_exit_marking)
        and out_time
        and out_time < logs[0].shift_end - timedelta(minutes=cint(self.early_exit_grace_period))
    ):
        early_exit = True

    if self.working_hours_threshold_for_absent and total_working_hours < self.working_hours_threshold_for_absent:
        return "Absent", total_working_hours, late_entry, early_exit, in_time, out_time

    if self.working_hours_threshold_for_half_day and total_working_hours < self.working_hours_threshold_for_half_day:
        return "Half Day", total_working_hours, late_entry, early_exit, in_time, out_time

    return "Present", total_working_hours, late_entry, early_exit, in_time, out_time

# Bind to ShiftType
ShiftType.get_attendance = custom_get_attendance

@frappe.whitelist()
def custom_process_auto_attendance(self):
	if (
		not cint(self.enable_auto_attendance)
		or not self.process_attendance_after
		or not self.last_sync_of_checkin
	):
		return

	logs = self.get_employee_checkins()
	group_key = lambda x: (x["employee"], x["shift_start"])  # noqa

	for key, group in groupby(sorted(logs, key=group_key), key=group_key):
		single_shift_logs = list(group)
		attendance_date = key[1].date()
		employee = key[0]

		if not self.should_mark_attendance(employee, attendance_date):
			continue

		(
			attendance_status,
			working_hours,
			late_entry,
			early_exit,
			in_time,
			out_time,
		) = self.get_attendance(single_shift_logs)

		attendance_name = mark_attendance_and_link_log(
			single_shift_logs,
			attendance_status,
			attendance_date,
			working_hours,
			late_entry,
			early_exit,
			in_time,
			out_time,
			self.name,
		)

		# not submitting attendance if status is "Invalid"
		if attendance_status == "Invalid" and attendance_name:
			frappe.db.set_value("Attendance", attendance_name, "docstatus", 0)

		# commit after processing checkin logs to avoid losing progress
		frappe.db.commit()  # nosemgrep

	assigned_employees = self.get_assigned_employees(self.process_attendance_after, True)

	# mark absent in batches & commit to avoid losing progress
	# since this tries to process remaining attendance from Process Attendance After to Last Sync
	for batch in create_batch(assigned_employees, EMPLOYEE_CHUNK_SIZE):
		for employee in batch:
			self.mark_absent_for_dates_with_no_attendance(employee)
			self.mark_absent_for_half_day_dates(employee)

		frappe.db.commit()  # nosemgrep

	# ✅ update Last Sync of Checkin
	self.last_sync_of_checkin = add_days(now_datetime(), 1)
	self.save(ignore_permissions=True)
	frappe.db.commit()

ShiftType.process_auto_attendance = custom_process_auto_attendance



def custom_validate(self, method=None):
		from erpnext.controllers.status_updater import validate_status

		validate_status(self.status, ["Present", "Absent", "On Leave", "Half Day", "Work From Home", "Invalid"])
		validate_active_employee(self.employee)
		self.validate_attendance_date()
		self.validate_duplicate_record()
		self.validate_overlapping_shift_attendance()
		self.validate_employee_status()
		self.check_leave_record()
Attendance.validate = custom_validate
