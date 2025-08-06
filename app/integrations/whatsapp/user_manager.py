"""
User management for WhatsApp Integration.

Handles user creation, authorization, and context extraction
to reduce complexity in main WhatsApp bot.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.crud import user_crud


class WhatsAppUserManager:
    """Manages WhatsApp user operations with reduced complexity."""

    def __init__(
        self,
        agent_id: str,
        db_session_factory: Optional[async_sessionmaker[AsyncSession]],
        logger: logging.LoggerAdapter,
    ):
        self.agent_id = agent_id
        self.db_session_factory = db_session_factory
        self.logger = logger

    def extract_platform_user_id(self, sender_info: Dict[str, Any]) -> Optional[str]:
        """Extract platform user ID from sender info."""
        platform_user_id = sender_info.get("id", "")
        if not platform_user_id:
            self.logger.warning("No platform_user_id found in sender_info")
            return None
        return platform_user_id

    def extract_contact_info(
        self, response: Dict[str, Any], sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract contact information with fallback logic."""
        contact_info = response.get("sender", {})
        if not contact_info:
            contact_info = sender_info
        return contact_info

    def extract_user_names(self, contact_info: Dict[str, Any]) -> str:
        """Extract user name with fallback logic."""
        first_name = contact_info.get("pushname") or contact_info.get("name") or ""
        profile_name = contact_info.get("shortName", "")

        if not first_name and profile_name:
            first_name = profile_name

        if not first_name:
            first_name = "WhatsApp User"

        return first_name

    def extract_phone_number(self, contact_info: Dict[str, Any]) -> Optional[str]:
        """Extract and validate phone number."""
        phone_number = contact_info.get("formattedName") or ""

        if phone_number and not phone_number.startswith("+"):
            phone_number = ""  # Reset if invalid format

        return phone_number if phone_number else None

    def create_user_context(
        self,
        platform_user_id: str,
        first_name: str,
        phone_number: Optional[str],
        chat_id: str,
    ) -> Dict[str, Any]:
        """Create base user context dictionary."""
        return {
            "platform_user_id": platform_user_id,
            "first_name": first_name,
            "phone_number": phone_number,
            "platform": "whatsapp",
            "chat_id": chat_id,
        }

    async def extract_user_context(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract user context from WhatsApp response with reduced complexity."""
        try:
            # Extract platform user ID
            platform_user_id = self.extract_platform_user_id(sender_info)
            if not platform_user_id:
                return None

            # Extract contact information
            contact_info = self.extract_contact_info(response, sender_info)

            # Get user names
            first_name = self.extract_user_names(contact_info)

            # Get phone number
            phone_number = self.extract_phone_number(contact_info)

            # Create user context
            user_context = self.create_user_context(
                platform_user_id, first_name, phone_number, chat_id
            )

            # Get or create user in database
            user_data = await self.get_or_create_user(
                platform_user_id=platform_user_id,
                first_name=first_name,
                phone_number=phone_number,
            )

            if user_data:
                user_context.update(user_data)

            return user_context

        except (KeyError, AttributeError, TypeError) as e:
            self.logger.error("Error in extract_user_context: %s", e, exc_info=True)
            return None

    def create_fallback_data(
        self, first_name: str, platform_user_id: str, last_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create fallback user data when database is unavailable."""
        fallback_data = {
            "first_name": first_name,
            "platform_user_id": platform_user_id,
            "is_authenticated": False,
        }
        if last_name:
            fallback_data["last_name"] = last_name
        return fallback_data

    def prepare_user_details(
        self, first_name: str, last_name: Optional[str], phone_number: Optional[str]
    ) -> Dict[str, Any]:
        """Prepare user details for database operations."""
        user_details = {
            "first_name": first_name,
            "username": first_name,  # Use first_name as username fallback
        }
        if last_name:
            user_details["last_name"] = last_name
        if phone_number:
            user_details["phone_number"] = phone_number
        return user_details

    async def check_user_authorization(
        self, session: AsyncSession, user_id: int
    ) -> bool:
        """Check if user is authorized for this agent."""
        auth_record = await user_crud.get_agent_user_authorization(
            session, self.agent_id, user_id
        )
        return bool(auth_record and auth_record.is_authorized) if auth_record else False

    async def auto_authorize_user_with_phone(
        self, session: AsyncSession, user_id: int, platform_user_id: str, phone_number: str
    ) -> bool:
        """Auto-authorize new user with phone number."""
        auth_record = await user_crud.update_agent_user_authorization(
            session, agent_id=self.agent_id, user_id=user_id, is_authorized=True
        )
        is_authorized = bool(auth_record and auth_record.is_authorized) if auth_record else False
        if is_authorized:
            self.logger.info(
                f"Auto-authorized WhatsApp user {platform_user_id} with phone {phone_number}"
            )
        return is_authorized

    def build_user_data_result(
        self, user_db: Any, platform_user_id: str, is_authorized: bool
    ) -> Dict[str, Any]:
        """Build final user data result with optional fields."""
        user_data_result = {
            "user_id": user_db.id,
            "first_name": user_db.first_name,
            "username": user_db.username,
            "platform_user_id": platform_user_id,
            "platform": "whatsapp",
            "is_authenticated": is_authorized,
        }

        # Add last_name only if it's not None or empty
        if user_db.last_name is not None and user_db.last_name.strip():
            user_data_result["last_name"] = user_db.last_name

        # Add phone_number only if it's not None or empty
        if user_db.phone_number is not None and user_db.phone_number.strip():
            user_data_result["phone_number"] = user_db.phone_number

        return user_data_result

    async def get_or_create_user(
        self,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get or create user with reduced complexity through delegation.

        Args:
            platform_user_id: WhatsApp user ID
            first_name: User's first name
            last_name: User's last name (optional)
            phone_number: User's phone number (optional)

        Returns:
            User data with authorization flag
        """
        try:
            if not self.db_session_factory:
                self.logger.warning("Database session factory not available")
                return self.create_fallback_data(first_name, platform_user_id, last_name)

            async with self.db_session_factory() as session:
                return await self._process_user_in_session(
                    session, platform_user_id, first_name, last_name, phone_number
                )

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error(f"Error getting/creating user {platform_user_id}: {e}", exc_info=True)
            return self.create_fallback_data(first_name, platform_user_id, last_name)

    async def _process_user_in_session(
        self,
        session: AsyncSession,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str],
        phone_number: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Process user operations within database session."""
        # Try to get existing user
        user_db = await user_crud.get_user_by_platform_id(
            session, "whatsapp", platform_user_id
        )

        is_new_user = False
        if not user_db:
            user_db = await self._create_new_user(
                session, platform_user_id, first_name, last_name, phone_number
            )
            is_new_user = True

        if not user_db:
            return self.create_fallback_data(first_name, platform_user_id, last_name)

        # Check authorization
        is_authorized = await self.check_user_authorization(session, user_db.id)

        # Auto-authorize new users with phone numbers
        if is_new_user and phone_number and not is_authorized:
            is_authorized = await self.auto_authorize_user_with_phone(
                session, user_db.id, platform_user_id, phone_number
            )

        return self.build_user_data_result(user_db, platform_user_id, is_authorized)

    async def _create_new_user(
        self,
        session: AsyncSession,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str],
        phone_number: Optional[str],
    ) -> Any:
        """Create new user in database."""
        user_details = self.prepare_user_details(first_name, last_name, phone_number)
        user_db = await user_crud.create_or_update_user(
            session, "whatsapp", platform_user_id, user_details
        )
        self.logger.info(
            f"Created new WhatsApp user: {platform_user_id}, "
            f"first_name: {first_name}, last_name: {last_name}, phone: {phone_number}"
        )
        return user_db
