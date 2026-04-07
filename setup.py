from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "fuzzy_ext",           
        ["fuzzy_ext.cpp"],     
        cxx_std=17             
    ),
]

setup(
    name="fuzzy_ext",
    version="1.0",
    description="C++ Fuzzy Matcher with pybind11",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
