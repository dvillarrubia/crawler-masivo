"""Initialize database tables. Run once before first use."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import init_db

if __name__ == "__main__":
    print("Creating database tables...")
    init_db()
    print("Done.")
