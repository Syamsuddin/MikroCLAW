"""Memungkinkan `python -m mikroclaw.web` tanpa entry-point script."""

from .app import main

if __name__ == "__main__":
    main()
