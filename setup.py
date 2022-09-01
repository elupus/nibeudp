from setuptools import find_packages, setup

setup(
    name="nibeudp",
    packages=find_packages(),
    install_requires=[
        "anyio",
    ],
    extras_require={"cli": ["asyncclick==8.*"]},
)
