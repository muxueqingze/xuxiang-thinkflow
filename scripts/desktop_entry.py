"""PyInstaller entry point; the service and CLI use the same source package."""
from src.desktop_service import main

if __name__ == "__main__":
    main()
