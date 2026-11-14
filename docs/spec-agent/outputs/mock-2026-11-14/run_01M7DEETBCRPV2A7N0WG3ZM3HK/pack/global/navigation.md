SCR-001 /sign_in -> SCR-002, SCR-004, SCR-045, SCR-066, SCR-079, SCR-081, SCR-092, SCR-107, SCR-122, SCR-130, SCR-S001
SCR-002 /verify_phone_code -> SCR-004, SCR-045, SCR-066, SCR-081, SCR-092, SCR-107, SCR-122, SCR-130
SCR-004 /bookings -> SCR-005, SCR-006, SCR-013, SCR-014, SCR-045, SCR-080, SCR-S003, SCR-S005, SCR-S006
SCR-005 /booking_details/:booking_id -> SCR-006, SCR-014
SCR-006 /new_booking -> SCR-007
SCR-007 /booking_submitted/:booking_id -> -
SCR-008 /register_company -> SCR-006
SCR-012 /claims -> SCR-013
SCR-013 /raise_a_claim -> SCR-012
SCR-014 /tracked_bookings -> SCR-015, SCR-017
SCR-015 /trip_map -> SCR-016
SCR-016 /trip_progress/:trip_id -> SCR-017
SCR-017 /delivery_proof/:trip_id -> -
SCR-026 /my_trips -> SCR-027, SCR-030, SCR-102
SCR-027 /trip_details/:trip_id -> SCR-028
SCR-028 /trip_status -> -
SCR-030 /trip_document_form -> -
SCR-031 /trip_costs -> SCR-032
SCR-032 /record_expense -> SCR-033
SCR-033 /request_advance -> SCR-034
SCR-034 /report_incident -> -
SCR-045 /finance_management -> SCR-046, SCR-047, SCR-049, SCR-052, SCR-080, SCR-122, SCR-S003, SCR-S005, SCR-S006
SCR-046 /finance_management_details/:invoice_id -> SCR-047
SCR-047 /finance_management_new_or_edit -> SCR-048
SCR-048 /finance_management_status/:invoice_id -> SCR-130
SCR-049 /invoices_and_payments -> SCR-050, SCR-051
SCR-050 /invoices_and_payments_details/:payment_id -> SCR-051
SCR-051 /invoices_and_payments_new_or_edit -> -
SCR-052 /invoices_and_payments_status/:payment_id -> -
SCR-066 /branch_performance -> SCR-068, SCR-069, SCR-080, SCR-107, SCR-S003, SCR-S005, SCR-S006
SCR-067 /branch_comparison -> SCR-068
SCR-068 /branch_performance_detail/:trip_id -> SCR-130
SCR-069 /performance_filters -> SCR-067
SCR-078 /language_settings -> -
SCR-079 /verify -> SCR-080, SCR-S008, SCR-S009
SCR-080 /profile -> SCR-008, SCR-S003
SCR-081 /dispatch_assignment -> SCR-080, SCR-082, SCR-083, SCR-130, SCR-S003, SCR-S005, SCR-S006
SCR-082 /dispatch_assignment_details/:trip_id -> SCR-083
SCR-083 /dispatch_assignment_new_or_edit -> SCR-084
SCR-084 /dispatch_assignment_status/:trip_id -> SCR-081
SCR-092 /trip_updates_and_delivery_proof -> SCR-001, SCR-027, SCR-028, SCR-031, SCR-080, SCR-093, SCR-S003, SCR-S005, SCR-S006
SCR-093 /trip_updates_and_delivery_proof_details/:trip_update_id -> SCR-094
SCR-094 /trip_updates_and_delivery_proof_new_or_edit -> SCR-095
SCR-095 /trip_updates_and_delivery_proof_status/:trip_update_id -> SCR-031
SCR-102 /trip_status_updates_new_or_edit -> -
SCR-107 /fleet_management -> SCR-066, SCR-080, SCR-108, SCR-109, SCR-S003, SCR-S005, SCR-S006
SCR-108 /fleet_management_details/:truck_id -> SCR-109
SCR-109 /fleet_management_new_or_edit -> SCR-110
SCR-110 /fleet_management_status/:truck_id -> SCR-122
SCR-122 /compliance_management -> SCR-045, SCR-080, SCR-123, SCR-124, SCR-S003, SCR-S005, SCR-S006
SCR-123 /compliance_management_details/:compliance_record_id -> SCR-124
SCR-124 /compliance_management_new_or_edit -> SCR-125
SCR-125 /compliance_management_status/:compliance_record_id -> SCR-130
SCR-130 /people -> SCR-080, SCR-081, SCR-131, SCR-S003, SCR-S005, SCR-S006
SCR-131 /person_details -> SCR-132
SCR-132 /invite_or_edit_a_person -> SCR-081
SCR-S001 /forgot_password -> SCR-S002
SCR-S002 /reset_password -> SCR-001
SCR-S003 /settings -> SCR-078, SCR-S004, SCR-S006, SCR-S007
SCR-S004 /notification_settings -> -
SCR-S005 /notifications -> -
SCR-S006 /help_and_support -> -
SCR-S007 /delete_account -> -
SCR-S008 /consent_and_age -> -
SCR-S009 /permission_explanation -> -
