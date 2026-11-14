SCR-001 /phone_sign_in -> SCR-002, SCR-003, SCR-010
SCR-002 /verify_phone -> SCR-003, SCR-010
SCR-003 /weekly_timetable -> SCR-001, SCR-004, SCR-006, SCR-007, SCR-010, SCR-S001, SCR-S002, SCR-S004
SCR-004 /class_details/:class_id -> SCR-005
SCR-005 /my_bookings -> SCR-004, SCR-006
SCR-006 /class_reminders -> -
SCR-007 /class_packs -> SCR-008
SCR-008 /pay_for_pack -> SCR-009
SCR-009 /purchase_confirmation/:payment_id -> -
SCR-010 /studio_schedule -> SCR-001, SCR-003, SCR-006, SCR-007, SCR-011, SCR-012, SCR-S001, SCR-S002, SCR-S004
SCR-011 /class_editor -> SCR-012
SCR-012 /class_attendance -> -
SCR-013 /language_settings -> -
SCR-S001 /profile -> SCR-S002
SCR-S002 /settings -> SCR-013, SCR-S003, SCR-S004, SCR-S005
SCR-S003 /notification_settings -> -
SCR-S004 /help_and_support -> -
SCR-S005 /delete_account -> -
