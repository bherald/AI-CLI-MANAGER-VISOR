"""One command for a local Windows beta build; never changes machine-wide settings."""
import os

from native_build import main as native_main


def main():
    if os.name != "nt":
        raise SystemExit("Run this script on Windows; use scripts/native_build.py on other hosts")
    native_main()


if __name__ == "__main__":
    main()
