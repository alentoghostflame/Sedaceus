from setuptools import setup, find_packages
import pathlib


# requirements = []
with open("requirements.txt") as f:
    requirements = f.read().splitlines()


here = pathlib.Path(__file__).parent.resolve()
long_description = (here / "README.md").read_text(encoding="utf-8")


setup(
    name="sedaceus",
    version="0.0.1",
    description="A Discord bot python library built on IPC",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/alentoghostflame/Sedaceus",
    author="Alex Schoenhofen",
    author_email="alexanderschoenhofen@gmail.com",
    license='MIT',
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.10",
    ],
    keywords="ipc, development",
    packages=find_packages(exclude="examples"),
    install_requires=requirements,
)
