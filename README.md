# cdlparser
Python 3 NetCDF CDL parser for netcdf
ported from https://github.com/rockdoc/cdlparser

Dependencies:
* PLY - http://www.dabeaz.com/ply/                                                                      
* netcdf4-python - http://code.google.com/p/netcdf4-python/                                             
* NumPy - http://numpy.scipy.org/   

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
