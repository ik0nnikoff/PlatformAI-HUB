import os
import sys
from logging.config import fileConfig
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool
from dotenv import load_dotenv # Import load_dotenv

from alembic import context

# --- Load .env file ---
# Construct the path to the .env file relative to this script's location
# Adjust the path depth ('..', '..') as needed
DOTENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
if os.path.exists(DOTENV_PATH):
    load_dotenv(dotenv_path=DOTENV_PATH)
    print(f"Alembic env.py: Loaded environment variables from {DOTENV_PATH}") # Debug print
else:
    print(f"Alembic env.py: Warning! .env file not found at {DOTENV_PATH}")


# --- Import Base and Models ---
# Add the project root directory to the Python path
# Adjust the path depth ('..') based on where alembic/env.py is relative to the project root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

# Now import Base and models from your application
# Ensure agent_manager is discoverable (e.g., via PYTHONPATH or installed package)
try:
    from agent_manager.db import Base
    from agent_manager.models import AgentConfigDB # Import all your DB models here
except ImportError as e:
     print(f"Error importing agent_manager modules in alembic/env.py: {e}")
     print("Ensure agent_manager is in PYTHONPATH or installed.")
     sys.exit(1)


# --- Alembic Configuration ---

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Target Metadata ---
# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata # Use Base from your db module

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

# --- Database URL ---
# Get database URL directly from environment variable
db_url = os.getenv("DATABASE_URL")
if not db_url:
    # Try getting from alembic config as a fallback, though unlikely now
    db_url = config.get_main_option("sqlalchemy.url")
if not db_url:
    raise ValueError("DATABASE_URL environment variable not set or found.")
else:
    print(f"Alembic env.py: Using database URL: {db_url[:db_url.find('@') + 1]}********") # Print URL safely


# --- Run Migrations ---

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=db_url, # Use db_url obtained from os.getenv
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    """Helper function to run migrations with a connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Create async engine
    connectable = create_async_engine(
        db_url, # Use db_url obtained from os.getenv
        poolclass=pool.NullPool, # Use NullPool for migrations
        future=True # Ensure future=True for SQLAlchemy 2.0 style
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations) # Run sync function within async context

    await connectable.dispose() # Dispose the engine


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Run the async online migrations
    asyncio.run(run_migrations_online())

