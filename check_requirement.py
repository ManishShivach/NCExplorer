# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""
Check which dependencies are installed and which are missing
"""

import sys
import importlib


def check_module(module_name):
    """Check if a module can be imported"""
    try:
        importlib.import_module(module_name)
        return True, None
    except ImportError as e:
        return False, str(e)


def main():
    print("=" * 60)
    print("GIS Toolkit - Dependency Checker")
    print("=" * 60 + "\n")

    # Define dependencies
    dependencies = {
        "Critical": [
            "PyQt6",
            "PyQt6.QtCore",
            "PyQt6.QtGui",
            "PyQt6.QtWidgets",
        ],
        "Core Functionality": [
            "numpy",
            "pandas",
            "matplotlib",
        ],
        "Geospatial": [
            "geopandas",
            "shapely",
            "rasterio",
            "pyproj",
        ],
        "Data Processing": [
            "xarray",
            "netCDF4",
            "h5py",
        ],
        "Optional": [
            "cartopy",
            "scipy",
            "PIL",
        ]
    }

    all_installed = True
    missing_critical = []

    for category, modules in dependencies.items():
        print(f"\n{category} Dependencies:")
        print("-" * 40)

        for module in modules:
            installed, error = check_module(module)

            if installed:
                print(f"  ✓ {module}")
            else:
                print(f"  ✗ {module} - MISSING")
                all_installed = False

                if category == "Critical":
                    missing_critical.append(module)

    print("\n" + "=" * 60)

    if all_installed:
        print("✓ All dependencies are installed!")
        print("You can proceed with building the application.")
    else:
        print("✗ Some dependencies are missing")

        if missing_critical:
            print("\n⚠ CRITICAL: The following are required:")
            for module in missing_critical:
                print(f"  • {module}")
            print("\nInstall with: pip install PyQt6")

        print("\nTo install all dependencies, run:")
        print("  pip install -r installer/requirements.txt")

    print("=" * 60 + "\n")

    return 0 if all_installed else 1


if __name__ == "__main__":
    sys.exit(main())