from setuptools import setup


with open("README.rst") as f:
    readme = f.read()


setup(
    name="tagnote",
    version="4.2.0",
    description="Minimalist note organization tool",
    long_description=readme,
    author="Michael Ren",
    author_email="michael.ren@mailbox.org",
    url="https://github.com/michael-ren/tagnote",
    packages=["tagnote"],
    python_requires=">=3.5",
    license="GPLv3",
    classifiers=(
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Environment :: Console",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: POSIX",
        "Operating System :: Unix"
    ),
    entry_points={
        "console_scripts": [
            'tag = tagnote.tag:main'
        ]
    }
)
