"""WhatsApp user management utilities."""
import logging
from typing import Optional, Dict, Any
import httpx


class UserManager:
    """Manages user operations for WhatsApp integration."""

    def __init__(self, bot_instance):
        """Initialize user manager."""
        self.bot = bot_instance
        self.logger = logging.getLogger(__name__)

    async def get_or_create_user(
        self,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get existing user or create new one."""
        try:
            # Check if user exists
            user_data = await self._fetch_existing_user(platform_user_id)
            if user_data:
                return user_data

            # Create new user
            return await self._create_new_user(
                platform_user_id, first_name, last_name, phone_number
            )

        except Exception as e:
            self.logger.error("Error in get_or_create_user: %s", e, exc_info=True)
            return None

    async def _fetch_existing_user(
        self, platform_user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch existing user from API."""
        try:
            url = "%s/users" % self.bot.api_base_url
            params = {
                "platform_user_id": platform_user_id, 
                "platform": "whatsapp"
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)

                if response.status_code == 200:
                    users_data = response.json()
                    if users_data and len(users_data) > 0:
                        user = users_data[0]
                        self.logger.debug("Found existing user: %s", user.get('id'))
                        return user
                elif response.status_code != 404:
                    self.logger.warning(
                        "Unexpected response when fetching user: %s",
                        response.status_code
                    )

        except Exception as e:
            self.logger.error("Error fetching existing user: %s", e, exc_info=True)

        return None

    async def _create_new_user(
        self,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str],
        phone_number: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Create new user via API."""
        try:
            url = "%s/users" % self.bot.api_base_url

            # Prepare user data
            user_data = {
                "platform_user_id": platform_user_id,
                "platform": "whatsapp",
                "first_name": first_name,
                "last_name": last_name,
                "phone_number": phone_number
            }

            # Remove None values
            user_data = {k: v for k, v in user_data.items() if v is not None}

            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=user_data)

                if response.status_code == 201:
                    created_user = response.json()
                    self.logger.info("Created new user: %s", created_user.get('id'))
                    return created_user
                else:
                    error_detail = ""
                    try:
                        error_data = response.json()
                        error_detail = error_data.get("detail", "")
                    except Exception:
                        error_detail = response.text

                    self.logger.error(
                        "Failed to create user. Status: %s, Detail: %s",
                        response.status_code, error_detail
                    )

        except Exception as e:
            self.logger.error("Error creating new user: %s", e, exc_info=True)

        return None

    def extract_user_context(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract user context from WhatsApp response."""
        try:
            # Extract platform user ID
            platform_user_id = sender_info.get("id", "")
            if not platform_user_id:
                self.logger.warning("No platform_user_id found in sender_info")
                return None

            # Extract contact information
            contact_info = response.get("sender", {})
            if not contact_info:
                # Fallback to sender_info
                contact_info = sender_info

            # Get user names
            first_name = contact_info.get("pushname") or contact_info.get("name") or ""
            profile_name = contact_info.get("shortName", "")

            # Use profile name if first_name is empty
            if not first_name and profile_name:
                first_name = profile_name

            # Default name if still empty
            if not first_name:
                first_name = "WhatsApp User"

            # Get phone number if available
            phone_number = contact_info.get("formattedName") or ""

            # Check if it looks like a phone number
            if phone_number and not phone_number.startswith("+"):
                phone_number = ""  # Reset if invalid format

            # Check if user is authenticated
            is_authenticated = self._check_user_authentication(platform_user_id)

            return {
                "platform_user_id": platform_user_id,
                "first_name": first_name,
                "phone_number": phone_number if phone_number else None,
                "is_authenticated": is_authenticated,
                "platform": "whatsapp",
                "chat_id": chat_id
            }

        except Exception as e:
            self.logger.error("Error extracting user context: %s", e, exc_info=True)
            return None

    def _check_user_authentication(self, platform_user_id: str) -> bool:
        """Check if user is authenticated."""
        # Basic authentication check
        # In real implementation, you might check against a database or cache
        return bool(platform_user_id and len(platform_user_id) > 5)
