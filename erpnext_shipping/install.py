from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields
from .property_setters import get_property_setters
from .utils import make_property_setters


def sync_customisations():
	"""Create/refresh the app's custom fields and property setters.

	Run on install *and* on every migrate. Running it only on install meant a
	field added to custom_fields.py after the app was first installed never
	appeared on an existing site — the code referencing it then failed with
	"Field ... not found" and the cause was invisible, because nothing errored
	during migrate.

	Both helpers are idempotent: existing fields are updated in place, missing
	ones created.
	"""
	create_custom_fields(get_custom_fields())
	make_property_setters(get_property_setters())


def after_install():
	sync_customisations()
