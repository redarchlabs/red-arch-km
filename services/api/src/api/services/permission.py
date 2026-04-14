"""Permission service: re-exports from permission_config for backward compatibility."""

from api.services.permission_config import (
    calculate_user_masks_from_membership,
    permission_config_to_masks,
)

__all__ = [
    "calculate_user_masks_from_membership",
    "permission_config_to_masks",
]
