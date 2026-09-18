app_name = "loan_assignment"
app_title = "Loan Assignment"
app_publisher = "Gopinadh"
app_description = "Employee Loan Lifecycle module for ERPNext/HRMS"
app_email = "gopinadh.p@techbulls.co.in"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "loan_assignment",
# 		"logo": "/assets/loan_assignment/logo.png",
# 		"title": "Loan Assignment",
# 		"route": "/loan_assignment",
# 		"has_permission": "loan_assignment.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/loan_assignment/css/loan_assignment.css"
# app_include_js = "/assets/loan_assignment/js/loan_assignment.js"

# include js, css files in header of web template
# web_include_css = "/assets/loan_assignment/css/loan_assignment.css"
# web_include_js = "/assets/loan_assignment/js/loan_assignment.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "loan_assignment/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "loan_assignment/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "loan_assignment.utils.jinja_methods",
# 	"filters": "loan_assignment.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "loan_assignment.install.before_install"
# after_install = "loan_assignment.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "loan_assignment.uninstall.before_uninstall"
# after_uninstall = "loan_assignment.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "loan_assignment.utils.before_app_install"
# after_app_install = "loan_assignment.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "loan_assignment.utils.before_app_uninstall"
# after_app_uninstall = "loan_assignment.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "loan_assignment.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "loan_assignment.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["loan_assignment.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"loan_assignment.tasks.all"
# 	],
# 	"daily": [
# 		"loan_assignment.tasks.daily"
# 	],
# 	"hourly": [
# 		"loan_assignment.tasks.hourly"
# 	],
# 	"weekly": [
# 		"loan_assignment.tasks.weekly"
# 	],
# 	"monthly": [
# 		"loan_assignment.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "loan_assignment.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "loan_assignment.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "loan_assignment.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "loan_assignment.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["loan_assignment.utils.before_request"]
# after_request = ["loan_assignment.utils.after_request"]

# Job Events
# ----------
# before_job = ["loan_assignment.utils.before_job"]
# after_job = ["loan_assignment.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"loan_assignment.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

