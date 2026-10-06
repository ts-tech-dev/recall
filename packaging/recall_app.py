"""PyInstaller entry point for the desktop app."""

import multiprocessing

from recall.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes (large PDFs) must not start the app again
    main()
