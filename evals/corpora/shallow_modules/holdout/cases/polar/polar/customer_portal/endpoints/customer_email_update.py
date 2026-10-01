"""Importer stub: keeps only the imports that reach the labelled package."""
from polar.customer_email_update.schemas import (
    CustomerEmailUpdateRequest,
    CustomerEmailUpdateVerifyRequest,
    CustomerEmailUpdateVerifyResponse,
)
from polar.customer_email_update.service import (
    InvalidCustomerEmailUpdate,
)
from polar.customer_email_update.service import (
    customer_email_update as customer_email_update_service,
)

# Placeholders for names other case files import from this module.
router = None
