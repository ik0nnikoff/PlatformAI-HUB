"""
User service for Telegram integration.

Handles user creation, authorization, and context extraction
following single responsibility principle.
"""

import logging
from typing import Any, Dict, Optional
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram.types import User

from app.db.crud import user_crud


@dataclass
class UserContext:
    """User context data container."""
    platform_user_id: str
    first_name: str
    is_authorized: bool
    last_name: Optional[str] = None
    username: Optional[str] = None


@dataclass
class ContactDetails:
    """Contact authorization details container."""
    contact_platform_user_id: str
    phone_number: str
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None


class UserService:
    """Manages Telegram user operations with focused responsibility."""

    def __init__(
        self,
        agent_id: str,
        db_session_factory: Optional[async_sessionmaker[AsyncSession]],
        logger: logging.LoggerAdapter,
    ):
        self.agent_id = agent_id
        self.db_session_factory = db_session_factory
        self.logger = logger

    def extract_platform_user_id(self, telegram_user: User) -> str:
        """Extract platform user ID from Telegram user."""
        return str(telegram_user.id)

    def extract_user_names(self, telegram_user: User) -> tuple[str, Optional[str]]:
        """Extract first and last names from Telegram user."""
        first_name = telegram_user.first_name or "Unknown"
        last_name = telegram_user.last_name
        return first_name, last_name

    def extract_username(self, telegram_user: User) -> Optional[str]:
        """Extract username from Telegram user."""
        return telegram_user.username

    def create_user_context(self, user_context: UserContext) -> Dict[str, Any]:
        """Create user context dictionary."""
        context = {
            "platform_user_id": user_context.platform_user_id,
            "first_name": user_context.first_name,
            "is_authenticated": user_context.is_authorized,
            "platform": "telegram",
        }

        if user_context.last_name:
            context["last_name"] = user_context.last_name
        if user_context.username:
            context["username"] = user_context.username

        return context

    async def get_or_create_user(
        self, telegram_user: User, is_authorized: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get or create user with authorization check."""
        if not self.db_session_factory:
            self.logger.warning("Database session factory not configured")
            return self.create_fallback_data(telegram_user)

        try:
            async with self.db_session_factory() as session:
                user_db = await self._get_or_create_user_db(session, telegram_user)
                if not user_db:
                    return self.create_fallback_data(telegram_user)

                authorization_status = await self._check_user_authorization(
                    session, user_db.id, is_authorized
                )

                return self._build_user_data(user_db, authorization_status)

        except (ConnectionError, TimeoutError, ValueError) as e:
            platform_user_id = self.extract_platform_user_id(telegram_user)
            self.logger.error(
                "Database error getting/creating user %s: %s", platform_user_id, e, exc_info=True
            )
            return self.create_fallback_data(telegram_user)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            platform_user_id = self.extract_platform_user_id(telegram_user)
            self.logger.error(
                "Unexpected error getting/creating user %s: %s", platform_user_id, e, exc_info=True
            )
            return self.create_fallback_data(telegram_user)

    async def _get_or_create_user_db(self, session, telegram_user: User):
        """Get existing or create new user in database."""
        platform_user_id = self.extract_platform_user_id(telegram_user)
        first_name, last_name = self.extract_user_names(telegram_user)

        # Try to get existing user
        user_db = await user_crud.get_user_by_platform_id(
            session, "telegram", platform_user_id
        )

        if not user_db:
            # Create new user
            user_details = self._prepare_user_details(telegram_user)
            user_db = await user_crud.create_or_update_user(
                session, "telegram", platform_user_id, user_details
            )
            self.logger.info(
                "Created new Telegram user: %s, name: %s %s",
                platform_user_id,
                first_name,
                last_name or "",
            )

        return user_db

    def _prepare_user_details(self, telegram_user: User) -> dict:
        """Prepare user details for database creation."""
        first_name, last_name = self.extract_user_names(telegram_user)
        username = self.extract_username(telegram_user)

        user_details = {
            "first_name": first_name,
            "username": username or first_name,
        }
        if last_name:
            user_details["last_name"] = last_name
        return user_details

    async def _check_user_authorization(
        self, session, user_id: int, is_authorized: bool
    ) -> bool:
        """Check user authorization status."""
        if is_authorized:
            auth_record = await user_crud.get_agent_user_authorization(
                session, self.agent_id, user_id
            )
            return auth_record and auth_record.is_authorized
        return is_authorized

    def _build_user_data(self, user_db, is_authorized: bool) -> Dict[str, Any]:
        """Build user data dictionary from database user."""
        platform_user_id = user_db.platform_user_id or str(user_db.id)

        user_data = {
            "user_id": user_db.id,
            "first_name": user_db.first_name,
            "username": user_db.username,
            "platform_user_id": platform_user_id,
            "platform": "telegram",
            "is_authenticated": is_authorized,
        }

        if user_db.last_name:
            user_data["last_name"] = user_db.last_name
        if user_db.phone_number:
            user_data["phone_number"] = user_db.phone_number

        return user_data

    def create_fallback_data(self, telegram_user: User) -> Dict[str, Any]:
        """Create fallback user data when database operations fail."""
        platform_user_id = self.extract_platform_user_id(telegram_user)
        first_name, last_name = self.extract_user_names(telegram_user)
        username = self.extract_username(telegram_user)

        fallback_data = {
            "platform_user_id": platform_user_id,
            "first_name": first_name,
            "username": username or first_name,
            "is_authenticated": False,
            "platform": "telegram",
        }

        if last_name:
            fallback_data["last_name"] = last_name

        return fallback_data

    async def authorize_user_by_contact(
        self, contact_details: ContactDetails
    ) -> Optional[Dict[str, Any]]:
        """Authorize user using contact information."""
        if not self.db_session_factory:
            self.logger.error("Database session factory not configured")
            return None

        try:
            async with self.db_session_factory() as session:
                user = await self._create_or_update_contact_user(
                    session, contact_details
                )

                if not user:
                    return None

                return await self._authorize_and_build_user_data(
                    session, user, contact_details
                )

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Database error authorizing user %s: %s",
                contact_details.contact_platform_user_id,
                e,
                exc_info=True,
            )
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error authorizing user %s: %s",
                contact_details.contact_platform_user_id,
                e,
                exc_info=True,
            )
            return None

    async def _create_or_update_contact_user(
        self, session, contact_details: ContactDetails
    ):
        """Create or update user with contact details."""
        user_details = self._prepare_contact_user_details(contact_details)

        return await user_crud.create_or_update_user(
            session, "telegram", contact_details.contact_platform_user_id, user_details
        )

    def _prepare_contact_user_details(self, contact_details: ContactDetails) -> dict:
        """Prepare user details for contact-based user creation."""
        user_details = {
            "phone_number": contact_details.phone_number,
            "first_name": contact_details.first_name,
            "username": contact_details.username,
        }
        if contact_details.last_name:
            user_details["last_name"] = contact_details.last_name

        # Filter out None values
        return {k: v for k, v in user_details.items() if v is not None}

    async def _authorize_and_build_user_data(
        self, session, user, contact_details: ContactDetails
    ) -> Optional[Dict[str, Any]]:
        """Authorize user and build response data."""
        auth_record = await user_crud.update_agent_user_authorization(
            session, self.agent_id, user.id, True
        )

        if auth_record and auth_record.is_authorized:
            self.logger.info(
                "User %s (TG: %s) authorized for agent %s",
                user.id,
                contact_details.contact_platform_user_id,
                self.agent_id,
            )
            return self._build_contact_user_response(user, contact_details)

        return None

    def _build_contact_user_response(
        self, user, contact_details: ContactDetails
    ) -> Dict[str, Any]:
        """Build user response data for contact authorization."""
        return {
            "user_id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "platform_user_id": contact_details.contact_platform_user_id,
            "platform": "telegram",
            "is_authenticated": True,
            "phone_number": contact_details.phone_number,
            "last_name": contact_details.last_name,
        }

    async def check_user_authorization(self, platform_user_id: str) -> bool:
        """Check if user is authorized for this agent."""
        if not self.db_session_factory:
            self.logger.warning("Database session factory not configured")
            return False

        try:
            async with self.db_session_factory() as session:
                user_db = await user_crud.get_user_by_platform_id(
                    session, "telegram", platform_user_id
                )
                if not user_db:
                    return False

                auth_record = await user_crud.get_agent_user_authorization(
                    session, self.agent_id, user_db.id
                )
                return auth_record and auth_record.is_authorized

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Database error checking authorization for %s: %s", platform_user_id, e
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error checking authorization for %s: %s", platform_user_id, e
            )
            return False
