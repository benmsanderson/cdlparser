# cdlparser
Python 3 NetCDF CDL parser for netcdf
ported from https://github.com/rockdoc/cdlparser

Install from pip:
```
pip install CDLparser
```

Usage:
```
import cdlparser

myparser = cdlparser.CDL3Parser()
myparser.parse_file('myfile.cdl', ncfile="myfile.nc")
```
