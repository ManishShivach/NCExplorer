from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("installer/requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith('#')]

setup(
    name="gis-toolkit",
    version="1.0.0",
    author="Manish Shivach",
    author_email="iammanishshivach@gmail.com",

    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
    ],
    python_requires=">=3.7",
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'gis-toolkit=installer.launcher:main',
        ],
    },
    include_package_data=True,
    package_data={
        'gis_toolkit': ['assets/**/*'],
    },
)