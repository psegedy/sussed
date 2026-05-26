-- sussed database initialization
-- This runs automatically when the PostgreSQL container starts fresh

-- Enable UUID extension (for uuid_generate_v4)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- We'll let SQLModel/SQLAlchemy handle table creation
-- This file is here for any custom extensions or initial setup

-- Log that initialization is complete
DO $$
BEGIN
    RAISE NOTICE 'sussed database initialized successfully! 🏠🔥';
END $$;
