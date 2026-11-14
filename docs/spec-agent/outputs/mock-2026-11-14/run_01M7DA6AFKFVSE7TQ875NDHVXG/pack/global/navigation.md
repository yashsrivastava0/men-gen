SCR-001 /sign_in -> SCR-005, SCR-007, SCR-019, SCR-024, SCR-S004
SCR-002 /choose_account -> SCR-003
SCR-003 /business_account/:business_account_id -> -
SCR-004 /account_profile -> SCR-S006
SCR-005 /weekly_produce -> SCR-002, SCR-004, SCR-006, SCR-011, SCR-S006, SCR-S007
SCR-006 /produce_details/:produce_listing_id -> SCR-012
SCR-007 /my_listings -> SCR-001, SCR-002, SCR-004, SCR-008, SCR-S001, SCR-S002, SCR-S006, SCR-S007, SCR-S009
SCR-008 /produce_listing_form -> SCR-016
SCR-010 /listing_spot_check/:produce_listing_id -> SCR-018
SCR-011 /my_orders -> SCR-012
SCR-012 /review_order -> SCR-013
SCR-013 /order_placed/:order_id -> SCR-014
SCR-014 /pay_for_order -> SCR-015
SCR-015 /payment_result/:order_id -> SCR-011
SCR-016 /seller_orders -> SCR-017
SCR-017 /order_details/:order_id -> -
SCR-018 /order_oversight -> SCR-023
SCR-019 /delivery_assignments -> SCR-002, SCR-004, SCR-020, SCR-021, SCR-S006, SCR-S007
SCR-020 /delivery_details/:delivery_id -> SCR-021
SCR-021 /update_delivery -> -
SCR-022 /arrange_delivery -> SCR-S003
SCR-023 /delivery_oversight -> SCR-022
SCR-024 /marketplace_overview -> SCR-002, SCR-004, SCR-025, SCR-S006, SCR-S007
SCR-025 /seller_reviews -> SCR-026
SCR-026 /review_seller -> SCR-027
SCR-027 /marketplace_orders -> SCR-010
SCR-028 /language_settings -> -
SCR-S001 /farmer_form -> -
SCR-S002 /fpo_form -> -
SCR-S003 /transport_partner_form -> -
SCR-S004 /forgot_password -> SCR-S005
SCR-S005 /reset_password -> SCR-001
SCR-S006 /settings -> SCR-028, SCR-S007, SCR-S008
SCR-S007 /help_and_support -> -
SCR-S008 /delete_account -> -
SCR-S009 /permission_explanation -> -
