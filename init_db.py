import asyncio
from app.core.database import engine, Base
import app.models  # Imports your models so SQLAlchemy registers them

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully in PostgreSQL!")

if __name__ == "__main__":
    asyncio.run(main())